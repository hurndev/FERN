"""RelayClient - Unified WebSocket client for FERN protocol.

Provides a single RelayClient class used by both the CLI client and web chat.
Supports one-shot connections (classmethods) for CLI and persistent connections
(instances) for the chat app.
"""

import asyncio
import json
from typing import Callable

import websockets

from .events import Event


class RelayClient:
    """WebSocket client for a single relay URL.

    Supports two usage patterns:
    - One-shot: call classmethod `RelayClient.{action}()` — opens a
      connection, sends one request, returns the response, closes.
    - Persistent: instantiate `RelayClient(url)`, call `connect()` once,
      then call action methods repeatedly on the same connection.
    """

    def __init__(self, relay_url: str, group_pubkey: str | None = None):
        self.relay_url = relay_url
        self.group_pubkey = group_pubkey
        self.ws: websockets.WebSocketClientProtocol | None = None
        self.connected: bool = False
        self._pending: dict[str, asyncio.Future] = {}
        self._connected_event: asyncio.Event = asyncio.Event()
        self._read_task: asyncio.Task | None = None
        self._retry_task: asyncio.Task | None = None
        self._on_event: Callable | None = None
        self._on_log: Callable | None = None
        self._on_sync_complete: Callable | None = None
        self._pending_syncs: list[int] = []
        self._subscribed: bool = False

    async def connect(
        self,
        on_event: Callable | None = None,
        on_log: Callable | None = None,
        subscribe: bool = True,
    ) -> None:
        """Establish persistent connection. Runs _connect_with_retry in background."""
        self._on_event = on_event
        self._on_log = on_log
        self._retry_task = asyncio.create_task(
            self._connect_with_retry(subscribe=subscribe)
        )

    async def _connect_with_retry(self, subscribe: bool = True) -> None:
        """Loop: connect -> start read loop -> on failure sleep 60s -> retry."""
        while True:
            try:
                self.ws = await websockets.connect(self.relay_url)
                self.connected = True
                self._subscribed = False
                if self._on_log:
                    await self._on_log("relay", f"Connected to {self.relay_url}")
                if subscribe and self.group_pubkey:
                    await self._send_subscribe()
                self._read_task = asyncio.create_task(self._read_loop())
                self._retry_task = None
                self._connected_event.set()
                for since in self._pending_syncs:
                    await self.sync(since)
                self._pending_syncs.clear()
                return
            except Exception as e:
                if self._on_log:
                    await self._on_log(
                        "error", f"Failed to connect to {self.relay_url}: {e}"
                    )
            await asyncio.sleep(60)

    async def _send_subscribe(self) -> None:
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
            if self._on_log:
                await self._on_log(
                    "relay",
                    f"Subscribed to {self.group_pubkey[:12]}... on {self.relay_url}",
                )

    async def _read_loop(self) -> None:
        """Read messages, dispatch to callbacks or _pending futures."""
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg.get("type") == "event" and self._on_event:
                    await self._on_event(msg["event"], self.relay_url)
                elif msg.get("type") == "sync_complete":
                    if self._on_sync_complete:
                        await self._on_sync_complete(self.relay_url)
                elif msg.get("type") == "summary":
                    fid = "__summary__"
                    if fid in self._pending:
                        self._pending.pop(fid).set_result(msg)
                elif msg.get("type") == "ok" and msg.get("id"):
                    fid = msg["id"]
                    if fid in self._pending:
                        self._pending.pop(fid).set_result(msg)
                elif msg.get("type") == "error" and msg.get("id"):
                    fid = msg["id"]
                    if fid in self._pending:
                        self._pending.pop(fid).set_result(msg)
                elif msg.get("type") == "ok":
                    if self._on_log:
                        await self._on_log("relay", f"OK: {msg.get('id', '?')[:12]}...")
                elif msg.get("type") == "error":
                    if self._on_log:
                        await self._on_log(
                            "error", f"Relay error: {msg.get('message', '?')}"
                        )
        except Exception as e:
            if self.connected:
                if self._on_log:
                    await self._on_log(
                        "error", f"Disconnected from {self.relay_url}: {e}"
                    )
        finally:
            was_connected = self.connected
            self.connected = False
            self._connected_event.clear()
            for fut in self._pending.values():
                fut.cancel()
            self._pending.clear()
            if was_connected and self._retry_task is None:
                if self._on_log:
                    await self._on_log(
                        "relay", f"Reconnecting to {self.relay_url} in 60s..."
                    )
                self._retry_task = asyncio.create_task(
                    self._connect_with_retry(subscribe=False)
                )

    async def _do(self, action: str, payload: dict) -> dict | None:
        """Send a request and wait for its correlated response. Returns None on timeout."""
        if self.ws and self.connected:
            try:
                import uuid

                event_id = str(uuid.uuid4())
                fut = asyncio.Future()
                self._pending[event_id] = fut

                await self.ws.send(json.dumps({"action": action, **payload}))

                try:
                    response = await asyncio.wait_for(fut, timeout=5.0)
                    return response
                except asyncio.TimeoutError:
                    if self._on_log:
                        await self._on_log(
                            "error", f"Request timed out to {self.relay_url}"
                        )
                    self._pending.pop(event_id, None)
                    return None
            except Exception as e:
                if self._on_log:
                    await self._on_log(
                        "error", f"Request failed to {self.relay_url}: {e}"
                    )
                self._pending.pop(event_id, None)
        return None

    async def summary(self) -> dict | None:
        """Fetch summary (count + tips) from the relay."""
        if not (self.ws and self.connected):
            return None
        try:
            fut = asyncio.Future()
            self._pending["__summary__"] = fut
            await self.ws.send(
                json.dumps(
                    {
                        "action": "summary",
                        "group": self.group_pubkey,
                    }
                )
            )
            msg = await asyncio.wait_for(fut, timeout=3.0)
            if msg.get("type") == "summary":
                return msg
        except Exception as e:
            if self._on_log:
                await self._on_log(
                    "error", f"Summary failed from {self.relay_url}: {e}"
                )
        finally:
            self._pending.pop("__summary__", None)
        return None

    async def sync(self, since: int = 0) -> list[dict]:
        """Fetch events from relay since timestamp. Returns list of events."""
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
                if self._on_log:
                    await self._on_log(
                        "sync",
                        f"Requested sync from {self.relay_url} (since={since})",
                    )
                return []
            except Exception as e:
                if self._on_log:
                    await self._on_log(
                        "error", f"Sync failed from {self.relay_url}: {e}"
                    )
        return []

    async def publish(self, event: Event) -> dict | None:
        """Publish an event and wait for relay response. Returns response dict or None."""
        if self.ws and self.connected:
            try:
                event_id = event["id"]
                fut = asyncio.Future()
                self._pending[event_id] = fut

                await self.ws.send(
                    json.dumps(
                        {
                            "action": "publish",
                            "event": event,
                        }
                    )
                )
                if self._on_log:
                    await self._on_log(
                        "publish", f"Published {event['type']} to {self.relay_url}"
                    )

                try:
                    response = await asyncio.wait_for(fut, timeout=5.0)
                    return response
                except asyncio.TimeoutError:
                    if self._on_log:
                        await self._on_log(
                            "error", f"Publish timed out to {self.relay_url}"
                        )
                    self._pending.pop(event_id, None)
                    return None
            except (Exception, asyncio.CancelledError):
                self._pending.pop(event["id"], None)
        return None

    async def get(self, event_id: str) -> dict | None:
        """Fetch a specific event by ID. Returns event dict or None."""
        if self.ws and self.connected:
            try:
                await self.ws.send(
                    json.dumps(
                        {
                            "action": "get",
                            "id": event_id,
                            "group": self.group_pubkey,
                        }
                    )
                )
                async for raw in self.ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "event":
                        return msg["event"]
                    elif msg.get("type") == "not_found":
                        return None
            except Exception as e:
                if self._on_log:
                    await self._on_log(
                        "error", f"Get failed from {self.relay_url}: {e}"
                    )
        return None

    async def subscribe(self) -> None:
        """Send subscribe action for the group."""
        await self._send_subscribe()

    @property
    def is_connected(self) -> bool:
        return self.connected

    @property
    def is_subscribed(self) -> bool:
        return self._subscribed

    async def wait_connected(self, timeout: float = 10.0) -> bool:
        """Wait until the relay connection is established. Returns True if connected."""
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout)
            return self.connected
        except asyncio.TimeoutError:
            return False

    def set_on_sync_complete(self, callback: Callable) -> None:
        """Set callback for sync_complete message."""
        self._on_sync_complete = callback

    async def close(self) -> None:
        """Close the connection and cancel retry task."""
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

    _subscribed: bool = False

    # --- Classmethods (one-shot, used by CLI) ---

    @classmethod
    async def fetch_summary(cls, relay_url: str, group_pubkey: str) -> dict | None:
        """Fetch summary (count + tips) from a relay. Returns summary dict or None."""
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
        except (asyncio.TimeoutError, Exception):
            pass
        return None

    @classmethod
    async def fetch_events(
        cls, relay_url: str, group_pubkey: str, since: int = 0
    ) -> list[Event]:
        """Fetch all events from a relay since timestamp. Returns list of events."""
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
        except Exception:
            pass
        return events

    @classmethod
    async def fetch_publish(cls, relay_url: str, event: Event) -> dict | None:
        """Publish an event to a relay (one-shot). Returns response dict or None."""
        try:
            async with asyncio.timeout(1.5):
                async with websockets.connect(relay_url) as ws:
                    await ws.send(json.dumps({"action": "publish", "event": event}))
                    return json.loads(await ws.recv())
        except (asyncio.TimeoutError, Exception):
            pass
        return None

    @classmethod
    async def fetch_event(cls, relay_url: str, event_id: str) -> dict | None:
        """Fetch a specific event by ID from a relay. Returns event dict or None."""
        try:
            async with asyncio.timeout(1.5):
                async with websockets.connect(relay_url) as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "action": "get",
                                "id": event_id,
                            }
                        )
                    )
                    msg = json.loads(await ws.recv())
                    if msg["type"] == "event":
                        return msg["event"]
                    elif msg["type"] == "not_found":
                        return None
        except (asyncio.TimeoutError, Exception):
            pass
        return None

    @classmethod
    async def fetch_genesis(cls, relay_url: str, group_pubkey: str) -> dict | None:
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
        except (asyncio.TimeoutError, Exception):
            pass
        return None
