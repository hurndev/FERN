from __future__ import annotations

import json
import urllib.request

from fern.transport.interfaces import RelayMetadata


async def fetch_relay_metadata(url: str) -> RelayMetadata:
    meta_url = url.replace("wss://", "https://").replace("ws://", "http://")
    try:
        with urllib.request.urlopen(meta_url, timeout=10) as resp:
            data = json.loads(resp.read())
        return RelayMetadata(
            name=data.get("name", ""),
            description=data.get("description", ""),
            pubkey=data.get("pubkey", ""),
            software=data.get("software", ""),
            version=data.get("version", ""),
            groups=tuple(data.get("groups", [])),
            retention=data.get("retention", {}).get("default", "full"),
        )
    except Exception:
        return RelayMetadata()
