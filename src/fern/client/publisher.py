from __future__ import annotations

import asyncio
from collections.abc import Sequence

from fern.events.event import Event
from fern.completeness.event_receipts import EventReceipt
from fern.transport.interfaces import RelayTransport


async def publish_event(
    event: Event,
    transports: Sequence[RelayTransport],
    event_receipt_store: object = None,
    min_event_receipts: int = 2,
) -> tuple[Event, list[EventReceipt]]:
    event_receipts: list[EventReceipt] = []

    async def _publish_to(transport: RelayTransport) -> None:
        try:
            event_receipt = await transport.publish(event)
            event_receipts.append(event_receipt)
            if event_receipt_store is not None and event.id is not None:
                await event_receipt_store.put_event_receipt(event.id, transport.relay_pubkey, event_receipt)  # type: ignore[attr-defined]
        except Exception:
            pass

    tasks = [_publish_to(t) for t in transports]
    await asyncio.gather(*tasks, return_exceptions=True)

    return event, event_receipts
