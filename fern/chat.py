"""FERN Chat App - Web-based chat client with client-side signing."""

import asyncio
import json
import logging
import os
import time
import traceback
from pathlib import Path

import click
from aiohttp import web, WSMsgType

logger = logging.getLogger(__name__)

from .dag import ClientStorage
from .events import Event
from .events import verify_event
from .relay import fetch_events
from .relay import fetch_summary
from .relay import publish_to_all
from .relay import subscribe_with_retry
from .storage import resolve_fern_dir
from .sync import decide_sync_action


class ChatSession:
    """Represents a browser client session."""

    def __init__(self, ws: web.WebSocketResponse, storage: ClientStorage):
        self.ws = ws
        self.storage = storage
        self.relay_urls: list[str] = []
        self.group_pubkey: str | None = None
        self._subscribe_tasks: list[asyncio.Task] = []

    async def send(self, data: dict):
        try:
            await self.ws.send_json(data)
        except Exception:
            logger.debug(
                "Failed to send to browser (connection closed?)", exc_info=True
            )

    async def log(self, kind: str, message: str):
        logger.info("[%s] %s", kind, message)
        await self.send(
            {
                "type": "log",
                "kind": kind,
                "message": message,
                "ts": int(time.time()),
            }
        )

    async def error(self, message: str, event_id: str | None = None):
        logger.error("[error] %s (event_id=%s)", message, event_id)
        payload: dict = {
            "type": "error",
            "message": message,
        }
        if event_id:
            payload["event_id"] = event_id
        await self.send(payload)

    async def handle_message(self, msg: dict):
        action = msg.get("action")

        if action == "set_relays":
            self.relay_urls = msg["relays"]
            self.group_pubkey = msg.get("group")
            await self.log("relay", f"Relays set: {', '.join(self.relay_urls)}")

        elif action == "publish":
            await self._handle_publish(msg)

        elif action == "sync":
            await self._smart_sync()

        elif action == "subscribe":
            await self._start_subscriptions()

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

    async def _handle_publish(self, msg: dict):
        event = msg["event"]
        target_relay = msg.get("relay")
        mode = msg.get("mode", "both")
        await self.log(
            "publish",
            f"Publishing {event['type']} (id={event['id'][:12]}..., mode={mode})",
        )

        valid, reason = verify_event(event)
        if not valid:
            await self.error(f"Event rejected: {reason}", event["id"])
            return

        if mode == "local_only":
            dag = self.storage.get_group_dag(event["group"])
            ok, reason = dag.add_event(event)
            if ok:
                await self.send({"type": "ok", "id": event["id"]})
            else:
                await self.error(
                    f"Failed to store event locally: {reason}", event["id"]
                )
            return

        relay_urls = [target_relay] if target_relay else self.relay_urls
        if not relay_urls:
            await self.error(
                "No relays configured. Your message has been saved in browser.",
                event["id"],
            )
            return

        results = await publish_to_all(relay_urls, event)

        for url, r in results.items():
            if isinstance(r, Exception):
                await self.log(
                    "error",
                    f"publish({url}) raised {type(r).__name__}: {r}",
                )

        published = any(
            isinstance(r, dict) and r.get("type") == "ok" for r in results.values()
        )

        if mode == "relay_only":
            if published:
                await self.send({"type": "ok", "id": event["id"]})
            else:
                await self.error("Failed to publish.", event["id"])
            return

        if published:
            dag = self.storage.get_group_dag(event["group"])
            ok, reason = dag.add_event(event)
            if ok or reason == "duplicate":
                await self.send({"type": "event", "event": event, "relay": "local"})
                await self.send({"type": "ok", "id": event["id"]})
            else:
                await self.error(f"Failed to store event: {reason}", event["id"])
        else:
            await self.error(
                "Failed to publish. Your message has been saved in browser.",
                event["id"],
            )

    async def _smart_sync(self):
        if not self.group_pubkey:
            await self.error("No group selected")
            return

        if not self.relay_urls:
            await self.error("No relays configured")
            return

        dag = self.storage.get_group_dag(self.group_pubkey)
        local_event_ids = set(dag.events.keys())
        local_latest_ts = max((e["ts"] for e in dag.events.values()), default=0)

        summaries: dict[str, dict] = {}
        summary_results = await asyncio.gather(
            *(fetch_summary(url, self.group_pubkey) for url in self.relay_urls),
            return_exceptions=True,
        )
        for url, s in zip(self.relay_urls, summary_results):
            if isinstance(s, dict):
                summaries[url] = s

        decision = decide_sync_action(local_event_ids, local_latest_ts, summaries)

        if decision.action == "full":
            await self.log("sync", "No local events - full sync required")
            for url in self.relay_urls:
                await self._sync_one(url, 0)
        elif decision.action == "skip":
            await self.log(
                "sync",
                f"Already in sync ({len(local_event_ids)} local events) - skipping",
            )
            for url in self.relay_urls:
                await self.send({"type": "sync_complete", "relay": url})
        else:
            await self.log("sync", f"Incremental sync since={decision.since}")
            for url in self.relay_urls:
                await self._sync_one(url, decision.since)

    async def _sync_one(self, relay_url: str, since: int):
        try:
            events = await fetch_events(relay_url, self.group_pubkey, since)
            new_count = 0
            if self.group_pubkey:
                dag = self.storage.get_group_dag(self.group_pubkey)
                for event in events:
                    ok, reason = dag.add_event(event)
                    if ok:
                        new_count += 1
                        await self.send(
                            {"type": "event", "event": event, "relay": relay_url}
                        )
            await self.log("sync", f"Synced {new_count} new events from {relay_url}")
            await self.send({"type": "sync_complete", "relay": relay_url})
        except Exception as e:
            await self.log(
                "error", f"Sync failed from {relay_url}: {type(e).__name__}: {e}"
            )

    async def _start_subscriptions(self):
        for task in self._subscribe_tasks:
            task.cancel()
        self._subscribe_tasks.clear()

        if not self.group_pubkey:
            await self.error("No group selected")
            return

        def on_error(url, exc):
            task = asyncio.create_task(
                self.log(
                    "error",
                    f"Subscription to {url} disconnected: {type(exc).__name__}: {exc}",
                )
            )
            task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )

        def on_reconnect(url):
            asyncio.create_task(
                self.log("relay", f"Reconnecting to {url} in 60s...")
            ).add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

        for url in self.relay_urls:
            task = asyncio.create_task(
                subscribe_with_retry(
                    url,
                    self.group_pubkey,
                    self._on_relay_event,
                    on_error=on_error,
                    on_reconnect=on_reconnect,
                )
            )
            self._subscribe_tasks.append(task)

    async def _on_relay_event(self, event: Event, relay_url: str):
        if self.group_pubkey:
            dag = self.storage.get_group_dag(self.group_pubkey)
            ok, reason = dag.add_event(event)
            if not ok:
                if reason != "duplicate":
                    eid = event.get("id", "?")[:16]
                    await self.log(
                        "error",
                        f"Invalid event from {relay_url}: {reason} ({eid}...)",
                    )
                return
        await self.send(
            {
                "type": "event",
                "event": event,
                "relay": relay_url,
            }
        )

    async def close(self):
        for task in self._subscribe_tasks:
            task.cancel()
        if self._subscribe_tasks:
            await asyncio.gather(*self._subscribe_tasks, return_exceptions=True)
        self._subscribe_tasks.clear()


async def _safe_handle(session: ChatSession, data: dict):
    action = data.get("action", "?")
    try:
        await session.handle_message(data)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("Unhandled error handling '%s': %s\n%s", action, e, tb)
        await session.send(
            {
                "type": "error",
                "message": f"Internal error handling '{action}': {type(e).__name__}: {e}",
            }
        )


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

        valid, reason = verify_event(event)
        if not valid:
            return web.json_response({"error": f"Invalid event: {reason}"}, status=400)

        dag = self.storage.get_group_dag(event["group"])
        ok, reason = dag.add_event(event)
        if not ok and reason != "duplicate":
            return web.json_response(
                {"error": f"Failed to store event: {reason}"}, status=400
            )

        return web.json_response({"ok": True, "group": event["group"]})

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        session = ChatSession(ws, self.storage)
        self.sessions.append(session)
        tasks: set[asyncio.Task] = set()

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    task = asyncio.create_task(_safe_handle(session, data))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    if msg.type == WSMsgType.ERROR:
                        logger.warning(
                            "WebSocket error from %s: %s",
                            request.remote,
                            ws.exception(),
                        )
                    break
        except Exception:
            logger.error("WebSocket loop error", exc_info=True)
        finally:
            for t in tasks:
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await session.close()
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    app = ChatApp(str(fern_dir), host=host, port=port)
    asyncio.run(app.start())


if __name__ == "__main__":
    main()
