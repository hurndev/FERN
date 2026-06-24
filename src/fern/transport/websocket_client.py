from __future__ import annotations

# mypy: ignore-errors
# This module handles JSON WebSocket messages with untyped dicts.

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

import websockets.asyncio.client as ws_client
from websockets.exceptions import ConnectionClosed

from fern.events.event import Event
from fern.completeness.event_receipts import EventReceipt
from fern.completeness.group_statuses import GroupStatus
from fern.completeness.fraud_proofs import FraudProof
from fern.completeness.heal_attestations import (
    GroupHostAttestation,
    HealChallenge,
    InventoryAttestation,
    Threshold,
    TrustedWitness,
)
from fern.transport.interfaces import (
    HealBatchResult,
    InventoryAttestationResult,
    RelayMetadata,
    SyncLockResult,
)


_PUSH_TYPES = frozenset({"event", "group_status"})
_RESPONSE_TYPES = frozenset(
    {
        "event_receipt",
        "not_found",
        "sync_complete",
        "ok",
        "error",
        "query_complete",
        "fraud_proof",
        "ids",
        "sync_lock_granted",
        "sync_lock_denied",
        "heal_challenge",
        "group_host_attestation",
        "inventory_attestation",
        "inventory_missing",
        "heal_batch_result",
    }
)


def _event_to_json(event: Event) -> dict:
    return {
        "id": event.id,
        "type": event.type,
        "group": event.group,
        "author": event.author,
        "parents": list(event.parents),
        "content": event.content,
        "ts": event.ts,
        "tags": [list(t) for t in event.tags],
        "sig": event.sig,
    }


def _json_to_event(d: dict) -> Event:
    parents = tuple(d.get("parents", []))
    content = d.get("content", {})
    tags = tuple(tuple(t) for t in d.get("tags", []))
    return Event(
        type=d["type"],
        group=d["group"],
        author=d["author"],
        parents=parents,
        content=content,
        ts=d["ts"],
        tags=tags,
        id=d.get("id"),
        sig=d.get("sig"),
    )


def _json_to_group_status(d: dict) -> GroupStatus:
    return GroupStatus(
        group=d["group"],
        relay=d["relay"],
        set_hash=d["set_hash"],
        tips=tuple(d["tips"]),
        count=d["count"],
        prev=d.get("prev"),
        ts=d["ts"],
        sig=d["sig"],
    )


def _heal_challenge_to_json(c: HealChallenge) -> dict:
    return {
        "type": c.type,
        "group": c.group,
        "receiver": c.receiver,
        "ids_hash": c.ids_hash,
        "count": c.count,
        "trusted_witnesses": [{"relay": w.relay, "url": w.url} for w in c.trusted_witnesses],
        "threshold": {
            "kind": c.threshold.kind,
            "num": c.threshold.num,
            "den": c.threshold.den,
            "min": c.threshold.min,
        },
        "nonce": c.nonce,
        "ts": c.ts,
        "expires": c.expires,
        "sig": c.sig,
    }


def _json_to_heal_challenge(d: dict) -> HealChallenge:
    tw = tuple(
        TrustedWitness(relay=w["relay"], url=w["url"])
        for w in d.get("trusted_witnesses", [])
    )
    thr_data = d.get("threshold", {})
    return HealChallenge(
        type=d.get("type", "heal_challenge"),
        group=d["group"],
        receiver=d["receiver"],
        ids_hash=d["ids_hash"],
        count=d["count"],
        trusted_witnesses=tw,
        threshold=Threshold(
            kind=thr_data.get("kind", "ratio"),
            num=thr_data.get("num", 2),
            den=thr_data.get("den", 3),
            min=thr_data.get("min", 2),
        ),
        nonce=d["nonce"],
        ts=d["ts"],
        expires=d["expires"],
        sig=d["sig"],
    )


def _group_host_attestation_to_json(a: GroupHostAttestation) -> dict:
    return {
        "type": a.type,
        "group": a.group,
        "relay": a.relay,
        "receiver": a.receiver,
        "challenge": a.challenge,
        "hosts": a.hosts,
        "ts": a.ts,
        "expires": a.expires,
        "sig": a.sig,
    }


def _json_to_group_host_attestation(d: dict) -> GroupHostAttestation:
    return GroupHostAttestation(
        type=d.get("type", "group_host_attestation"),
        group=d["group"],
        relay=d["relay"],
        receiver=d["receiver"],
        challenge=d["challenge"],
        hosts=d["hosts"],
        ts=d["ts"],
        expires=d["expires"],
        sig=d["sig"],
    )


def _inventory_attestation_to_json(a: InventoryAttestation) -> dict:
    return {
        "type": a.type,
        "group": a.group,
        "relay": a.relay,
        "receiver": a.receiver,
        "challenge": a.challenge,
        "ids_hash": a.ids_hash,
        "count": a.count,
        "ts": a.ts,
        "expires": a.expires,
        "sig": a.sig,
    }


def _json_to_inventory_attestation(d: dict) -> InventoryAttestation:
    return InventoryAttestation(
        type=d.get("type", "inventory_attestation"),
        group=d["group"],
        relay=d["relay"],
        receiver=d["receiver"],
        challenge=d["challenge"],
        ids_hash=d["ids_hash"],
        count=d["count"],
        ts=d["ts"],
        expires=d["expires"],
        sig=d["sig"],
    )


def _event_receipt_to_json(event_receipt: EventReceipt) -> dict:
    return {
        "event_id": event_receipt.event_id,
        "group": event_receipt.group,
        "relay": event_receipt.relay,
        "ts": event_receipt.ts,
        "sig": event_receipt.sig,
    }


class WebSocketRelayClient:
    def __init__(self, url: str, relay_pubkey: str = "") -> None:
        self.url = url
        self.relay_pubkey = relay_pubkey
        self._ws: ws_client.ClientConnection | None = None
        self._event_callbacks: list[Callable[[Event], Awaitable[None]]] = []
        self._group_status_callbacks: list[Callable[[GroupStatus], Awaitable[None]]] = []
        self._response_queue: asyncio.Queue[dict] | None = None
        self._listen_task: asyncio.Task | None = None
        self._subscribed_groups: set[str] = set()
        self._awaiting_response: bool = False

    async def connect(self) -> None:
        ws_url = self.url
        if not ws_url.startswith(("ws://", "wss://")):
            ws_url = f"ws://{ws_url}"
        self._ws = await ws_client.connect(ws_url)
        self._response_queue = asyncio.Queue()
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def close(self) -> None:
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def fetch_metadata(self) -> RelayMetadata:
        import urllib.request

        meta_url = self.url.replace("wss://", "https://").replace("ws://", "http://")
        try:
            with urllib.request.urlopen(meta_url, timeout=10) as resp:
                data = json.loads(resp.read())
            self.relay_pubkey = data.get("pubkey", "")
            return RelayMetadata(
                name=data.get("name", ""),
                description=data.get("description", ""),
                pubkey=data.get("pubkey", ""),
                software=data.get("software", ""),
                version=data.get("version", ""),
                groups=tuple(data.get("groups", [])),
                retention=data.get("retention", {}).get("default", "full"),
            )
        except Exception:
            return RelayMetadata()

    async def subscribe(self, group: str) -> None:
        msg = {"action": "subscribe", "group": group}
        await self._send(msg)
        self._subscribed_groups.add(group)

    async def unsubscribe(self, group: str) -> None:
        msg = {"action": "unsubscribe", "group": group}
        await self._send(msg)
        self._subscribed_groups.discard(group)

    async def publish(self, event: Event) -> EventReceipt:
        msg = {"action": "publish", "event": _event_to_json(event)}
        await self._send(msg)
        response = await self._recv_response()
        if response.get("type") == "event_receipt":
            r = response["event_receipt"]
            return EventReceipt(
                event_id=r["event_id"],
                group=r["group"],
                relay=r["relay"],
                ts=r["ts"],
                sig=r["sig"],
            )
        raise ValueError(f"publish failed: {response.get('message', 'unknown error')}")

    async def heal(self, event: Event) -> EventReceipt:
        msg = {"action": "heal", "event": _event_to_json(event)}
        await self._send(msg)
        response = await self._recv_response()
        if response.get("type") == "event_receipt":
            r = response["event_receipt"]
            return EventReceipt(
                event_id=r["event_id"],
                group=r["group"],
                relay=r["relay"],
                ts=r["ts"],
                sig=r["sig"],
            )
        raise ValueError(f"heal failed: {response.get('message', 'unknown error')}")

    async def get(self, event_id: str) -> Event | None:
        msg = {"action": "get", "id": event_id}
        self._awaiting_response = True
        try:
            await self._send(msg)

            while True:
                response = await self._recv_response()
                r_type = response.get("type")
                if r_type == "event":
                    return _json_to_event(response["event"])
                if r_type == "not_found":
                    return None
                if r_type in _PUSH_TYPES:
                    self._route_push(response)
                    continue
                return None
        finally:
            self._awaiting_response = False

    async def sync(self, group: str, since_ts: int | None = None) -> AsyncIterator[Event]:
        msg: dict = {"action": "sync", "group": group}
        if since_ts is not None:
            msg["since"] = since_ts
        self._awaiting_response = True
        try:
            await self._send(msg)

            while True:
                response = await self._recv_response()
                r_type = response.get("type")
                if r_type == "sync_complete":
                    break
                if r_type == "event":
                    yield _json_to_event(response["event"])
                elif r_type in ("group_status",):
                    self._route_push(response)
        finally:
            self._awaiting_response = False

    async def sync_ids(self, group: str) -> list[str]:
        msg = {"action": "sync_ids", "group": group}
        self._awaiting_response = True
        try:
            await self._send(msg)
            while True:
                response = await self._recv_response()
                r_type = response.get("type")
                if r_type == "ids":
                    return list(response.get("ids", []))
                if r_type == "error":
                    raise ValueError(f"sync_ids failed: {response.get('message')}")
                if r_type in _PUSH_TYPES:
                    self._route_push(response)
                    continue
                raise ValueError(f"unexpected response: {r_type}")
        finally:
            self._awaiting_response = False

    async def sync_lock(self, group: str, client_id: str) -> SyncLockResult:
        msg = {"action": "sync_lock", "group": group, "client_id": client_id}
        self._awaiting_response = True
        try:
            await self._send(msg)
            while True:
                response = await self._recv_response()
                r_type = response.get("type")
                if r_type == "sync_lock_granted":
                    return SyncLockResult(
                        granted=True,
                        ttl=int(response["ttl"]) if response.get("ttl") is not None else None,
                    )
                if r_type == "sync_lock_denied":
                    return SyncLockResult(
                        granted=False,
                        expires_in=(
                            int(response["expires_in"])
                            if response.get("expires_in") is not None
                            else None
                        ),
                    )
                if r_type == "error":
                    raise ValueError(f"sync_lock failed: {response.get('message')}")
                if r_type in _PUSH_TYPES:
                    self._route_push(response)
                    continue
                raise ValueError(f"unexpected response: {r_type}")
        finally:
            self._awaiting_response = False

    async def sync_unlock(self, group: str, client_id: str) -> None:
        msg = {"action": "sync_unlock", "group": group, "client_id": client_id}
        self._awaiting_response = True
        try:
            await self._send(msg)
            while True:
                response = await self._recv_response()
                r_type = response.get("type")
                if r_type == "ok":
                    return
                if r_type == "error":
                    raise ValueError(f"sync_unlock failed: {response.get('message')}")
                if r_type in _PUSH_TYPES:
                    self._route_push(response)
                    continue
                raise ValueError(f"unexpected response: {r_type}")
        finally:
            self._awaiting_response = False

    async def request_group_status(self, group: str) -> GroupStatus:
        msg = {"action": "group_status", "group": group}
        self._awaiting_response = True
        try:
            await self._send(msg)

            while True:
                response = await self._recv_response()
                r_type = response.get("type")
                if r_type == "group_status":
                    return _json_to_group_status(response["group_status"])
                if r_type == "error":
                    raise ValueError(f"group_status request failed: {response.get('message')}")
                if r_type in _PUSH_TYPES:
                    self._route_push(response)
                    continue
                raise ValueError(f"unexpected response: {r_type}")
        finally:
            self._awaiting_response = False

    async def submit_fraud_proof(self, proof: FraudProof) -> str:
        msg = {
            "action": "submit_fraud_proof",
            "fraud_proof": {
                "type": proof.type,
                "group": proof.group,
                "relay": proof.relay,
                "event_id": proof.event_id,
                "event": _event_to_json(proof.event) if proof.event else None,
                "event_receipt": _event_receipt_to_json(proof.event_receipt) if proof.event_receipt else None,
                "evidence": proof.evidence,
            },
        }
        await self._send(msg)
        response = await self._recv_response()
        if response.get("type") == "ok":
            return response.get("id", "")
        raise ValueError(f"submit_fraud_proof failed: {response.get('message', 'unknown error')}")

    async def query_fraud_proofs(
        self, *, relay: str | None = None, group: str | None = None
    ) -> AsyncIterator[FraudProof]:
        msg: dict = {"action": "query_fraud_proofs"}
        if relay is not None:
            msg["relay"] = relay
        if group is not None:
            msg["group"] = group
        await self._send(msg)

        while True:
            response = await self._recv_response()
            r_type = response.get("type")
            if r_type == "query_complete":
                break
            if r_type == "fraud_proof":
                fp = response["fraud_proof"]
                event = _json_to_event(fp["event"]) if fp.get("event") else None
                event_receipt_data = fp.get("event_receipt", {})
                event_receipt = (
                    EventReceipt(
                        event_id=event_receipt_data.get("event_id", ""),
                        group=event_receipt_data.get("group", ""),
                        relay=event_receipt_data.get("relay", ""),
                        ts=event_receipt_data.get("ts", 0),
                        sig=event_receipt_data.get("sig", ""),
                    )
                    if event_receipt_data
                    else None
                )
                yield FraudProof(
                    type=fp.get("type", "fraud_proof"),
                    group=fp.get("group", ""),
                    relay=fp.get("relay", ""),
                    event_id=fp.get("event_id", ""),
                    event=event,
                    event_receipt=event_receipt,
                    evidence=fp.get("evidence", ""),
                )

    async def get_heal_challenge(self, group: str, ids: Sequence[str]) -> HealChallenge:
        msg = {"action": "get_heal_challenge", "group": group, "ids": list(ids)}
        self._awaiting_response = True
        try:
            await self._send(msg)
            while True:
                response = await self._recv_response()
                r_type = response.get("type")
                if r_type == "heal_challenge":
                    return _json_to_heal_challenge(response["heal_challenge"])
                if r_type == "error":
                    raise ValueError(f"get_heal_challenge failed: {response.get('message')}")
                if r_type in _PUSH_TYPES:
                    self._route_push(response)
                    continue
                raise ValueError(f"unexpected response: {r_type}")
        finally:
            self._awaiting_response = False

    async def get_group_host_attestation(
        self, challenge: HealChallenge
    ) -> GroupHostAttestation | None:
        msg = {
            "action": "get_group_host_attestation",
            "heal_challenge": _heal_challenge_to_json(challenge),
        }
        self._awaiting_response = True
        try:
            await self._send(msg)
            while True:
                response = await self._recv_response()
                r_type = response.get("type")
                if r_type == "group_host_attestation":
                    return _json_to_group_host_attestation(response["group_host_attestation"])
                if r_type == "error":
                    raise ValueError(f"get_group_host_attestation failed: {response.get('message')}")
                if r_type in _PUSH_TYPES:
                    self._route_push(response)
                    continue
                raise ValueError(f"unexpected response: {r_type}")
        finally:
            self._awaiting_response = False

    async def get_inventory_attestation(
        self, challenge: HealChallenge, ids: Sequence[str]
    ) -> InventoryAttestationResult:
        msg = {
            "action": "get_inventory_attestation",
            "heal_challenge": _heal_challenge_to_json(challenge),
            "ids": list(ids),
        }
        self._awaiting_response = True
        try:
            await self._send(msg)
            while True:
                response = await self._recv_response()
                r_type = response.get("type")
                if r_type == "inventory_attestation":
                    att = _json_to_inventory_attestation(response["inventory_attestation"])
                    covered = tuple(response.get("ids", []))
                    missing = tuple(response.get("missing", []))
                    return InventoryAttestationResult(
                        attestation=att, covered=covered, missing=missing
                    )
                if r_type == "inventory_missing":
                    return InventoryAttestationResult(
                        inventory_missing=True,
                        missing=tuple(response.get("missing", [])),
                    )
                if r_type == "error":
                    raise ValueError(f"get_inventory_attestation failed: {response.get('message')}")
                if r_type in _PUSH_TYPES:
                    self._route_push(response)
                    continue
                raise ValueError(f"unexpected response: {r_type}")
        finally:
            self._awaiting_response = False

    async def heal_batch(
        self,
        *,
        challenge: HealChallenge,
        events: Sequence[Event],
        group_host_attestations: Sequence[GroupHostAttestation],
        inventory_attestations: Sequence[tuple[InventoryAttestation, Sequence[str]]],
    ) -> HealBatchResult:
        msg: dict = {
            "action": "heal_batch",
            "heal_challenge": _heal_challenge_to_json(challenge),
            "events": [_event_to_json(e) for e in events],
            "group_host_attestations": [
                _group_host_attestation_to_json(a) for a in group_host_attestations
            ],
            "inventory_attestations": [
                {
                    "inventory_attestation": _inventory_attestation_to_json(a),
                    "ids": list(covered),
                }
                for a, covered in inventory_attestations
            ],
        }
        self._awaiting_response = True
        try:
            await self._send(msg)
            while True:
                response = await self._recv_response()
                r_type = response.get("type")
                if r_type == "heal_batch_result":
                    return HealBatchResult(
                        stored=tuple(response.get("stored", [])),
                        already_have=tuple(response.get("already_have", [])),
                        rejected=tuple(
                            (r["id"], r["reason"]) for r in response.get("rejected", [])
                        ),
                    )
                if r_type == "error":
                    raise ValueError(f"heal_batch failed: {response.get('message')}")
                if r_type in _PUSH_TYPES:
                    self._route_push(response)
                    continue
                raise ValueError(f"unexpected response: {r_type}")
        finally:
            self._awaiting_response = False

    def on_event(self, callback: Callable[[Event], Awaitable[None]]) -> None:
        self._event_callbacks.append(callback)

    def on_group_status(self, callback: Callable[[GroupStatus], Awaitable[None]]) -> None:
        self._group_status_callbacks.append(callback)

    def _route_push(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "event":
            event = _json_to_event(msg["event"])
            for cb in self._event_callbacks:
                asyncio.create_task(cb(event))
        elif msg_type == "group_status":
            att = _json_to_group_status(msg["group_status"])
            for cb in self._group_status_callbacks:
                asyncio.create_task(cb(att))

    async def _send(self, msg: dict) -> None:
        if self._ws is None:
            raise RuntimeError("Not connected")
        await self._ws.send(json.dumps(msg, ensure_ascii=False))

    async def _recv_response(self) -> dict:
        if self._response_queue is None:
            raise RuntimeError("Not connected")
        return await self._response_queue.get()

    async def _listen_loop(self) -> None:
        while self._ws is not None:
            try:
                data = await self._ws.recv()
                msg = json.loads(data)
                msg_type = msg.get("type", "")

                if msg_type in _PUSH_TYPES and not self._awaiting_response:
                    self._route_push(msg)
                else:
                    if self._response_queue is not None:
                        await self._response_queue.put(msg)
                    else:
                        self._route_push(msg)

            except ConnectionClosed:
                break
            except Exception:
                break
