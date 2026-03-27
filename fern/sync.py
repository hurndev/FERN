"""Shared sync decision logic for FERN clients.

Pure functions that decide sync strategy based on local DAG state and relay
summaries. No I/O — both the CLI and web chat use these to decide what to do,
then execute the decision with their own connection machinery.
"""

from dataclasses import dataclass

CLOCK_SKEW_BUFFER = 60


@dataclass
class SyncDecision:
    action: str  # "skip", "full", "incremental"
    since: int = 0
    relay_count: int = 0
    relays_agree: bool = True


def decide_sync_action(
    local_count: int,
    local_tips: set[str],
    local_latest_ts: int,
    relay_summaries: dict[str, dict],
) -> SyncDecision:
    """Decide sync strategy based on local state and relay summaries.

    Args:
        local_count: Number of events in the local DAG.
        local_tips: Set of tip event IDs in the local DAG.
        local_latest_ts: Timestamp of the latest event in the local DAG.
        relay_summaries: Mapping of relay URL -> {"count": int, "tips": [str, ...]}.

    Returns:
        SyncDecision indicating what action to take.
    """
    if local_count == 0:
        return SyncDecision(action="full", since=0)

    if not relay_summaries:
        return SyncDecision(
            action="incremental",
            since=max(0, local_latest_ts - CLOCK_SKEW_BUFFER),
        )

    first_count = None
    first_tips = None
    relays_agree = True

    for s in relay_summaries.values():
        count = s.get("count", 0)
        tips = set(s.get("tips", []))
        if first_count is None:
            first_count = count
            first_tips = tips
        elif count != first_count or tips != first_tips:
            relays_agree = False
            break

    if not relays_agree:
        return SyncDecision(
            action="incremental",
            since=max(0, local_latest_ts - CLOCK_SKEW_BUFFER),
            relay_count=first_count or 0,
            relays_agree=False,
        )

    if first_count is not None:
        if first_count == local_count and first_tips == local_tips:
            return SyncDecision(
                action="skip",
                relay_count=first_count,
            )

        if first_count <= local_count:
            return SyncDecision(
                action="skip",
                relay_count=first_count,
            )

    return SyncDecision(
        action="incremental",
        since=max(0, local_latest_ts - CLOCK_SKEW_BUFFER),
        relay_count=first_count or 0,
    )
