from __future__ import annotations

import asyncio
import socket

import pytest

from fern.bft.node import ConsensusTiming, ValidatorNode
from fern.bft.admission import prepare_validator_history
from fern.bft.client import sync_from_validators
from fern.bft.store import BFTStore
from fern.bft.websocket import BFTWebSocketClient, ServerMetadata, ValidatorServer
from fern.crypto.keys import Keypair
from tests.bft.helpers import genesis_fixture, message_fixture, validator_fixture


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_websocket_bootstrap_submit_and_sync(tmp_path) -> None:
    keys, validator_set = validator_fixture(faults=0)
    _group_key, founder, genesis, initial_head, channel_id = genesis_fixture(validator_set)
    store = BFTStore(tmp_path / "server.sqlite")
    timing = ConsensusTiming(
        block_interval=0.02,
        observation_timeout=0.02,
        observation_timeout_delta=0.0,
        proposal_timeout=0.02,
        proposal_timeout_delta=0.0,
        prevote_timeout=0.02,
        prevote_timeout_delta=0.0,
        precommit_timeout=0.02,
        precommit_timeout_delta=0.0,
        round_backoff=0.01,
    )
    node = ValidatorNode(keypair=keys[0], store=store, timing=timing)
    port = _unused_port()
    server = ValidatorServer(
        node=node,
        metadata=ServerMetadata(name="test", description="", pubkey=keys[0].pubkey_hex),
        host="127.0.0.1",
        port=port,
    )
    shutdown = asyncio.Event()
    task = asyncio.create_task(server.run(shutdown))
    client = BFTWebSocketClient(f"ws://127.0.0.1:{port}")
    for _ in range(100):
        try:
            await client.metadata()
            break
        except OSError:
            await asyncio.sleep(0.01)
    await client.bootstrap(genesis)
    event = message_fixture(founder, initial_head, channel_id)
    receipt = await client.submit_event(event)
    assert receipt.event_id == event.id

    for _ in range(200):
        status = await client.status(genesis.group)
        if status.height == 1:
            break
        await asyncio.sleep(0.01)
    assert status.height == 1
    commits = await client.get_commits(genesis.group)
    assert len(commits) == 1
    assert commits[0].block.candidate.events == (event,)

    client_store = BFTStore(tmp_path / "client.sqlite")
    result = await sync_from_validators(
        group=genesis.group,
        urls=[f"ws://127.0.0.1:{port}"],
        store=client_store,
    )
    assert result.height_after == 1
    assert client_store.get_chain_head(genesis.group).block_hash == status.block_hash
    client_store.close()

    prospective_key = Keypair.generate()
    prospective_store = BFTStore(tmp_path / "prospective.sqlite")
    prepared = await prepare_validator_history(
        group=genesis.group,
        urls=[f"ws://127.0.0.1:{port}"],
        store=prospective_store,
        keypair=prospective_key,
        trusted_operators={keys[0].pubkey_hex: "operator-a"},
        minimum_operators=1,
    )
    assert prepared.admission.admission_class == "normal"
    assert prepared.readiness.validator == prospective_key.pubkey_hex
    assert prepared.readiness.checkpoint_height == 1
    prospective_store.close()

    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert server._server is None
    await node.stop()
    store.close()
