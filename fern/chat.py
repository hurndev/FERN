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
from .storage import resolve_fern_dir


class RelayConnection:
    """Manages a WebSocket connection to a relay for a specific group."""

    def __init__(self, relay_url: str, group_pubkey: str, subscribe: bool = True):
        self.relay_url = relay_url
        self.group_pubkey = group_pubkey
        self.subscribe = subscribe
        self.ws = None
        self.connected = False
        self._read_task = None
        self._on_event = None
        self._on_log = None
        self._on_sync_complete = None
        self._subscribed = False
        self._pending_responses: dict[str, asyncio.Future] = {}

    async def connect(self, on_event, on_log):
        self._on_event = on_event
        self._on_log = on_log
        self._retry_task = asyncio.create_task(self._connect_with_retry())

    async def _connect_with_retry(self):
        """Connect with automatic retry every 60 seconds if relay is down."""
        import websockets

        while True:
            try:
                self.ws = await websockets.connect(self.relay_url)
                self.connected = True
                await self._on_log("relay", f"Connected to {self.relay_url}")
                if self.subscribe:
                    await self._send_subscribe()
                self._read_task = asyncio.create_task(self._read_loop())
                self._retry_task = None
                return
            except Exception as e:
                await self._on_log(
                    "error", f"Failed to connect to {self.relay_url}: {e}"
                )
                if self._on_sync_complete:
                    await self._on_sync_complete(self.relay_url)
            await asyncio.sleep(60)

    async def _send_subscribe(self):
        """Send subscribe action to relay."""
        if self.ws and self.connected and not self._subscribed:
            await self.ws.send(
                json.dumps(
                    {
                        "action": "subscribe",
                        "group": self.group_pubkey,
                    }
                )
            )
            self._subscribed = True
            await self._on_log(
                "relay",
                f"Subscribed to {self.group_pubkey[:12]}... on {self.relay_url}",
            )

    async def _read_loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg.get("type") == "event" and self._on_event:
                    await self._on_event(msg["event"], self.relay_url)
                elif msg.get("type") == "sync_complete":
                    if self._on_sync_complete:
                        await self._on_sync_complete(self.relay_url)
                elif msg.get("type") == "ok" and msg.get("id"):
                    fid = msg["id"]
                    if fid in self._pending_responses:
                        self._pending_responses.pop(fid).set_result(msg)
                elif msg.get("type") == "error" and msg.get("id"):
                    fid = msg["id"]
                    if fid in self._pending_responses:
                        self._pending_responses.pop(fid).set_result(msg)
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
            was_connected = self.connected
            self.connected = False
            for fut in self._pending_responses.values():
                fut.cancel()
            self._pending_responses.clear()
            if was_connected and self._retry_task is None:
                await self._on_log(
                    "relay", f"Reconnecting to {self.relay_url} in 60s..."
                )
                self._retry_task = asyncio.create_task(self._connect_with_retry())

    def set_on_sync_complete(self, callback):
        """Set callback for sync_complete message."""
        self._on_sync_complete = callback

    def is_subscribed(self) -> bool:
        return self._subscribed

    async def publish(self, event: dict) -> dict | None:
        """Publish an event and wait for relay response. Returns response dict or None."""
        if self.ws and self.connected:
            try:
                event_id = event["id"]
                fut = asyncio.Future()
                self._pending_responses[event_id] = fut

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

                try:
                    response = await asyncio.wait_for(fut, timeout=5.0)
                    return response
                except asyncio.TimeoutError:
                    await self._on_log(
                        "error", f"Publish timed out to {self.relay_url}"
                    )
                    self._pending_responses.pop(event_id, None)
                    return None
            except Exception as e:
                await self._on_log("error", f"Publish failed to {self.relay_url}: {e}")
                self._pending_responses.pop(event["id"], None)
        return None

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
        if self._retry_task:
            self._retry_task.cancel()
            self._retry_task = None
        if self._read_task:
            self._read_task.cancel()
            self._read_task = None
        if self.ws:
            await self.ws.close()
            self.ws = None


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
            conn = RelayConnection(relay_url, group_pubkey, subscribe=False)
            self.relay_connections[relay_url] = conn

            async def on_sync_complete(url):
                await self.log("sync", f"Sync complete from {url}")
                await self.send({"type": "sync_complete", "relay": url})

            conn.set_on_sync_complete(on_sync_complete)
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
                    {
                        "type": "error",
                        "message": f"Invalid event: {reason}",
                        "event_id": event["id"],
                    }
                )
                return

            # Publish to relay FIRST, only store locally if relay accepts
            published = False
            if not self.relay_connections:
                await self.log("error", "No relay connections")
                await self.send(
                    {
                        "type": "error",
                        "message": "No relay connected. Your message has been saved in browser.",
                        "event_id": event["id"],
                    }
                )
                return

            if relay_url and relay_url in self.relay_connections:
                result = await self.relay_connections[relay_url].publish(event)
                published = result.get("type") == "ok" if result else False
            elif relay_url:
                # Specific relay requested but not connected - this is a definite failure
                published = False
            else:
                # No specific relay - publish to all and count successes
                success_count = 0
                fail_count = 0
                for conn in self.relay_connections.values():
                    result = await conn.publish(event)
                    if result and result.get("type") == "ok":
                        success_count += 1
                    else:
                        fail_count += 1
                # Consider it a success if at least one relay accepted
                published = success_count > 0

            if published:
                dag = self.storage.get_group_dag(event["group"])
                dag.add_event(event)
                await self.send({"type": "ok", "id": event["id"]})
            else:
                await self.send(
                    {
                        "type": "error",
                        "message": "Failed to publish. Your message has been saved in browser.",
                        "event_id": event["id"],
                    }
                )

        elif action == "sync":
            relay_url = msg.get("relay")
            since = msg.get("since", 0)
            if relay_url and relay_url in self.relay_connections:
                await self.relay_connections[relay_url].sync(since)
            else:
                for conn in self.relay_connections.values():
                    await conn.sync(since)

        elif action == "subscribe":
            relay_url = msg.get("relay")
            if relay_url and relay_url in self.relay_connections:
                await self.relay_connections[relay_url]._send_subscribe()
            else:
                for conn in self.relay_connections.values():
                    await conn._send_subscribe()

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
            if not ok and reason != "duplicate":
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
        self.app.router.add_get("/api/keys", self.handle_get_keys)
        self.app.router.add_post("/api/keys", self.handle_post_keys)
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

    async def handle_get_keys(self, request: web.Request) -> web.Response:
        """Get or generate user keypair. Returns {pub, priv} or just {pub} if existing."""
        from . import crypto

        key_path = self.storage.get_user_key_path()
        if os.path.exists(key_path):
            privkey = crypto.load_private_key(key_path)
            pubkey = crypto.public_key_from_private(privkey)
        else:
            privkey, pubkey = crypto.generate_keypair()
            crypto.save_keypair(privkey, key_path)

        return web.json_response({"pub": pubkey, "priv": privkey})

    async def handle_post_keys(self, request: web.Request) -> web.Response:
        """Import a private key from PEM. Body: {priv: pem_string}."""
        from . import crypto

        try:
            body = await request.json()
            priv_pem_str = body.get("priv", "")
            if not priv_pem_str:
                return web.json_response({"error": "no priv key provided"}, status=400)

            priv_pem_bytes = (
                priv_pem_str.encode() if isinstance(priv_pem_str, str) else priv_pem_str
            )
            privkey = crypto.load_private_key_from_pem(priv_pem_bytes)
            pubkey = crypto.public_key_from_private(privkey)
            key_path = self.storage.get_user_key_path()
            crypto.save_keypair(privkey, key_path)
            return web.json_response({"pub": pubkey})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

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
@click.option("--home", default=None, help="Home directory containing .fern folder")
@click.option("--host", default="127.0.0.1", help="Bind host")
@click.option("--port", default=8080, help="Bind port")
def main(home: str | None, host: str, port: int):
    """FERN Chat - Web-based chat client.

    Uses ~/.fern by default. Set FERN_TEST_USER to use /tmp/<user>/.fern
    instead. Use --home to specify a custom home directory.
    """
    fern_dir = resolve_fern_dir(home)
    app = ChatApp(str(fern_dir), host=host, port=port)
    asyncio.run(app.start())


if __name__ == "__main__":
    main()
