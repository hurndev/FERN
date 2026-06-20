from __future__ import annotations

from collections.abc import Sequence

from fern.transport.interfaces import RelayTransport


async def subscribe_to_relays(
    group: str,
    transports: Sequence[RelayTransport],
) -> None:
    for transport in transports:
        try:
            await transport.subscribe(group)
        except Exception:
            pass


async def unsubscribe_from_relays(
    group: str,
    transports: Sequence[RelayTransport],
) -> None:
    for transport in transports:
        try:
            await transport.unsubscribe(group)
        except Exception:
            pass
