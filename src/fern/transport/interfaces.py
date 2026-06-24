from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from fern.completeness.event_receipts import EventReceipt
from fern.completeness.fraud_proofs import FraudProof
from fern.completeness.group_statuses import GroupStatus
from fern.completeness.heal_attestations import (
    GroupHostAttestation,
    HealChallenge,
    InventoryAttestation,
)
from fern.events.event import Event


@dataclass(frozen=True)
class RelayMetadata:
    name: str = ""
    description: str = ""
    pubkey: str = ""
    software: str = ""
    version: str = ""
    groups: tuple[str, ...] = ()
    retention: str = "full"


@dataclass(frozen=True)
class SyncLockResult:
    granted: bool
    ttl: int | None = None
    expires_in: int | None = None


@dataclass(frozen=True)
class InventoryAttestationResult:
    attestation: InventoryAttestation | None = None
    covered: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    inventory_missing: bool = False


@dataclass(frozen=True)
class HealBatchResult:
    stored: tuple[str, ...] = ()
    already_have: tuple[str, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()


class RelayTransport(Protocol):
    url: str
    relay_pubkey: str

    async def connect(self) -> None: ...
    async def close(self) -> None: ...

    async def fetch_metadata(self) -> RelayMetadata: ...
    async def subscribe(self, group: str) -> None: ...
    async def unsubscribe(self, group: str) -> None: ...

    async def publish(self, event: Event) -> EventReceipt: ...
    async def heal(self, event: Event) -> EventReceipt: ...
    async def get(self, event_id: str) -> Event | None: ...
    def sync(self, group: str, since_ts: int | None = None) -> AsyncIterator[Event]: ...
    async def sync_ids(self, group: str) -> list[str]: ...
    async def sync_lock(self, group: str, client_id: str) -> SyncLockResult: ...
    async def sync_unlock(self, group: str, client_id: str) -> None: ...
    async def request_group_status(self, group: str) -> GroupStatus: ...
    async def submit_fraud_proof(self, proof: FraudProof) -> str: ...
    def query_fraud_proofs(
        self, *, relay: str | None = None, group: str | None = None
    ) -> AsyncIterator[FraudProof]: ...

    async def get_heal_challenge(
        self, group: str, ids: Sequence[str]
    ) -> HealChallenge: ...
    async def get_group_host_attestation(
        self, challenge: HealChallenge
    ) -> GroupHostAttestation | None: ...
    async def get_inventory_attestation(
        self, challenge: HealChallenge, ids: Sequence[str]
    ) -> InventoryAttestationResult: ...
    async def heal_batch(
        self,
        *,
        challenge: HealChallenge,
        events: Sequence[Event],
        group_host_attestations: Sequence[GroupHostAttestation],
        inventory_attestations: Sequence[tuple[InventoryAttestation, Sequence[str]]],
    ) -> HealBatchResult: ...

    def on_event(self, callback: Callable[[Event], Awaitable[None]]) -> None: ...
    def on_group_status(self, callback: Callable[[GroupStatus], Awaitable[None]]) -> None: ...
