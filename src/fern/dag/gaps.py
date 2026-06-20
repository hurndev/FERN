from __future__ import annotations

from collections.abc import Iterable

from fern.events.event import Event


def find_missing_parents(events: Iterable[Event]) -> frozenset[str]:
    event_ids: set[str] = set()
    referenced: set[str] = set()
    for event in events:
        if event.id is not None:
            event_ids.add(event.id)
        for p in event.parents:
            if p:
                referenced.add(p)
    return frozenset(referenced - event_ids)
