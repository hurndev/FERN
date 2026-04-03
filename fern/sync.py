"""Shared sync decision logic for FERN clients.

Pure functions that decide sync strategy based on local DAG state and relay
summaries. No I/O — the CLI and Qt worker use these to decide what to do,
then execute the decision with their own connection machinery.

This module also contains the shared sync-and-heal implementation used by
both the CLI client and the Qt worker.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Callable

from .events import (
    Event,
    verify_event,
    verify_event_id,
    verify_event_signature,
    derive_group_state,
)

CLOCK_SKEW_BUFFER = 60


@dataclass
class SyncDecision:
    action: str
    since: int = 0
    relays_agree: bool = True


@dataclass
class SyncResult:
    hint_relays: list[str] = field(default_factory=list)
    canonical_relays: list[str] = field(default_factory=list)
    bad_relays: list[str] = field(default_factory=list)
    sync_rounds: int = 0
    total_events: int = 0
    new_events: int = 0
    invalid_events: int = 0
    healed_events: int = 0
    gaps: list[str] = field(default_factory=list)
    skipped: bool = False
    error: str | None = None


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


def validate_genesis(event: Event) -> tuple[bool, str]:
    """Validate a genesis event. Returns (valid, reason)."""
    if not event:
        return False, "no event"

    required = ["id", "type", "group", "author", "parents", "content", "ts", "sig"]
    for field_name in required:
        if field_name not in event:
            return False, f"missing field: {field_name}"

    if event["type"] != "group_genesis":
        return False, "not a genesis event"

    if event["parents"]:
        return False, "genesis must have empty parents"

    if not verify_event_id(event):
        return False, "event ID mismatch"

    if not verify_event_signature(event, event["group"]):
        return False, "invalid signature"

    return True, "ok"


async def fetch_and_validate_events(
    relay_urls: list[str],
    group_pubkey: str,
    since: int = 0,
    skip_genesis_validation: bool = False,
    on_log: Callable[[str], None] | None = None,
) -> tuple[dict[str, Event], dict[str, set[str]], list[str], int]:
    """Fetch events from multiple relays in parallel and validate them.

    Args:
        relay_urls: List of relay URLs to fetch from.
        group_pubkey: Group public key.
        since: Only fetch events with ts >= since (0 = full sync).
        skip_genesis_validation: If True, skip genesis validation (for incremental sync).
        on_log: Optional callback for log messages.

    Returns:
        - all_validated: dict mapping event_id -> event
        - relay_event_ids: dict mapping relay_url -> set of event_ids it has
        - good_relays: list of relays that returned valid genesis
        - invalid_count: number of invalid events discarded
    """
    from . import relay

    def log(msg: str):
        if on_log:
            on_log(msg)

    all_validated: dict[str, Event] = {}
    relay_event_ids: dict[str, set[str]] = {}
    good_relays: list[str] = []
    invalid_count = 0

    if skip_genesis_validation:
        good_relays = list(relay_urls)
        valid_genesis = None
    else:
        genesis_tasks = [relay.fetch_genesis(url, group_pubkey) for url in relay_urls]
        genesis_results = await asyncio.gather(*genesis_tasks)

        valid_genesis = None
        for url, genesis in zip(relay_urls, genesis_results):
            if genesis is None:
                log(f"    {url}: no genesis found")
                continue
            ok, reason = validate_genesis(genesis)
            if ok:
                if valid_genesis is None:
                    valid_genesis = genesis
                elif genesis["id"] != valid_genesis["id"]:
                    log(f"    {url}: DIFFERENT genesis - discarding")
                    continue
                good_relays.append(url)
            else:
                log(f"    {url}: genesis INVALID ({reason}) - discarding")

    if not good_relays:
        return {}, {}, [], 0

    fetch_tasks = [relay.fetch_events(url, group_pubkey, since) for url in good_relays]
    fetch_results = await asyncio.gather(*fetch_tasks)

    for url, events in zip(good_relays, fetch_results):
        ids = set()
        for event in events:
            ok, reason = verify_event(event)
            if not ok:
                invalid_count += 1
                eid = event.get("id", "?")[:16]
                log(f"    {url}: invalid {event.get('type', '?')} {eid}... ({reason})")
                continue
            ids.add(event["id"])
            if event["id"] not in all_validated:
                all_validated[event["id"]] = event
        relay_event_ids[url] = ids

    return all_validated, relay_event_ids, good_relays, invalid_count


async def run_sync_and_heal(
    dag,
    hint_relays: list[str],
    lock=None,
    on_log: Callable[[str], None] | None = None,
) -> SyncResult:
    """Full sync-and-heal cycle with relay discovery.

    The sync process handles group migration by discovering canonical relays
    through the event history itself, rather than trusting hint relays.

    Process:
      1. Check local state - if we have events, get latest timestamp and tips
      2. Query relay summaries to check if sync is even needed
      3. If summaries match local state, skip sync (already in sync)
      4. If not, do incremental or full sync depending on local state
      5. Heal any divergence across canonical relays

    Args:
        dag: EventDAG instance for the group.
        hint_relays: Initial relay hints to try.
        lock: Optional lock for thread-safe access.
        on_log: Optional callback for log messages.

    Returns:
        SyncResult with sync statistics.
    """
    from . import relay

    def log(msg: str):
        if on_log:
            on_log(msg)

    result = SyncResult(hint_relays=list(hint_relays))

    if not hint_relays:
        return result

    # local_events snapshot — these are the persistent working set for this sync
    local_events: dict[str, dict] = dict(dag.events)
    local_event_ids_snapshot = set(local_events.keys())
    local_count = len(local_events)

    local_latest_ts = 0
    if local_count > 0:
        all_local = sorted(local_events.values(), key=lambda e: (e["ts"], e["id"]))
        if all_local:
            local_latest_ts = all_local[-1]["ts"]

        log(f"  Local state: {local_count} events, latest ts={local_latest_ts}")
        log("  Fetching relay summaries to check if sync is needed...")

        summary_tasks = [
            relay.fetch_summary(url, dag.group_pubkey) for url in hint_relays
        ]
        summary_results = await asyncio.gather(*summary_tasks)

        relay_summaries: dict[str, dict] = {}
        for url, s in zip(hint_relays, summary_results):
            if isinstance(s, dict):
                relay_summaries[url] = s

        if relay_summaries:
            decision = decide_sync_action(
                set(local_events.keys()), local_latest_ts, relay_summaries
            )

            if decision.action == "skip":
                result.skipped = True
                state = derive_group_state(local_events.values())
                result.canonical_relays = (
                    state.relays if state.relays else list(hint_relays)
                )

                if lock:
                    with lock:
                        gaps = dag.get_missing_parents()
                else:
                    gaps = dag.get_missing_parents()
                result.gaps = sorted(gaps)

                log(
                    f"  [SKIP] All relay tips present in local DAG ({local_count} events)"
                )
                if gaps:
                    log(f"  WARNING: {len(gaps)} gap(s) detected")
                else:
                    log("  DAG complete - no gaps")
                return result

            log(f"  Relay has new tips not in local DAG - need {decision.action} sync")

    current_relays = list(hint_relays)
    all_validated: dict[str, dict] = dict(local_events)
    seen_relays: set[str] = set()
    all_relay_event_ids: dict[str, set[str]] = {}

    while current_relays:
        result.sync_rounds += 1
        round_num = result.sync_rounds

        log(f"  [Sync round {round_num}] Using relays: {', '.join(current_relays)}")

        seen_relays.update(current_relays)

        sync_since = local_latest_ts if local_latest_ts > 0 else 0
        skip_genesis = local_latest_ts > 0

        if sync_since > 0:
            log(f"    Incremental sync since ts={sync_since}")

        (
            events,
            relay_event_ids,
            good_relays,
            invalid_count,
        ) = await fetch_and_validate_events(
            current_relays,
            dag.group_pubkey,
            since=sync_since,
            skip_genesis_validation=skip_genesis,
            on_log=on_log,
        )

        result.invalid_events += invalid_count

        new_events = 0
        for eid, event in events.items():
            if eid not in all_validated:
                all_validated[eid] = event
                new_events += 1

        log(
            f"    Fetched {len(events)} events ({new_events} new, {invalid_count} invalid)"
        )

        bad_this_round = [r for r in current_relays if r not in good_relays]
        result.bad_relays.extend(bad_this_round)

        if not good_relays:
            log("    No working relays found.")
            break

        for url, ids in relay_event_ids.items():
            all_relay_event_ids[url] = ids

        state = derive_group_state(all_validated.values())
        derived_relays = state.relays if state.relays else []

        log(
            f"    Derived canonical relays: {derived_relays or '(none in group state)'}"
        )

        derived_set = frozenset(derived_relays)
        if derived_set == frozenset(current_relays) or not derived_relays:
            log("    Relay list stable - sync complete")
            break

        new_relays = [r for r in derived_relays if r not in seen_relays]
        if not new_relays:
            log("    All derived relays already synced - sync complete")
            break

        log("    Group migrated - switching to canonical relays")
        current_relays = derived_relays

    result.total_events = len(all_validated)

    state = derive_group_state(all_validated.values())
    canonical_relays = state.relays if state.relays else list(seen_relays)
    result.canonical_relays = canonical_relays

    log(f"\n  [Healing] Canonical relays: {', '.join(canonical_relays)}")

    canonical_event_ids: dict[str, set[str]] = {}
    for url in canonical_relays:
        if url in all_relay_event_ids:
            canonical_event_ids[url] = all_relay_event_ids[url]
        else:
            fetched = await relay.fetch_events(url, dag.group_pubkey, since=sync_since)
            ids = set()
            for event in fetched:
                ok, _ = verify_event(event)
                if ok:
                    ids.add(event["id"])
                    if event["id"] not in all_validated:
                        all_validated[event["id"]] = event
            canonical_event_ids[url] = ids

    events_to_heal = (
        set(all_validated.keys())
        if sync_since == 0
        else (set(all_validated.keys()) - local_event_ids_snapshot)
    )
    healed = 0

    log(f"    Healing {len(events_to_heal)} event(s)")

    for url in canonical_relays:
        relay_ids = canonical_event_ids.get(url, set())
        missing = events_to_heal - relay_ids
        if missing:
            log(f"    {url}: missing {len(missing)} event(s), pushing...")
            for event_id in missing:
                event = all_validated[event_id]
                res = await relay.publish(url, event)
                if res and res.get("type") == "ok":
                    healed += 1
        else:
            log(f"    {url}: up to date")

    result.healed_events = healed

    new_added = 0
    rejected = 0
    for event in sorted(all_validated.values(), key=lambda e: (e["ts"], e["id"])):
        if lock:
            with lock:
                ok, reason = dag.add_event(event, skip_verify=True)
        else:
            ok, reason = dag.add_event(event, skip_verify=True)
        if ok:
            new_added += 1
        elif reason != "duplicate":
            eid = event.get("id", "?")[:16]
            log(f"    [REJECTED] {event['type']} {eid}... ({reason})")
            rejected += 1

    result.new_events = new_added
    result.total_events = dag.count

    if lock:
        with lock:
            gaps = dag.get_missing_parents()
    else:
        gaps = dag.get_missing_parents()
    result.gaps = sorted(gaps)

    log(f"\n  Added {new_added} new events locally ({dag.count} total)")
    if rejected:
        log(f"  {rejected} rejected")
    if gaps:
        log(f"  WARNING: {len(gaps)} gap(s)")
        for g in sorted(gaps)[:5]:
            log(f"    {g[:16]}...")
        if len(gaps) > 5:
            log(f"    ... and {len(gaps) - 5} more")
    else:
        log("  DAG complete - no gaps")

    return result
