from __future__ import annotations

from collections.abc import Sequence

from fern.events.event import Event
from fern.events.validation import verify_event
from fern.client.sync import sync_diff
from fern.storage.interfaces import EventStore
from fern.transport.interfaces import RelayTransport


async def fetch_genesis(group_pubkey: str, transports: Sequence[RelayTransport]) -> Event | None:
    for transport in transports:
        try:
            group_status = await transport.request_group_status(group_pubkey)
            for tip_id in group_status.tips:
                event = await transport.get(tip_id)
                if event is not None:
                    while event.id is not None:
                        if event.type == "genesis":
                            verify_event(event)
                            return event
                        if not event.parents:
                            break
                        event = await transport.get(event.parents[0])
                        if event is None:
                            break
        except Exception:
            continue

    for transport in transports:
        try:
            async for event in transport.sync(group_pubkey):
                if event.type == "genesis" and not event.parents:
                    verify_event(event)
                    return event
        except Exception:
            continue

    return None


async def initial_sync(
    group_pubkey: str,
    transports: Sequence[RelayTransport],
    store: EventStore,
    *,
    client_id: str | None = None,
    wait_on_lock: bool = False,
) -> list[Event]:
    if client_id is not None:
        for transport in transports:
            try:
                await sync_diff(
                    transport=transport,
                    group=group_pubkey,
                    store=store,
                    client_id=client_id,
                    wait_on_lock=wait_on_lock,
                )
            except Exception:
                continue
    else:
        for transport in transports:
            try:
                async for event in transport.sync(group_pubkey):
                    eid = event.id
                    if eid is None:
                        continue
                    verify_event(event)
                    await store.put_event(event)
            except Exception:
                continue

    all_events: dict[str, Event] = {}
    async for event in store.iter_group_events(group_pubkey):
        if event.id is not None:
            all_events[event.id] = event
    return list(all_events.values())
