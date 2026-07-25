from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from pathlib import Path

from fern.bft.node import ConsensusTiming, ValidatorNode
from fern.bft.store import BFTStore
from fern.bft.validators import Validator, make_validator_set
from fern.bft.websocket import BFTWebSocketClient, PeerBroadcaster, ServerMetadata, ValidatorServer
from fern.crypto.keys import Keypair
from fern.events.event import Event
from tests.bft.helpers import genesis_fixture


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class LiveValidator:
    index: int
    keypair: Keypair
    url: str
    store_path: Path
    timing: ConsensusTiming
    ingress_limit: int = 10_000
    ingress_window_seconds: int = 60
    store: BFTStore | None = None
    node: ValidatorNode | None = None
    server: ValidatorServer | None = None
    shutdown: asyncio.Event | None = None
    task: asyncio.Task[None] | None = None

    @property
    def online(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self) -> None:
        if self.online:
            return
        port = int(self.url.rsplit(":", 1)[1])
        self.store = BFTStore(self.store_path)
        self.node = ValidatorNode(
            keypair=self.keypair,
            store=self.store,
            broadcast=PeerBroadcaster(self.keypair.pubkey_hex, timeout=0.25),
            timing=self.timing,
            autostart=False,
        )
        self.server = ValidatorServer(
            node=self.node,
            metadata=ServerMetadata(
                name=f"validator-{self.index}",
                description="live system test validator",
                pubkey=self.keypair.pubkey_hex,
            ),
            host="127.0.0.1",
            port=port,
            ingress_limit=self.ingress_limit,
            ingress_window_seconds=self.ingress_window_seconds,
        )
        self.shutdown = asyncio.Event()
        self.task = asyncio.create_task(
            self.server.run(self.shutdown), name=f"live-validator-{self.index}"
        )
        client = BFTWebSocketClient(self.url, timeout=0.25)
        for _ in range(200):
            if self.task.done():
                await self.task
            try:
                await client.metadata()
                return
            except (OSError, TimeoutError):
                await asyncio.sleep(0.01)
        raise AssertionError(f"validator {self.index} did not start at {self.url}")

    async def stop(self) -> None:
        if self.shutdown is not None:
            self.shutdown.set()
        if self.task is not None:
            await asyncio.wait_for(self.task, timeout=3)
        if self.node is not None:
            await asyncio.wait_for(self.node.stop(), timeout=3)
        if self.store is not None:
            self.store.close()
        self.store = None
        self.node = None
        self.server = None
        self.shutdown = None
        self.task = None


class LiveCluster:
    def __init__(
        self,
        root: Path,
        *,
        validator_count: int = 4,
        faults: int = 1,
        timing: ConsensusTiming | None = None,
        ingress_limit: int = 10_000,
        ingress_window_seconds: int = 60,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.timing = timing or ConsensusTiming(
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
        keys = tuple(Keypair.generate() for _ in range(validator_count))
        ports = tuple(_unused_port() for _ in keys)
        validators = tuple(
            Validator(
                pubkey=key.pubkey_hex,
                url=f"ws://127.0.0.1:{port}",
                operator=f"operator-{index}",
            )
            for index, (key, port) in enumerate(zip(keys, ports, strict=True))
        )
        self.validator_set = make_validator_set(validators, epoch=0, fault_tolerance=faults)
        self.validators = [
            LiveValidator(
                index=index,
                keypair=key,
                url=f"ws://127.0.0.1:{port}",
                store_path=root / f"validator-{index}.sqlite",
                timing=self.timing,
                ingress_limit=ingress_limit,
                ingress_window_seconds=ingress_window_seconds,
            )
            for index, (key, port) in enumerate(zip(keys, ports, strict=True))
        ]
        self.group_key, self.founder, self.genesis, self.initial_head, self.channel_id = (
            genesis_fixture(self.validator_set)
        )

    @property
    def group(self) -> str:
        return self.genesis.group

    @property
    def urls(self) -> list[str]:
        return [validator.url for validator in self.validators]

    async def start(self) -> None:
        await asyncio.gather(*(validator.start() for validator in self.validators))
        await asyncio.gather(
            *(
                BFTWebSocketClient(validator.url, timeout=1).bootstrap(self.genesis)
                for validator in self.validators
            )
        )

    async def close(self) -> None:
        await asyncio.gather(
            *(validator.stop() for validator in self.validators if validator.online)
        )

    async def wait_for_height(
        self, height: int, *, indices: tuple[int, ...] | None = None, timeout: float = 8
    ) -> None:
        selected = indices or tuple(
            validator.index for validator in self.validators if validator.online
        )
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            statuses = await asyncio.gather(
                *(
                    BFTWebSocketClient(self.validators[index].url, timeout=0.5).status(self.group)
                    for index in selected
                ),
                return_exceptions=True,
            )
            if statuses and all(
                not isinstance(status, BaseException) and status.height >= height
                for status in statuses
            ):
                return
            await asyncio.sleep(0.05)
        observed = []
        for index in selected:
            validator = self.validators[index]
            if validator.store is None:
                observed.append(f"{index}=offline")
            else:
                observed.append(f"{index}={validator.store.get_chain_head(self.group).height}")
        raise AssertionError(f"validators did not reach height {height}: {', '.join(observed)}")

    async def submit(
        self, event: Event, *, indices: tuple[int, ...] | None = None
    ) -> tuple[object, ...]:
        selected = indices or tuple(
            validator.index for validator in self.validators if validator.online
        )
        return tuple(
            await asyncio.gather(
                *(
                    BFTWebSocketClient(self.validators[index].url, timeout=1).submit_event(event)
                    for index in selected
                ),
                return_exceptions=True,
            )
        )


__all__ = ["LiveCluster", "LiveValidator"]
