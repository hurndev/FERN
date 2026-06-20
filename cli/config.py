from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from fern.transport.websocket_client import WebSocketRelayClient

DEFAULT_CONFIG_DIR = Path(os.environ.get("FERN_HOME") or (Path.home() / ".fern"))
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_CACHE_DIR = DEFAULT_CONFIG_DIR / "cache"


def ensure_config_dir() -> Path:
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_CONFIG_DIR


def load_config(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        path = DEFAULT_CONFIG_FILE
    if path.exists():
        return dict(json.loads(path.read_text()))
    return {"groups": {}, "group_order": []}


def save_config(config: dict[str, Any], path: Path | None = None) -> None:
    if path is None:
        path = DEFAULT_CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    config.setdefault("groups", {})
    config.setdefault("group_order", [])
    path.write_text(json.dumps(config, indent=2))


def get_cache_path(group_pubkey: str) -> Path:
    return DEFAULT_CACHE_DIR / f"{group_pubkey}.sqlite"


def get_canonical_relay_urls(group_pubkey: str, config: dict[str, Any]) -> list[str]:
    group_info = config.get("groups", {}).get(group_pubkey, {})
    return list(group_info.get("relays", []))


def parse_group_address(address: str) -> tuple[str, list[str]]:
    addr = address
    if addr.startswith("fern:"):
        addr = addr[5:]
    if "@" in addr:
        group_pubkey, relays_part = addr.split("@", 1)
        relays = [r.strip() for r in relays_part.split(",") if r.strip()]
    else:
        group_pubkey = addr
        relays = []
    return group_pubkey, relays


def resolve_group(group_id: str, config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    group_order: list[str] = config.get("group_order", [])
    groups: dict[str, dict[str, Any]] = config.get("groups", {})

    if group_id.isdigit():
        idx = int(group_id) - 1
        if 0 <= idx < len(group_order):
            group_pubkey = group_order[idx]
            return group_pubkey, groups.get(group_pubkey, {})

    if len(group_id) == 64 and all(c in "0123456789abcdef" for c in group_id):
        return group_id, groups.get(group_id, {})

    if group_id in groups:
        return group_id, groups[group_id]

    raise ValueError(f"Group not found: {group_id}. Run 'fern group list' to see known groups.")


def add_group_to_order(group_pubkey: str, config: dict[str, Any]) -> int:
    group_order: list[str] = config.setdefault("group_order", [])
    if group_pubkey not in group_order:
        group_order.append(group_pubkey)
    return group_order.index(group_pubkey) + 1


async def connect_transports(urls: list[str]) -> list[WebSocketRelayClient]:
    from fern.transport.websocket_client import WebSocketRelayClient

    transports: list[WebSocketRelayClient] = []
    for url in urls:
        t = WebSocketRelayClient(url)
        try:
            await t.connect()
            transports.append(t)
        except Exception:
            pass
    return transports
