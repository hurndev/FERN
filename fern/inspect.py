"""FERN DAG Inspector - Web-based DAG visualization with real-time updates."""

import asyncio
import json
import os
import time
from pathlib import Path

import click
from aiohttp import web, WSMsgType

from .dag import ClientStorage, EventDAG
from .events import verify_event_id, verify_event_signature
from .storage import get_storage_path


def verify_event_full(event: dict) -> dict:
    """Returns verification dict."""
    id_ok = verify_event_id(event)
    signer = event["group"] if event["type"] == "group_genesis" else event["author"]
    sig_ok = verify_event_signature(event, signer)
    return {"id_valid": id_ok, "sig_valid": sig_ok, "valid": id_ok and sig_ok}


def event_to_dict(event: dict) -> dict:
    """Sanitize event for JSON serialization."""
    return {
        "id": event["id"],
        "type": event["type"],
        "group": event["group"],
        "author": event["author"],
        "parents": event.get("parents", []),
        "content": event["content"],
        "ts": event["ts"],
        "sig": event["sig"],
        "verification": verify_event_full(event),
    }


class WebVisualiser:
    """Web server for FERN DAG visualization."""

    def __init__(self, storage_dir: str, host: str = "127.0.0.1", port: int = 8080):
        self.storage = ClientStorage(os.path.expanduser(storage_dir))
        self.host = host
        self.port = port
        self.ws_clients: set[web.WebSocketResponse] = set()
        self._group_mtimes: dict[str, float] = {}
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
        self.app.router.add_get("/api/groups/{group_pubkey}/dag", self.handle_group_dag)
        self.app.router.add_get("/ws", self.handle_ws)

    def _get_static_dir(self) -> Path:
        return Path(__file__).parent / "static"

    async def handle_index(self, request: web.Request) -> web.Response:
        index_path = self._get_static_dir() / "index.html"
        return web.FileResponse(index_path)

    def _get_storage(self, request: web.Request) -> ClientStorage:
        """Get storage from request, with optional ?storage=path override."""
        custom = request.query.get("storage")
        if custom:
            return ClientStorage(os.path.expanduser(custom))
        return self.storage

    async def handle_groups(self, request: web.Request) -> web.Response:
        storage = self._get_storage(request)
        groups = storage.list_groups()
        result = []
        for gpub in groups:
            dag = storage.get_group_dag(gpub)
            state = dag.get_state()
            result.append(
                {
                    "pubkey": gpub,
                    "name": state.metadata.get("name", "unnamed"),
                    "event_count": dag.count,
                    "member_count": len(state.members),
                }
            )
        return web.json_response(result)

    async def handle_group_events(self, request: web.Request) -> web.Response:
        group_pubkey = request.match_info["group_pubkey"]
        storage = self._get_storage(request)
        dag = storage.get_group_dag(group_pubkey)
        events = [event_to_dict(e) for e in dag.get_all_events()]
        return web.json_response(events)

    async def handle_group_state(self, request: web.Request) -> web.Response:
        group_pubkey = request.match_info["group_pubkey"]
        storage = self._get_storage(request)
        dag = storage.get_group_dag(group_pubkey)
        state = dag.get_state()
        gaps = dag.get_missing_parents()
        tips = dag.get_tips()
        return web.json_response(
            {
                "pubkey": group_pubkey,
                "name": state.metadata.get("name", "unnamed"),
                "description": state.metadata.get("description", ""),
                "members": sorted(state.members),
                "joined": sorted(state.joined),
                "mods": sorted(state.mods),
                "relays": state.relays,
                "public": state.public,
                "event_count": dag.count,
                "tips": tips,
                "gaps": sorted(gaps),
            }
        )

    async def handle_group_dag(self, request: web.Request) -> web.Response:
        """Return nodes + edges for graph rendering."""
        group_pubkey = request.match_info["group_pubkey"]
        storage = self._get_storage(request)
        dag = storage.get_group_dag(group_pubkey)
        events = dag.get_all_events()

        nodes = []
        for e in events:
            v = verify_event_full(e)
            content_preview = ""
            if e["type"] == "message" and isinstance(e["content"], str):
                content_preview = e["content"][:60]
            elif isinstance(e["content"], dict):
                if "name" in e["content"]:
                    content_preview = f"name={e['content']['name']}"
                elif "invitee" in e["content"]:
                    content_preview = f"invitee={e['content']['invitee'][:12]}"
                elif "target" in e["content"]:
                    content_preview = f"target={e['content']['target'][:12]}"
            nodes.append(
                {
                    "id": e["id"],
                    "type": e["type"],
                    "author": e["author"][:12],
                    "ts": e["ts"],
                    "content_preview": content_preview,
                    "valid": v["valid"],
                }
            )

        edges = []
        for e in events:
            for parent_id in e.get("parents", []):
                edges.append({"source": parent_id, "target": e["id"]})

        return web.json_response({"nodes": nodes, "edges": edges})

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_clients.add(ws)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("action") == "subscribe":
                        group = data.get("group")
                        if group:
                            dag = self.storage.get_group_dag(group)
                            events = [event_to_dict(e) for e in dag.get_all_events()]
                            await ws.send_json({"type": "full_sync", "events": events})
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            self.ws_clients.discard(ws)

        return ws

    async def _broadcast_changes(self):
        """Poll storage for changes and broadcast to WebSocket clients."""
        while True:
            await asyncio.sleep(0.5)
            if not self.ws_clients:
                continue

            groups = self.storage.list_groups()
            for gpub in groups:
                dag = self.storage.get_group_dag(gpub)
                mtime = dag.db_path.stat().st_mtime if dag.db_path.exists() else 0
                prev = self._group_mtimes.get(gpub, 0)
                if mtime != prev:
                    self._group_mtimes[gpub] = mtime
                    events = [event_to_dict(e) for e in dag.get_all_events()]
                    dead = set()
                    for ws in self.ws_clients:
                        try:
                            await ws.send_json(
                                {
                                    "type": "update",
                                    "group": gpub,
                                    "events": events,
                                }
                            )
                        except Exception:
                            dead.add(ws)
                    self.ws_clients -= dead

    async def start(self):
        """Start the web server."""

        async def _start_bg(app):
            app["broadcast_task"] = asyncio.create_task(self._broadcast_changes())

        self.app.on_startup.append(_start_bg)

        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()

        print(f"FERN Web Visualiser running at http://{self.host}:{self.port}")
        print(f"Storage: {os.path.expanduser(self.storage.base_dir)}")
        print("Press Ctrl+C to stop.")

        try:
            await asyncio.Event().wait()
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            if "broadcast_task" in self.app:
                self.app["broadcast_task"].cancel()
            await runner.cleanup()


@click.command()
@click.option("--host", default="127.0.0.1", help="Bind host")
@click.option("--port", default=8080, help="Bind port")
@click.option("--storage", default=None, help="Storage directory")
def main(host: str, port: int, storage: str | None):
    """FERN DAG Inspector - Visualise group event history."""
    storage_dir = storage or get_storage_path("FERN_DAG_STORAGE")
    vis = WebVisualiser(storage_dir, host=host, port=port)
    asyncio.run(vis.start())


if __name__ == "__main__":
    main()
