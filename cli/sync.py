from __future__ import annotations

from collections.abc import Sequence

from fern.client.sync import HealMode, SyncDiffResult, sync_diff
from fern.storage.interfaces import EventStore
from fern.transport.interfaces import RelayTransport


async def sync_group_from_transports(
    *,
    group_pubkey: str,
    store: EventStore,
    transports: Sequence[RelayTransport],
    client_id: str,
    heal_mode: HealMode = HealMode.AUTO,
    fast_heal_min_events: int = 3,
) -> list[SyncDiffResult]:
    """Synchronise a group cache from connected relays without waiting on held locks."""
    results: list[SyncDiffResult] = []
    for transport in transports:
        siblings = [t for t in transports if t is not transport]
        try:
            result = await sync_diff(
                transport=transport,
                group=group_pubkey,
                store=store,
                client_id=client_id,
                wait_on_lock=False,
                heal_mode=heal_mode,
                sibling_transports=siblings,
                fast_heal_min_events=fast_heal_min_events,
            )
            results.append(result)
        except Exception:
            continue
    return results
