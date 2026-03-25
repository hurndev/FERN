"""FERN Relay Server - WebSocket-based event storage and forwarding."""

import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path

import websockets
import click

from .crypto import verify as verify_sig
from .events import canonical_serialise, verify_event_id


def display_event(event: dict, count: int) -> None:
    """Display a new event in an ASCII box."""
    etype = event["type"]
    author = event["author"][:16]
    group = event["group"][:16]
    eid = event["id"][:16]
    ts = datetime.fromtimestamp(event["ts"]).strftime("%H:%M:%S")

    if etype == "message" and isinstance(event["content"], str):
        content = event["content"]
        if len(content) > 50:
            content = content[:47] + "..."
    elif etype == "group_genesis" and isinstance(event["content"], dict):
        content = f'created "{event["content"].get("name", "?")}"'
    elif etype == "group_invite" and isinstance(event["content"], dict):
        content = f"invitee={event['content']['invitee'][:16]}"
    elif etype == "group_join":
        content = f"{author[:16]} joined"
    elif etype == "group_leave":
        content = f"{author[:16]} left"
    elif etype == "group_kick" and isinstance(event["content"], dict):
        content = f"target={event['content']['target'][:16]}"
    elif etype == "mod_add" and isinstance(event["content"], dict):
        content = f"target={event['content']['target'][:16]}"
    elif etype == "mod_remove" and isinstance(event["content"], dict):
        content = f"target={event['content']['target'][:16]}"
    elif etype == "relay_update" and isinstance(event["content"], dict):
        relays = event["content"].get("relays", [])
        content = f"{len(relays)} relay(s)"
    elif etype == "group_metadata":
        content = "updated"
    else:
        content = str(event.get("content", ""))[:50]

    parents = len(event.get("parents", []))

    lines = [
        f"\u250c\u2500\u2500 New Event #{count} \u2500{'\u2500' * 44}\u2510",
        f"\u2502  type:     {etype:<46}\u2502",
        f"\u2502  id:       {eid}...{' ' * 28}\u2502",
        f"\u2502  group:    {group}...{' ' * 28}\u2502",
        f"\u2502  author:   {author}...{' ' * 28}\u2502",
        f"\u2502  time:     {ts:<46}\u2502",
        f"\u2502  parents:  {parents:<46}\u2502",
        f"\u2502  content:  {content:<46}\u2502",
        f"\u2514{'\u2500' * 60}\u2518",
    ]
    print("\n".join(lines))


class RelayServer:
    """FERN Relay Server. Stores events per group, validates and forwards."""

    def __init__(
        self, host: str = "0.0.0.0", port: int = 8787, storage_dir: str = "./relay_data"
    ):
        self.host = host
        self.port = port
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # group_pubkey -> { event_id -> event }
        self.groups: dict[str, dict[str, dict]] = {}
        # group_pubkey -> set of connected websockets
        self.subscriptions: dict[str, set] = {}
        # connected websockets (for cleanup)
        self.connections: set = set()
        # total events received counter
        self.event_count: int = 0

        self._load_all_groups()

    def _group_dir(self, group_pubkey: str) -> Path:
        d = self.storage_dir / group_pubkey
        d.mkdir(exist_ok=True)
        return d

    def _load_all_groups(self) -> None:
        """Load all stored groups from disk on startup."""
        for group_dir in self.storage_dir.iterdir():
            if group_dir.is_dir() and len(group_dir.name) == 64:
                events_file = group_dir / "events.json"
                if events_file.exists():
                    with open(events_file, "r") as f:
                        events = json.load(f)
                    self.groups[group_dir.name] = {e["id"]: e for e in events}
                    self.subscriptions[group_dir.name] = set()
                    print(
                        f"Loaded group {group_dir.name[:12]}... with {len(events)} events"
                    )

    def _save_group(self, group_pubkey: str) -> None:
        """Persist group events to disk."""
        events_file = self._group_dir(group_pubkey) / "events.json"
        events = list(self.groups.get(group_pubkey, {}).values())
        with open(events_file, "w") as f:
            json.dump(events, f, indent=2)

    def store_event(self, event: dict) -> tuple[bool, str]:
        """Validate and store an event. Returns (success, reason)."""
        if not verify_event_id(event):
            return False, "invalid event id"

        # Genesis is signed with group key, all others with author key
        signer = event["group"] if event["type"] == "group_genesis" else event["author"]

        canonical = canonical_serialise(
            event["type"],
            event["group"],
            event["author"],
            event["parents"],
            event["content"],
            event["ts"],
        )
        if not verify_sig(signer, event["sig"], canonical):
            return False, "invalid signature"

        # Non-genesis events must have at least one parent
        if event["type"] != "group_genesis":
            parents = event.get("parents", [])
            if not parents or not isinstance(parents, list) or len(parents) == 0:
                return False, "non-genesis event must have at least one parent"

        group = event["group"]
        if group not in self.groups:
            self.groups[group] = {}
            self.subscriptions[group] = set()

        if event["id"] in self.groups[group]:
            return False, "duplicate"

        self.groups[group][event["id"]] = event
        self._save_group(group)
        self.event_count += 1
        display_event(event, self.event_count)
        return True, "ok"

    async def handle_subscribe(self, ws, group: str) -> None:
        """Handle subscription to a group.

        Subscribes to receive new events only. For historical events, use the sync action first.
        """
        print(f"    subscribe: group={group[:16]}...")
        if group not in self.subscriptions:
            self.subscriptions[group] = set()
        self.subscriptions[group].add(ws)
        print(f"    subscribed - waiting for new events")

    async def handle_publish(self, ws, event: dict) -> None:
        """Handle event publishing."""
        eid = event.get("id", "?")[:16]
        etype = event.get("type", "?")
        group = event.get("group", "?")[:16]
        print(f"    publish: type={etype} id={eid}... group={group}...")
        success, reason = self.store_event(event)

        if success:
            # Acknowledge to sender
            await ws.send(json.dumps({"type": "ok", "id": event["id"]}))

            # Broadcast to all subscribers of this group
            group_full = event["group"]
            if group_full in self.subscriptions:
                subscribers = len(self.subscriptions[group_full]) - 1
                print(f"    broadcast to {subscribers} subscriber(s)")
                msg = json.dumps({"type": "event", "event": event})
                dead = set()
                for sub_ws in self.subscriptions[group_full]:
                    if sub_ws != ws:
                        try:
                            await sub_ws.send(msg)
                        except websockets.ConnectionClosed:
                            dead.add(sub_ws)
                self.subscriptions[group_full] -= dead
        else:
            print(f"  \u2717 Rejected: {reason} ({eid}...)")
            await ws.send(json.dumps({"type": "error", "message": reason}))

    async def handle_get(self, ws, event_id: str, group: str | None = None) -> None:
        """Handle request for a specific event by ID."""
        print(
            f"    get: event_id={event_id[:16]}... group={group[:16] if group else 'any'}..."
        )
        if group and group in self.groups:
            event = self.groups[group].get(event_id)
            if event:
                print(f"    found in group {group[:16]}...")
                await ws.send(json.dumps({"type": "event", "event": event}))
                return
        else:
            # Search all groups
            for gpub, grp_events in self.groups.items():
                if event_id in grp_events:
                    print(f"    found in group {gpub[:16]}...")
                    await ws.send(
                        json.dumps({"type": "event", "event": grp_events[event_id]})
                    )
                    return

        print(f"    not found")
        await ws.send(json.dumps({"type": "not_found", "id": event_id}))

    async def handle_sync(self, ws, group: str, since: int) -> None:
        """Handle sync request - send all events since a timestamp."""
        print(f"    sync: group={group[:16]}... since={since}")
        if group in self.groups:
            events = sorted(
                [e for e in self.groups[group].values() if e["ts"] >= since],
                key=lambda e: (e["ts"], e["id"]),
            )
            print(f"    sending {len(events)} event(s)")
            for event in events:
                await ws.send(json.dumps({"type": "event", "event": event}))
        await ws.send(json.dumps({"type": "sync_complete", "group": group}))

    async def handle_summary(self, ws, group: str) -> None:
        """Handle summary request for cross-relay verification."""
        print(f"    summary: group={group[:16]}...")
        if group in self.groups:
            events = self.groups[group]
            all_parents = set()
            for event in events.values():
                all_parents.update(event.get("parents", []))
            tips = sorted(set(events.keys()) - all_parents)
            print(f"    responding: {len(events)} events, {len(tips)} tips")
            await ws.send(
                json.dumps(
                    {
                        "type": "summary",
                        "group": group,
                        "count": len(events),
                        "tips": tips,
                    }
                )
            )
        else:
            print(f"    responding: group not found")
            await ws.send(
                json.dumps(
                    {
                        "type": "summary",
                        "group": group,
                        "count": 0,
                        "tips": [],
                    }
                )
            )

    async def handler(self, ws) -> None:
        """Handle a single WebSocket connection."""
        self.connections.add(ws)
        try:
            async for raw_msg in ws:
                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    await ws.send(
                        json.dumps({"type": "error", "message": "invalid json"})
                    )
                    print(f"  [REQUEST] invalid json from {ws.remote_address}")
                    continue

                action = msg.get("action")
                print(f"  [REQUEST] {action} from {ws.remote_address}")

                try:
                    if action == "subscribe":
                        await self.handle_subscribe(ws, msg["group"])
                    elif action == "publish":
                        await self.handle_publish(ws, msg["event"])
                    elif action == "get":
                        await self.handle_get(ws, msg["id"], msg.get("group"))
                    elif action == "sync":
                        await self.handle_sync(ws, msg["group"], msg.get("since", 0))
                    elif action == "summary":
                        await self.handle_summary(ws, msg["group"])
                    else:
                        await ws.send(
                            json.dumps(
                                {
                                    "type": "error",
                                    "message": f"unknown action: {action}",
                                }
                            )
                        )
                except Exception as e:
                    await ws.send(json.dumps({"type": "error", "message": str(e)}))
        finally:
            self.connections.discard(ws)
            for group_subs in self.subscriptions.values():
                group_subs.discard(ws)

    async def start(self) -> None:
        """Start the relay server."""
        print(f"FERN Relay Server listening on ws://{self.host}:{self.port}")
        print(f"Storage: {self.storage_dir}")
        async with websockets.serve(self.handler, self.host, self.port) as server:
            await asyncio.Future()  # Run forever


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--host", default="0.0.0.0", help="Bind host")
@click.option("--port", default=8787, help="Bind port")
@click.option("--storage", default="./relay_data", help="Storage directory")
def cli(ctx, host, port, storage):
    """FERN Relay Server."""
    if ctx.invoked_subcommand is None:
        server = RelayServer(host=host, port=port, storage_dir=storage)
        asyncio.run(server.start())


@cli.command()
@click.option("--host", default="0.0.0.0", help="Bind host")
@click.option("--port", default=8787, help="Bind port")
@click.option("--storage", default="./relay_data", help="Storage directory")
def serve(host: str, port: int, storage: str) -> None:
    """Start the FERN relay server."""
    server = RelayServer(host=host, port=port, storage_dir=storage)
    asyncio.run(server.start())


@cli.command()
@click.option("--storage", default="./relay_data", help="Storage directory")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
def wipe(storage: str, yes: bool) -> None:
    """Delete all stored events, keeping nothing."""
    storage_dir = Path(storage)
    if not storage_dir.exists():
        click.echo("Storage directory does not exist.")
        return

    group_dirs = [d for d in storage_dir.iterdir() if d.is_dir() and len(d.name) == 64]
    if not group_dirs:
        click.echo("No stored events to wipe.")
        return

    total_events = 0
    for d in group_dirs:
        events_file = d / "events.json"
        if events_file.exists():
            with open(events_file) as f:
                total_events += len(json.load(f))

    if not yes:
        click.echo(
            f"This will delete {len(group_dirs)} group(s) with {total_events} total event(s) from {storage_dir}."
        )
        if not click.confirm("Continue?"):
            return

    for d in group_dirs:
        shutil.rmtree(d)
    click.echo(f"Wiped {len(group_dirs)} group(s) with {total_events} event(s).")


def main():
    cli()


if __name__ == "__main__":
    main()
