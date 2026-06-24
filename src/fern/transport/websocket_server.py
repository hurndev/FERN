from __future__ import annotations

# mypy: ignore-errors
# This module handles JSON WebSocket messages with untyped dicts.

import asyncio
from dataclasses import dataclass
import json
import logging
import time

import websockets.asyncio.server as ws_server
from websockets.datastructures import Headers
from websockets.http11 import Response

from fern.events.event import Event
from fern.events.limits import MAX_EVENT_BYTES
from fern.events.validation import verify_event
from fern.completeness.event_receipts import EventReceipt, build_event_receipt
from fern.completeness.group_statuses import (
    GroupStatus,
    build_group_status,
    compute_set_hash,
)
from fern.completeness.fraud_proofs import (
    FraudProof,
    verify_fraud_proof,
    compute_fraud_proof_id,
)
from fern.completeness.heal_attestations import (
    GroupHostAttestation,
    HealChallenge,
    InventoryAttestation,
    Threshold,
    TrustedWitness,
    build_group_host_attestation,
    build_heal_challenge,
    build_inventory_attestation,
    compute_challenge_id,
    verify_heal_challenge,
)
from fern.crypto.keys import Keypair
from fern.relay.admission import InventoryEvidence, compute_admission
from fern.relay.metadata_handler import build_metadata
from fern.relay.rate_limiter import RateLimiter
from fern.relay.store import RelayStore
from fern.relay.trust_config import load_trust_config
from fern.storage.sqlite_store import SqliteStore


logger = logging.getLogger("fern.relay")


@dataclass
class SyncLockLease:
    holder: str
    expires_at: float
    connection_id: int | None = None


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


EVENT_FIELDS = {"id", "type", "group", "author", "parents", "content", "ts", "tags", "sig"}


def _json_to_event_dict(d: dict) -> Event:
    if not isinstance(d, dict):
        raise ValueError("event must be an object")
    keys = set(d.keys())
    if keys != EVENT_FIELDS:
        missing = EVENT_FIELDS - keys
        extra = keys - EVENT_FIELDS
        if missing:
            raise ValueError(f"event missing field: {sorted(missing)[0]}")
        raise ValueError(f"event has unsigned extra field: {sorted(extra)[0]}")
    if not isinstance(d["parents"], list):
        raise ValueError("parents must be an array")
    if not isinstance(d["content"], dict):
        raise ValueError("content must be an object")
    if not isinstance(d["tags"], list):
        raise ValueError("tags must be an array")
    if any(not isinstance(t, list) for t in d["tags"]):
        raise ValueError("each tag must be an array")
    parents = tuple(d["parents"])
    content = d["content"]
    tags = tuple(tuple(t) for t in d["tags"])
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


def _group_status_to_json(att: GroupStatus) -> dict:
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
        trust_config_path: str | None = None,
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
        self._last_group_statuses: dict[str, GroupStatus] = {}
        self._group_status_intervals: dict[str, float] = {}
        self._group_status_tasks: dict[str, asyncio.Task] = {}
        self._hosted_groups: set[str] = set()
        self._sync_locks: dict[str, SyncLockLease] = {}
        self._started = False
        self._trust_config = load_trust_config(trust_config_path)
        self._rate_limiter = RateLimiter()
        self._max_message_bytes = self._trust_config.max_message_bytes

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
            max_size=self._max_message_bytes,
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
                    raw_size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
                    msg = json.loads(raw)
                    action = msg.get("action", "")
                    if action != "heal_batch" and raw_size > MAX_EVENT_BYTES:
                        await ws.send(json.dumps({"type": "error", "message": "message exceeds 32 KiB"}))
                        continue
                    if raw_size > self._max_message_bytes:
                        await ws.send(json.dumps({"type": "error", "message": "message exceeds relay limit"}))
                        continue
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
            connection_id = id(ws)
            for group, lease in list(self._sync_locks.items()):
                if lease.connection_id == connection_id:
                    del self._sync_locks[group]
                    logger.info(
                        "sync_lock group=%s... released after disconnect of %s...",
                        group[:16],
                        lease.holder[:16],
                    )
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
        elif action == "heal":
            return await self._handle_heal(msg, ws)
        elif action == "get":
            return await self._handle_get(msg)
        elif action == "sync":
            return await self._handle_sync(msg, ws)
        elif action == "sync_ids":
            return await self._handle_sync_ids(msg)
        elif action == "sync_lock":
            return await self._handle_sync_lock(msg, ws)
        elif action == "sync_unlock":
            return await self._handle_sync_unlock(msg)
        elif action == "group_status":
            return await self._handle_group_status_request(msg)
        elif action == "submit_fraud_proof":
            return await self._handle_submit_fraud_proof(msg)
        elif action == "query_fraud_proofs":
            return await self._handle_query_fraud_proofs(msg, ws)
        elif action == "get_heal_challenge":
            return await self._handle_get_heal_challenge(msg, ws)
        elif action == "get_group_host_attestation":
            return await self._handle_get_group_host_attestation(msg, ws)
        elif action == "get_inventory_attestation":
            return await self._handle_get_inventory_attestation(msg, ws)
        elif action == "heal_batch":
            return await self._handle_heal_batch(msg, ws)
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

        att = await self._build_and_store_group_status(group)
        return [{"type": "group_status", "group_status": _group_status_to_json(att)}]

    async def _handle_unsubscribe(
        self, msg: dict, ws: ws_server.ServerConnection
    ) -> list[dict] | None:
        group = msg.get("group", "")
        if group in self._subscribers:
            self._subscribers[group].discard(ws)
            logger.info("unsubscribe from group %s (%d remaining)", group[:16] + "...", len(self._subscribers[group]))
        return [{"type": "ok", "message": "unsubscribed"}]

    async def _handle_publish(self, msg: dict, ws: ws_server.ServerConnection) -> list[dict] | None:
        rl = self._check_rate_limit("publish", ws)
        if rl is not None:
            return rl
        return await self._store_event_message(msg, action="publish", broadcast=True)

    async def _handle_heal(
        self, msg: dict, ws: ws_server.ServerConnection
    ) -> list[dict] | None:
        rl = self._check_rate_limit("slow_heal", ws)
        if rl is not None:
            return rl
        return await self._store_event_message(msg, action="heal", broadcast=False)

    def _event_receipt_response(self, event_receipt: EventReceipt) -> list[dict]:
        return [
            {
                "type": "event_receipt",
                "event_receipt": {
                    "event_id": event_receipt.event_id,
                    "group": event_receipt.group,
                    "relay": event_receipt.relay,
                    "ts": event_receipt.ts,
                    "sig": event_receipt.sig,
                },
            }
        ]

    def _client_ip(self, ws: ws_server.ServerConnection) -> str:
        peer = getattr(ws, "remote_address", None)
        if peer is None:
            return "unknown"
        return str(peer[0]) if isinstance(peer, tuple) and len(peer) >= 1 else "unknown"

    def _check_rate_limit(self, action: str, ws: ws_server.ServerConnection) -> list[dict] | None:
        rl = self._trust_config.rate_limits.get(action)
        if rl is None:
            return None
        ip = self._client_ip(ws)
        if not self._rate_limiter.allow(action, ip, rl.max, rl.window_seconds):
            logger.warning("rate limit hit action=%s ip=%s", action, ip)
            return [{"type": "error", "message": "rate limit exceeded"}]
        return None

    async def _store_event_message(
        self, msg: dict, *, action: str, broadcast: bool
    ) -> list[dict] | None:
        try:
            event_dict = msg.get("event", {})
            event = _json_to_event_dict(event_dict)

            if event.id and await self._store.has_event(event.id):
                stored = await self._store.get_event(event.id)
                if stored is not None:
                    event_receipt = build_event_receipt(
                        event=stored,
                        relay_keypair=self._keypair,
                        ts=int(time.time()),
                    )
                    logger.info(
                        "%s duplicate id=%s... -> event_receipt (skipped verify/store/broadcast)",
                        action,
                        event.id[:16],
                    )
                    return self._event_receipt_response(event_receipt)

            verify_event(event)

            is_new_group = event.type == "genesis" and event.group not in self._hosted_groups
            if is_new_group:
                self._hosted_groups.add(event.group)
                logger.info(
                    "auto-hosting new group %s (genesis by %s)",
                    event.group[:16] + "...",
                    event.author[:16] + "...",
                )

            if event.group not in self._hosted_groups:
                logger.warning(
                    "rejecting %s: group %s not hosted", action, event.group[:16] + "..."
                )
                return [{"type": "error", "message": "group not hosted"}]

            await self._relay_store.ingest(event)
            eid = event.id[:16] + "..." if event.id else "?"
            logger.info(
                "%s event type=%s group=%s author=%s id=%s broadcast=%s",
                action,
                event.type,
                event.group[:16] + "...",
                event.author[:16] + "...",
                eid,
                broadcast,
            )

            event_receipt = build_event_receipt(
                event=event,
                relay_keypair=self._keypair,
                ts=int(time.time()),
            )
            response = self._event_receipt_response(event_receipt)

            if broadcast:
                await self._broadcast_event(event)

            return response
        except Exception as e:
            logger.warning("%s failed: %s", action, e)
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

    async def _handle_sync_ids(self, msg: dict) -> list[dict] | None:
        group = msg.get("group", "")
        if not group:
            return [{"type": "error", "message": "group required"}]
        if group not in self._hosted_groups:
            return [{"type": "error", "message": "group not hosted"}]

        known_set = await self._store.get_known_set(group)
        ids = sorted(known_set)
        logger.info("sync_ids group=%s... -> %d ids", group[:16], len(ids))
        return [{"type": "ids", "group": group, "ids": ids}]

    async def _handle_sync_lock(
        self, msg: dict, ws: ws_server.ServerConnection | None = None
    ) -> list[dict]:
        group = msg.get("group", "")
        client_id = msg.get("client_id", "")
        if not group or not client_id:
            return [{"type": "error", "message": "group and client_id required"}]
        if group not in self._hosted_groups:
            return [{"type": "error", "message": "group not hosted"}]

        now = time.time()
        ttl = 30
        existing = self._sync_locks.get(group)
        if existing is not None:
            if existing.expires_at > now and existing.holder != client_id:
                return [
                    {
                        "type": "sync_lock_denied",
                        "group": group,
                        "expires_in": max(1, int(existing.expires_at - now)),
                    }
                ]
            if existing.expires_at > now:
                remaining = max(1, int(existing.expires_at - now))
                return [{"type": "sync_lock_granted", "group": group, "ttl": remaining}]

        self._sync_locks[group] = SyncLockLease(
            holder=client_id,
            expires_at=now + ttl,
            connection_id=id(ws) if ws is not None else None,
        )
        logger.info("sync_lock group=%s... granted to %s...", group[:16], client_id[:16])
        return [{"type": "sync_lock_granted", "group": group, "ttl": ttl}]

    async def _handle_sync_unlock(self, msg: dict) -> list[dict]:
        group = msg.get("group", "")
        client_id = msg.get("client_id", "")
        existing = self._sync_locks.get(group)
        if existing and existing.holder == client_id:
            del self._sync_locks[group]
            logger.info("sync_unlock group=%s... released by %s...", group[:16], client_id[:16])
        return [{"type": "ok", "message": "unlocked"}]

    async def _handle_group_status_request(self, msg: dict) -> list[dict] | None:
        group = msg.get("group", "")
        logger.info("group_status request group=%s...", group[:16])
        if group not in self._hosted_groups:
            return [{"type": "error", "message": "group not hosted"}]
        att = await self._build_and_store_group_status(group)
        return [{"type": "group_status", "group_status": _group_status_to_json(att)}]

    async def _handle_submit_fraud_proof(self, msg: dict) -> list[dict] | None:
        try:
            fp_dict = msg.get("fraud_proof", {})
            event = _json_to_event_dict(fp_dict.get("event", {})) if fp_dict.get("event") else None
            event_receipt_data = fp_dict.get("event_receipt", {})
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
            proof = FraudProof(
                type=fp_dict.get("type", "fraud_proof"),
                group=fp_dict.get("group", ""),
                relay=fp_dict.get("relay", ""),
                event_id=fp_dict.get("event_id", ""),
                event=event,
                event_receipt=event_receipt,
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
                "event_receipt": {
                    "event_id": fp.event_receipt.event_id,
                    "group": fp.event_receipt.group,
                    "relay": fp.event_receipt.relay,
                    "ts": fp.event_receipt.ts,
                    "sig": fp.event_receipt.sig,
                }
                if fp.event_receipt
                else None,
                "evidence": fp.evidence,
            },
        }

    def _heal_challenge_to_json(self, c: HealChallenge) -> dict:
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

    def _json_to_heal_challenge(self, d: dict) -> HealChallenge:
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

    def _group_host_attestation_to_json(self, a: GroupHostAttestation) -> dict:
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

    def _json_to_group_host_attestation(self, d: dict) -> GroupHostAttestation:
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

    def _inventory_attestation_to_json(self, a: InventoryAttestation) -> dict:
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

    def _json_to_inventory_attestation(self, d: dict) -> InventoryAttestation:
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

    async def _handle_get_heal_challenge(
        self, msg: dict, ws: ws_server.ServerConnection
    ) -> list[dict]:
        rl = self._check_rate_limit("get_heal_challenge", ws)
        if rl is not None:
            return rl

        if not self._trust_config.has_trusted_witnesses:
            return [{"type": "error", "message": "no trusted witnesses configured"}]

        group = msg.get("group", "")
        ids = msg.get("ids", [])
        if not group or not isinstance(ids, list) or not ids:
            return [{"type": "error", "message": "group and ids required"}]

        unique_ids = sorted(set(ids))
        if len(unique_ids) != len(ids):
            return [{"type": "error", "message": "duplicate event IDs"}]
        if len(unique_ids) > self._trust_config.batch_limits.max_events:
            return [{"type": "error", "message": "batch too large"}]

        for eid in unique_ids:
            if not isinstance(eid, str) or len(eid) != 64:
                return [{"type": "error", "message": "invalid event ID"}]

        now = int(time.time())
        challenge = build_heal_challenge(
            group=group,
            receiver_keypair=self._keypair,
            ids=unique_ids,
            trusted_witnesses=self._trust_config.trusted_witness_relays,
            threshold=self._trust_config.threshold,
            ts=now,
            expires=now + self._trust_config.challenge_expiry_seconds,
        )
        logger.info(
            "get_heal_challenge group=%s... ids=%d witnesses=%d",
            group[:16], len(unique_ids), len(challenge.trusted_witnesses),
        )
        return [{"type": "heal_challenge", "heal_challenge": self._heal_challenge_to_json(challenge)}]

    async def _handle_get_group_host_attestation(
        self, msg: dict, ws: ws_server.ServerConnection
    ) -> list[dict]:
        rl = self._check_rate_limit("get_group_host_attestation", ws)
        if rl is not None:
            return rl

        try:
            challenge = self._json_to_heal_challenge(msg.get("heal_challenge", {}))
        except Exception as e:
            return [{"type": "error", "message": f"invalid challenge: {e}"}]

        now = int(time.time())
        if not verify_heal_challenge(challenge, now_ts=now):
            return [{"type": "error", "message": "invalid challenge"}]

        own_pub = self._keypair.pubkey_hex
        witness_pubkeys = {w.relay for w in challenge.trusted_witnesses}
        if own_pub not in witness_pubkeys:
            return [{"type": "error", "message": "not a witness for this challenge"}]

        if not self._trust_config.is_willing_to_witness_for(challenge.receiver):
            return [{"type": "error", "message": "not willing to witness for this receiver"}]

        hosts = challenge.group in self._hosted_groups
        att = build_group_host_attestation(
            group=challenge.group,
            witness_keypair=self._keypair,
            receiver=challenge.receiver,
            challenge_id=compute_challenge_id(challenge),
            hosts=hosts,
            ts=now,
            expires=min(challenge.expires, now + self._trust_config.challenge_expiry_seconds),
        )
        logger.info(
            "get_group_host_attestation group=%s... hosts=%s",
            challenge.group[:16], hosts,
        )
        return [{"type": "group_host_attestation", "group_host_attestation": self._group_host_attestation_to_json(att)}]

    async def _handle_get_inventory_attestation(
        self, msg: dict, ws: ws_server.ServerConnection
    ) -> list[dict]:
        rl = self._check_rate_limit("get_inventory_attestation", ws)
        if rl is not None:
            return rl

        try:
            challenge = self._json_to_heal_challenge(msg.get("heal_challenge", {}))
        except Exception as e:
            return [{"type": "error", "message": f"invalid challenge: {e}"}]

        ids = msg.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return [{"type": "error", "message": "ids required"}]

        now = int(time.time())
        if not verify_heal_challenge(challenge, now_ts=now):
            return [{"type": "error", "message": "invalid challenge"}]

        own_pub = self._keypair.pubkey_hex
        witness_pubkeys = {w.relay for w in challenge.trusted_witnesses}
        if own_pub not in witness_pubkeys:
            return [{"type": "error", "message": "not a witness for this challenge"}]

        if compute_set_hash(ids) != challenge.ids_hash or len(ids) != challenge.count:
            return [{"type": "error", "message": "ids do not match challenge"}]

        covered: list[str] = []
        missing: list[str] = []
        for eid in ids:
            if await self._store.has_event(eid):
                covered.append(eid)
            else:
                missing.append(eid)

        if not covered:
            logger.info(
                "get_inventory_attestation group=%s... -> inventory_missing",
                challenge.group[:16],
            )
            return [{"type": "inventory_missing", "missing": missing}]

        att = build_inventory_attestation(
            group=challenge.group,
            witness_keypair=self._keypair,
            receiver=challenge.receiver,
            challenge_id=compute_challenge_id(challenge),
            covered_ids=covered,
            ts=now,
            expires=min(challenge.expires, now + self._trust_config.challenge_expiry_seconds),
        )
        logger.info(
            "get_inventory_attestation group=%s... covered=%d missing=%d",
            challenge.group[:16], len(covered), len(missing),
        )
        return [{
            "type": "inventory_attestation",
            "inventory_attestation": self._inventory_attestation_to_json(att),
            "ids": covered,
            "missing": missing,
        }]

    async def _handle_heal_batch(
        self, msg: dict, ws: ws_server.ServerConnection
    ) -> list[dict]:
        rl = self._check_rate_limit("heal_batch", ws)
        if rl is not None:
            return rl

        try:
            challenge = self._json_to_heal_challenge(msg.get("heal_challenge", {}))
        except Exception as e:
            return [{"type": "error", "message": f"invalid challenge: {e}"}]

        now = int(time.time())
        if not verify_heal_challenge(challenge, receiver_pubkey=self._keypair.pubkey_hex, now_ts=now):
            return [{"type": "error", "message": "invalid challenge"}]
        if challenge.expires <= now:
            return [{"type": "error", "message": "challenge expired"}]

        raw_events = msg.get("events", [])
        if not isinstance(raw_events, list) or not raw_events:
            return [{"type": "error", "message": "events required"}]

        events: list[Event] = []
        event_ids: list[str] = []
        for ed in raw_events:
            try:
                event = _json_to_event_dict(ed)
            except Exception as e:
                return [{"type": "error", "message": f"invalid event: {e}"}]

            event_json_bytes = len(json.dumps(ed, ensure_ascii=False).encode("utf-8"))
            if event_json_bytes > MAX_EVENT_BYTES:
                return [{"type": "error", "message": "event exceeds 32 KiB"}]

            try:
                verify_event(event)
            except Exception as e:
                return [{"type": "error", "message": f"event verification failed: {e}"}]

            if event.group != challenge.group:
                return [{"type": "error", "message": "event group does not match challenge"}]

            if event.id is not None:
                event_ids.append(event.id)
            events.append(event)

        unique_ids = sorted(set(event_ids))
        if len(unique_ids) != len(event_ids):
            return [{"type": "error", "message": "duplicate event IDs in batch"}]
        if compute_set_hash(unique_ids) != challenge.ids_hash or len(unique_ids) != challenge.count:
            return [{"type": "error", "message": "event IDs do not match challenge"}]

        raw_host_atts = msg.get("group_host_attestations", [])
        host_atts: list[GroupHostAttestation] = []
        for hd in raw_host_atts:
            try:
                host_atts.append(self._json_to_group_host_attestation(hd))
            except Exception as e:
                return [{"type": "error", "message": f"invalid host attestation: {e}"}]

        raw_inv_atts = msg.get("inventory_attestations", [])
        inv_evidence: list[InventoryEvidence] = []
        for item in raw_inv_atts:
            try:
                att = self._json_to_inventory_attestation(item.get("inventory_attestation", {}))
            except Exception as e:
                return [{"type": "error", "message": f"invalid inventory attestation: {e}"}]
            covered = item.get("ids", [])
            inv_evidence.append(InventoryEvidence(att, frozenset(covered)))

        already_have_ids: set[str] = set()
        for eid in unique_ids:
            if await self._store.has_event(eid):
                already_have_ids.add(eid)

        remaining_quota: int | None = None
        if self._trust_config.per_group_storage_quota is not None:
            current = await self._store.count_events(challenge.group)
            remaining_quota = max(0, self._trust_config.per_group_storage_quota - current)

        decision = compute_admission(
            challenge=challenge,
            event_ids=unique_ids,
            already_have_ids=frozenset(already_have_ids),
            group_host_attestations=host_atts,
            inventory_evidence=inv_evidence,
            now_ts=now,
            remaining_quota=remaining_quota,
        )

        events_by_id = {e.id: e for e in events if e.id is not None}
        stored: list[str] = []
        for eid in decision.accepted:
            event = events_by_id.get(eid)
            if event is None:
                continue
            if event.type == "genesis" and event.group not in self._hosted_groups:
                self._hosted_groups.add(event.group)
                logger.info("auto-hosting group %s (genesis in heal_batch)", event.group[:16] + "...")
            await self._relay_store.ingest(event)
            witnesses = decision.admitted_by.get(eid, ())
            await self._store.put_heal_provenance(eid, challenge.group, list(witnesses), now)
            stored.append(eid)

        if stored:
            await self._build_and_store_group_status(challenge.group)

        logger.info(
            "heal_batch group=%s... stored=%d already_have=%d rejected=%d",
            challenge.group[:16], len(stored), len(decision.already_have), len(decision.rejected),
        )
        return [{
            "type": "heal_batch_result",
            "stored": list(stored),
            "already_have": list(decision.already_have),
            "rejected": [{"id": eid, "reason": reason} for eid, reason in decision.rejected],
        }]

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

    async def _broadcast_group_status(self, group: str, att: GroupStatus) -> None:
        subs = self._subscribers.get(group, set())
        msg = json.dumps(
            {"type": "group_status", "group_status": _group_status_to_json(att)}, default=str
        )
        delivered = 0
        for sub in list(subs):
            try:
                await sub.send(msg)
                delivered += 1
            except Exception:
                subs.discard(sub)
        if subs:
            logger.info("broadcast group_status to %d/%d subscribers for group %s...", delivered, len(subs), group[:16])

    async def _build_and_store_group_status(self, group: str) -> GroupStatus:
        known_set = await self._store.get_known_set(group)
        tips = await self._store.get_tips(group)
        count = await self._store.count_events(group)

        prev = self._last_group_statuses.get(group)
        att = build_group_status(
            group=group,
            relay_keypair=self._keypair,
            known_set=known_set,
            tips=tips,
            count=count,
            prev=prev,
            ts=int(time.time()),
        )
        self._last_group_statuses[group] = att

        await self._broadcast_group_status(group, att)
        return att
