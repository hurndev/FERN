"""FERN Test Harness - Tools for testing the FERN protocol.

Provides utilities for:
- Spawning test users with isolated storage and keypairs
- Concurrent multi-user message sending
- Network partition simulation
"""

import asyncio
import json
import os
import shutil
from pathlib import Path

import click

from . import crypto
from .relay import publish as relay_publish
from .config import BOOTSTRAP_RELAYS
from .events import _build_event


# =============================================================================
# User spawning
# =============================================================================


@click.group(name="test")
def test_cli():
    """FERN Test Harness - testing utilities for the FERN protocol."""
    pass


@test_cli.command(name="spawn-user")
@click.argument("name")
@click.option("--storage", default=None, help="Storage base directory (default: /tmp)")
def spawn_user(name: str, storage: str | None):
    """Create an isolated test user with keypair in /tmp/<name>/.fern.

    Returns the user's pubkey and storage path. The user is created in
    /tmp/<name> by default, or under --storage if specified.

    Output can be shell-evaluated to export variables:
        eval $(fern test spawn-user alice)
    """
    if storage:
        base_dir = Path(storage)
    else:
        base_dir = Path("/tmp")

    user_dir = base_dir / name
    user_dir.mkdir(parents=True, exist_ok=True)

    privkey, pubkey = crypto.generate_keypair()

    keys_dir = user_dir / ".fern" / "keys"
    keys_dir.mkdir(parents=True, exist_ok=True)
    key_path = keys_dir / "user.pem"
    crypto.save_keypair(privkey, str(key_path))

    groups_dir = user_dir / ".fern" / "groups"
    groups_dir.mkdir(parents=True, exist_ok=True)

    click.echo(f"# User: {name}")
    click.echo(f"export FERN_TEST_HOME={user_dir}")
    click.echo(f"export FERN_TEST_USER={name}")
    click.echo(f"export FERN_TEST_PUBKEY={pubkey}")
    click.echo(f"# Key stored at: {key_path}")


@test_cli.command(name="list-users")
@click.option("--storage", default=None, help="Storage base directory (default: /tmp)")
def list_users(storage: str | None):
    """List all spawned test users in a storage directory."""
    base_dir = Path(storage) if storage else Path("/tmp")

    if not base_dir.exists():
        click.echo("No test users found.")
        return

    found = False
    for user_dir in sorted(base_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        key_path = user_dir / ".fern" / "keys" / "user.pem"
        if not key_path.exists():
            continue

        found = True
        privkey = crypto.load_private_key(str(key_path))
        pubkey = crypto.public_key_from_private(privkey)

        groups_dir = user_dir / ".fern" / "groups"
        group_count = len(list(groups_dir.glob("*.json"))) if groups_dir.exists() else 0

        click.echo(f"{user_dir.name}: {pubkey[:16]}... ({group_count} groups)")

    if not found:
        click.echo("No test users found.")


@test_cli.command(name="wipe-users")
@click.option("--storage", default=None, help="Storage base directory (default: /tmp)")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def wipe_users(storage: str | None, yes: bool):
    """Delete all spawned test users."""
    base_dir = Path(storage) if storage else Path("/tmp")

    if not base_dir.exists():
        click.echo("No test users found.")
        return

    user_dirs = [
        d
        for d in base_dir.iterdir()
        if d.is_dir() and (d / ".fern" / "keys" / "user.pem").exists()
    ]

    if not user_dirs:
        click.echo("No test users found.")
        return

    if not yes:
        click.echo(f"This will delete {len(user_dirs)} test user(s):")
        for d in user_dirs:
            click.echo(f"  {d}")
        if not click.confirm("Continue?"):
            return

    for d in user_dirs:
        shutil.rmtree(d)

    click.echo(f"Deleted {len(user_dirs)} test user(s).")


# =============================================================================
# Concurrent multi-user sending
# =============================================================================


@test_cli.command(name="multi-send")
@click.argument("group_pubkey")
@click.argument("users", nargs=-1, required=True)
@click.option("--relay", default=None, help="Relay URL (default: localhost:8787)")
@click.option("--count", default=1, help="Messages per user")
@click.option("--concurrent", is_flag=True, help="Send all messages simultaneously")
def multi_send(
    group_pubkey: str,
    users: tuple[str, ...],
    relay: str | None,
    count: int,
    concurrent: bool,
):
    """Have multiple users send messages concurrently.

    USERS are usernames or paths to test user directories (will use FERN_TEST_HOME env).

    Example:
        fern test multi-send <group> alice bob carol --concurrent --count 5
    """
    if not relay:
        relay = BOOTSTRAP_RELAYS[0]

    async def send_as_user(user_name: str, user_home: str, msg_num: int) -> dict:
        """Send a single message as a user."""
        fern_dir = Path(user_home) / ".fern"
        key_path = fern_dir / "keys" / "user.pem"
        if not key_path.exists():
            return {"user": user_name, "success": False, "error": "no key"}

        privkey = crypto.load_private_key(str(key_path))
        pubkey = crypto.public_key_from_private(privkey)

        storage_dir = fern_dir / "groups"
        from .dag import EventDAG

        dag = EventDAG(group_pubkey, str(storage_dir))
        tips = dag.get_tips()
        if not tips:
            return {"user": user_name, "success": False, "error": "no events in group"}

        from .events import create_message

        event = create_message(
            group_hex=group_pubkey,
            author_hex=pubkey,
            author_privkey=privkey,
            content=f"[{user_name}] message {msg_num}",
            parents=tips,
        )

        try:
            resp = await relay_publish(relay, event)
            return {
                "user": user_name,
                "success": resp.get("type") == "ok" if resp else False,
                "event_id": event["id"],
            }
        except Exception as e:
            return {"user": user_name, "success": False, "error": str(e)}

    async def run():
        tasks = []
        for user_name in users:
            home = os.environ.get("FERN_TEST_HOME", "")
            if not home or not Path(home).exists():
                home = str(Path("/tmp") / user_name)

            for i in range(count):
                if concurrent:
                    tasks.append(send_as_user(user_name, home, i + 1))
                else:
                    result = await send_as_user(user_name, home, i + 1)
                    if result["success"]:
                        click.echo(f"  {result['user']}: {result['event_id'][:16]}...")
                    else:
                        click.echo(
                            f"  {result['user']}: FAILED ({result.get('error')})"
                        )

        if tasks:
            results = await asyncio.gather(*tasks)
            success = sum(1 for r in results if r["success"])
            click.echo(f"\nResults: {success}/{len(results)} succeeded")
            for r in results:
                if not r["success"]:
                    click.echo(f"  {r['user']}: FAILED ({r.get('error')})")

    asyncio.run(run())


# =============================================================================
# Network partition simulation
# =============================================================================


@test_cli.command(name="partition")
@click.argument("relay")
@click.argument("action", type=click.Choice(["create", "remove", "list"]))
@click.option("--name", default="default", help="Partition name")
def partition(relay: str, action: str, name: str):
    """Simulate network partitions by controlling relay connectivity.

    This is a placeholder. Real partition simulation requires:
    - A network namespace tool (e.g., iptables, tc)
    - Or a relay-level "block peer" feature
    - Or a test proxy that can drop connections

    For now, this prints instructions for manual testing.

    ACTIONS:
        create  - Create a partition blocking the given relay
        remove  - Remove a partition
        list    - List active partitions
    """
    if action == "create":
        click.echo(f"Partition '{name}' for {relay}")
        click.echo(f"\nTo simulate a partition, you can:")
        click.echo(f"  1. Stop the relay: kill the relay process")
        click.echo(
            f"  2. Use iptables to block: sudo iptables -A INPUT -p tcp --dport 8787 -j DROP"
        )
        click.echo(f"  3. Use a test proxy that intercepts and drops connections")

    elif action == "remove":
        click.echo(f"Removing partition '{name}' for {relay}")
        click.echo(f"\nTo remove a partition:")
        click.echo(f"  1. Restart the relay process")
        click.echo(f"  2. Clear iptables: sudo iptables -F")
        click.echo(f"  3. Or wait for clients to reconnect")

    elif action == "list":
        click.echo(f"Active partitions:")
        click.echo(f"  (none)")
        click.echo(f"\nNote: Partitions must be managed manually or via a test proxy.")


# =============================================================================
# Event inspection from relay
# =============================================================================


@test_cli.command(name="watch")
@click.argument("group_pubkey")
@click.option("--relay", default="ws://localhost:8787", help="Relay URL")
def watch(group_pubkey: str, relay: str):
    """Watch and print events from a relay in real-time.

    Useful for observing what events are being published during tests.
    """
    import websockets
    from .events import verify_event_id, verify_event_signature

    async def run():
        try:
            async with websockets.connect(relay) as ws:
                await ws.send(
                    json.dumps({"action": "subscribe", "group": group_pubkey})
                )
                click.echo(f"Watching {group_pubkey[:16]}... on {relay}")
                click.echo("Press Ctrl+C to stop.\n")

                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "event":
                        event = msg["event"]
                        eid = event["id"][:16]
                        etype = event["type"]
                        author = event["author"][:12]
                        ts = event.get("ts", 0)

                        content = ""
                        if etype == "message":
                            content = f" - {event['content'][:50]}"
                        elif etype == "group_join":
                            content = " joined"
                        elif etype == "group_leave":
                            content = " left"

                        click.echo(f"[{ts}] {eid}... [{etype}] @{author}...{content}")
        except KeyboardInterrupt:
            click.echo("\nStopped.")
        except Exception as e:
            click.echo(f"Error: {e}", err=True)

    asyncio.run(run())


# =============================================================================
# Custom event builder
# =============================================================================


EVENT_TYPES = [
    "message",
    "group_genesis",
    "group_invite",
    "group_join",
    "group_leave",
    "group_kick",
    "mod_add",
    "mod_remove",
    "relay_update",
    "group_metadata",
]


@test_cli.command(name="custom-event")
@click.argument("group_pubkey")
@click.option(
    "--type",
    "event_type",
    type=click.Choice(EVENT_TYPES),
    required=True,
    help="Event type",
)
@click.option(
    "--content",
    default=None,
    help='Event content as JSON (e.g. \'{"target":"abc..."}\') or plain string for message type',
)
@click.option(
    "--target",
    default=None,
    help='Shorthand: sets content to {"target": VALUE} for kick/mod_add/mod_remove or {"invitee": VALUE} for invite',
)
@click.option(
    "--parents",
    default="auto",
    help="Parents: 'auto' (current tips), 'none' (empty), or comma-separated event IDs",
)
@click.option(
    "--ts", default=None, type=int, help="Custom Unix timestamp (default: now)"
)
@click.option(
    "--mode",
    type=click.Choice(["both", "relay-only", "local-only"]),
    default="both",
    help="Publish mode: both=relay+local, relay-only=skip local DAG, local-only=skip relay",
)
@click.option(
    "--relay", default=None, help="Relay URL (default: first bootstrap relay)"
)
@click.option(
    "--user", default=None, help="Test user name (looks up key in /tmp/<name>/.fern)"
)
def custom_event(
    group_pubkey: str,
    event_type: str,
    content: str | None,
    target: str | None,
    parents: str,
    ts: int | None,
    mode: str,
    relay: str | None,
    user: str | None,
):
    """Build and publish a custom event with full control over all fields.

    Useful for testing edge cases: missing parents, bogus timestamps,
    events published to relay only (not stored locally), etc.

    Uses the test user's keypair from FERN_TEST_HOME or --user.

    Examples:

        # Normal message, auto parents and timestamp
        fern test custom-event <group> --type message --content "hello"

        # Kick a user without adding to local DAG
        fern test custom-event <group> --type group_kick --target <pubkey> --mode relay-only

        # Message with timestamp=0 to test conflict resolution
        fern test custom-event <group> --type message --content "first!" --ts 0

        # Event with no parents (creates DAG branch)
        fern test custom-event <group> --type message --content "orphan" --parents none

        # Custom parents by ID
        fern test custom-event <group> --type message --content "merge" --parents id1,id2,id3
    """
    from .dag import EventDAG

    if not relay:
        relay = BOOTSTRAP_RELAYS[0]

    user_home = os.environ.get("FERN_TEST_HOME", "")
    if user:
        user_home = str(Path("/tmp") / user)
    if not user_home or not Path(user_home).exists():
        click.echo("Error: No test user. Set FERN_TEST_HOME or use --user.", err=True)
        return

    fern_dir = Path(user_home) / ".fern"
    key_path = fern_dir / "keys" / "user.pem"
    if not key_path.exists():
        click.echo(f"Error: No keypair at {key_path}", err=True)
        return

    privkey = crypto.load_private_key(str(key_path))
    pubkey = crypto.public_key_from_private(privkey)

    storage_dir = fern_dir / "groups"
    dag = EventDAG(group_pubkey, str(storage_dir))

    if content is None and target is not None:
        if event_type == "group_invite":
            content = json.dumps({"invitee": target, "role": "member"})
        else:
            content = json.dumps({"target": target})

    if content is None:
        if event_type == "message":
            content = "test message"
        else:
            content = "{}"

    try:
        parsed_content = json.loads(content)
    except json.JSONDecodeError:
        parsed_content = content

    if parents == "auto":
        parent_ids = dag.get_tips()
    elif parents == "none":
        parent_ids = []
    else:
        parent_ids = [p.strip() for p in parents.split(",") if p.strip()]

    event = _build_event(
        event_type=event_type,
        group_hex=group_pubkey,
        author_hex=pubkey,
        parents=parent_ids,
        content=parsed_content,
        signer_private_key=privkey,
        ts=ts,
    )

    click.echo(f"Event ID:    {event['id']}")
    click.echo(f"Type:        {event['type']}")
    click.echo(f"Author:      {event['author'][:24]}...")
    click.echo(f"Parents:     {event['parents']}")
    click.echo(f"Timestamp:   {event['ts']}")
    click.echo(f"Content:     {json.dumps(event['content'])[:80]}")
    click.echo(f"Mode:        {mode}")

    async def run():
        relay_ok = False

        if mode in ("both", "relay-only"):
            try:
                resp = await relay_publish(relay, event)
                if resp and resp.get("type") == "ok":
                    click.echo(f"Relay:       published to {relay}")
                    relay_ok = True
                else:
                    click.echo(f"Relay:       FAILED ({resp})", err=True)
            except Exception as e:
                click.echo(f"Relay:       ERROR ({e})", err=True)

        if mode in ("both", "local-only"):
            ok, reason = dag.add_event(event)
            if ok:
                click.echo(f"Local DAG:   stored ({dag.count} events)")
            else:
                click.echo(f"Local DAG:   FAILED ({reason})", err=True)

        if mode == "relay-only":
            click.echo(f"Local DAG:   skipped (relay-only mode)")
        if mode == "local-only":
            click.echo(f"Relay:       skipped (local-only mode)")

        click.echo(f"Sig:         {event['sig'][:24]}...")

    asyncio.run(run())


if __name__ == "__main__":
    test_cli()
