from __future__ import annotations

from collections.abc import Sequence

from fern.events.event import Event
from fern.transport.interfaces import RelayTransport


async def heal_missing(
    *,
    event_id: str,
    target_relay: RelayTransport,
    sibling_relays: Sequence[RelayTransport],
) -> Event | None:
    for sibling in sibling_relays:
        try:
            event = await sibling.get(event_id)
            if event is not None:
                await target_relay.heal(event)
                return event
        except Exception:
            continue
    return None
