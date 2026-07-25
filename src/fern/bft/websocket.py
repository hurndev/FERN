from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from fern.bft.blocks import Commit
from fern.bft.canonical import sign_payload, verify_payload_signature
from fern.bft.canonical import strict_int
from fern.bft.certificates import IngressReceipt
from fern.bft.constants import MAX_BLOCK_BYTES, PROTOCOL_VERSION
from fern.bft.manifest import (
    HistoryManifest,
    HostingAttestation,
    sign_history_manifest,
    sign_hosting_attestation,
    verify_history_manifest,
)
from fern.bft.node import ValidatorNode
from fern.bft.validators import ValidatorSet
from fern.events.event import Event
from fern.errors import VerificationError
from fern.validator.rate_limiter import RateLimiter
from fern.crypto.encoding import is_valid_event_id_hex, is_valid_pubkey_hex, is_valid_sig_hex
from fern.crypto.keys import Keypair


logger = logging.getLogger(__name__)


def _encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return {str(key): item for key, item in value.items()}


class PeerBroadcaster:
    """Best-effort authenticated-object gossip to the active validator set."""

    def __init__(self, local_pubkey: str, *, timeout: float = 3.0) -> None:
        self.local_pubkey = local_pubkey
        self.timeout = timeout

    async def __call__(self, message: dict[str, object], validators: ValidatorSet) -> None:
        group = _message_group(message)

        async def send_peer(url: str) -> str | None:
            try:
                async with connect(
                    url,
                    open_timeout=self.timeout,
                    close_timeout=self.timeout,
                    max_size=MAX_BLOCK_BYTES * 2,
                ) as websocket:
                    await websocket.send(
                        _encode({"action": "peer", "group": group, "message": message})
                    )
                    await asyncio.wait_for(websocket.recv(), timeout=self.timeout)
                return None
            except (OSError, TimeoutError, ConnectionClosed) as exc:
                return f"{url} ({type(exc).__name__})"

        targets = tuple(
            validator.url
            for validator in validators.validators
            if validator.pubkey != self.local_pubkey
        )
        results = await asyncio.gather(*(send_peer(url) for url in targets))
        failures = tuple(result for result in results if result is not None)
        if failures:
            logger.debug(
                "peer broadcast incomplete group=%s type=%s delivered=%d/%d failures=%s",
                group[:12],
                message.get("type", "unknown"),
                len(targets) - len(failures),
                len(targets),
                ", ".join(failures),
            )


def _message_group(message: dict[str, object]) -> str:
    for key in ("event", "candidate", "observation", "proposal", "vote", "commit"):
        nested = message.get(key)
        if not isinstance(nested, dict):
            continue
        group = nested.get("group")
        if isinstance(group, str):
            return group
        block = nested.get("block")
        if isinstance(block, dict) and isinstance(block.get("group"), str):
            return str(block["group"])
    raise ValueError("peer message is not bound to a group")


@dataclass(frozen=True)
class ServerMetadata:
    name: str
    description: str
    pubkey: str
    version: str = "0.2.0-bft"

    def to_dict(self, groups: tuple[str, ...]) -> dict[str, object]:
        return {
            "protocol": PROTOCOL_VERSION,
            "name": self.name,
            "description": self.description,
            "pubkey": self.pubkey,
            "software": "fern-bft-python",
            "version": self.version,
            "groups": list(groups),
            "retention": {"default": "full"},
            "role": "validator",
        }


class ValidatorServer:
    def __init__(
        self,
        *,
        node: ValidatorNode,
        metadata: ServerMetadata,
        host: str = "0.0.0.0",
        port: int = 8765,
        allow_genesis: bool = True,
        ingress_limit: int = 60,
        ingress_window_seconds: int = 60,
        maximum_message_bytes: int = MAX_BLOCK_BYTES * 2,
        catchup_interval: float = 2.0,
        catchup_timeout: float = 1.0,
    ) -> None:
        self.node = node
        self.store = node.store
        self.metadata = metadata
        self.host = host
        self.port = port
        self.allow_genesis = allow_genesis
        self.ingress_limit = ingress_limit
        self.ingress_window_seconds = ingress_window_seconds
        self.maximum_message_bytes = maximum_message_bytes
        self.catchup_interval = catchup_interval
        self.catchup_timeout = catchup_timeout
        self._rate_limiter = RateLimiter()
        self._subscriptions: dict[str, set[ServerConnection]] = {}
        self._attached_engines: dict[str, int] = {}
        self._catchup_requested = asyncio.Event()
        self._server: Any = None
        for group in node.engines:
            self._attach_engine(group)

    def _attach_engine(self, group: str) -> None:
        engine = self.node.engines.get(group)
        if engine is None or self._attached_engines.get(group) == id(engine):
            return
        engine.add_commit_listener(self._push_commit)
        engine.add_pending_listener(self._push_pending)
        self._attached_engines[group] = id(engine)

    async def _push_commit(self, commit: Commit) -> None:
        await self._push(
            commit.block.group,
            {"type": "commit", "commit": commit.to_dict()},
        )

    async def _push_pending(self, event: Event, receipt: IngressReceipt) -> None:
        await self._push(
            event.group,
            {
                "type": "pending_event",
                "event": event.to_dict(),
                "ingress_receipt": receipt.to_dict(),
            },
        )

    async def _push(self, group: str, message: dict[str, object]) -> None:
        subscribers = tuple(self._subscriptions.get(group, set()))
        if not subscribers:
            return
        encoded = _encode(message)
        stale: list[ServerConnection] = []
        for websocket in subscribers:
            try:
                await websocket.send(encoded)
            except ConnectionClosed:
                stale.append(websocket)
        for websocket in stale:
            self._subscriptions.get(group, set()).discard(websocket)

    async def handler(self, websocket: ServerConnection) -> None:
        subscribed: set[str] = set()
        try:
            async for raw in websocket:
                action = "unknown"
                try:
                    if not isinstance(raw, str):
                        raise ValueError("binary messages are not supported")
                    request = json.loads(raw)
                    if not isinstance(request, dict):
                        raise ValueError("request must be an object")
                    action = str(request.get("action", ""))
                    self._check_rate_limit(websocket, action)
                    response = await self._handle_request(websocket, request, subscribed)
                except (VerificationError, ValueError, TypeError, KeyError) as exc:
                    logger.warning(
                        "request rejected action=%s remote=%s reason=%s",
                        action,
                        websocket.remote_address,
                        exc,
                    )
                    response = {"type": "error", "message": str(exc)}
                await websocket.send(_encode(response))
        except ConnectionClosed:
            pass
        finally:
            for group in subscribed:
                self._subscriptions.get(group, set()).discard(websocket)

    def _check_rate_limit(self, websocket: ServerConnection, action: str) -> None:
        remote = websocket.remote_address
        key = str(remote[0]) if isinstance(remote, tuple) and remote else "unknown"
        if action == "submit_event":
            maximum, window = self.ingress_limit, self.ingress_window_seconds
        elif action in {"bootstrap", "history_manifest", "hosting_attestation"}:
            maximum, window = 30, 60
        elif action == "peer":
            maximum, window = 600, 60
        else:
            maximum, window = 240, 60
        if (
            maximum <= 0
            or window <= 0
            or not self._rate_limiter.allow(action, key, maximum, window)
        ):
            raise ValueError("rate limit exceeded")

    async def _handle_request(
        self,
        websocket: ServerConnection,
        request: dict[str, object],
        subscribed: set[str],
    ) -> dict[str, object]:
        action = str(request.get("action", ""))
        if action == "metadata":
            return {
                "type": "metadata",
                "metadata": self.metadata.to_dict(self.store.hosted_groups()),
            }
        if action == "bootstrap":
            if not self.allow_genesis:
                raise ValueError("genesis auto-hosting is disabled")
            genesis = Event.from_dict(_object(request.get("genesis"), "genesis"))
            head = self.node.bootstrap(genesis)
            self._attach_engine(genesis.group)
            return {"type": "bootstrapped", "group": genesis.group, "state_root": head.state.root}
        if action == "peer":
            group = str(request.get("group", ""))
            message = _object(request.get("message"), "message")
            if _message_group(message) != group:
                raise ValueError("peer message group mismatch")
            await self.node.handle_peer_message(group, message)
            if group in self.node.engines:
                self._attach_engine(group)
            raw_commit = message.get("commit") if message.get("type") == "commit" else None
            if isinstance(raw_commit, dict):
                raw_block = raw_commit.get("block")
                if isinstance(raw_block, dict) and isinstance(raw_block.get("height"), int):
                    local_height = self.store.get_chain_head(group).height
                    if int(raw_block["height"]) > local_height:
                        self._catchup_requested.set()
            return {"type": "ok"}
        if action == "submit_event":
            event = Event.from_dict(_object(request.get("event"), "event"))
            receipt = await self.node.submit_event(event)
            return {"type": "ingress_receipt", "ingress_receipt": receipt.to_dict()}
        if action == "get_genesis":
            group = str(request.get("group", ""))
            stored_genesis = self.store.get_genesis(group)
            if stored_genesis is None:
                return {"type": "not_found", "group": group}
            return {"type": "genesis", "genesis": stored_genesis.to_dict()}
        if action == "status":
            group = str(request.get("group", ""))
            head = self.store.get_chain_head(group)
            signed_status = sign_validator_status(
                ValidatorStatus(
                    group=group,
                    chain_id=head.state.chain_id,
                    height=head.height,
                    block_hash=head.block_hash,
                    history_root=head.history_root,
                    state_root=head.state.root,
                    epoch=head.state.validator_set.epoch,
                    validator_set=head.state.validator_set,
                    logical_bytes=head.logical_bytes,
                    validator=self.node.keypair.pubkey_hex,
                ),
                self.node.keypair,
            )
            return signed_status.to_dict()
        if action == "history_manifest":
            group = str(request.get("group", ""))
            head = self.store.get_chain_head(group)
            now = int(time.time())
            manifest = sign_history_manifest(
                HistoryManifest(
                    group=group,
                    chain_id=head.state.chain_id,
                    epoch=head.state.validator_set.epoch,
                    height=head.height,
                    block_hash=head.block_hash,
                    history_root=head.history_root,
                    state_root=head.state.root,
                    event_count=self.store.event_count(group) + 1,
                    block_count=head.height,
                    logical_bytes=head.logical_bytes,
                    max_block_bytes=MAX_BLOCK_BYTES,
                    validator=self.node.keypair.pubkey_hex,
                    ts=now,
                    expires=now + 300,
                ),
                self.node.keypair,
            )
            return {"type": "history_manifest", "history_manifest": manifest.to_dict()}
        if action == "hosting_attestation":
            raw_manifest = _object(request.get("history_manifest"), "history_manifest")
            manifest = HistoryManifest.from_dict(raw_manifest)
            head = self.store.get_chain_head(manifest.group)
            if not (
                verify_history_manifest(manifest)
                and manifest.validator in head.state.validator_set.pubkeys
                and manifest.height == head.height
                and manifest.block_hash == head.block_hash
                and manifest.history_root == head.history_root
                and manifest.logical_bytes == head.logical_bytes
                and self.node.keypair.pubkey_hex in head.state.validator_set.pubkeys
            ):
                raise ValueError("manifest does not match complete local history")
            now = int(time.time())
            attestation = sign_hosting_attestation(
                HostingAttestation(
                    group=manifest.group,
                    chain_id=manifest.chain_id,
                    epoch=manifest.epoch,
                    manifest_id=manifest.id,
                    checkpoint_height=manifest.height,
                    block_hash=manifest.block_hash,
                    history_root=manifest.history_root,
                    logical_bytes=manifest.logical_bytes,
                    validator=self.node.keypair.pubkey_hex,
                    complete=True,
                    admission_class="normal",
                    ts=now,
                    expires=min(manifest.expires, now + 300),
                ),
                self.node.keypair,
            )
            return {
                "type": "hosting_attestation",
                "hosting_attestation": attestation.to_dict(),
            }
        if action == "get_commits":
            group = str(request.get("group", ""))
            from_height = max(1, strict_int(request.get("from_height", 1), "from_height"))
            limit = min(100, max(1, strict_int(request.get("limit", 100), "limit")))
            commits = self.store.commits(group, from_height)[:limit]
            return {
                "type": "commits",
                "group": group,
                "from_height": from_height,
                "commits": [commit.to_dict() for commit in commits],
                "more": len(commits) == limit,
            }
        if action == "get_event":
            event_id = str(request.get("id", ""))
            result = self.store.get_event(event_id)
            if result is None:
                return {"type": "not_found", "id": event_id}
            event, event_status, height, certified_time_ms = result
            return {
                "type": "event",
                "event": event.to_dict(),
                "status": event_status,
                "height": height,
                "certified_time_ms": certified_time_ms,
            }
        if action == "get_pending":
            group = str(request.get("group", ""))
            return {
                "type": "pending_events",
                "group": group,
                "events": [event.to_dict() for event in self.store.pending_events(group)],
            }
        if action == "subscribe":
            group = str(request.get("group", ""))
            self.store.get_chain_head(group)
            self._subscriptions.setdefault(group, set()).add(websocket)
            subscribed.add(group)
            logger.debug(
                "client subscribed group=%s remote=%s subscribers=%d",
                group[:12],
                websocket.remote_address,
                len(self._subscriptions[group]),
            )
            return {"type": "subscribed", "group": group}
        if action == "unsubscribe":
            group = str(request.get("group", ""))
            self._subscriptions.get(group, set()).discard(websocket)
            subscribed.discard(group)
            logger.debug(
                "client unsubscribed group=%s remote=%s",
                group[:12],
                websocket.remote_address,
            )
            return {"type": "unsubscribed", "group": group}
        raise ValueError(f"unknown action: {action}")

    async def _catch_up_group(self, group: str) -> None:
        """Synchronize a lagging hosted group from its verified validator endpoints."""

        head = self.store.get_chain_head(group)
        urls = [
            validator.url
            for validator in head.state.validator_set.validators
            if validator.pubkey != self.node.keypair.pubkey_hex
        ]
        if not urls:
            return
        statuses = await asyncio.gather(
            *(BFTWebSocketClient(url, timeout=self.catchup_timeout).status(group) for url in urls),
            return_exceptions=True,
        )
        ahead = {
            status.validator: status
            for status in statuses
            if isinstance(status, ValidatorStatus)
            and status.validator in head.state.validator_set.pubkeys
            and status.chain_id == head.state.chain_id
            and status.height > head.height
        }
        catchup_threshold = head.state.validator_set.round_catchup_threshold
        if len(ahead) < catchup_threshold:
            return
        supported_height = max(
            height
            for height in {status.height for status in ahead.values()}
            if sum(status.height >= height for status in ahead.values()) >= catchup_threshold
        )

        logger.info(
            "validator catch-up starting group=%s local_height=%d remote_height=%d peers=%d",
            group[:12],
            head.height,
            supported_height,
            len(ahead),
        )

        async def synchronize() -> object:
            # Imported lazily to avoid the client/websocket module cycle.
            from fern.bft.client import sync_from_validators

            return await sync_from_validators(group=group, urls=urls, store=self.store)

        try:
            result = await self.node.synchronize_group(group, synchronize)
        except Exception as exc:
            logger.warning(
                "validator catch-up failed group=%s local_height=%d reason=%s",
                group[:12],
                self.store.get_chain_head(group).height,
                exc,
            )
            return
        self._attach_engine(group)
        logger.info(
            "validator catch-up complete group=%s height=%d commits=%s",
            group[:12],
            self.store.get_chain_head(group).height,
            getattr(result, "commits_added", "unknown"),
        )

    async def _catch_up_all(self) -> None:
        for group in self.store.hosted_groups():
            await self._catch_up_group(group)

    async def _catch_up_loop(self) -> None:
        while True:
            self._catchup_requested.clear()
            try:
                await asyncio.wait_for(
                    self._catchup_requested.wait(), timeout=self.catchup_interval
                )
            except TimeoutError:
                pass
            await self._catch_up_all()

    async def run(self, shutdown: asyncio.Event | None = None) -> None:
        catchup_task: asyncio.Task[None] | None = None
        try:
            async with serve(
                self.handler,
                self.host,
                self.port,
                max_size=self.maximum_message_bytes,
                ping_interval=20,
                ping_timeout=20,
            ) as server:
                self._server = server
                logger.info(
                    "validator websocket listening host=%s port=%d open_genesis=%s",
                    self.host,
                    self.port,
                    self.allow_genesis,
                )
                await self._catch_up_all()
                self.node.start()
                for group in self.node.engines:
                    self._attach_engine(group)
                if self.catchup_interval > 0:
                    catchup_task = asyncio.create_task(
                        self._catch_up_loop(), name="fern-validator-catchup"
                    )
                if shutdown is None:
                    await server.serve_forever()
                else:
                    await shutdown.wait()
        finally:
            if catchup_task is not None:
                catchup_task.cancel()
                await asyncio.gather(catchup_task, return_exceptions=True)
            self._server = None
            logger.info("validator websocket stopped")


@dataclass(frozen=True)
class ValidatorStatus:
    group: str
    chain_id: str
    height: int
    block_hash: str
    history_root: str
    state_root: str
    epoch: int
    validator_set: ValidatorSet
    logical_bytes: int
    validator: str
    sig: str = ""

    def signing_payload(self) -> list[object]:
        return [
            "validator_status",
            self.group,
            self.chain_id,
            self.height,
            self.block_hash,
            self.history_root,
            self.state_root,
            self.epoch,
            self.validator_set.to_dict(),
            self.logical_bytes,
            self.validator,
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "status",
            "group": self.group,
            "chain_id": self.chain_id,
            "height": self.height,
            "block_hash": self.block_hash,
            "history_root": self.history_root,
            "state_root": self.state_root,
            "epoch": self.epoch,
            "validator_set": self.validator_set.to_dict(),
            "logical_bytes": self.logical_bytes,
            "validator": self.validator,
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ValidatorStatus:
        return cls(
            group=str(value.get("group", "")),
            chain_id=str(value.get("chain_id", "")),
            height=strict_int(value.get("height"), "height"),
            block_hash=str(value.get("block_hash", "")),
            history_root=str(value.get("history_root", "")),
            state_root=str(value.get("state_root", "")),
            epoch=strict_int(value.get("epoch"), "epoch"),
            validator_set=ValidatorSet.from_dict(
                _object(value.get("validator_set"), "validator_set")
            ),
            logical_bytes=strict_int(value.get("logical_bytes"), "logical_bytes"),
            validator=str(value.get("validator", "")),
            sig=str(value.get("sig", "")),
        )


def sign_validator_status(status: ValidatorStatus, keypair: Keypair) -> ValidatorStatus:
    if status.validator != keypair.pubkey_hex:
        raise ValueError("status validator does not match signing key")
    return replace(status, sig=sign_payload(keypair, status.signing_payload()))


def verify_validator_status(status: ValidatorStatus) -> bool:
    try:
        valid_set = status.validator_set.epoch == status.epoch
    except ValueError:
        return False
    return (
        valid_set
        and is_valid_pubkey_hex(status.group)
        and is_valid_event_id_hex(status.chain_id)
        and status.height >= 0
        and all(
            is_valid_event_id_hex(value)
            for value in (status.block_hash, status.history_root, status.state_root)
        )
        and status.logical_bytes >= 0
        and status.validator in status.validator_set.pubkeys
        and is_valid_sig_hex(status.sig)
        and verify_payload_signature(status.validator, status.signing_payload(), status.sig)
    )


class BFTWebSocketClient:
    def __init__(self, url: str, *, timeout: float = 5.0) -> None:
        self.url = url
        self.timeout = timeout

    async def _request(self, request: dict[str, object]) -> dict[str, object]:
        async with connect(
            self.url,
            open_timeout=self.timeout,
            close_timeout=self.timeout,
            max_size=MAX_BLOCK_BYTES * 2,
        ) as websocket:
            await websocket.send(_encode(request))
            raw = await asyncio.wait_for(websocket.recv(), timeout=self.timeout)
        if not isinstance(raw, str):
            raise ValueError("validator returned a binary response")
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise ValueError("validator response must be an object")
        if response.get("type") == "error":
            raise ValueError(str(response.get("message", "validator error")))
        return {str(key): item for key, item in response.items()}

    async def metadata(self) -> dict[str, object]:
        response = await self._request({"action": "metadata"})
        return _object(response.get("metadata"), "metadata")

    async def bootstrap(self, genesis: Event) -> None:
        await self._request({"action": "bootstrap", "genesis": genesis.to_dict()})

    async def submit_event(self, event: Event) -> IngressReceipt:
        response = await self._request({"action": "submit_event", "event": event.to_dict()})
        return IngressReceipt.from_dict(_object(response.get("ingress_receipt"), "ingress_receipt"))

    async def get_genesis(self, group: str) -> Event | None:
        response = await self._request({"action": "get_genesis", "group": group})
        if response.get("type") == "not_found":
            return None
        return Event.from_dict(_object(response.get("genesis"), "genesis"))

    async def status(self, group: str) -> ValidatorStatus:
        response = await self._request({"action": "status", "group": group})
        status = ValidatorStatus.from_dict(response)
        if status.group != group or not verify_validator_status(status):
            raise ValueError("validator returned an invalid signed status")
        return status

    async def get_commits(
        self, group: str, from_height: int = 1, limit: int = 100
    ) -> tuple[Commit, ...]:
        response = await self._request(
            {
                "action": "get_commits",
                "group": group,
                "from_height": from_height,
                "limit": limit,
            }
        )
        raw = response.get("commits", [])
        if not isinstance(raw, list):
            raise ValueError("commits response is invalid")
        commits = tuple(Commit.from_dict(item) for item in raw if isinstance(item, dict))
        if len(commits) != len(raw):
            raise ValueError("invalid commit entry")
        return commits

    async def history_manifest(self, group: str) -> HistoryManifest:
        response = await self._request({"action": "history_manifest", "group": group})
        return HistoryManifest.from_dict(
            _object(response.get("history_manifest"), "history_manifest")
        )

    async def hosting_attestation(self, manifest: HistoryManifest) -> HostingAttestation:
        response = await self._request(
            {"action": "hosting_attestation", "history_manifest": manifest.to_dict()}
        )
        return HostingAttestation.from_dict(
            _object(response.get("hosting_attestation"), "hosting_attestation")
        )

    async def get_pending(self, group: str) -> tuple[Event, ...]:
        response = await self._request({"action": "get_pending", "group": group})
        raw = response.get("events", [])
        if not isinstance(raw, list):
            raise ValueError("pending events response is invalid")
        events = tuple(Event.from_dict(item) for item in raw if isinstance(item, dict))
        if len(events) != len(raw):
            raise ValueError("invalid pending event")
        return events

    async def subscribe(self, group: str) -> AsyncIterator[dict[str, object]]:
        async with connect(
            self.url,
            open_timeout=self.timeout,
            close_timeout=self.timeout,
            max_size=MAX_BLOCK_BYTES * 2,
        ) as websocket:
            await websocket.send(_encode({"action": "subscribe", "group": group}))
            while True:
                raw = await websocket.recv()
                if not isinstance(raw, str):
                    continue
                value = json.loads(raw)
                if isinstance(value, dict):
                    yield {str(key): item for key, item in value.items()}


def parse_group_address(address: str) -> tuple[str, list[str]]:
    value = address[5:] if address.startswith("fern:") else address
    if "@" not in value:
        return value, []
    group, raw_urls = value.split("@", 1)
    return group, [url.strip() for url in raw_urls.split(",") if url.strip()]


__all__ = [
    "BFTWebSocketClient",
    "PeerBroadcaster",
    "ServerMetadata",
    "ValidatorServer",
    "ValidatorStatus",
    "sign_validator_status",
    "verify_validator_status",
    "parse_group_address",
]
