from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from fern.events.event import Event
from fern.completeness.event_receipts import EventReceipt


class MemoryStore:
    def __init__(self) -> None:
        self._events: dict[str, Event] = {}
        self._event_receipts: dict[tuple[str, str], EventReceipt] = {}
        self._heal_provenance: dict[str, set[str]] = {}

    async def put_event(self, event: Event) -> None:
        assert event.id is not None
        self._events[event.id] = event

    async def get_event(self, event_id: str) -> Event | None:
        return self._events.get(event_id)

    async def has_event(self, event_id: str) -> bool:
        return event_id in self._events

    async def iter_all_events(self) -> AsyncIterator[Event]:
        for event in self._events.values():
            yield event

    async def iter_group_events(self, group: str) -> AsyncIterator[Event]:
        for event in self._events.values():
            if event.group == group:
                yield event

    async def iter_since(self, group: str, since_ts: int) -> AsyncIterator[Event]:
        for event in self._events.values():
            if event.group == group and event.ts > since_ts:
                yield event

    async def count_events(self, group: str) -> int:
        return sum(1 for e in self._events.values() if e.group == group)

    async def get_tips(self, group: str) -> list[str]:
        from fern.dag.heads import compute_heads

        group_events = [e for e in self._events.values() if e.group == group]
        return list(compute_heads(group_events))

    async def get_known_set(self, group: str) -> frozenset[str]:
        return frozenset(
            e.id for e in self._events.values() if e.group == group and e.id is not None
        )

    async def get_parent_map(self, group: str) -> Mapping[str, frozenset[str]]:
        from fern.dag.heads import parent_to_children

        group_events = [e for e in self._events.values() if e.group == group]
        return parent_to_children(group_events)

    async def get_hosted_groups(self) -> list[str]:
        return list({e.group for e in self._events.values()})

    async def put_event_receipt(self, event_id: str, relay_pubkey: str, event_receipt: EventReceipt) -> None:
        self._event_receipts[(event_id, relay_pubkey)] = event_receipt

    async def get_event_receipt(self, event_id: str, relay_pubkey: str) -> EventReceipt | None:
        return self._event_receipts.get((event_id, relay_pubkey))

    async def iter_event_receipts_for_event(self, event_id: str) -> AsyncIterator[EventReceipt]:
        for (eid, _), event_receipt in self._event_receipts.items():
            if eid == event_id:
                yield event_receipt

    async def delete_event(self, event_id: str) -> None:
        self._events.pop(event_id, None)
        self._heal_provenance.pop(event_id, None)

    async def put_heal_provenance(
        self, event_id: str, group: str, witness_pubkeys: list[str], ts: int
    ) -> None:
        existing = self._heal_provenance.setdefault(event_id, set())
        existing.update(witness_pubkeys)

    async def get_heal_provenance(self, event_id: str) -> list[str]:
        return sorted(self._heal_provenance.get(event_id, set()))

    async def iter_events_admitted_by(
        self, witness_pubkey: str, group: str | None = None
    ) -> AsyncIterator[str]:
        for event_id, witnesses in self._heal_provenance.items():
            if witness_pubkey in witnesses:
                yield event_id

    async def delete_events_admitted_only_by(self, witness_pubkey: str) -> list[str]:
        orphans = [
            event_id
            for event_id, witnesses in self._heal_provenance.items()
            if witnesses == {witness_pubkey}
        ]
        for eid in orphans:
            self._events.pop(eid, None)
            self._heal_provenance.pop(eid, None)
        return orphans
