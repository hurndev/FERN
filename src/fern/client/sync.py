from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fern.completeness.group_statuses import compute_set_hash, verify_group_status
from fern.events.event import Event
from fern.events.validation import verify_event
from fern.storage.interfaces import EventStore
from fern.transport.interfaces import RelayTransport, SyncLockResult


@dataclass(frozen=True)
class SyncDiffResult:
    fetched: int = 0
    healed: int = 0
    used_fallback: bool = False
    skipped_locked: bool = False


async def _full_sync(
    *, transport: RelayTransport, group: str, store: EventStore
) -> SyncDiffResult:
    fetched = 0
    async for event in transport.sync(group):
        eid = event.id
        if eid is None:
            continue
        try:
            verify_event(event)
        except Exception:
            continue
        if not await store.has_event(eid):
            fetched += 1
        await store.put_event(event)
    return SyncDiffResult(fetched=fetched, used_fallback=True)


async def _try_heal(transport: RelayTransport, event: Event) -> bool:
    try:
        await transport.heal(event)
        return True
    except (AttributeError, NotImplementedError):
        try:
            await transport.publish(event)
            return True
        except Exception:
            return False
    except Exception:
        return False


async def _heal_events(
    *, transport: RelayTransport, events: list[Event], batch_size: int
) -> int:
    healed = 0
    for i in range(0, len(events), batch_size):
        batch = events[i : i + batch_size]
        results = await asyncio.gather(
            *(_try_heal(transport, event) for event in batch),
            return_exceptions=False,
        )
        healed += sum(1 for ok in results if ok)
    return healed


async def _local_group_events(store: EventStore, group: str) -> list[Event]:
    events: list[Event] = []
    async for event in store.iter_group_events(group):
        events.append(event)
    return sorted(
        events,
        key=lambda event: (
            0 if event.type == "genesis" else 1,
            event.ts,
            event.id or "",
        ),
    )


async def sync_diff(
    *,
    transport: RelayTransport,
    group: str,
    store: EventStore,
    client_id: str,
    batch_size: int = 10,
    wait_on_lock: bool = False,
) -> SyncDiffResult:
    """Synchronise one relay using group_statuses, ID diffing, and advisory heal locks."""
    try:
        group_status = await transport.request_group_status(group)
        if not verify_group_status(group_status):
            return await _full_sync(transport=transport, group=group, store=store)
    except Exception as e:
        if "group not hosted" in str(e).lower():
            local_events = await _local_group_events(store, group)
            healed = await _heal_events(
                transport=transport, events=local_events, batch_size=batch_size
            )
            return SyncDiffResult(healed=healed)
        return await _full_sync(transport=transport, group=group, store=store)

    local_ids = await store.get_known_set(group)
    if group_status.set_hash == compute_set_hash(local_ids):
        return SyncDiffResult()

    lock_acquired = False
    try:
        try:
            lock_result = await transport.sync_lock(group, client_id)
        except (AttributeError, NotImplementedError):
            lock_result = SyncLockResult(granted=True)

        if not lock_result.granted:
            if not wait_on_lock:
                return SyncDiffResult(skipped_locked=True)

            await asyncio.sleep(lock_result.expires_in or 30)
            try:
                recheck = await transport.request_group_status(group)
            except Exception:
                return await _full_sync(transport=transport, group=group, store=store)
            local_ids = await store.get_known_set(group)
            if verify_group_status(recheck) and recheck.set_hash == compute_set_hash(local_ids):
                return SyncDiffResult()
            lock_result = await transport.sync_lock(group, client_id)
            if not lock_result.granted:
                return SyncDiffResult(skipped_locked=True)

        lock_acquired = True

        try:
            relay_ids = frozenset(await transport.sync_ids(group))
        except Exception as e:
            if "group not hosted" in str(e).lower():
                local_events = await _local_group_events(store, group)
                healed = await _heal_events(
                    transport=transport, events=local_events, batch_size=batch_size
                )
                return SyncDiffResult(healed=healed)
            return await _full_sync(transport=transport, group=group, store=store)

        local_ids = await store.get_known_set(group)
        missing_locally = sorted(relay_ids - local_ids)
        missing_on_relay = sorted(local_ids - relay_ids)

        fetched = 0
        for event_id in missing_locally:
            event = await transport.get(event_id)
            if event is None:
                continue
            try:
                verify_event(event)
            except Exception:
                continue
            if event.id is not None and not await store.has_event(event.id):
                fetched += 1
            await store.put_event(event)

        missing_on_relay_set = set(missing_on_relay)
        to_heal = [
            event
            for event in await _local_group_events(store, group)
            if event.id in missing_on_relay_set
        ]
        healed = await _heal_events(
            transport=transport, events=to_heal, batch_size=batch_size
        )

        return SyncDiffResult(fetched=fetched, healed=healed)
    finally:
        if lock_acquired:
            try:
                await transport.sync_unlock(group, client_id)
            except Exception:
                pass
