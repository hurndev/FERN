from __future__ import annotations

from fern.events.event import Event
from fern.storage.interfaces import EventStore


class RelayStore:
    def __init__(self, event_store: EventStore, *, gc_threshold: int = 1000) -> None:
        self._store = event_store
        self.gc_threshold = gc_threshold

    @property
    def store(self) -> EventStore:
        return self._store

    async def ingest(self, event: Event) -> None:
        await self._store.put_event(event)

    async def should_gc(self, group: str) -> list[str]:
        count = await self._store.count_events(group)
        if count <= self.gc_threshold:
            return []

        tips = await self._store.get_tips(group)
        await self._store.get_parent_map(group)

        candidates = []
        for tip in tips:
            candidates.append(tip)

        return candidates
