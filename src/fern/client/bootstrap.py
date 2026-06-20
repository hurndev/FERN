from __future__ import annotations

from collections.abc import Sequence

from fern.events.event import Event
from fern.events.validation import verify_event
from fern.storage.interfaces import EventStore
from fern.transport.interfaces import RelayTransport


async def fetch_genesis(group_pubkey: str, transports: Sequence[RelayTransport]) -> Event | None:
    for transport in transports:
        try:
            attestation = await transport.request_attestation(group_pubkey)
            for tip_id in attestation.tips:
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
) -> list[Event]:
    all_events: dict[str, Event] = {}
    for transport in transports:
        try:
            async for event in transport.sync(group_pubkey):
                eid = event.id
                if eid is not None and eid not in all_events:
                    verify_event(event)
                    all_events[eid] = event
                    await store.put_event(event)
        except Exception:
            continue
    return list(all_events.values())
