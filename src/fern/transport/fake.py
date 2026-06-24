from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from fern.events.event import Event
from fern.events.validation import verify_event
from fern.completeness.event_receipts import EventReceipt, build_event_receipt
from fern.completeness.group_statuses import GroupStatus, build_group_status, compute_set_hash
from fern.completeness.fraud_proofs import (
    FraudProof,
    verify_fraud_proof,
    compute_fraud_proof_id,
)
from fern.completeness.heal_attestations import (
    GroupHostAttestation,
    HealChallenge,
    InventoryAttestation,
    build_group_host_attestation,
    build_heal_challenge,
    build_inventory_attestation,
    compute_challenge_id,
    verify_heal_challenge,
)
from fern.crypto.keys import Keypair
from fern.relay.admission import InventoryEvidence, compute_admission
from fern.relay.trust_config import RelayTrustConfig
from fern.storage.memory import MemoryStore
from fern.transport.interfaces import (
    HealBatchResult,
    InventoryAttestationResult,
    RelayMetadata,
    SyncLockResult,
)


class FakeRelay:
    def __init__(
        self,
        relay_keypair: Keypair | None = None,
        trust_config: RelayTrustConfig | None = None,
    ):
        if relay_keypair is None:
            relay_keypair = Keypair.generate()
        self._keypair = relay_keypair
        self._store = MemoryStore()
        self._event_receipts: dict[tuple[str, str], EventReceipt] = {}
        self._fraud_proofs: dict[str, FraudProof] = {}
        self._deleted_events: set[str] = set()
        self._event_callbacks: list[Callable[[Event], Awaitable[None]]] = []
        self._group_status_callbacks: list[Callable[[GroupStatus], Awaitable[None]]] = []
        self._subscribed_groups: set[str] = set()
        self._hosted_groups: set[str] = set()
        self._last_group_statuses: dict[str, GroupStatus] = {}
        self._publish_lock = asyncio.Lock()
        self._sync_locks: dict[str, tuple[str, float]] = {}
        self._trust_config = trust_config or RelayTrustConfig()

    @property
    def url(self) -> str:
        return f"fake://relay.{self._keypair.pubkey_hex[:8]}"

    @property
    def relay_pubkey(self) -> str:
        return self._keypair.pubkey_hex

    @property
    def keypair(self) -> Keypair:
        return self._keypair

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def fetch_metadata(self) -> RelayMetadata:
        return RelayMetadata(
            name="Fake Relay",
            description="In-process fake relay",
            pubkey=self.relay_pubkey,
            software="fern-test",
            version="0.1.0",
            retention="full",
        )

    async def subscribe(self, group: str) -> None:
        self._subscribed_groups.add(group)
        self._hosted_groups.add(group)

    async def unsubscribe(self, group: str) -> None:
        self._subscribed_groups.discard(group)

    async def publish(self, event: Event) -> EventReceipt:
        async with self._publish_lock:
            verify_event(event)
            if event.type == "genesis":
                self._hosted_groups.add(event.group)
            await self._store.put_event(event)
            event_receipt = build_event_receipt(
                event=event,
                relay_keypair=self._keypair,
                ts=int(time.time()),
            )
            assert event.id is not None
            self._event_receipts[(event.id, self.relay_pubkey)] = event_receipt

            if event.group in self._subscribed_groups:
                for cb in self._event_callbacks:
                    asyncio.ensure_future(cb(event))

            return event_receipt

    async def heal(self, event: Event) -> EventReceipt:
        async with self._publish_lock:
            verify_event(event)
            if event.type == "genesis":
                self._hosted_groups.add(event.group)
            await self._store.put_event(event)
            event_receipt = build_event_receipt(
                event=event,
                relay_keypair=self._keypair,
                ts=int(time.time()),
            )
            assert event.id is not None
            self._event_receipts[(event.id, self.relay_pubkey)] = event_receipt
            return event_receipt

    async def get(self, event_id: str) -> Event | None:
        if event_id in self._deleted_events:
            return None
        return await self._store.get_event(event_id)

    async def sync(self, group: str, since_ts: int | None = None) -> AsyncIterator[Event]:
        async for event in self._store.iter_group_events(group):
            if since_ts is None or event.ts > since_ts:
                yield event

    async def sync_ids(self, group: str) -> list[str]:
        return sorted(await self._store.get_known_set(group))

    async def sync_lock(self, group: str, client_id: str) -> SyncLockResult:
        now = time.time()
        ttl = 30
        existing = self._sync_locks.get(group)
        if existing is not None:
            holder, expires_at = existing
            if expires_at > now and holder != client_id:
                return SyncLockResult(granted=False, expires_in=max(1, int(expires_at - now)))
            if expires_at > now:
                return SyncLockResult(granted=True, ttl=max(1, int(expires_at - now)))

        self._sync_locks[group] = (client_id, now + ttl)
        return SyncLockResult(granted=True, ttl=ttl)

    async def sync_unlock(self, group: str, client_id: str) -> None:
        existing = self._sync_locks.get(group)
        if existing and existing[0] == client_id:
            del self._sync_locks[group]

    async def request_group_status(self, group: str) -> GroupStatus:
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

        if group in self._subscribed_groups:
            for cb in self._group_status_callbacks:
                asyncio.ensure_future(cb(att))

        return att

    async def submit_fraud_proof(self, proof: FraudProof) -> str:
        if not verify_fraud_proof(proof):
            raise ValueError("Invalid fraud proof")
        fp_id = compute_fraud_proof_id(proof)
        self._fraud_proofs[fp_id] = proof
        return fp_id

    async def query_fraud_proofs(
        self, *, relay: str | None = None, group: str | None = None
    ) -> AsyncIterator[FraudProof]:
        for fp in self._fraud_proofs.values():
            if relay is not None and fp.relay != relay:
                continue
            if group is not None and fp.group != group:
                continue
            yield fp

    def on_event(self, callback: Callable[[Event], Awaitable[None]]) -> None:
        self._event_callbacks.append(callback)

    def on_group_status(self, callback: Callable[[GroupStatus], Awaitable[None]]) -> None:
        self._group_status_callbacks.append(callback)

    @property
    def trust_config(self) -> RelayTrustConfig:
        return self._trust_config

    @property
    def hosted_groups(self) -> set[str]:
        return self._hosted_groups

    async def get_heal_challenge(self, group: str, ids: Sequence[str]) -> HealChallenge:
        if not self._trust_config.has_trusted_witnesses:
            raise ValueError("no trusted witnesses configured")
        unique_ids = sorted(set(ids))
        if len(unique_ids) > self._trust_config.batch_limits.max_events:
            raise ValueError("batch too large")
        now = int(time.time())
        return build_heal_challenge(
            group=group,
            receiver_keypair=self._keypair,
            ids=unique_ids,
            trusted_witnesses=self._trust_config.trusted_witness_relays,
            threshold=self._trust_config.threshold,
            ts=now,
            expires=now + self._trust_config.challenge_expiry_seconds,
        )

    async def get_group_host_attestation(
        self, challenge: HealChallenge
    ) -> GroupHostAttestation | None:
        if not verify_heal_challenge(
            challenge, receiver_pubkey=challenge.receiver, now_ts=int(time.time())
        ):
            raise ValueError("invalid challenge")
        own_pub = self._keypair.pubkey_hex
        witness_pubkeys = {w.relay for w in challenge.trusted_witnesses}
        if own_pub not in witness_pubkeys:
            raise ValueError("not a witness for this challenge")
        if not self._trust_config.is_willing_to_witness_for(challenge.receiver):
            raise ValueError("not willing to witness for this receiver")
        hosts = challenge.group in self._hosted_groups
        now = int(time.time())
        return build_group_host_attestation(
            group=challenge.group,
            witness_keypair=self._keypair,
            receiver=challenge.receiver,
            challenge_id=compute_challenge_id(challenge),
            hosts=hosts,
            ts=now,
            expires=min(challenge.expires, now + self._trust_config.challenge_expiry_seconds),
        )

    async def get_inventory_attestation(
        self, challenge: HealChallenge, ids: Sequence[str]
    ) -> InventoryAttestationResult:
        if not verify_heal_challenge(
            challenge, receiver_pubkey=challenge.receiver, now_ts=int(time.time())
        ):
            raise ValueError("invalid challenge")
        own_pub = self._keypair.pubkey_hex
        witness_pubkeys = {w.relay for w in challenge.trusted_witnesses}
        if own_pub not in witness_pubkeys:
            raise ValueError("not a witness for this challenge")
        if compute_set_hash(ids) != challenge.ids_hash or len(ids) != challenge.count:
            raise ValueError("ids do not match challenge")
        covered: list[str] = []
        missing: list[str] = []
        for eid in ids:
            if await self._store.has_event(eid):
                covered.append(eid)
            else:
                missing.append(eid)
        if not covered:
            return InventoryAttestationResult(inventory_missing=True, missing=tuple(missing))
        now = int(time.time())
        att = build_inventory_attestation(
            group=challenge.group,
            witness_keypair=self._keypair,
            receiver=challenge.receiver,
            challenge_id=compute_challenge_id(challenge),
            covered_ids=covered,
            ts=now,
            expires=min(challenge.expires, now + self._trust_config.challenge_expiry_seconds),
        )
        return InventoryAttestationResult(
            attestation=att, covered=tuple(covered), missing=tuple(missing)
        )

    async def heal_batch(
        self,
        *,
        challenge: HealChallenge,
        events: Sequence[Event],
        group_host_attestations: Sequence[GroupHostAttestation],
        inventory_attestations: Sequence[tuple[InventoryAttestation, Sequence[str]]],
    ) -> HealBatchResult:
        now = int(time.time())
        if not verify_heal_challenge(
            challenge, receiver_pubkey=self._keypair.pubkey_hex, now_ts=now
        ):
            raise ValueError("invalid challenge")
        if challenge.expires <= now:
            raise ValueError("challenge expired")

        event_ids = sorted({e.id for e in events if e.id is not None})
        if compute_set_hash(event_ids) != challenge.ids_hash or len(event_ids) != challenge.count:
            raise ValueError("event ids do not match challenge")

        for event in events:
            verify_event(event)
            if event.group != challenge.group:
                raise ValueError("event group mismatch")

        already_have_ids: frozenset[str] = frozenset()
        for eid in event_ids:
            if await self._store.has_event(eid):
                already_have_ids = already_have_ids | {eid}

        inv_evidence: list[InventoryEvidence] = []
        for att, covered in inventory_attestations:
            inv_evidence.append(InventoryEvidence(att, frozenset(covered)))

        remaining_quota: int | None = None
        if self._trust_config.per_group_storage_quota is not None:
            current = await self._store.count_events(challenge.group)
            remaining_quota = max(0, self._trust_config.per_group_storage_quota - current)

        decision = compute_admission(
            challenge=challenge,
            event_ids=event_ids,
            already_have_ids=already_have_ids,
            group_host_attestations=group_host_attestations,
            inventory_evidence=inv_evidence,
            now_ts=now,
            remaining_quota=remaining_quota,
        )

        events_by_id = {e.id: e for e in events if e.id is not None}
        for eid in decision.accepted:
            event = events_by_id[eid]
            if event.type == "genesis":
                self._hosted_groups.add(event.group)
            await self._store.put_event(event)
            witnesses = decision.admitted_by.get(eid, ())
            await self._store.put_heal_provenance(eid, challenge.group, list(witnesses), now)

        if decision.accepted:
            await self._request_group_status_internal(challenge.group)

        return HealBatchResult(
            stored=decision.accepted,
            already_have=decision.already_have,
            rejected=decision.rejected,
        )

    async def _request_group_status_internal(self, group: str) -> GroupStatus:
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
        if group in self._subscribed_groups:
            for cb in self._group_status_callbacks:
                asyncio.ensure_future(cb(att))
        return att

    def drop_event(self, event_id: str) -> None:
        if event_id in self._store._events:
            del self._store._events[event_id]
        self._deleted_events.add(event_id)

    def __repr__(self) -> str:
        return f"FakeRelay({self.relay_pubkey[:12]}...)"


class FakeRelayNetwork:
    def __init__(self) -> None:
        self.relays: list[FakeRelay] = []

    def spawn(self, count: int = 3) -> list[FakeRelay]:
        new_relays = [FakeRelay() for _ in range(count)]
        self.relays.extend(new_relays)
        return new_relays

    def connect_relays(self) -> None:
        pass
