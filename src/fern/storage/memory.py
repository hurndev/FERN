from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from fern.events.event import Event
from fern.completeness.receipts import Receipt


class MemoryStore:
    def __init__(self) -> None:
        self._events: dict[str, Event] = {}
        self._receipts: dict[tuple[str, str], Receipt] = {}

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

    async def put_receipt(self, event_id: str, relay_pubkey: str, receipt: Receipt) -> None:
        self._receipts[(event_id, relay_pubkey)] = receipt

    async def get_receipt(self, event_id: str, relay_pubkey: str) -> Receipt | None:
        return self._receipts.get((event_id, relay_pubkey))

    async def iter_receipts_for_event(self, event_id: str) -> AsyncIterator[Receipt]:
        for (eid, _), receipt in self._receipts.items():
            if eid == event_id:
                yield receipt

    async def delete_event(self, event_id: str) -> None:
        self._events.pop(event_id, None)
