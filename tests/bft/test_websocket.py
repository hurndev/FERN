from __future__ import annotations

import asyncio
import os
import socket
import time

import pytest

from fern.bft.node import ConsensusTiming, ValidatorNode
from fern.bft.admission import prepare_validator_history
from fern.bft.client import sync_from_validators
from fern.bft.notices import OperatorNotice, verify_operator_notice
from fern.bft.store import BFTStore
from fern.bft.websocket import BFTWebSocketClient, ServerMetadata, ValidatorServer
from fern.crypto.keys import Keypair
from fern.validator.config import ValidatorConfig, save_config
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


@pytest.mark.asyncio
async def test_request_readiness_policy_gated_remote_preparation(tmp_path) -> None:
    keys, validator_set = validator_fixture(faults=0)
    _group_key, founder, genesis, initial_head, channel_id = genesis_fixture(validator_set)
    host_store = BFTStore(tmp_path / "host.sqlite")
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
    host_node = ValidatorNode(keypair=keys[0], store=host_store, timing=timing)
    host_port = _unused_port()
    host_server = ValidatorServer(
        node=host_node,
        metadata=ServerMetadata(name="host", description="", pubkey=keys[0].pubkey_hex),
        host="127.0.0.1",
        port=host_port,
    )
    host_shutdown = asyncio.Event()
    host_task = asyncio.create_task(host_server.run(host_shutdown))
    host_url = f"ws://127.0.0.1:{host_port}"
    host_client = BFTWebSocketClient(host_url)
    for _ in range(100):
        try:
            await host_client.metadata()
            break
        except OSError:
            await asyncio.sleep(0.01)
    await host_client.bootstrap(genesis)
    event = message_fixture(founder, initial_head, channel_id)
    await host_client.submit_event(event)
    for _ in range(200):
        status = await host_client.status(genesis.group)
        if status.height == 1:
            break
        await asyncio.sleep(0.01)
    assert status.height == 1

    async def start_candidate(name: str, trusted: dict[str, str]) -> tuple[
        Keypair, BFTStore, ValidatorNode, str, asyncio.Event, asyncio.Task[None]
    ]:
        key = Keypair.generate()
        candidate_store = BFTStore(tmp_path / f"{name}.sqlite")
        candidate_node = ValidatorNode(keypair=key, store=candidate_store, timing=timing)
        port = _unused_port()
        candidate_server = ValidatorServer(
            node=candidate_node,
            metadata=ServerMetadata(name=name, description="", pubkey=key.pubkey_hex),
            host="127.0.0.1",
            port=port,
            trusted_operators=trusted,
            minimum_trusted_operators=1,
        )
        shutdown = asyncio.Event()
        task = asyncio.create_task(candidate_server.run(shutdown))
        client = BFTWebSocketClient(f"ws://127.0.0.1:{port}")
        for _ in range(100):
            try:
                await client.metadata()
                break
            except OSError:
                await asyncio.sleep(0.01)
        return key, candidate_store, candidate_node, f"ws://127.0.0.1:{port}", shutdown, task

    trusted_key, trusted_store, trusted_node, trusted_url, trusted_shutdown, trusted_task = (
        await start_candidate("trusted", {keys[0].pubkey_hex: "operator-a"})
    )
    untrusted_key, untrusted_store, untrusted_node, untrusted_url, untrusted_shutdown, untrusted_task = (
        await start_candidate("untrusted", {})
    )

    try:
        # A validator trusting the host operator is admitted, fully verified,
        # and returns readiness bound to the current checkpoint.
        readiness = await BFTWebSocketClient(trusted_url, timeout=30).request_readiness(
            genesis.group, [host_url]
        )
        assert readiness.validator == trusted_key.pubkey_hex
        assert readiness.checkpoint_height == 1
        assert readiness.from_epoch == 0 and readiness.to_epoch == 1
        assert trusted_store.get_chain_head(genesis.group).height == 1

        # A validator with no trusted hosts is refused before any history
        # transfer: the WoT threshold protects its permanent storage.
        with pytest.raises(ValueError, match="admission threshold was not met"):
            await BFTWebSocketClient(untrusted_url, timeout=10).request_readiness(
                genesis.group, [host_url]
            )
        assert untrusted_store.get_genesis(genesis.group) is None

        # An already-active validator cannot request readiness for its own group.
        with pytest.raises(ValueError, match="already active"):
            await host_client.request_readiness(genesis.group, [host_url])
    finally:
        for shutdown in (host_shutdown, trusted_shutdown, untrusted_shutdown):
            shutdown.set()
        await asyncio.wait_for(host_task, timeout=1.0)
        await asyncio.wait_for(trusted_task, timeout=1.0)
        await asyncio.wait_for(untrusted_task, timeout=1.0)
        for node in (host_node, trusted_node, untrusted_node):
            await node.stop()
        for candidate_store in (host_store, trusted_store, untrusted_store):
            candidate_store.close()


@pytest.mark.asyncio
async def test_metadata_serves_signed_operator_notice(tmp_path) -> None:
    keys, _validator_set = validator_fixture(faults=0)
    config_path = tmp_path / "config.json"
    key_path = tmp_path / "validator.key"
    key_path.write_text(keys[0].privkey_hex, encoding="utf-8")
    now = int(time.time())

    def write_notice(text: str, expires: int, stamp: int) -> None:
        save_config(
            ValidatorConfig(
                store=str(tmp_path / "server.sqlite"),
                key_file=str(key_path),
                notice_text=text,
                notice_ts=now,
                notice_expires=expires,
            ),
            config_path,
        )
        # Distinct mtimes so the server's change detection fires even on
        # filesystems with coarse timestamp granularity.
        os.utime(config_path, (stamp, stamp))

    write_notice("Down for maintenance Tuesday", now + 3600, now + 10)

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
        notice_file=config_path,
    )
    shutdown = asyncio.Event()
    task = asyncio.create_task(server.run(shutdown))
    client = BFTWebSocketClient(f"ws://127.0.0.1:{port}")
    metadata: dict[str, object] = {}
    for _ in range(100):
        try:
            metadata = await client.metadata()
            break
        except OSError:
            await asyncio.sleep(0.01)

    try:
        # An active notice is served, signed by the validator key.
        raw_notice = metadata.get("notice")
        assert isinstance(raw_notice, dict)
        notice = OperatorNotice.from_dict(raw_notice)
        assert notice.validator == keys[0].pubkey_hex
        assert notice.text == "Down for maintenance Tuesday"
        assert verify_operator_notice(notice)

        # An expired notice is not served.
        write_notice("Down for maintenance Tuesday", now - 10, now + 20)
        metadata = await client.metadata()
        assert "notice" not in metadata

        # A replaced notice is picked up without a restart.
        write_notice("Back up again", now + 3600, now + 30)
        metadata = await client.metadata()
        raw_notice = metadata.get("notice")
        assert isinstance(raw_notice, dict)
        assert OperatorNotice.from_dict(raw_notice).text == "Back up again"
    finally:
        shutdown.set()
        await asyncio.wait_for(task, timeout=1.0)
        await node.stop()
        store.close()


@pytest.mark.asyncio
async def test_peer_notice_aggregation(tmp_path) -> None:
    """A validator collects verified notices from its consensus peers."""

    keys, base_validator_set = validator_fixture(faults=0, validator_count=2)
    now = int(time.time())

    port_a = _unused_port()
    port_b = _unused_port()

    # Update validator URLs to match the actual server ports.
    from fern.bft.validators import Validator, make_validator_set

    validator_set = make_validator_set(
        [
            Validator(pubkey=keys[0].pubkey_hex, url=f"ws://127.0.0.1:{port_a}"),
            Validator(pubkey=keys[1].pubkey_hex, url=f"ws://127.0.0.1:{port_b}"),
        ],
        epoch=0,
        fault_tolerance=0,
    )

    # Validator A has a notice.
    config_a_path = tmp_path / "config_a.json"
    key_a_path = tmp_path / "key_a"
    key_a_path.write_text(keys[0].privkey_hex, encoding="utf-8")
    save_config(
        ValidatorConfig(
            store=str(tmp_path / "a.sqlite"),
            key_file=str(key_a_path),
            notice_text="Validator A maintenance",
            notice_ts=now,
            notice_expires=now + 3600,
        ),
        config_a_path,
    )

    # Validator B has no notice.
    config_b_path = tmp_path / "config_b.json"
    key_b_path = tmp_path / "key_b"
    key_b_path.write_text(keys[1].privkey_hex, encoding="utf-8")
    save_config(
        ValidatorConfig(
            store=str(tmp_path / "b.sqlite"),
            key_file=str(key_b_path),
        ),
        config_b_path,
    )

    _group_key, _founder, genesis_event, _head, _channel = genesis_fixture(validator_set)

    # Bootstrap both validators with the same group.
    store_a = BFTStore(tmp_path / "a.sqlite")
    node_a = ValidatorNode(
        keypair=keys[0], store=store_a,
        timing=ConsensusTiming(
            block_interval=0.02, observation_timeout=0.02,
            observation_timeout_delta=0.0, proposal_timeout=0.02,
            proposal_timeout_delta=0.0, prevote_timeout=0.02,
            prevote_timeout_delta=0.0, precommit_timeout=0.02,
            precommit_timeout_delta=0.0, round_backoff=0.01,
        ),
    )
    node_a.bootstrap(genesis_event)
    server_a = ValidatorServer(
        node=node_a,
        metadata=ServerMetadata(name="a", description="", pubkey=keys[0].pubkey_hex),
        host="127.0.0.1", port=port_a, notice_file=config_a_path,
    )

    store_b = BFTStore(tmp_path / "b.sqlite")
    node_b = ValidatorNode(
        keypair=keys[1], store=store_b,
        timing=ConsensusTiming(
            block_interval=0.02, observation_timeout=0.02,
            observation_timeout_delta=0.0, proposal_timeout=0.02,
            proposal_timeout_delta=0.0, prevote_timeout=0.02,
            prevote_timeout_delta=0.0, precommit_timeout=0.02,
            precommit_timeout_delta=0.0, round_backoff=0.01,
        ),
    )
    node_b.bootstrap(genesis_event)
    server_b = ValidatorServer(
        node=node_b,
        metadata=ServerMetadata(name="b", description="", pubkey=keys[1].pubkey_hex),
        host="127.0.0.1", port=port_b, notice_file=config_b_path,
        peer_notice_refresh_interval=0.0,  # disable background loop
    )

    shutdown_a = asyncio.Event()
    shutdown_b = asyncio.Event()
    task_a = asyncio.create_task(server_a.run(shutdown_a))
    task_b = asyncio.create_task(server_b.run(shutdown_b))

    # Wait for both servers to start.
    for _ in range(100):
        try:
            await BFTWebSocketClient(f"ws://127.0.0.1:{port_a}").metadata()
            await BFTWebSocketClient(f"ws://127.0.0.1:{port_b}").metadata()
            break
        except OSError:
            await asyncio.sleep(0.01)

    try:
        # Before refresh, B has no peer notices (the background loop is off).
        metadata_b = await BFTWebSocketClient(f"ws://127.0.0.1:{port_b}").metadata()
        assert "peer_notices" not in metadata_b

        # After refresh, B sees A's notice.
        await server_b._refresh_peer_notices()
        metadata_b = await BFTWebSocketClient(f"ws://127.0.0.1:{port_b}").metadata()
        raw_peer = metadata_b.get("peer_notices")
        assert isinstance(raw_peer, list) and len(raw_peer) == 1
        peer_notice = OperatorNotice.from_dict(
            {str(k): v for k, v in raw_peer[0].items()}
        )
        assert peer_notice.validator == keys[0].pubkey_hex
        assert peer_notice.text == "Validator A maintenance"
        assert verify_operator_notice(peer_notice)

        # B itself does not appear in peer notices.
        peer_pubkeys = {
            OperatorNotice.from_dict({str(k): v for k, v in n.items()}).validator
            for n in raw_peer if isinstance(n, dict)
        }
        assert keys[1].pubkey_hex not in peer_pubkeys
    finally:
        shutdown_a.set()
        shutdown_b.set()
        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=2.0)
        await node_a.stop()
        await node_b.stop()
        store_a.close()
        store_b.close()
