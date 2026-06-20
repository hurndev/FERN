from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Awaitable
from typing import Any

from fern.crypto.keys import Keypair
from fern.storage.interfaces import EventStore


async def attestation_loop(
    *,
    store: EventStore,
    group: str,
    relay_keypair: Keypair,
    last_attestations: dict[str, Any],
    broadcast_fn: Callable[[str, Any], Awaitable[None]] | None = None,
    interval_seconds: int = 5,
) -> None:
    from fern.completeness.attestations import build_attestation

    while True:
        await asyncio.sleep(interval_seconds)

        known_set = await store.get_known_set(group)
        tips = await store.get_tips(group)
        count = await store.count_events(group)

        prev = last_attestations.get(group)
        att = build_attestation(
            group=group,
            relay_keypair=relay_keypair,
            known_set=known_set,
            tips=tips,
            count=count,
            prev=prev,
            ts=int(time.time()),
        )
        last_attestations[group] = att

        if broadcast_fn is not None:
            await broadcast_fn(group, att)
