from __future__ import annotations

from collections.abc import Callable, Awaitable, Sequence

import asyncio

from fern.events.event import Event
from fern.events.types import ProtocolTypes
from fern.events.validation import verify_event
from fern.completeness.attestations import Attestation
from fern.completeness.trust_ledger import TrustLedger
from fern.completeness.receipts import Receipt
from fern.identity.user import UserIdentity
from fern.state.types import GroupState
from fern.state.machine import derive_group_state
from fern.storage.interfaces import EventStore, ReceiptStore
from fern.transport.interfaces import RelayTransport
from fern.client.bootstrap import fetch_genesis, initial_sync
from fern.client.publisher import publish_event
from fern.client.sync import sync_diff
from fern.client.subscriber import subscribe_to_relays
from fern.client.monitor_runner import run_monitor_pass


class GroupSession:
    def __init__(
        self,
        *,
        user: UserIdentity,
        store: EventStore,
        receipt_store: ReceiptStore,
        trust_ledger: TrustLedger | None = None,
    ) -> None:
        self._user = user
        self._store = store
        self._receipt_store = receipt_store
        self._trust_ledger = trust_ledger or TrustLedger()
        self._transports: list[RelayTransport] = []
        self._group_pubkey: str | None = None
        self._state: GroupState | None = None
        self._event_callbacks: list[Callable[[Event], Awaitable[None]]] = []
        self._attestation_callbacks: list[Callable[[Attestation], Awaitable[None]]] = []
        self._state_callbacks: list[Callable[[GroupState], Awaitable[None]]] = []
        self._state_events_seen: set[str] = set()
        self._syncs_in_flight: set[tuple[str, str]] = set()

    @property
    def user(self) -> UserIdentity:
        return self._user

    @property
    def state(self) -> GroupState | None:
        return self._state

    @property
    def trust_ledger(self) -> TrustLedger:
        return self._trust_ledger

    @property
    def group_pubkey(self) -> str | None:
        return self._group_pubkey

    async def join_group(
        self,
        group_pubkey: str,
        transports: Sequence[RelayTransport],
    ) -> GroupState:
        self._group_pubkey = group_pubkey
        self._transports = list(transports)

        for transport in self._transports:
            await transport.connect()

        genesis = await fetch_genesis(group_pubkey, self._transports)
        if genesis is None:
            raise ValueError(f"Could not fetch genesis for group {group_pubkey}")
        await self._store.put_event(genesis)
        self._state_events_seen.add(genesis.id or "")

        events = await initial_sync(
            group_pubkey,
            self._transports,
            self._store,
            client_id=self._user.pubkey,
        )

        for transport in self._transports:
            transport.on_event(self._handle_event)
            transport.on_attestation(self._handle_attestation)

        state, rejected = derive_group_state(events)
        self._state = state

        for event in events:
            if event.id and event.type != ProtocolTypes.GENESIS:
                self._state_events_seen.add(event.id)

        await subscribe_to_relays(group_pubkey, self._transports)

        return state

    async def publish(self, event: Event) -> tuple[Event, list[Receipt]]:
        return await publish_event(
            event,
            self._transports,
            receipt_store=self._receipt_store,
        )

    async def get_known_set(self) -> frozenset[str]:
        if self._group_pubkey is None:
            return frozenset()
        return await self._store.get_known_set(self._group_pubkey)

    async def refresh_state(self) -> GroupState | None:
        if self._group_pubkey is None:
            return None
        events = []
        async for e in self._store.iter_group_events(self._group_pubkey):
            events.append(e)
        state, _ = derive_group_state(events)
        self._state = state
        return state

    def on_event(self, callback: Callable[[Event], Awaitable[None]]) -> None:
        self._event_callbacks.append(callback)

    def on_attestation(self, callback: Callable[[Attestation], Awaitable[None]]) -> None:
        self._attestation_callbacks.append(callback)

    def on_state_change(self, callback: Callable[[GroupState], Awaitable[None]]) -> None:
        self._state_callbacks.append(callback)

    async def _handle_event(self, event: Event) -> None:
        try:
            verify_event(event)
        except Exception:
            return
        await self._store.put_event(event)

        if event.id and event.id in self._state_events_seen:
            return
        if event.id:
            self._state_events_seen.add(event.id)

        if event.type != ProtocolTypes.GENESIS and event.type in (
            ProtocolTypes.JOIN,
            ProtocolTypes.LEAVE,
            ProtocolTypes.INVITE,
            ProtocolTypes.KICK,
            ProtocolTypes.BAN,
            ProtocolTypes.UNBAN,
            ProtocolTypes.MOD_ADD,
            ProtocolTypes.MOD_REMOVE,
            ProtocolTypes.RELAY_UPDATE,
            ProtocolTypes.METADATA_UPDATE,
        ):
            old_state = self._state
            await self.refresh_state()
            if self._state is not None and self._state != old_state:
                for cb in self._state_callbacks:
                    try:
                        asyncio.ensure_future(cb(self._state))
                    except Exception:
                        pass

        for callback in self._event_callbacks:
            try:
                asyncio.ensure_future(callback(event))
            except Exception:
                pass

    async def _handle_attestation(self, attestation: Attestation) -> None:
        if self._group_pubkey is None:
            return

        for transport in self._transports:
            if transport.relay_pubkey == attestation.relay:
                sibling_attestations = {}
                for t in self._transports:
                    if t.relay_pubkey != attestation.relay and t.relay_pubkey:
                        entry = self._trust_ledger.entries.get(t.relay_pubkey)
                        if entry and entry.last_attestation:
                            sibling_attestations[t.relay_pubkey] = entry.last_attestation

                known_set = await self.get_known_set()

                receipts_for_relay: dict[str, Receipt] = {}

                try:
                    await run_monitor_pass(
                        relay=transport,
                        attestation=attestation,
                        local_known_set=known_set,
                        receipts_for_relay=receipts_for_relay,
                        trust_ledger=self._trust_ledger,
                        sibling_attestations=sibling_attestations,
                    )
                except Exception:
                    pass

                key = (transport.relay_pubkey, self._group_pubkey)
                if key not in self._syncs_in_flight:
                    self._syncs_in_flight.add(key)
                    try:
                        result = await sync_diff(
                            transport=transport,
                            group=self._group_pubkey,
                            store=self._store,
                            client_id=self._user.pubkey,
                            wait_on_lock=False,
                        )
                        if result.fetched > 0:
                            await self.refresh_state()
                    except Exception:
                        pass
                    finally:
                        self._syncs_in_flight.discard(key)
                break

        for cb in self._attestation_callbacks:
            try:
                asyncio.ensure_future(cb(attestation))
            except Exception:
                pass

    async def close(self) -> None:
        for transport in self._transports:
            try:
                await transport.close()
            except Exception:
                pass
