"""Shared in-process FERN cluster for the chess example.

Used by ``run_cluster.py`` (and the examples in the README) to stand up real
validators — communicating over WebSocket on localhost — that host a chess
group. It lives in the example, not the core.
"""
from __future__ import annotations

import asyncio
import secrets
import socket
from pathlib import Path

from fern.bft.node import ConsensusTiming, ValidatorNode
from fern.bft.store import BFTStore
from fern.bft.validators import Validator, ValidatorSet, make_validator_set
from fern.bft.websocket import (
    BFTWebSocketClient,
    PeerBroadcaster,
    ServerMetadata,
    ValidatorServer,
)
from fern.crypto.keys import Keypair
from fern.events.build import build_event
from fern.events.event import Event

# Fast consensus timing so blocks finalize in a fraction of a second locally.
FAST_TIMING = ConsensusTiming(
    block_interval=0.08,
    observation_timeout=0.25,
    observation_timeout_delta=0.0,
    proposal_timeout=0.25,
    proposal_timeout_delta=0.0,
    prevote_timeout=0.25,
    prevote_timeout_delta=0.0,
    precommit_timeout=0.25,
    precommit_timeout_delta=0.0,
    round_backoff=0.02,
)


# The core's default ingress limit (60 requests/60s per IP) is too tight for
# scripted play, where every CLI action makes several requests; raise it.
INGRESS_LIMIT = 10_000
INGRESS_WINDOW_SECONDS = 60


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_chess_genesis(
    validator_set: ValidatorSet, founder: Keypair, *, name: str = "Example chess club"
) -> Event:
    """Build a genesis event for a new chess group founded by ``founder``."""
    group_key = Keypair.generate()
    return build_event(
        type="genesis",
        group=group_key.pubkey_hex,
        author_keypair=founder,
        seq=0,
        group_keypair=group_key,
        content={
            "chain_id": secrets.token_hex(32),
            "name": name,
            "description": "FERN app-module example",
            "public": True,
            "founder": founder.pubkey_hex,
            "validators": [v.to_dict() for v in validator_set.validators],
            "fault_tolerance": validator_set.fault_tolerance,
            "app": "chess",
        },
    )


class MiniCluster:
    """A throwaway in-process FERN validator cluster (no dependency on tests/)."""

    def __init__(self, root: Path, count: int = 4, faults: int = 1) -> None:
        keys = [Keypair.generate() for _ in range(count)]
        ports = [_unused_port() for _ in keys]
        validators = [
            Validator(pubkey=key.pubkey_hex, url=f"ws://127.0.0.1:{port}", operator=f"operator-{i}")
            for i, (key, port) in enumerate(zip(keys, ports, strict=True))
        ]
        self.validator_set = make_validator_set(validators, epoch=0, fault_tolerance=faults)
        self.urls = [validator.url for validator in validators]
        self._specs = list(zip(keys, ports, strict=True))
        self._root = root
        self._running: list[
            tuple[ValidatorNode, ValidatorServer, BFTStore, asyncio.Event, asyncio.Task[None]]
        ] = []

    async def start(self, genesis: Event) -> None:
        for index, (key, port) in enumerate(self._specs):
            store = BFTStore(self._root / f"validator-{index}.sqlite")
            node = ValidatorNode(
                keypair=key,
                store=store,
                broadcast=PeerBroadcaster(key.pubkey_hex, timeout=0.25),
                timing=FAST_TIMING,
                autostart=False,
            )
            server = ValidatorServer(
                node=node,
                metadata=ServerMetadata(
                    name=f"chess-validator-{index}",
                    description="example chess cluster",
                    pubkey=key.pubkey_hex,
                ),
                host="127.0.0.1",
                port=port,
                ingress_limit=INGRESS_LIMIT,
                ingress_window_seconds=INGRESS_WINDOW_SECONDS,
            )
            shutdown = asyncio.Event()
            task = asyncio.create_task(server.run(shutdown), name=f"chess-validator-{index}")
            self._running.append((node, server, store, shutdown, task))
            client = BFTWebSocketClient(f"ws://127.0.0.1:{port}", timeout=0.25)
            for _ in range(200):
                if task.done():
                    await task
                try:
                    await client.metadata()
                    break
                except (OSError, TimeoutError):
                    await asyncio.sleep(0.01)
            else:
                raise AssertionError(f"validator {index} did not start")
        await asyncio.gather(
            *(BFTWebSocketClient(url, timeout=1).bootstrap(genesis) for url in self.urls)
        )

    async def stop(self) -> None:
        for node, _server, store, shutdown, task in self._running:
            shutdown.set()
            await asyncio.wait_for(task, timeout=3)
            await asyncio.wait_for(node.stop(), timeout=3)
            store.close()
        self._running.clear()


__all__ = ["FAST_TIMING", "MiniCluster", "build_chess_genesis"]
