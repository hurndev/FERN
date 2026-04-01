"""FERN Debugging Tools - Protocol verification and DAG inspection utilities."""

import asyncio
import json
import os
from collections import defaultdict

import click

from .events import verify_event_id, verify_event_signature
from .dag import EventDAG
from .relay import fetch_event, fetch_events, fetch_publish, fetch_summary
from .config import BOOTSTRAP_RELAYS


DEFAULT_STORAGE = "~/.fern"


@click.group()
def cli():
    """FERN Debug - Protocol debugging and verification tools."""
    pass


@cli.command()
@click.argument("event_json")
def verify(event_json: str):
    """Verify an event's signature and ID integrity.

    EVENT_JSON can be a JSON string or a path to a JSON file.
    """
    if os.path.exists(event_json):
        with open(event_json, "r") as f:
            event = json.load(f)
    else:
        event = json.loads(event_json)

    click.echo(f"Event ID:  {event.get('id', '?')}")
    click.echo(f"Type:      {event.get('type', '?')}")
    click.echo(f"Group:     {event.get('group', '?')[:16]}...")
    click.echo(f"Author:    {event.get('author', '?')[:16]}...")
    click.echo(f"Timestamp: {event.get('ts', '?')}")
    click.echo()

    # Check ID
    id_ok = verify_event_id(event)
    click.echo(f"ID valid:      {'PASS' if id_ok else 'FAIL'}")

    # Check signature
    if event.get("type") == "group_genesis":
        signer = event.get("group", "")
    else:
        signer = event.get("author", "")
    sig_ok = verify_event_signature(event, signer)
    click.echo(f"Signature:     {'PASS' if sig_ok else 'FAIL'}")
    click.echo(f"Signer pubkey: {signer[:16]}...")

    # Overall
    click.echo(f"\nOverall: {'VALID' if (id_ok and sig_ok) else 'INVALID'}")


@cli.command()
@click.argument("group_pubkey")
@click.option("--storage", default=None, help="Storage directory")
def dag_tree(group_pubkey: str, storage: str | None):
    """Show the DAG structure for a group as a tree."""
    base = storage or os.path.expanduser(DEFAULT_STORAGE)
    dag = EventDAG(group_pubkey, os.path.join(base, "groups"))

    if dag.count == 0:
        click.echo("No events found.")
        return

    # Build adjacency
    children = defaultdict(list)
    events = {e["id"]: e for e in dag.get_all_events()}

    for eid, event in events.items():
        for parent_id in event.get("parents", []):
            children[parent_id].append(eid)

    # Find roots (genesis)
    roots = [eid for eid, e in events.items() if not e.get("parents")]

    def print_tree(node_id: str, indent: int = 0):
        event = events.get(node_id)
        if event is None:
            click.echo(f"{'  ' * indent}[missing: {node_id[:16]}...]")
            return
        etype = event["type"]
        author = event["author"][:8]
        ts = event["ts"]
        click.echo(f"{'  ' * indent}{node_id[:16]}... [{etype}] @{author} ts={ts}")
        for child_id in sorted(children.get(node_id, [])):
            print_tree(child_id, indent + 1)

    for root in sorted(roots):
        print_tree(root)

    click.echo(f"\nTotal events: {dag.count}")
    tips = dag.get_tips()
    click.echo(f"Tips: {len(tips)}")
    for t in tips:
        click.echo(f"  {t[:16]}...")


@cli.command()
@click.argument("group_pubkey")
@click.option("--storage", default=None, help="Storage directory")
def state(group_pubkey: str, storage: str | None):
    """Show the derived group state for a group."""
    base = storage or os.path.expanduser(DEFAULT_STORAGE)
    dag = EventDAG(group_pubkey, os.path.join(base, "groups"))

    if dag.count == 0:
        click.echo("No events found.")
        return

    gs = dag.get_state()

    click.echo(f"Group: {group_pubkey}")
    if gs.metadata.get("name"):
        click.echo(f"Name:  {gs.metadata['name']}")
    if gs.metadata.get("description"):
        click.echo(f"Desc:  {gs.metadata['description']}")

    click.echo(f"\nMembers ({len(gs.members)}):")
    for m in sorted(gs.members):
        mod_marker = " [mod]" if m in gs.mods else ""
        click.echo(f"  {m}{mod_marker}")

    click.echo(f"\nRelays ({len(gs.relays)}):")
    for r in gs.relays:
        click.echo(f"  {r}")

    click.echo(f"\nEvents: {dag.count}")
    gaps = dag.get_missing_parents()
    if gaps:
        click.echo(f"Gaps:   {len(gaps)} missing parent(s)")
        for g in sorted(gaps):
            click.echo(f"  {g[:16]}...")
    else:
        click.echo("Gaps:   none")


@cli.command()
@click.argument("group_pubkey")
@click.option("--storage", default=None, help="Storage directory")
def gaps(group_pubkey: str, storage: str | None):
    """Show missing parent events (gaps) in a group's DAG."""
    base = storage or os.path.expanduser(DEFAULT_STORAGE)
    dag = EventDAG(group_pubkey, os.path.join(base, "groups"))

    missing = dag.get_missing_parents()
    if not missing:
        click.echo("No gaps found. DAG is complete.")
        return

    click.echo(f"Found {len(missing)} gap(s):")
    for m in sorted(missing):
        # Find which events reference this missing parent
        referencing = []
        for event in dag.get_all_events():
            if m in event.get("parents", []):
                referencing.append(event["id"][:16])
        click.echo(f"  {m[:16]}... (referenced by: {', '.join(referencing[:5])})")


@cli.command()
@click.argument("group_pubkey")
@click.option("--relay", multiple=True, help="Relay URLs to compare")
def compare_relays(group_pubkey: str, relay: tuple[str, ...]):
    """Compare event sets across multiple relays for cross-relay verification."""
    if not relay:
        relay = tuple(BOOTSTRAP_RELAYS)

    async def get_all_event_ids(url: str) -> set[str]:
        events = await fetch_events(url, group_pubkey, since=0)
        return {e["id"] for e in events}

    async def run():
        summaries = {}
        event_ids = {}

        for url in relay:
            summaries[url] = await fetch_summary(url, group_pubkey)
            event_ids[url] = await get_all_event_ids(url)

        click.echo(f"Group: {group_pubkey[:16]}...\n")

        # Show summaries
        click.echo("Relay Summaries:")
        for url in relay:
            s = summaries[url]
            if s.get("type") == "summary":
                click.echo(f"  {url}: {s['count']} events, {len(s['tips'])} tip(s)")
            else:
                click.echo(f"  {url}: ERROR - {s.get('message', 'unknown')}")

        # Compare
        click.echo("\nCross-Relay Comparison:")
        if len(relay) < 2:
            click.echo("  Need at least 2 relays to compare.")
            return

        all_ids = list(event_ids.values())
        base = all_ids[0]
        for i, url in enumerate(relay[1:], 1):
            other = all_ids[i]
            missing_from_other = base - other
            extra_on_other = other - base

            if not missing_from_other and not extra_on_other:
                click.echo(f"  {relay[0]} <-> {url}: IDENTICAL")
            else:
                if missing_from_other:
                    click.echo(
                        f"  Missing from {url}: {len(missing_from_other)} event(s)"
                    )
                if extra_on_other:
                    click.echo(f"  Extra on {url}: {len(extra_on_other)} event(s)")

    asyncio.run(run())


@cli.command()
@click.argument("group_pubkey")
@click.option("--relay", default=None, help="Relay URL")
def dag_health(group_pubkey: str, relay: str | None):
    """Full DAG health check: integrity, gaps, state consistency."""
    base = os.path.expanduser(DEFAULT_STORAGE)
    dag = EventDAG(group_pubkey, os.path.join(base, "groups"))

    click.echo(f"DAG Health Check: {group_pubkey[:16]}...\n")

    if dag.count == 0:
        click.echo("No events found.")
        return

    issues = []
    events = dag.get_all_events()

    # 1. Verify all event signatures and IDs
    click.echo("1. Event Verification:")
    sig_failures = 0
    id_failures = 0
    for event in events:
        if not verify_event_id(event):
            id_failures += 1
        signer = event["group"] if event["type"] == "group_genesis" else event["author"]
        if not verify_event_signature(event, signer):
            sig_failures += 1

    if sig_failures or id_failures:
        issues.append(f"{sig_failures} signature failures, {id_failures} ID mismatches")
        click.echo(f"   FAIL: {sig_failures} bad signatures, {id_failures} bad IDs")
    else:
        click.echo(f"   PASS: All {dag.count} events verified")

    # 2. Check for gaps
    click.echo("\n2. Gap Check:")
    gaps = dag.get_missing_parents()
    if gaps:
        issues.append(f"{len(gaps)} missing parents")
        click.echo(f"   WARN: {len(gaps)} gap(s) detected")
        for g in sorted(gaps)[:5]:
            click.echo(f"     - {g[:16]}...")
    else:
        click.echo("   PASS: No gaps")

    # 3. Genesis check
    click.echo("\n3. Genesis Check:")
    genesis_events = [e for e in events if e["type"] == "group_genesis"]
    if len(genesis_events) == 0:
        issues.append("no genesis event")
        click.echo("   FAIL: No genesis event found")
    elif len(genesis_events) > 1:
        issues.append("multiple genesis events")
        click.echo(f"   FAIL: {len(genesis_events)} genesis events (should be 1)")
    else:
        click.echo("   PASS: Exactly 1 genesis event")

    # 4. State derivation
    click.echo("\n4. State Derivation:")
    state = dag.get_state()
    click.echo(f"   Members: {len(state.members)}")
    click.echo(f"   Mods: {len(state.mods)}")
    click.echo(f"   Relays: {state.relays}")

    # 5. Tips
    click.echo("\n5. DAG Tips:")
    tips = dag.get_tips()
    click.echo(f"   {len(tips)} tip(s)")
    if len(tips) > 1:
        click.echo(
            f"   Note: Multiple tips indicate parallel branches (normal for concurrent activity)"
        )

    # Summary
    click.echo(f"\n{'=' * 40}")
    if not issues:
        click.echo("HEALTH: OK")
    else:
        click.echo(f"HEALTH: {len(issues)} issue(s)")
        for issue in issues:
            click.echo(f"  - {issue}")


@cli.command()
@click.argument("group_pubkey")
@click.option("--storage", default=None, help="Storage directory")
@click.option("--type-filter", default=None, help="Filter by event type")
def events_list(group_pubkey: str, storage: str | None, type_filter: str | None):
    """List all events in a group with details."""
    base = storage or os.path.expanduser(DEFAULT_STORAGE)
    dag = EventDAG(group_pubkey, os.path.join(base, "groups"))

    events = dag.get_all_events()
    if type_filter:
        events = [e for e in events if e["type"] == type_filter]

    if not events:
        click.echo("No events found.")
        return

    for event in events:
        etype = event["type"]
        eid = event["id"][:16]
        author = event["author"][:16]
        ts = event["ts"]
        parents = len(event.get("parents", []))

        content_preview = ""
        if etype == "message":
            content_preview = f' "{event["content"][:50]}"'
        elif isinstance(event.get("content"), dict):
            if "name" in event["content"]:
                content_preview = f' name="{event["content"]["name"]}"'

        click.echo(
            f"{eid}... | {etype:16} | @{author}... | ts={ts} | p={parents}{content_preview}"
        )

    click.echo(f"\nTotal: {len(events)} events")


@cli.command()
@click.argument("event_json")
def serialise(event_json: str):
    """Show the canonical serialisation of an event (for debugging)."""
    from .events import canonical_serialise, compute_event_id

    if os.path.exists(event_json):
        with open(event_json, "r") as f:
            event = json.load(f)
    else:
        event = json.loads(event_json)

    canonical = canonical_serialise(
        event["type"],
        event["group"],
        event["author"],
        event["parents"],
        event["content"],
        event["ts"],
    )

    click.echo("Canonical serialisation:")
    click.echo(canonical.decode("utf-8"))
    click.echo(f"\nSHA-256: {compute_event_id(canonical)}")
    click.echo(f"Event ID: {event.get('id', 'N/A')}")
    click.echo(f"Match: {compute_event_id(canonical) == event.get('id')}")


# =============================================================================
# Relay inspection commands
# =============================================================================


@click.group(name="relay")
def relay_cli():
    """Inspect and control relay servers."""
    pass


@relay_cli.command(name="get")
@click.argument("event_id")
@click.option("--relay", default="ws://localhost:8787", help="Relay URL")
@click.option(
    "--group", "group_pubkey", default=None, help="Group pubkey (optional hint)"
)
def relay_get(event_id: str, relay: str, group_pubkey: str | None):
    """Fetch a specific event from a relay by ID."""

    async def run():
        try:
            event = await fetch_event(relay, event_id)
            if event:
                click.echo(json.dumps(event, indent=2))
            else:
                click.echo(f"Event {event_id[:16]}... not found on {relay}", err=True)
        except Exception as e:
            click.echo(f"Connection failed: {e}", err=True)

    asyncio.run(run())


@relay_cli.command(name="summary")
@click.argument("group_pubkey")
@click.option("--relay", default="ws://localhost:8787", help="Relay URL")
def relay_summary(group_pubkey: str, relay: str):
    """Get event count and tips from a relay for a group."""

    async def run():
        try:
            response = await fetch_summary(relay, group_pubkey)
            if response and response.get("type") == "summary":
                click.echo(f"Relay: {relay}")
                click.echo(f"Group: {group_pubkey[:16]}...")
                click.echo(f"Events: {response['count']}")
                click.echo(f"Tips: {len(response['tips'])}")
                for tip in response["tips"]:
                    click.echo(f"  {tip[:16]}...")
            else:
                click.echo(
                    f"Error: {response.get('message', 'unknown') if response else 'no response'}",
                    err=True,
                )
        except Exception as e:
            click.echo(f"Connection failed: {e}", err=True)

    asyncio.run(run())


@relay_cli.command(name="gc-status")
@click.option("--relay", default="ws://localhost:8787", help="Relay URL")
def relay_gc_status(relay: str):
    """Show GC status of a relay (which events would be GC'd)."""
    click.echo(f"GC status for {relay}")
    click.echo("(Relay GC is not yet implemented - this is a placeholder)")


@relay_cli.command(name="list-groups")
@click.option("--relay", default="ws://localhost:8787", help="Relay URL")
def relay_list_groups(relay: str):
    """List all groups stored on a relay."""
    click.echo(f"Listing groups on {relay}...")
    click.echo("(Use fern relay summary <group> to inspect a specific group)")


# Register relay subcommands
cli.add_command(relay_cli)


# =============================================================================
# Raw event publishing
# =============================================================================


@cli.command(name="publish-raw")
@click.argument("relay")
@click.argument("event_json")
def publish_raw(relay: str, event_json: str):
    """Publish a raw JSON event to a relay, bypassing client construction.

    EVENT_JSON can be a JSON string or a path to a JSON file.
    Use this to inject manually constructed or tampered events for testing.
    """
    if os.path.exists(event_json):
        with open(event_json, "r") as f:
            event = json.load(f)
    else:
        event = json.loads(event_json)

    async def run():
        try:
            response = await fetch_publish(relay, event)
            if response and response.get("type") == "ok":
                click.echo(f"Published: {response.get('id', '?')[:16]}...")
            elif response and response.get("type") == "error":
                click.echo(f"Rejected: {response.get('message', 'unknown')}", err=True)
            else:
                click.echo(f"Unexpected: {response}", err=True)
        except Exception as e:
            click.echo(f"Connection failed: {e}", err=True)

    asyncio.run(run())


if __name__ == "__main__":
    cli()
