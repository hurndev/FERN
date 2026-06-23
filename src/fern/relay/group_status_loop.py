from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Awaitable
from typing import Any

from fern.crypto.keys import Keypair
from fern.storage.interfaces import EventStore


async def group_status_loop(
    *,
    store: EventStore,
    group: str,
    relay_keypair: Keypair,
    last_group_statuses: dict[str, Any],
    broadcast_fn: Callable[[str, Any], Awaitable[None]] | None = None,
    interval_seconds: int = 5,
) -> None:
    from fern.completeness.group_statuses import build_group_status

    while True:
        await asyncio.sleep(interval_seconds)

        known_set = await store.get_known_set(group)
        tips = await store.get_tips(group)
        count = await store.count_events(group)

        prev = last_group_statuses.get(group)
        att = build_group_status(
            group=group,
            relay_keypair=relay_keypair,
            known_set=known_set,
            tips=tips,
            count=count,
            prev=prev,
            ts=int(time.time()),
        )
        last_group_statuses[group] = att

        if broadcast_fn is not None:
            await broadcast_fn(group, att)
