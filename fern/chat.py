"""FERN Chat App - Web-based chat client with client-side signing."""

import asyncio
import json
import os
import time
from pathlib import Path

import click
from aiohttp import web, WSMsgType

from .dag import ClientStorage
from .events import verify_event
from .storage import get_storage_path


class RelayConnection:
    """Manages a WebSocket connection to a relay for a specific group."""

    def __init__(self, relay_url: str, group_pubkey: str):
        self.relay_url = relay_url
        self.group_pubkey = group_pubkey
        self.ws = None
        self.connected = False
        self._read_task = None
        self._on_event = None
        self._on_log = None

    async def connect(self, on_event, on_log):
        self._on_event = on_event
        self._on_log = on_log
        try:
            import websockets

            self.ws = await websockets.connect(self.relay_url)
            self.connected = True
            await on_log("relay", f"Connected to {self.relay_url}")
            # Subscribe
            await self.ws.send(
                json.dumps(
                    {
                        "action": "subscribe",
                        "group": self.group_pubkey,
                    }
                )
            )
            await on_log(
                "relay",
                f"Subscribed to {self.group_pubkey[:12]}... on {self.relay_url}",
            )
            # Read loop
            self._read_task = asyncio.create_task(self._read_loop())
        except Exception as e:
            await on_log("error", f"Failed to connect to {self.relay_url}: {e}")

    async def _read_loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg.get("type") == "event" and self._on_event:
                    await self._on_event(msg["event"], self.relay_url)
                elif msg.get("type") == "ok":
                    await self._on_log("relay", f"OK: {msg.get('id', '?')[:12]}...")
                elif msg.get("type") == "error":
                    await self._on_log(
                        "error", f"Relay error: {msg.get('message', '?')}"
                    )
        except Exception as e:
            if self.connected:
                await self._on_log("error", f"Disconnected from {self.relay_url}: {e}")
        finally:
            self.connected = False

    async def publish(self, event: dict):
        if self.ws and self.connected:
            try:
                await self.ws.send(
                    json.dumps(
                        {
                            "action": "publish",
                            "event": event,
                        }
                    )
                )
                await self._on_log(
                    "publish", f"Published {event['type']} to {self.relay_url}"
                )
            except Exception as e:
                await self._on_log("error", f"Publish failed to {self.relay_url}: {e}")

    async def sync(self, since: int = 0):
        if self.ws and self.connected:
            try:
                await self.ws.send(
                    json.dumps(
                        {
                            "action": "sync",
                            "group": self.group_pubkey,
                            "since": since,
                        }
                    )
                )
                await self._on_log(
                    "sync", f"Requested sync from {self.relay_url} (since={since})"
                )
            except Exception as e:
                await self._on_log("error", f"Sync failed from {self.relay_url}: {e}")

    async def close(self):
        self.connected = False
        if self._read_task:
            self._read_task.cancel()
        if self.ws:
            await self.ws.close()


class ChatSession:
    """Represents a browser client session."""

    def __init__(self, ws: web.WebSocketResponse, storage: ClientStorage):
        self.ws = ws
        self.storage = storage
        self.relay_connections: dict[str, RelayConnection] = {}
        self.group_pubkey: str | None = None

    async def send(self, data: dict):
        try:
            await self.ws.send_json(data)
        except Exception:
            pass

    async def log(self, kind: str, message: str):
        await self.send(
            {
                "type": "log",
                "kind": kind,
                "message": message,
                "ts": int(time.time()),
            }
        )

    async def handle_message(self, msg: dict):
        action = msg.get("action")

        if action == "connect_relay":
            relay_url = msg["relay"]
            group_pubkey = msg["group"]
            self.group_pubkey = group_pubkey
            conn = RelayConnection(relay_url, group_pubkey)
            self.relay_connections[relay_url] = conn
            await conn.connect(
                on_event=self._on_relay_event,
                on_log=self.log,
            )

        elif action == "disconnect_relay":
            relay_url = msg["relay"]
            if relay_url in self.relay_connections:
                await self.relay_connections[relay_url].close()
                del self.relay_connections[relay_url]
                await self.log("relay", f"Disconnected from {relay_url}")

        elif action == "publish":
            event = msg["event"]
            relay_url = msg.get("relay")
            await self.log(
                "publish", f"Publishing {event['type']} (id={event['id'][:12]}...)"
            )

            # Verify before publishing
            valid, reason = verify_event(event)
            if not valid:
                await self.log("error", f"Event rejected: {reason}")
                await self.send(
                    {"type": "error", "message": f"Invalid event: {reason}"}
                )
                return

            # Store locally
            dag = self.storage.get_group_dag(event["group"])
            dag.add_event(event)

            # Publish to specified relay or all
            if relay_url and relay_url in self.relay_connections:
                await self.relay_connections[relay_url].publish(event)
            else:
                for conn in self.relay_connections.values():
                    await conn.publish(event)

            await self.send({"type": "ok", "id": event["id"]})

        elif action == "sync":
            relay_url = msg.get("relay")
            since = msg.get("since", 0)
            if relay_url and relay_url in self.relay_connections:
                await self.relay_connections[relay_url].sync(since)
            else:
                for conn in self.relay_connections.values():
                    await conn.sync(since)

        elif action == "load_local":
            group_pubkey = msg["group"]
            dag = self.storage.get_group_dag(group_pubkey)
            events = dag.get_all_events()
            for event in events:
                await self.send(
                    {
                        "type": "event",
                        "event": event,
                        "relay": "local",
                    }
                )
            await self.log("local", f"Loaded {len(events)} events from local cache")

    async def _on_relay_event(self, event: dict, relay_url: str):
        # Store locally
        if self.group_pubkey:
            dag = self.storage.get_group_dag(self.group_pubkey)
            ok, reason = dag.add_event(event)
            if not ok:
                eid = event.get("id", "?")[:16]
                await self.log(
                    "error", f"Invalid event from {relay_url}: {reason} ({eid}...)"
                )
        # Forward to browser
        await self.send(
            {
                "type": "event",
                "event": event,
                "relay": relay_url,
            }
        )

    async def close(self):
        for conn in self.relay_connections.values():
            await conn.close()
        self.relay_connections.clear()


class ChatApp:
    """Web-based FERN chat application."""

    def __init__(self, storage_dir: str, host: str = "127.0.0.1", port: int = 8080):
        self.storage = ClientStorage(os.path.expanduser(storage_dir))
        self.host = host
        self.port = port
        self.sessions: list[ChatSession] = []
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_static("/static", self._get_static_dir())
        self.app.router.add_get("/api/groups", self.handle_groups)
        self.app.router.add_get("/api/groups/{group_pubkey}", self.handle_group_events)
        self.app.router.add_get(
            "/api/groups/{group_pubkey}/state", self.handle_group_state
        )
        self.app.router.add_post("/api/groups", self.handle_create_group)
        self.app.router.add_get("/ws", self.handle_ws)

    def _get_static_dir(self) -> Path:
        return Path(__file__).parent / "static"

    async def handle_index(self, request: web.Request) -> web.Response:
        index_path = self._get_static_dir() / "chat.html"
        return web.FileResponse(index_path)

    async def handle_groups(self, request: web.Request) -> web.Response:
        groups = self.storage.list_groups()
        result = []
        for gpub in groups:
            dag = self.storage.get_group_dag(gpub)
            state = dag.get_state()
            result.append(
                {
                    "pubkey": gpub,
                    "name": state.metadata.get("name", "unnamed"),
                    "description": state.metadata.get("description", ""),
                    "public": state.public,
                    "event_count": dag.count,
                    "member_count": len(state.joined),
                    "members": sorted(state.members),
                    "joined": sorted(state.joined),
                    "mods": sorted(state.mods),
                    "relays": state.relays,
                }
            )
        return web.json_response(result)

    async def handle_group_events(self, request: web.Request) -> web.Response:
        group_pubkey = request.match_info["group_pubkey"]
        dag = self.storage.get_group_dag(group_pubkey)
        since = int(request.query.get("since", 0))
        if since:
            events = dag.get_events_since(since)
        else:
            events = dag.get_all_events()
        return web.json_response(events)

    async def handle_group_state(self, request: web.Request) -> web.Response:
        group_pubkey = request.match_info["group_pubkey"]
        dag = self.storage.get_group_dag(group_pubkey)
        state = dag.get_state()
        return web.json_response(
            {
                "pubkey": group_pubkey,
                "name": state.metadata.get("name", "unnamed"),
                "description": state.metadata.get("description", ""),
                "members": sorted(state.members),
                "mods": sorted(state.mods),
                "relays": state.relays,
                "event_count": dag.count,
            }
        )

    async def handle_create_group(self, request: web.Request) -> web.Response:
        data = await request.json()
        event = data.get("event")
        if not event:
            return web.json_response({"error": "No event provided"}, status=400)

        # Verify the genesis event
        valid, reason = verify_event(event)
        if not valid:
            return web.json_response({"error": f"Invalid event: {reason}"}, status=400)

        # Store locally
        dag = self.storage.get_group_dag(event["group"])
        dag.add_event(event)

        return web.json_response({"ok": True, "group": event["group"]})

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        session = ChatSession(ws, self.storage)
        self.sessions.append(session)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await session.handle_message(data)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        except Exception:
            pass
        finally:
            await session.close()
            self.sessions.discard(session) if hasattr(
                self.sessions, "discard"
            ) else None
            if session in self.sessions:
                self.sessions.remove(session)

        return ws

    async def start(self):
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()

        print(f"FERN Chat running at http://{self.host}:{self.port}")
        print(f"Storage: {os.path.expanduser(self.storage.base_dir)}")
        print("Press Ctrl+C to stop.")

        try:
            await asyncio.Event().wait()
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            for session in list(self.sessions):
                await session.close()
            await runner.cleanup()


@click.command()
@click.option("--host", default="127.0.0.1", help="Bind host")
@click.option("--port", default=8080, help="Bind port")
@click.option("--storage", default=None, help="Storage directory")
def main(host: str, port: int, storage: str | None):
    """FERN Chat - Web-based chat client."""
    storage_dir = storage or get_storage_path("FERN_CHAT_STORAGE")
    app = ChatApp(storage_dir, host=host, port=port)
    asyncio.run(app.start())


if __name__ == "__main__":
    main()
