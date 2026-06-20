from __future__ import annotations

# mypy: ignore-errors
# This module handles JSON WebSocket messages with untyped dicts.

import asyncio
import json
import logging
import time

import websockets.asyncio.server as ws_server
from websockets.datastructures import Headers
from websockets.http11 import Response

from fern.events.event import Event
from fern.events.validation import verify_event
from fern.completeness.receipts import Receipt, build_receipt
from fern.completeness.attestations import (
    Attestation,
    build_attestation,
)
from fern.completeness.fraud_proofs import (
    FraudProof,
    verify_fraud_proof,
    compute_fraud_proof_id,
)
from fern.crypto.keys import Keypair
from fern.relay.metadata_handler import build_metadata
from fern.relay.store import RelayStore
from fern.storage.sqlite_store import SqliteStore


logger = logging.getLogger("fern.relay")


def _event_to_json_dict(event: Event) -> dict:
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


def _json_to_event_dict(d: dict) -> Event:
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


def _attestation_to_json(att: Attestation) -> dict:
    return {
        "group": att.group,
        "relay": att.relay,
        "set_hash": att.set_hash,
        "tips": list(att.tips),
        "count": att.count,
        "prev": att.prev,
        "ts": att.ts,
        "sig": att.sig,
    }


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


class RelayServer:
    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 8765,
        name: str = "FERN Relay",
        description: str = "",
        relay_keypair: Keypair | None = None,
        store_path: str = "relay.db",
    ):
        self.host = host
        self.port = port
        self.name = name
        self.description = description
        self._keypair = relay_keypair or Keypair.generate()
        self._store = SqliteStore(store_path)
        self._relay_store = RelayStore(self._store)
        self._fraud_proofs: dict[str, FraudProof] = {}
        self._subscribers: dict[str, set[ws_server.ServerConnection]] = {}
        self._last_attestations: dict[str, Attestation] = {}
        self._attestation_intervals: dict[str, float] = {}
        self._attestation_tasks: dict[str, asyncio.Task] = {}
        self._hosted_groups: set[str] = set()
        self._started = False

    @property
    def pubkey(self) -> str:
        return self._keypair.pubkey_hex

    async def start(self) -> None:
        await self._store.open()
        self._started = True

        persisted_groups = await self._store.get_hosted_groups()
        for g in persisted_groups:
            self._hosted_groups.add(g)

        logger.info(
            "relay listening on %s:%d (pubkey=%s, hosting %d group%s)",
            self.host,
            self.port,
            self._keypair.pubkey_hex[:16] + "...",
            len(self._hosted_groups),
            "s" if len(self._hosted_groups) != 1 else "",
        )
        for g in sorted(self._hosted_groups):
            count = await self._store.count_events(g)
            logger.info("  hosting group %s... (%d events)", g[:16], count)

        async with ws_server.serve(
            self._handle_connection,
            self.host,
            self.port,
            process_request=self._handle_http_request,
        ):
            await asyncio.get_running_loop().create_future()

    def _handle_http_request(
        self, connection: ws_server.ServerConnection, request: object
    ) -> Response | None:
        """Respond to plain HTTP requests with the relay metadata (spec section 10.6).

        WebSocket upgrade requests carry an ``Upgrade: websocket`` header and
        fall through to ``None`` so the normal WebSocket handshake proceeds.
        Any other plain HTTP GET returns the metadata JSON at any path, so
        clients querying the relay's base URL (with ``wss://`` -> ``https://``
        scheme substitution) receive the expected response regardless of path.
        """
        upgrade = getattr(request, "headers", Headers()).get("Upgrade", "").lower()
        if upgrade == "websocket":
            return None
        path = getattr(request, "path", "/")
        logger.info("HTTP GET %s -> metadata", path)
        metadata = build_metadata(
            relay_keypair=self._keypair,
            name=self.name,
            description=self.description,
            groups=sorted(self._hosted_groups),
            retention="full",
        )
        body = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
        return Response(
            status_code=200,
            reason_phrase="OK",
            headers=Headers(
                [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                    ("Access-Control-Allow-Origin", "*"),
                    ("Access-Control-Allow-Methods", "GET, OPTIONS"),
                    ("Access-Control-Allow-Headers", "Content-Type"),
                ]
            ),
            body=body,
        )

    async def _handle_connection(self, ws: ws_server.ServerConnection) -> None:
        peer = getattr(ws, "remote_address", None)
        logger.info("connection opened from %s", peer)
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    responses = await self._process_message(msg, ws)
                    if responses is not None:
                        for response in responses:
                            await ws.send(json.dumps(response, ensure_ascii=False, default=str))
                except json.JSONDecodeError:
                    logger.warning("invalid JSON from %s", peer)
                    await ws.send(json.dumps({"type": "error", "message": "invalid JSON"}))
        except Exception as e:
            logger.warning("connection error from %s: %s", peer, e)
        finally:
            for group, subs in list(self._subscribers.items()):
                if ws in subs:
                    subs.discard(ws)
                    logger.info("unsubscribed %s from group %s", peer, group[:16] + "...")
            logger.info("connection closed from %s", peer)

    async def _process_message(
        self, msg: dict, ws: ws_server.ServerConnection
    ) -> list[dict] | None:
        action = msg.get("action", "")

        if action == "subscribe":
            return await self._handle_subscribe(msg, ws)
        elif action == "unsubscribe":
            return await self._handle_unsubscribe(msg, ws)
        elif action == "publish":
            return await self._handle_publish(msg, ws)
        elif action == "get":
            return await self._handle_get(msg)
        elif action == "sync":
            return await self._handle_sync(msg, ws)
        elif action == "attestation":
            return await self._handle_attestation_request(msg)
        elif action == "submit_fraud_proof":
            return await self._handle_submit_fraud_proof(msg)
        elif action == "query_fraud_proofs":
            return await self._handle_query_fraud_proofs(msg, ws)
        else:
            return [{"type": "error", "message": f"unknown action: {action}"}]

    async def _handle_subscribe(
        self, msg: dict, ws: ws_server.ServerConnection
    ) -> list[dict] | None:
        group = msg.get("group", "")
        if not group:
            return [{"type": "error", "message": "group required"}]

        if group not in self._subscribers:
            self._subscribers[group] = set()
        self._subscribers[group].add(ws)

        if group not in self._hosted_groups:
            logger.info("auto-hosting group %s", group[:16] + "...")
        self._hosted_groups.add(group)

        logger.info("subscribe to group %s (%d subscribers)", group[:16] + "...", len(self._subscribers[group]))

        att = await self._build_and_store_attestation(group)
        return [{"type": "attestation", "attestation": _attestation_to_json(att)}]

    async def _handle_unsubscribe(
        self, msg: dict, ws: ws_server.ServerConnection
    ) -> list[dict] | None:
        group = msg.get("group", "")
        if group in self._subscribers:
            self._subscribers[group].discard(ws)
            logger.info("unsubscribe from group %s (%d remaining)", group[:16] + "...", len(self._subscribers[group]))
        return [{"type": "ok", "message": "unsubscribed"}]

    async def _handle_publish(self, msg: dict, ws: ws_server.ServerConnection) -> list[dict] | None:
        try:
            event_dict = msg.get("event", {})
            event = _json_to_event_dict(event_dict)
            verify_event(event)

            is_new_group = event.type == "genesis" and event.group not in self._hosted_groups
            if is_new_group:
                self._hosted_groups.add(event.group)
                logger.info("auto-hosting new group %s (genesis by %s)", event.group[:16] + "...", event.author[:16] + "...")

            if event.group not in self._hosted_groups:
                logger.warning("rejecting publish: group %s not hosted", event.group[:16] + "...")
                return [{"type": "error", "message": "group not hosted"}]

            await self._relay_store.ingest(event)
            eid = event.id[:16] + "..." if event.id else "?"
            logger.info(
                "publish event type=%s group=%s author=%s id=%s",
                event.type,
                event.group[:16] + "...",
                event.author[:16] + "...",
                eid,
            )

            receipt = build_receipt(
                event=event,
                relay_keypair=self._keypair,
                ts=int(time.time()),
            )
            response = [
                {
                    "type": "receipt",
                    "receipt": {
                        "event_id": receipt.event_id,
                        "group": receipt.group,
                        "relay": receipt.relay,
                        "ts": receipt.ts,
                        "sig": receipt.sig,
                    },
                }
            ]

            await self._broadcast_event(event)

            return response
        except Exception as e:
            logger.warning("publish failed: %s", e)
            return [{"type": "error", "message": str(e)}]

    async def _handle_get(self, msg: dict) -> list[dict] | None:
        event_id = msg.get("id", "")
        event = await self._store.get_event(event_id)
        if event is None:
            logger.info("get id=%s... -> not_found", event_id[:16])
            return [{"type": "not_found", "id": event_id}]
        logger.info("get id=%s... -> type=%s", event_id[:16], event.type)
        return [{"type": "event", "event": _event_to_json_dict(event)}]

    async def _handle_sync(self, msg: dict, ws: ws_server.ServerConnection) -> list[dict] | None:
        group = msg.get("group", "")
        since = msg.get("since")

        if not group:
            return [{"type": "error", "message": "group required"}]

        responses: list[dict] = []
        async for event in self._store.iter_group_events(group):
            if since is not None and event.ts <= since:
                continue
            responses.append({"type": "event", "event": _event_to_json_dict(event)})

        count = len([r for r in responses if r.get("type") == "event"])
        since_str = f" since={since}" if since is not None else ""
        logger.info("sync group=%s...%s -> %d events", group[:16], since_str, count)
        responses.append({"type": "sync_complete", "group": group, "count": count})
        return responses

    async def _handle_attestation_request(self, msg: dict) -> list[dict] | None:
        group = msg.get("group", "")
        logger.info("attestation request group=%s...", group[:16])
        att = await self._build_and_store_attestation(group)
        return [{"type": "attestation", "attestation": _attestation_to_json(att)}]

    async def _handle_submit_fraud_proof(self, msg: dict) -> list[dict] | None:
        try:
            fp_dict = msg.get("fraud_proof", {})
            event = _json_to_event_dict(fp_dict.get("event", {})) if fp_dict.get("event") else None
            receipt_data = fp_dict.get("receipt", {})
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
            proof = FraudProof(
                type=fp_dict.get("type", "fraud_proof"),
                group=fp_dict.get("group", ""),
                relay=fp_dict.get("relay", ""),
                event_id=fp_dict.get("event_id", ""),
                event=event,
                receipt=receipt,
                evidence=fp_dict.get("evidence", ""),
            )
            if not verify_fraud_proof(proof):
                logger.warning("fraud proof rejected: invalid")
                return [{"type": "error", "message": "invalid fraud proof"}]
            fp_id = compute_fraud_proof_id(proof)
            self._fraud_proofs[fp_id] = proof
            logger.info("fraud proof stored id=%s... relay=%s...", fp_id[:16], proof.relay[:16])
            return [{"type": "ok", "id": fp_id}]
        except Exception as e:
            logger.warning("fraud proof failed: %s", e)
            return [{"type": "error", "message": str(e)}]

    async def _handle_query_fraud_proofs(
        self, msg: dict, ws: ws_server.ServerConnection
    ) -> list[dict] | None:
        relay_filter = msg.get("relay")
        group_filter = msg.get("group")

        responses: list[dict] = []
        for fp in self._fraud_proofs.values():
            if relay_filter is not None and fp.relay != relay_filter:
                continue
            if group_filter is not None and fp.group != group_filter:
                continue
            responses.append(self._fraud_proof_to_dict(fp))

        filter_str = []
        if relay_filter:
            filter_str.append(f"relay={relay_filter[:16]}")
        if group_filter:
            filter_str.append(f"group={group_filter[:16]}")
        logger.info("query_fraud_proofs %s -> %d results", " ".join(filter_str) or "(all)", len(responses))

        responses.append({"type": "query_complete", "count": len(responses)})
        return responses

    def _fraud_proof_to_dict(self, fp: FraudProof) -> dict:
        return {
            "type": "fraud_proof",
            "fraud_proof": {
                "type": fp.type,
                "group": fp.group,
                "relay": fp.relay,
                "event_id": fp.event_id,
                "event": _event_to_json_dict(fp.event) if fp.event else None,
                "receipt": {
                    "event_id": fp.receipt.event_id,
                    "group": fp.receipt.group,
                    "relay": fp.receipt.relay,
                    "ts": fp.receipt.ts,
                    "sig": fp.receipt.sig,
                }
                if fp.receipt
                else None,
                "evidence": fp.evidence,
            },
        }

    async def _broadcast_event(self, event: Event) -> None:
        subs = self._subscribers.get(event.group, set())
        msg = json.dumps({"type": "event", "event": _event_to_json_dict(event)}, default=str)
        delivered = 0
        for sub in list(subs):
            try:
                await sub.send(msg)
                delivered += 1
            except Exception:
                subs.discard(sub)
        if subs:
            logger.info("broadcast event %s... to %d/%d subscribers", event.id[:16] if event.id else "?", delivered, len(subs))

    async def _broadcast_attestation(self, group: str, att: Attestation) -> None:
        subs = self._subscribers.get(group, set())
        msg = json.dumps(
            {"type": "attestation", "attestation": _attestation_to_json(att)}, default=str
        )
        delivered = 0
        for sub in list(subs):
            try:
                await sub.send(msg)
                delivered += 1
            except Exception:
                subs.discard(sub)
        if subs:
            logger.info("broadcast attestation to %d/%d subscribers for group %s...", delivered, len(subs), group[:16])

    async def _build_and_store_attestation(self, group: str) -> Attestation:
        known_set = await self._store.get_known_set(group)
        tips = await self._store.get_tips(group)
        count = await self._store.count_events(group)

        prev = self._last_attestations.get(group)
        att = build_attestation(
            group=group,
            relay_keypair=self._keypair,
            known_set=known_set,
            tips=tips,
            count=count,
            prev=prev,
            ts=int(time.time()),
        )
        self._last_attestations[group] = att

        await self._broadcast_attestation(group, att)
        return att
