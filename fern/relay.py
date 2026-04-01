"""Relay functions for FERN protocol.

One-shot functions for publish, sync, summary, etc — open a WebSocket,
send a request, read the response, close. Used by both CLI and web chat.

The `subscribe` function is the exception — it opens a persistent connection
that streams events until cancelled.
"""

import asyncio
import json

import websockets

from .events import Event

_DEFAULT_TIMEOUT = 1.5


async def fetch_summary(relay_url: str, group_pubkey: str) -> dict | None:
    """Fetch summary (count + tips) from a relay."""
    try:
        async with asyncio.timeout(_DEFAULT_TIMEOUT):
            async with websockets.connect(relay_url) as ws:
                await ws.send(json.dumps({"action": "summary", "group": group_pubkey}))
                msg = json.loads(await ws.recv())
                if msg.get("type") == "summary":
                    return msg
    except (asyncio.TimeoutError, Exception):
        pass
    return None


async def fetch_events(
    relay_url: str, group_pubkey: str, since: int = 0
) -> list[Event]:
    """Fetch events from a relay since timestamp."""
    events: list[Event] = []
    try:
        async with websockets.connect(relay_url) as ws:
            await ws.send(
                json.dumps({"action": "sync", "group": group_pubkey, "since": since})
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


async def publish(relay_url: str, event: Event) -> dict | None:
    """Publish an event to a relay. Returns response dict or None."""
    try:
        async with asyncio.timeout(_DEFAULT_TIMEOUT):
            async with websockets.connect(relay_url) as ws:
                await ws.send(json.dumps({"action": "publish", "event": event}))
                return json.loads(await ws.recv())
    except (asyncio.TimeoutError, Exception):
        pass
    return None


async def fetch_event(relay_url: str, event_id: str) -> dict | None:
    """Fetch a specific event by ID from a relay."""
    try:
        async with asyncio.timeout(_DEFAULT_TIMEOUT):
            async with websockets.connect(relay_url) as ws:
                await ws.send(json.dumps({"action": "get", "id": event_id}))
                msg = json.loads(await ws.recv())
                if msg["type"] == "event":
                    return msg["event"]
                if msg["type"] == "not_found":
                    return None
    except (asyncio.TimeoutError, Exception):
        pass
    return None


async def fetch_genesis(relay_url: str, group_pubkey: str) -> dict | None:
    """Fetch the genesis event for a group from a relay."""
    try:
        async with asyncio.timeout(_DEFAULT_TIMEOUT):
            async with websockets.connect(relay_url) as ws:
                await ws.send(
                    json.dumps({"action": "get_genesis", "group": group_pubkey})
                )
                msg = json.loads(await ws.recv())
                if msg["type"] == "event":
                    return msg["event"]
                if msg["type"] == "not_found":
                    return None
    except (asyncio.TimeoutError, Exception):
        pass
    return None


async def subscribe(
    relay_url: str,
    group_pubkey: str,
    on_event,
) -> None:
    """Subscribe to a group on a relay. Streams events via on_event callback.

    on_event is called as on_event(event, relay_url) for each incoming event.
    Runs until the connection drops or is cancelled. Callers should wrap in a
    retry loop if desired.
    """
    async with websockets.connect(relay_url) as ws:
        await ws.send(json.dumps({"action": "subscribe", "group": group_pubkey}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") == "event":
                await on_event(msg["event"], relay_url)
