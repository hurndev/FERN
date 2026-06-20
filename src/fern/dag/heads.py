from __future__ import annotations

from collections.abc import Iterable

from fern.events.event import Event


def compute_heads(events: Iterable[Event]) -> frozenset[str]:
    event_ids: set[str] = set()
    referenced_ids: set[str] = set()
    for event in events:
        if event.id is not None:
            event_ids.add(event.id)
        for p in event.parents:
            if p:
                referenced_ids.add(p)
    heads = event_ids - referenced_ids
    return frozenset(heads)


def parent_to_children(events: Iterable[Event]) -> dict[str, frozenset[str]]:
    mapping: dict[str, set[str]] = {}
    for event in events:
        for p in event.parents:
            if p and event.id:
                if p not in mapping:
                    mapping[p] = set()
                mapping[p].add(event.id)
    return {k: frozenset(v) for k, v in mapping.items()}
