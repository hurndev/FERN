"""FERN DAG Inspector - Web-based DAG visualization with real-time updates."""

import asyncio
import json
import os
from pathlib import Path

import click
from aiohttp import web, WSMsgType

from .dag import EventDAG
from .events import Event, verify_event_id, verify_event_signature
from .storage import resolve_fern_dir


def verify_event_full(event: Event) -> dict:
    """Returns verification dict."""
    id_ok = verify_event_id(event)
    signer = event["group"] if event["type"] == "group_genesis" else event["author"]
    sig_ok = verify_event_signature(event, signer)
    return {"id_valid": id_ok, "sig_valid": sig_ok, "valid": id_ok and sig_ok}


def event_to_dict(event: Event) -> dict:
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

    def __init__(self, fern_dir: str, host: str = "127.0.0.1", port: int = 8080):
        self.fern_dir = Path(fern_dir)
        self.host = host
        self.port = port
        self.ws_clients: set[web.WebSocketResponse] = set()
        self._group_mtimes: dict[str, float] = {}
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_static("/static", self._get_static_dir())
        self.app.router.add_get("/api/info", self.handle_info)
        self.app.router.add_get("/api/groups", self.handle_groups)
        self.app.router.add_get("/api/groups/{group_pubkey}", self.handle_group_events)
        self.app.router.add_get(
            "/api/groups/{group_pubkey}/state", self.handle_group_state
        )
        self.app.router.add_get("/api/groups/{group_pubkey}/dag", self.handle_group_dag)
        self.app.router.add_get("/ws", self.handle_ws)

    def _get_static_dir(self) -> Path:
        return Path(__file__).parent / "static"

    def _list_groups(self) -> list[str]:
        groups_dir = self.fern_dir / "groups"
        if not groups_dir.exists():
            return []
        return sorted(f.stem for f in groups_dir.glob("*.json"))

    def _get_dag(self, group_pubkey: str) -> EventDAG:
        groups_dir = self.fern_dir / "groups"
        return EventDAG(group_pubkey, str(groups_dir))

    async def handle_index(self, request: web.Request) -> web.Response:
        index_path = self._get_static_dir() / "index.html"
        return web.FileResponse(index_path)

    async def handle_info(self, request: web.Request) -> web.Response:
        return web.json_response({"storage": str(self.fern_dir)})

    async def handle_groups(self, request: web.Request) -> web.Response:
        groups = self._list_groups()
        result = []
        for gpub in groups:
            dag = self._get_dag(gpub)
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
        dag = self._get_dag(group_pubkey)
        events = [event_to_dict(e) for e in dag.get_all_events()]
        return web.json_response(events)

    async def handle_group_state(self, request: web.Request) -> web.Response:
        group_pubkey = request.match_info["group_pubkey"]
        dag = self._get_dag(group_pubkey)
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
        dag = self._get_dag(group_pubkey)
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
                            dag = self._get_dag(group)
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

            groups = self._list_groups()
            for gpub in groups:
                dag = self._get_dag(gpub)
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
        print(f"Data: {self.fern_dir}")
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
@click.option("--home", default=None, help="Home directory containing .fern folder")
@click.option("--host", default="127.0.0.1", help="Bind host")
@click.option("--port", default=8080, help="Bind port")
def main(home: str | None, host: str, port: int):
    """FERN DAG Inspector - Visualise group event history.

    Uses ~/.fern by default. Set FERN_TEST_USER to use /tmp/<user>/.fern
    instead. Use --home to specify a custom home directory.
    """
    fern_dir = resolve_fern_dir(home)
    if not fern_dir.exists():
        click.echo(f"Error: no .fern directory found at path: {fern_dir}", err=True)
        raise SystemExit(1)

    vis = WebVisualiser(str(fern_dir), host=host, port=port)
    asyncio.run(vis.start())


if __name__ == "__main__":
    main()
