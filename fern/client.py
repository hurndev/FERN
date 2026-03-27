"""FERN CLI Client - Command-line interface for the Fault-tolerant Event Relay Network."""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import click
import websockets

from . import crypto
from .events import (
    create_group_genesis,
    create_message,
    create_group_invite,
    create_group_join,
    create_group_leave,
    create_group_kick,
    create_mod_add,
    create_mod_remove,
    create_relay_update,
    verify_event,
    verify_event_id,
    verify_event_signature,
)
from .dag import ClientStorage, EventDAG
from .storage import resolve_fern_dir


BOOTSTRAP_RELAYS = ["ws://localhost:8787", "ws://localhost:8788"]

_fern_home: str | None = None


def get_storage() -> ClientStorage:
    return ClientStorage(str(resolve_fern_dir(_fern_home)))


def get_canonical_relays(
    dag: EventDAG, fallback_relays: list[str] | None = None
) -> list[str]:
    """Get canonical relay list from group state, with fallback."""
    state = dag.get_state()
    if state.relays:
        return list(state.relays)
    if fallback_relays:
        return list(fallback_relays)
    env_relay = os.environ.get("FERN_RELAY")
    if env_relay:
        return [env_relay]
    return list(BOOTSTRAP_RELAYS)


async def publish_to_relays(event: dict, relay_urls: list[str]) -> dict:
    """Publish an event to multiple relays in parallel. Returns {url: response}."""
    results = {}

    async def pub_one(url: str) -> None:
        try:
            async with websockets.connect(url) as ws:
                await ws.send(json.dumps({"action": "publish", "event": event}))
                results[url] = json.loads(await ws.recv())
        except Exception as e:
            results[url] = {"type": "error", "message": str(e)}

    await asyncio.gather(*(pub_one(url) for url in relay_urls))
    return results


async def fetch_genesis(relay_url: str, group_pubkey: str) -> dict | None:
    """Fetch the genesis event from a relay. Returns event or None."""
    try:
        async with asyncio.timeout(1.5):
            async with websockets.connect(relay_url) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "action": "get_genesis",
                            "group": group_pubkey,
                        }
                    )
                )
                msg = json.loads(await ws.recv())
                if msg["type"] == "event":
                    return msg["event"]
                elif msg["type"] == "not_found":
                    return None
    except asyncio.TimeoutError:
        click.echo(f"    {relay_url}: timeout fetching genesis", err=True)
    except Exception:
        pass
    return None


async def fetch_summary_from_relay(relay_url: str, group_pubkey: str) -> dict | None:
    """Fetch a summary (event count and tips) from a relay. Returns summary or None."""
    try:
        async with asyncio.timeout(1.5):
            async with websockets.connect(relay_url) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "action": "summary",
                            "group": group_pubkey,
                        }
                    )
                )
                msg = json.loads(await ws.recv())
                if msg.get("type") == "summary":
                    return msg
    except asyncio.TimeoutError:
        click.echo(f"    {relay_url}: timeout fetching summary", err=True)
    except Exception:
        pass
    return None


def validate_genesis(event: dict) -> tuple[bool, str]:
    """Validate a genesis event. Returns (valid, reason)."""
    if not event:
        return False, "no event"

    # Check required fields
    for field in [
        "id",
        "type",
        "group",
        "author",
        "parents",
        "content",
        "ts",
        "sig",
    ]:
        if field not in event:
            return False, f"missing field: {field}"

    if event["type"] != "group_genesis":
        return False, "not a genesis event"

    if event["parents"]:
        return False, "genesis must have empty parents"

    # Verify ID matches canonical serialisation
    if not verify_event_id(event):
        return False, "event ID mismatch"

    # Verify signature against group pubkey
    if not verify_event_signature(event, event["group"]):
        return False, "invalid signature"

    return True, "ok"


async def fetch_events_from_relay(
    relay_url: str, group_pubkey: str, since: int = 0
) -> list[dict]:
    """Fetch events from a relay since a timestamp. Returns list of raw events."""
    events = []
    try:
        async with websockets.connect(relay_url) as ws:
            await ws.send(
                json.dumps(
                    {
                        "action": "sync",
                        "group": group_pubkey,
                        "since": since,
                    }
                )
            )
            async for raw in ws:
                msg = json.loads(raw)
                if msg["type"] == "event":
                    events.append(msg["event"])
                elif msg["type"] == "sync_complete":
                    break
    except Exception as e:
        click.echo(f"  Error fetching from {relay_url}: {e}", err=True)
    return events


async def fetch_and_validate_events(
    relay_urls: list[str],
    group_pubkey: str,
    since: int = 0,
    skip_genesis_validation: bool = False,
) -> tuple[dict[str, dict], dict[str, set[str]], list[str], int]:
    """Fetch events from multiple relays in parallel and validate them.

    Args:
        relay_urls: List of relay URLs to fetch from
        group_pubkey: Group public key
        since: Only fetch events with ts >= since (0 = full sync)
        skip_genesis_validation: If True, skip genesis validation (for incremental sync)

    Returns:
        - all_validated: dict mapping event_id -> event
        - relay_event_ids: dict mapping relay_url -> set of event_ids it has
        - good_relays: list of relays that returned valid genesis
        - invalid_count: number of invalid events discarded
    """
    all_validated: dict[str, dict] = {}
    relay_event_ids: dict[str, set[str]] = {}
    good_relays: list[str] = []
    invalid_count = 0

    # Fetch genesis from all relays to validate them (unless skipped)
    if skip_genesis_validation:
        # For incremental sync, assume local genesis is valid and all relays are good
        good_relays = list(relay_urls)
        valid_genesis = None
    else:
        genesis_tasks = [fetch_genesis(url, group_pubkey) for url in relay_urls]
        genesis_results = await asyncio.gather(*genesis_tasks)

        valid_genesis = None
        for url, genesis in zip(relay_urls, genesis_results):
            if genesis is None:
                click.echo(f"    {url}: no genesis found")
                continue
            ok, reason = validate_genesis(genesis)
            if ok:
                if valid_genesis is None:
                    valid_genesis = genesis
                elif genesis["id"] != valid_genesis["id"]:
                    click.echo(f"    {url}: DIFFERENT genesis - discarding", err=True)
                    continue
                good_relays.append(url)
            else:
                click.echo(
                    f"    {url}: genesis INVALID ({reason}) - discarding", err=True
                )

    if not good_relays:
        return {}, {}, [], 0

    # Fetch events from good relays (using since parameter for incremental sync)
    fetch_tasks = [
        fetch_events_from_relay(url, group_pubkey, since) for url in good_relays
    ]
    fetch_results = await asyncio.gather(*fetch_tasks)

    for url, events in zip(good_relays, fetch_results):
        ids = set()
        for event in events:
            ok, reason = verify_event(event)
            if not ok:
                invalid_count += 1
                continue
            ids.add(event["id"])
            if event["id"] not in all_validated:
                all_validated[event["id"]] = event
        relay_event_ids[url] = ids

    return all_validated, relay_event_ids, good_relays, invalid_count


async def sync_and_heal(dag: EventDAG, hint_relays: list[str]) -> dict:
    """Full sync-and-heal cycle with relay discovery.

    The sync process handles group migration by discovering canonical relays
    through the event history itself, rather than trusting hint relays.

    Process:
      1. Check local state - if we have events, get latest timestamp and tips
      2. Query relay summaries to check if sync is even needed
      3. If summaries match local state, skip sync (already in sync)
      4. If not, do incremental or full sync depending on local state
      5. Heal any divergence across canonical relays

    Returns summary dict with sync statistics.
    """
    summary: dict[str, Any] = {
        "hint_relays": list(hint_relays),
        "canonical_relays": [],
        "bad_relays": [],
        "sync_rounds": 0,
        "total_events": 0,
        "invalid_events": 0,
        "healed_events": 0,
        "gaps": [],
        "skipped": False,
    }

    if not hint_relays:
        return summary

    # =========================================================================
    # PHASE 0: CHECK IF SYNC IS NEEDED (incremental sync optimization)
    # =========================================================================
    # If we have local events, check relay summaries first to avoid
    # unnecessary full history downloads.
    # =========================================================================

    local_latest_ts = 0
    local_tips: set[str] = set()
    local_count = dag.count

    if local_count > 0:
        all_events = dag.get_all_events()
        if all_events:
            local_latest_ts = all_events[-1]["ts"]
        local_tips = set(dag.get_tips())

        click.echo(f"  Local state: {local_count} events, latest ts={local_latest_ts}")
        click.echo(f"  Fetching relay summaries to check if sync is needed...")

        # Fetch summaries from all hint relays
        summary_tasks = [
            fetch_summary_from_relay(url, dag.group_pubkey) for url in hint_relays
        ]
        summary_results = await asyncio.gather(*summary_tasks)

        relay_summaries: dict[str, dict] = {}
        for url, s in zip(hint_relays, summary_results):
            if s is not None:
                relay_summaries[url] = s

        if relay_summaries:
            # Check if all relays agree and match local state
            all_match = True
            first_count = None
            first_tips = None

            for url, s in relay_summaries.items():
                count = s.get("count", 0)
                tips = set(s.get("tips", []))

                if first_count is None:
                    first_count = count
                    first_tips = tips
                elif count != first_count or tips != first_tips:
                    # Relays disagree with each other - need full sync
                    all_match = False
                    break

            if all_match and first_count is not None:
                if first_count == local_count and first_tips == local_tips:
                    # Local state matches all relays - we're in sync!
                    summary["skipped"] = True
                    click.echo(
                        f"  [SKIP] Local state matches relay summaries "
                        f"({local_count} events, {len(local_tips)} tips)"
                    )

                    # Still need to update canonical relays from group state
                    state = dag.get_state()
                    summary["canonical_relays"] = (
                        state.relays if state.relays else list(hint_relays)
                    )

                    # Check for gaps
                    gaps = dag.get_missing_parents()
                    summary["gaps"] = sorted(gaps)
                    if gaps:
                        click.echo(f"  WARNING: {len(gaps)} gap(s) detected")
                    else:
                        click.echo("  DAG complete - no gaps")

                    return summary
                elif first_count <= local_count:
                    # Relays have same or fewer events than local - our local state
                    # might be ahead or we have extra events (gap healing needed)
                    click.echo(
                        f"  Local has {local_count} events, relays have {first_count} - "
                        f"checking if healing needed"
                    )
                else:
                    # Relays have more events - need incremental sync
                    click.echo(
                        f"  Relay has {first_count} events (local has {local_count}) - "
                        f"need incremental sync"
                    )

    # =========================================================================
    # PHASE 1: SYNC WITH RELAY DISCOVERY
    # =========================================================================
    # We sync from relays, then check if the group has migrated to different
    # relays. If so, we switch to the new relays and continue. This repeats
    # until the relay list stabilises (derived relays match used relays).
    # =========================================================================

    current_relays = list(hint_relays)
    all_validated: dict[str, dict] = {}
    seen_relays: set[str] = set()
    event_ids_before_sync = set(dag.events.keys())

    while current_relays:
        summary["sync_rounds"] += 1
        round_num = summary["sync_rounds"]

        click.echo(
            f"  [Sync round {round_num}] Using relays: {', '.join(current_relays)}"
        )

        # Track which relays we've already used (to detect stability)
        used_this_round = frozenset(current_relays)
        seen_relays.update(current_relays)

        # Determine sync mode: incremental if we have local events, full otherwise
        sync_since = local_latest_ts if local_latest_ts > 0 else 0
        skip_genesis = local_latest_ts > 0

        if sync_since > 0:
            click.echo(f"    Incremental sync since ts={sync_since}")

        # Fetch and validate events from current relays
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
        )

        summary["invalid_events"] += invalid_count

        # Merge new events into our validated set
        new_events = 0
        for eid, event in events.items():
            if eid not in all_validated:
                all_validated[eid] = event
                new_events += 1

        click.echo(
            f"    Fetched {len(events)} events ({new_events} new, {invalid_count} invalid)"
        )

        # Track bad relays
        bad_this_round = [r for r in current_relays if r not in good_relays]
        summary["bad_relays"].extend(bad_this_round)

        if not good_relays:
            click.echo("    No working relays found.", err=True)
            break

        # Store events in DAG temporarily to derive group state
        # (We'll re-store them properly after healing)
        for event in all_validated.values():
            dag.add_event(event, skip_verify=True, skip_save=True, skip_auth=True)

        # Derive canonical relays from group state
        state = dag.get_state()
        derived_relays = state.relays if state.relays else []

        click.echo(
            f"    Derived canonical relays: {derived_relays or '(none in group state)'}"
        )

        # Check if relay list has stabilised
        derived_set = frozenset(derived_relays)
        if derived_set == used_this_round or not derived_relays:
            # Relays match what we just used, or no relays defined in group
            click.echo("    Relay list stable - sync complete")
            break

        # Group has migrated - switch to new canonical relays
        new_relays = [r for r in derived_relays if r not in seen_relays]
        if not new_relays:
            # All derived relays already used, we're stable
            click.echo("    All derived relays already synced - sync complete")
            break

        click.echo(f"    Group migrated - switching to canonical relays")
        current_relays = derived_relays

        # Clear DAG for re-derivation with new events
        dag.events.clear()
        dag.children.clear()

    summary["total_events"] = len(all_validated)

    # Compute events received during this sync (for incremental sync healing)
    new_event_ids = set(all_validated.keys()) - event_ids_before_sync

    # =========================================================================
    # PHASE 2: HEAL CANONICAL RELAYS
    # =========================================================================
    # Now that we have the full history and know the canonical relays,
    # we heal any divergence between them by cross-referencing event sets.
    # =========================================================================

    # Merge local events into all_validated BEFORE healing
    # (local events are in dag.events but may not be in all_validated yet)
    for eid, event in dag.events.items():
        if eid not in all_validated:
            all_validated[eid] = event

    # Determine final canonical relays from group state
    state = dag.get_state()
    canonical_relays = state.relays if state.relays else list(seen_relays)
    summary["canonical_relays"] = canonical_relays

    click.echo(f"\n  [Healing] Canonical relays: {', '.join(canonical_relays)}")

    # Fetch event IDs from all canonical relays to compare
    # Use same `since` parameter for consistency - we only need to heal recent events
    canonical_event_ids: dict[str, set[str]] = {}
    for url in canonical_relays:
        if url in relay_event_ids:
            canonical_event_ids[url] = relay_event_ids[url]
        else:
            # Fetch from this relay if we haven't already (with same since parameter)
            events = await fetch_events_from_relay(
                url, dag.group_pubkey, since=sync_since
            )
            ids = set()
            for event in events:
                ok, _ = verify_event(event)
                if ok:
                    ids.add(event["id"])
                    if event["id"] not in all_validated:
                        all_validated[event["id"]] = event
            canonical_event_ids[url] = ids

    # Heal: push missing events to relays that don't have them
    # For full sync: heal all events (we have complete picture from all relays)
    # For incremental sync: only heal the new events we received in this sync
    events_to_heal = set(all_validated.keys()) if sync_since == 0 else new_event_ids
    healed = 0

    click.echo(f"    Healing {len(events_to_heal)} event(s)")

    for url in canonical_relays:
        relay_ids = canonical_event_ids.get(url, set())
        missing = events_to_heal - relay_ids
        if missing:
            click.echo(f"    {url}: missing {len(missing)} event(s), pushing...")
            for event_id in missing:
                event = all_validated[event_id]
                results = await publish_to_relays(event, [url])
                if results.get(url, {}).get("type") == "ok":
                    healed += 1
        else:
            click.echo(f"    {url}: up to date")

    summary["healed_events"] = healed

    # =========================================================================
    # PHASE 3: FINALISE LOCAL STORAGE
    # =========================================================================

    # Clear and re-store all events in proper order
    dag.events.clear()
    dag.children.clear()
    dag._rebuild_children()

    stored = 0
    for event in sorted(all_validated.values(), key=lambda e: (e["ts"], e["id"])):
        ok, _ = dag.add_event(event, skip_verify=True, skip_save=True)
        if ok:
            stored += 1

    dag._save()

    click.echo(f"\n  Stored {stored} events locally")

    # Check for gaps in the DAG
    gaps = dag.get_missing_parents()
    summary["gaps"] = sorted(gaps)
    if gaps:
        click.echo(f"  WARNING: {len(gaps)} gap(s) - missing parent events:")
        for g in sorted(gaps)[:5]:
            click.echo(f"    {g[:16]}...")
        if len(gaps) > 5:
            click.echo(f"    ... and {len(gaps) - 5} more")
    else:
        click.echo("  DAG complete - no gaps")

    return summary


async def subscribe_group(dag: EventDAG, relay_urls: list[str], callback=None) -> None:
    """Subscribe to a group on all relays simultaneously and process incoming events."""

    async def sub_one(relay_url: str) -> None:
        while True:
            try:
                async with websockets.connect(relay_url) as ws:
                    click.echo(f"  {relay_url}: connected")
                    await ws.send(
                        json.dumps({"action": "subscribe", "group": dag.group_pubkey})
                    )
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg["type"] == "event":
                            ok, reason = dag.add_event(msg["event"])
                            if callback:
                                callback(msg["event"], ok, reason)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                click.echo(f"  {relay_url}: error: {e}", err=True)
                click.echo(f"  {relay_url}: retrying in 60s...")
            await asyncio.sleep(60)

    await asyncio.gather(*[sub_one(url) for url in relay_urls])


def format_event(event: dict) -> str:
    """Format an event for display."""
    etype = event["type"]
    author = event["author"][:12]
    ts = event["ts"]

    if etype == "message":
        content = event["content"]
        return f"[{ts}] {author}: {content}"
    elif etype == "group_genesis":
        name = event["content"].get("name", "?")
        return f"[{ts}] *** Group created: {name} ***"
    elif etype == "group_invite":
        invitee = event["content"]["invitee"][:12]
        return f"[{ts}] *** {author} invited {invitee} ***"
    elif etype == "group_join":
        return f"[{ts}] *** {author} joined ***"
    elif etype == "group_leave":
        return f"[{ts}] *** {author} left ***"
    elif etype == "group_kick":
        target = event["content"]["target"][:12]
        return f"[{ts}] *** {author} kicked {target} ***"
    elif etype == "mod_add":
        target = event["content"]["target"][:12]
        return f"[{ts}] *** {author} promoted {target} to mod ***"
    elif etype == "mod_remove":
        target = event["content"]["target"][:12]
        return f"[{ts}] *** {author} demoted {target} from mod ***"
    elif etype == "relay_update":
        relays = event["content"].get("relays", [])
        return f"[{ts}] *** {author} updated relays: {', '.join(relays)} ***"
    elif etype == "group_metadata":
        return f"[{ts}] *** {author} updated group metadata ***"
    else:
        return f"[{ts}] {author}: [{etype}] {json.dumps(event['content'])}"


def print_heal_summary(summary: dict) -> None:
    """Print a brief summary after sync-and-heal."""
    if summary.get("bad_relays"):
        click.echo(f"  Discarded {len(summary['bad_relays'])} unreliable relay(s).")
    if summary.get("canonical_relays"):
        click.echo(f"  Canonical relays: {', '.join(summary['canonical_relays'])}")


# --- CLI ---


@click.group()
@click.option("--home", help="Home directory containing .fern folder")
@click.pass_context
def cli(ctx, home):
    """FERN - Fault-tolerant Event Relay Network CLI client.

    Uses ~/.fern by default. Set FERN_TEST_USER to use /tmp/<user>/.fern
    instead. Use --home to specify a custom home directory.
    """
    global _fern_home
    _fern_home = home
    ctx.ensure_object(dict)


@cli.command()
def keygen():
    """Generate a new Ed25519 keypair for user identity."""
    storage = get_storage()
    privkey, pubkey = crypto.generate_keypair()
    path = storage.get_user_key_path()

    if os.path.exists(path):
        if not click.confirm(f"Key already exists at {path}. Overwrite?"):
            return

    crypto.save_keypair(privkey, path)
    click.echo(f"Generated new identity:")
    click.echo(f"  Public key: {pubkey}")
    click.echo(f"  Private key saved to: {path}")


@cli.command()
def profile():
    """Display your public key."""
    storage = get_storage()
    path = storage.get_user_key_path()

    if not os.path.exists(path):
        click.echo("No keypair found. Run 'fern keygen' first.", err=True)
        return

    privkey = crypto.load_private_key(path)
    pubkey = crypto.public_key_from_private(privkey)
    click.echo(f"Public key: {pubkey}")


@cli.command()
@click.option("--name", prompt="Group name", help="Group name")
@click.option("--description", default="", help="Group description")
@click.option("--public/--private", default=True, help="Public or private group")
@click.option(
    "--relays",
    default=None,
    help="Canonical relays as comma-separated list (e.g. ws://localhost:8787,ws://localhost:8788). If not provided, prompted.",
)
@click.option("--group-key-name", default="default", help="Name for group key file")
def create_group(
    name: str,
    description: str,
    public: bool,
    relays: str | None,
    group_key_name: str,
):
    """Create a new group and publish genesis to relay.

    Prompts for canonical relays, defaulting to bootstrap relays (localhost:8787, localhost:8788).
    Use --relays to specify relays without prompting (e.g. --relays ws://relay1:8787,ws://relay2:8788).
    """
    storage = get_storage()

    # Load user key
    user_key_path = storage.get_user_key_path()
    if not os.path.exists(user_key_path):
        click.echo("No user key found. Run 'fern keygen' first.", err=True)
        sys.exit(1)

    user_privkey = crypto.load_private_key(user_key_path)
    user_pubkey = crypto.public_key_from_private(user_privkey)

    # Generate group keypair
    group_privkey, group_pubkey = crypto.generate_keypair()

    # Save group key
    storage.save_group_key(group_privkey, group_key_name)

    # Determine relays
    if relays:
        relay_list = [r.strip() for r in relays.split(",") if r.strip()]
    else:
        default_relays = ",".join(BOOTSTRAP_RELAYS)
        relay_str = click.prompt(
            f"Canonical relays",
            default=default_relays,
            show_default=True,
        )
        relay_list = [r.strip() for r in relay_str.split(",") if r.strip()]

    # Create genesis event
    genesis = create_group_genesis(
        group_privkey=group_privkey,
        founder_pubkey=user_pubkey,
        name=name,
        description=description,
        public=public,
        relays=relay_list,
    )

    # Store locally
    dag = storage.get_group_dag(group_pubkey)
    dag.add_event(genesis)

    # Publish to relays
    click.echo(f"Publishing genesis to {len(relay_list)} relay(s)...")
    results = asyncio.run(publish_to_relays(genesis, relay_list))

    for url, result in results.items():
        if result.get("type") == "ok":
            click.echo(f"  {url}: OK")
        else:
            click.echo(f"  {url}: {result.get('message', 'unknown error')}", err=True)

    click.echo(f"\nGroup created!")
    click.echo(f"  Name: {name}")
    click.echo(f"  Group pubkey: {group_pubkey}")
    click.echo(f"  Address: {group_pubkey}@{','.join(relay_list)}")
    click.echo(f"  Relays: {', '.join(relay_list)}")
    click.echo(f"  Group key saved to: {storage.get_group_key_path(group_key_name)}")


@cli.command()
@click.argument("group_pubkey")
@click.option(
    "--relay",
    default=None,
    help="Extra relay to sync and publish to (in addition to canonical)",
)
@click.option(
    "--force-relay", default=None, help="Only use this relay, ignore canonical relays"
)
@click.option(
    "--no-sync", is_flag=True, help="Skip syncing/healing, just publish directly"
)
@click.option("--message", "-m", prompt="Message", help="Message to send")
def send(
    group_pubkey: str,
    relay: str | None,
    force_relay: str | None,
    no_sync: bool,
    message: str,
):
    """Send a message to a group. Use --relay to add an extra relay, --force-relay to use only that relay."""
    if "@" in group_pubkey:
        group_pubkey = group_pubkey.split("@", 1)[0]
    storage = get_storage()

    # Load user key
    user_key_path = storage.get_user_key_path()
    if not os.path.exists(user_key_path):
        click.echo("No user key found. Run 'fern keygen' first.", err=True)
        sys.exit(1)

    user_privkey = crypto.load_private_key(user_key_path)
    user_pubkey = crypto.public_key_from_private(user_privkey)

    # Get local DAG
    dag = storage.get_group_dag(group_pubkey)

    # Determine relay list
    if force_relay:
        relays = [force_relay]
    else:
        fallback = [relay] if relay else BOOTSTRAP_RELAYS
        relays = get_canonical_relays(dag, fallback)
        if relay and relay not in relays:
            relays.append(relay)

    # Sync and heal (unless --no-sync)
    if no_sync:
        if force_relay:
            relays = [force_relay]
        elif relay:
            relays = get_canonical_relays(dag)
            if relay not in relays:
                relays.append(relay)
    else:
        click.echo(f"Syncing from {len(relays)} relay(s): {', '.join(relays)}")
        summary = asyncio.run(sync_and_heal(dag, relays))
        print_heal_summary(summary)

        if not force_relay:
            relays = get_canonical_relays(dag)

    # Get parent events (current tips)
    tips = dag.get_tips()
    if not tips:
        click.echo(
            "No events found for this group. Is the group pubkey correct?", err=True
        )
        sys.exit(1)

    # Check if user is joined
    state = dag.get_state()
    if not state.can_post(user_pubkey):
        click.echo(
            "Error: You must join the group before posting. Use 'fern join' first.",
            err=True,
        )
        sys.exit(1)

    # Create message
    event = create_message(
        group_hex=group_pubkey,
        author_hex=user_pubkey,
        author_privkey=user_privkey,
        content=message,
        parents=tips,
    )

    # Publish to relays FIRST - don't add to local DAG until relay accepts
    click.echo(f"Publishing to {len(relays)} relay(s): {', '.join(relays)}")
    results = asyncio.run(publish_to_relays(event, relays))

    success = 0
    accepted_relays = []
    for url, result in results.items():
        if result.get("type") == "ok":
            success += 1
            accepted_relays.append(url)
        else:
            click.echo(f"  {url}: {result.get('message', 'error')}", err=True)

    if accepted_relays:
        dag.add_event(event)
        click.echo(f"Sent ({success}/{len(relays)} relays).")
    else:
        click.echo(
            "Failed to send to any relay. Please try again when a relay is available.",
            err=True,
        )


@cli.command()
@click.argument("group_pubkey")
@click.option(
    "--relay", default=None, help="Relay URL (used for bootstrap if no local state)"
)
@click.option("--limit", default=50, help="Max messages to show")
def messages(group_pubkey: str, relay: str | None, limit: int):
    """Show messages in a group."""
    storage = get_storage()
    dag = storage.get_group_dag(group_pubkey)
    fallback = [relay] if relay else BOOTSTRAP_RELAYS

    # Sync and heal
    relays = get_canonical_relays(dag, fallback)
    summary = asyncio.run(sync_and_heal(dag, relays))
    print_heal_summary(summary)

    events = dag.get_all_events()
    for event in events[-limit:]:
        click.echo(format_event(event))

    # Show gaps
    gaps = dag.get_missing_parents()
    if gaps:
        click.echo(f"\n[Warning: {len(gaps)} missing parent event(s)]")


@cli.command()
@click.argument("group_pubkey")
@click.option("--relay", default=None, help="Relay URL")
def subscribe(group_pubkey: str, relay: str | None):
    """Subscribe to a group and display messages in real-time."""
    storage = get_storage()
    dag = storage.get_group_dag(group_pubkey)
    fallback = [relay] if relay else BOOTSTRAP_RELAYS

    # Sync and heal first
    relays = get_canonical_relays(dag, fallback)
    click.echo(f"Syncing from {len(relays)} relay(s)...")
    summary = asyncio.run(sync_and_heal(dag, relays))
    print_heal_summary(summary)

    # Subscribe on all canonical relays simultaneously
    sub_relays = [relay] if relay else relays
    click.echo(f"Subscribing to {group_pubkey[:12]}... on {len(sub_relays)} relay(s)")
    click.echo("Press Ctrl+C to stop.\n")

    def on_event(event, ok, reason):
        if ok:
            click.echo(format_event(event))
        elif reason == "duplicate":
            pass  # Already seen - normal during catch-up, not an error
        else:
            eid = event.get("id", "?")[:16]
            click.echo(f"[INVALID EVENT: {reason}] {eid}...")

    try:
        asyncio.run(subscribe_group(dag, sub_relays, callback=on_event))
    except KeyboardInterrupt:
        click.echo("\nUnsubscribed.")


@cli.command()
@click.argument("address")
def join(address: str):
    """Join a group. Optionally provide relay hints via pubkey@relay1,relay2.

    If no @relays suffix is provided, uses bootstrap relays and prints a warning.
    You must join before you can post messages.
    """
    # Parse address for relay hints
    if "@" in address:
        group_pubkey, relay_hints = address.split("@", 1)
        relay_urls = [
            f"ws://{r}" if not r.startswith("ws") else r for r in relay_hints.split(",")
        ]
    else:
        group_pubkey = address
        relay_urls = None

    storage = get_storage()
    dag = storage.get_group_dag(group_pubkey)

    if relay_urls:
        click.echo(f"Joining group {group_pubkey[:12]}...")
        click.echo(f"Relays: {', '.join(relay_urls)}")
    else:
        click.echo("No relay hints provided, using bootstrap relays.")
        relay_urls = list(BOOTSTRAP_RELAYS)

    # Full sync and heal (sync_and_heal handles relay discovery)
    summary = asyncio.run(sync_and_heal(dag, relay_urls))
    print_heal_summary(summary)

    state = dag.get_state()
    name = state.metadata.get("name", "unnamed")
    click.echo(f"Synced: {name} ({dag.count} events)")

    if state.relays:
        click.echo(f"Canonical relays: {', '.join(state.relays)}")

    user_privkey = crypto.load_private_key(storage.get_user_key_path())
    user_pubkey = crypto.public_key_from_private(user_privkey)

    # Check if already joined
    if state.is_joined(user_pubkey):
        click.echo("You are already a member of this group.")
        return

    # In private groups, check if invited
    if not state.public and not state.is_member(user_pubkey):
        click.echo(
            "Warning: You have not been invited to this private group. You can view but not post."
        )
        return

    relays = get_canonical_relays(dag, relay_urls)
    tips = dag.get_tips()
    if tips:
        event = create_group_join(
            group_hex=group_pubkey,
            author_hex=user_pubkey,
            author_privkey=user_privkey,
            parents=tips,
        )
        dag.add_event(event)
        asyncio.run(publish_to_relays(event, relays))
        click.echo("Joined! You can now post messages.")


@cli.command()
@click.argument("group_pubkey")
@click.option("--relay", default=None, help="Relay URL")
def leave(group_pubkey: str, relay: str | None):
    """Leave a group. You will no longer be able to post messages."""
    storage = get_storage()
    fallback = [relay] if relay else BOOTSTRAP_RELAYS

    user_privkey = crypto.load_private_key(storage.get_user_key_path())
    user_pubkey = crypto.public_key_from_private(user_privkey)

    dag = storage.get_group_dag(group_pubkey)

    # Sync and heal
    relays = get_canonical_relays(dag, fallback)
    asyncio.run(sync_and_heal(dag, relays))
    relays = get_canonical_relays(dag, fallback)

    state = dag.get_state()
    if not state.is_joined(user_pubkey):
        click.echo("You are not a member of this group.")
        return

    tips = dag.get_tips()
    event = create_group_leave(
        group_hex=group_pubkey,
        author_hex=user_pubkey,
        author_privkey=user_privkey,
        parents=tips,
    )

    dag.add_event(event)
    asyncio.run(publish_to_relays(event, relays))
    click.echo(f"Left group {group_pubkey[:12]}...")


@cli.command()
@click.argument("group_pubkey")
@click.argument("invitee_pubkey")
@click.option("--relay", default=None, help="Relay URL")
def invite(group_pubkey: str, invitee_pubkey: str, relay: str | None):
    """Invite a user to a group (mod only)."""
    storage = get_storage()
    fallback = [relay] if relay else BOOTSTRAP_RELAYS

    user_privkey = crypto.load_private_key(storage.get_user_key_path())
    user_pubkey = crypto.public_key_from_private(user_privkey)

    dag = storage.get_group_dag(group_pubkey)

    # Sync and heal
    relays = get_canonical_relays(dag, fallback)
    asyncio.run(sync_and_heal(dag, relays))
    relays = get_canonical_relays(dag, fallback)

    state = dag.get_state()
    if not state.is_mod(user_pubkey):
        click.echo("Error: You must be a mod to invite users.", err=True)
        sys.exit(1)

    tips = dag.get_tips()
    if not tips:
        click.echo("Error: No events in group.", err=True)
        sys.exit(1)

    event = create_group_invite(
        group_hex=group_pubkey,
        author_hex=user_pubkey,
        author_privkey=user_privkey,
        invitee=invitee_pubkey,
        parents=tips,
    )

    dag.add_event(event)
    results = asyncio.run(publish_to_relays(event, relays))
    click.echo(f"Invited {invitee_pubkey[:12]}... to group.")


@cli.command()
@click.argument("group_pubkey")
@click.argument("target_pubkey")
@click.option("--relay", default=None, help="Relay URL")
def kick(group_pubkey: str, target_pubkey: str, relay: str | None):
    """Kick a user from a group (mod only)."""
    storage = get_storage()
    fallback = [relay] if relay else BOOTSTRAP_RELAYS

    user_privkey = crypto.load_private_key(storage.get_user_key_path())
    user_pubkey = crypto.public_key_from_private(user_privkey)

    dag = storage.get_group_dag(group_pubkey)

    relays = get_canonical_relays(dag, fallback)
    asyncio.run(sync_and_heal(dag, relays))
    relays = get_canonical_relays(dag, fallback)

    state = dag.get_state()
    if not state.is_mod(user_pubkey):
        click.echo("Error: You must be a mod to kick users.", err=True)
        sys.exit(1)

    tips = dag.get_tips()
    event = create_group_kick(
        group_hex=group_pubkey,
        author_hex=user_pubkey,
        author_privkey=user_privkey,
        target=target_pubkey,
        parents=tips,
    )

    dag.add_event(event)
    asyncio.run(publish_to_relays(event, relays))
    click.echo(f"Kicked {target_pubkey[:12]}... from group.")


@cli.command()
@click.argument("group_pubkey")
@click.argument("target_pubkey")
@click.option("--relay", default=None, help="Relay URL")
def mod_add(group_pubkey: str, target_pubkey: str, relay: str | None):
    """Promote a user to moderator (mod only)."""
    storage = get_storage()
    fallback = [relay] if relay else BOOTSTRAP_RELAYS

    user_privkey = crypto.load_private_key(storage.get_user_key_path())
    user_pubkey = crypto.public_key_from_private(user_privkey)

    dag = storage.get_group_dag(group_pubkey)

    relays = get_canonical_relays(dag, fallback)
    asyncio.run(sync_and_heal(dag, relays))
    relays = get_canonical_relays(dag, fallback)

    state = dag.get_state()
    if not state.is_mod(user_pubkey):
        click.echo("Error: You must be a mod.", err=True)
        sys.exit(1)

    tips = dag.get_tips()
    event = create_mod_add(
        group_hex=group_pubkey,
        author_hex=user_pubkey,
        author_privkey=user_privkey,
        target=target_pubkey,
        parents=tips,
    )

    dag.add_event(event)
    asyncio.run(publish_to_relays(event, relays))
    click.echo(f"Promoted {target_pubkey[:12]}... to mod.")


@cli.command()
@click.argument("group_pubkey")
@click.argument("target_pubkey")
@click.option("--relay", default=None, help="Relay URL")
def mod_remove(group_pubkey: str, target_pubkey: str, relay: str | None):
    """Demote a moderator to regular member (mod only)."""
    storage = get_storage()
    fallback = [relay] if relay else BOOTSTRAP_RELAYS

    user_privkey = crypto.load_private_key(storage.get_user_key_path())
    user_pubkey = crypto.public_key_from_private(user_privkey)

    dag = storage.get_group_dag(group_pubkey)

    relays = get_canonical_relays(dag, fallback)
    asyncio.run(sync_and_heal(dag, relays))
    relays = get_canonical_relays(dag, fallback)

    state = dag.get_state()
    if not state.is_mod(user_pubkey):
        click.echo("Error: You must be a mod.", err=True)
        sys.exit(1)

    tips = dag.get_tips()
    event = create_mod_remove(
        group_hex=group_pubkey,
        author_hex=user_pubkey,
        author_privkey=user_privkey,
        target=target_pubkey,
        parents=tips,
    )

    dag.add_event(event)
    asyncio.run(publish_to_relays(event, relays))
    click.echo(f"Demoted {target_pubkey[:12]}... from mod.")


@cli.command()
@click.argument("group_pubkey")
@click.argument("new_relays", nargs=-1, required=True)
@click.option("--relay", default=None, help="Current relay URL to publish to")
def relay_update(group_pubkey: str, new_relays: tuple[str, ...], relay: str | None):
    """Update the group's canonical relay list (mod only).

    NEW_RELAYS are the new relay URLs, e.g.:
      fern relay-update <group> ws://relay-a:8787 ws://relay-b:8788

    Publishes the update to all current relays, then seeds the new relays
    with the full local event history.
    """
    storage = get_storage()
    fallback = [relay] if relay else BOOTSTRAP_RELAYS

    user_privkey = crypto.load_private_key(storage.get_user_key_path())
    user_pubkey = crypto.public_key_from_private(user_privkey)

    dag = storage.get_group_dag(group_pubkey)

    # Sync and heal current relays first
    old_relays = get_canonical_relays(dag, fallback)
    click.echo(f"Syncing from {len(old_relays)} current relay(s)...")
    asyncio.run(sync_and_heal(dag, old_relays))
    old_relays = get_canonical_relays(dag, fallback)

    state = dag.get_state()
    if not state.is_mod(user_pubkey):
        click.echo("Error: You must be a mod to update relays.", err=True)
        sys.exit(1)

    tips = dag.get_tips()
    new_relay_list = list(new_relays)

    event = create_relay_update(
        group_hex=group_pubkey,
        author_hex=user_pubkey,
        author_privkey=user_privkey,
        new_relays=new_relay_list,
        parents=tips,
    )

    dag.add_event(event)

    # Publish update to ALL current relays
    click.echo(f"Publishing relay_update to {len(old_relays)} current relay(s)...")
    asyncio.run(publish_to_relays(event, old_relays))
    click.echo(f"Updated relays to: {', '.join(new_relay_list)}")

    # Seed new relays with full local history
    all_local = dag.get_all_events()
    click.echo(
        f"\nSeeding {len(new_relay_list)} new relay(s) with {len(all_local)} event(s)..."
    )

    async def seed_relay(url: str) -> dict:
        """Push all local events to a relay using a single connection."""
        result = {"ok": 0, "error": 0, "dup": 0}
        try:
            async with websockets.connect(url) as ws:
                for ev in all_local:
                    try:
                        await ws.send(
                            json.dumps(
                                {
                                    "action": "publish",
                                    "event": ev,
                                }
                            )
                        )
                        resp = json.loads(await ws.recv())
                        rtype = resp.get("type")
                        if rtype == "ok":
                            result["ok"] += 1
                        elif rtype == "error" and "duplicate" in resp.get(
                            "message", ""
                        ):
                            result["dup"] += 1
                        else:
                            result["error"] += 1
                    except Exception:
                        result["error"] += 1
        except Exception:
            result["error"] = len(all_local)
        return result

    async def seed_all():
        results = await asyncio.gather(*[seed_relay(url) for url in new_relay_list])
        return dict(zip(new_relay_list, results))

    seed_results = asyncio.run(seed_all())
    for url, counts in seed_results.items():
        click.echo(
            f"  {url}: {counts['ok']} published, {counts['dup']} already present, {counts['error']} errors"
        )

    click.echo(
        "\nRelay migration complete. Clients should sync to pick up the new relay list."
    )


@cli.command()
def groups():
    """List all known groups."""
    storage = get_storage()
    group_list = storage.list_groups()
    if not group_list:
        click.echo("No groups found. Create one with 'fern create'.")
        return

    for gpub in group_list:
        dag = storage.get_group_dag(gpub)
        state = dag.get_state()
        name = state.metadata.get("name", "unnamed")
        click.echo(f"  {gpub}  ({name}, {dag.count} events)")


@cli.command()
@click.argument("group_pubkey")
@click.option("--relay", default=None, help="Relay URL")
def sync(group_pubkey: str, relay: str | None):
    """Sync a group from all canonical relays, healing any divergence."""
    storage = get_storage()
    dag = storage.get_group_dag(group_pubkey)
    fallback = [relay] if relay else BOOTSTRAP_RELAYS

    relays = get_canonical_relays(dag, fallback)
    click.echo(f"Sync-and-heal across {len(relays)} relay(s)...")

    summary = asyncio.run(sync_and_heal(dag, relays))
    print_heal_summary(summary)

    click.echo(f"Total local events: {dag.count}")


@cli.command()
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def wipe(yes: bool):
    """Delete all stored events and groups, keeping your keypair."""
    storage = get_storage()
    groups_dir = storage.groups_dir

    group_files = list(groups_dir.glob("*.json"))
    if not group_files:
        click.echo("No stored events to wipe.")
        return

    if not yes:
        total_events = 0
        for f in group_files:
            with open(f) as fh:
                data = json.load(fh)
            total_events += len(data.get("events", []))
        click.echo(
            f"This will delete {len(group_files)} group(s) with {total_events} total event(s)."
        )
        click.echo(f"Keypair at {storage.get_user_key_path()} will be kept.")
        if not click.confirm("Continue?"):
            return

    for f in group_files:
        f.unlink()
    click.echo(f"Wiped {len(group_files)} group(s). Keypair preserved.")


if __name__ == "__main__":
    cli()
