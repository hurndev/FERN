from __future__ import annotations

import asyncio
from collections.abc import Sequence

from fern.events.event import Event
from fern.completeness.receipts import Receipt
from fern.transport.interfaces import RelayTransport


async def publish_event(
    event: Event,
    transports: Sequence[RelayTransport],
    receipt_store: object = None,
    min_receipts: int = 2,
) -> tuple[Event, list[Receipt]]:
    receipts: list[Receipt] = []

    async def _publish_to(transport: RelayTransport) -> None:
        try:
            receipt = await transport.publish(event)
            receipts.append(receipt)
            if receipt_store is not None and event.id is not None:
                await receipt_store.put_receipt(event.id, transport.relay_pubkey, receipt)  # type: ignore[attr-defined]
        except Exception:
            pass

    tasks = [_publish_to(t) for t in transports]
    await asyncio.gather(*tasks, return_exceptions=True)

    return event, receipts
