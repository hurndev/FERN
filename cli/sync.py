from __future__ import annotations

from collections.abc import Sequence

from fern.client.sync import SyncDiffResult, sync_diff
from fern.storage.interfaces import EventStore
from fern.transport.interfaces import RelayTransport


async def sync_group_from_transports(
    *,
    group_pubkey: str,
    store: EventStore,
    transports: Sequence[RelayTransport],
    client_id: str,
) -> list[SyncDiffResult]:
    """Synchronise a group cache from connected relays without waiting on held locks."""
    results: list[SyncDiffResult] = []
    for transport in transports:
        try:
            result = await sync_diff(
                transport=transport,
                group=group_pubkey,
                store=store,
                client_id=client_id,
                wait_on_lock=False,
            )
            results.append(result)
        except Exception:
            continue
    return results
