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
    relays_agree: bool = True


def decide_sync_action(
    local_event_ids: set[str],
    local_latest_ts: int,
    relay_summaries: dict[str, dict],
) -> SyncDecision:
    """Decide sync strategy based on local DAG state and relay summaries.

    Compares relay tips (DAG frontier) against local events. Count is not used
    because relays store events that clients may reject (e.g. unauthorized
    events). If the client already knows all relay tips, there is nothing new
    to fetch.

    Args:
        local_event_ids: Set of all event IDs in the local DAG.
        local_latest_ts: Timestamp of the latest event in the local DAG.
        relay_summaries: Mapping of relay URL -> {"count": int, "tips": [str, ...]}.

    Returns:
        SyncDecision indicating what action to take.
    """
    if not local_event_ids:
        return SyncDecision(action="full", since=0)

    if not relay_summaries:
        return SyncDecision(
            action="incremental",
            since=max(0, local_latest_ts - CLOCK_SKEW_BUFFER),
        )

    all_relay_tips: set[str] = set()
    first_tips: set[str] | None = None
    relays_agree = True

    for s in relay_summaries.values():
        tips = set(s.get("tips", []))
        all_relay_tips.update(tips)
        if first_tips is None:
            first_tips = tips
        elif tips != first_tips:
            relays_agree = False

    if all_relay_tips.issubset(local_event_ids):
        return SyncDecision(action="skip", relays_agree=relays_agree)

    return SyncDecision(
        action="incremental",
        since=max(0, local_latest_ts - CLOCK_SKEW_BUFFER),
        relays_agree=relays_agree,
    )
