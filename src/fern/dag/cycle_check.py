from __future__ import annotations

from collections.abc import Iterable

from fern.events.event import Event


def has_cycle(events: Iterable[Event]) -> bool:
    parent_map: dict[str, list[str]] = {}
    for event in events:
        if event.id is not None:
            parent_map[event.id] = list(p for p in event.parents if p)

    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)
        for parent in parent_map.get(node, []):
            if parent not in visited:
                if dfs(parent):
                    return True
            elif parent in rec_stack:
                return True
        rec_stack.discard(node)
        return False

    for n in parent_map:
        if n not in visited:
            if dfs(n):
                return True
    return False
