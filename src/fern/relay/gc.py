from __future__ import annotations

from fern.storage.interfaces import EventStore


async def garbage_collect(store: EventStore, group: str, threshold: int) -> int:
    count = await store.count_events(group)
    if count <= threshold:
        return 0

    tips = await store.get_tips(group)
    parent_map = await store.get_parent_map(group)

    all_events = []
    async for event in store.iter_group_events(group):
        all_events.append(event)

    event_ids = [e.id for e in all_events if e.id is not None]
    event_ids_sorted = sorted(event_ids)

    candidates: list[str] = []
    for tip_id in tips:
        if tip_id not in event_ids_sorted:
            continue

        tip_index = event_ids_sorted.index(tip_id)
        subsequent_count = len(event_ids_sorted) - tip_index - 1
        if subsequent_count < threshold:
            continue

        references_after = any(
            tip_id in (parent_map.get(e_id, frozenset()) or frozenset())
            for e_id in event_ids_sorted[tip_index + 1 : tip_index + 1 + threshold]
        )
        if not references_after:
            candidates.append(tip_id)

    for event_id in candidates:
        await store.delete_event(event_id)

    return len(candidates)
