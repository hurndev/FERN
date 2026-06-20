from __future__ import annotations

# mypy: ignore-errors
# This module handles JSON WebSocket messages with untyped dicts.

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

import websockets.asyncio.client as ws_client
from websockets.exceptions import ConnectionClosed

from fern.events.event import Event
from fern.completeness.receipts import Receipt
from fern.completeness.attestations import Attestation
from fern.completeness.fraud_proofs import FraudProof
from fern.transport.interfaces import RelayMetadata


_PUSH_TYPES = frozenset({"event", "attestation"})
_RESPONSE_TYPES = frozenset(
    {
        "receipt",
        "not_found",
        "sync_complete",
        "ok",
        "error",
        "query_complete",
        "fraud_proof",
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


def _json_to_attestation(d: dict) -> Attestation:
    return Attestation(
        group=d["group"],
        relay=d["relay"],
        set_hash=d["set_hash"],
        tips=tuple(d["tips"]),
        count=d["count"],
        prev=d.get("prev"),
        ts=d["ts"],
        sig=d["sig"],
    )


def _receipt_to_json(receipt: Receipt) -> dict:
    return {
        "event_id": receipt.event_id,
        "group": receipt.group,
        "relay": receipt.relay,
        "ts": receipt.ts,
        "sig": receipt.sig,
    }


class WebSocketRelayClient:
    def __init__(self, url: str, relay_pubkey: str = "") -> None:
        self.url = url
        self.relay_pubkey = relay_pubkey
        self._ws: ws_client.ClientConnection | None = None
        self._event_callbacks: list[Callable[[Event], Awaitable[None]]] = []
        self._attestation_callbacks: list[Callable[[Attestation], Awaitable[None]]] = []
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

    async def publish(self, event: Event) -> Receipt:
        msg = {"action": "publish", "event": _event_to_json(event)}
        await self._send(msg)
        response = await self._recv_response()
        if response.get("type") == "receipt":
            r = response["receipt"]
            return Receipt(
                event_id=r["event_id"],
                group=r["group"],
                relay=r["relay"],
                ts=r["ts"],
                sig=r["sig"],
            )
        raise ValueError(f"publish failed: {response.get('message', 'unknown error')}")

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
                elif r_type in ("attestation",):
                    self._route_push(response)
        finally:
            self._awaiting_response = False

    async def request_attestation(self, group: str) -> Attestation:
        msg = {"action": "attestation", "group": group}
        self._awaiting_response = True
        try:
            await self._send(msg)

            while True:
                response = await self._recv_response()
                r_type = response.get("type")
                if r_type == "attestation":
                    return _json_to_attestation(response["attestation"])
                if r_type == "error":
                    raise ValueError(f"attestation request failed: {response.get('message')}")
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
                "receipt": _receipt_to_json(proof.receipt) if proof.receipt else None,
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
                receipt_data = fp.get("receipt", {})
                receipt = (
                    Receipt(
                        event_id=receipt_data.get("event_id", ""),
                        group=receipt_data.get("group", ""),
                        relay=receipt_data.get("relay", ""),
                        ts=receipt_data.get("ts", 0),
                        sig=receipt_data.get("sig", ""),
                    )
                    if receipt_data
                    else None
                )
                yield FraudProof(
                    type=fp.get("type", "fraud_proof"),
                    group=fp.get("group", ""),
                    relay=fp.get("relay", ""),
                    event_id=fp.get("event_id", ""),
                    event=event,
                    receipt=receipt,
                    evidence=fp.get("evidence", ""),
                )

    def on_event(self, callback: Callable[[Event], Awaitable[None]]) -> None:
        self._event_callbacks.append(callback)

    def on_attestation(self, callback: Callable[[Attestation], Awaitable[None]]) -> None:
        self._attestation_callbacks.append(callback)

    def _route_push(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "event":
            event = _json_to_event(msg["event"])
            for cb in self._event_callbacks:
                asyncio.create_task(cb(event))
        elif msg_type == "attestation":
            att = _json_to_attestation(msg["attestation"])
            for cb in self._attestation_callbacks:
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
