from __future__ import annotations

from typing import Any

import pytest

from fern.chat.messages import build_chat_message
from fern.crypto.keys import Keypair
from fern.events.build import build_event
from fern.events.types import ProtocolTypes
from fern.identity.group import GroupKeypair
from fern.identity.user import UserIdentity
import fern.transport.websocket_server as websocket_server
from fern.relay.store import RelayStore
from fern.storage.memory import MemoryStore
from fern.transport.websocket_server import RelayServer, _event_to_json_dict


@pytest.mark.asyncio
async def test_relay_healing_actions(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    founder = UserIdentity(keypair=Keypair.from_privkey(b"found" + b"\x00" * 27))
    alice = UserIdentity(keypair=Keypair.from_privkey(b"alice" + b"\x00" * 27))
    group_keypair = GroupKeypair(keypair=Keypair.from_privkey(b"group" + b"\x00" * 27))
    server = RelayServer(store_path=str(tmp_path / "relay.sqlite"))
    memory_store = MemoryStore()
    server._store = memory_store
    server._relay_store = RelayStore(memory_store)
    try:
        genesis = build_event(
            type=ProtocolTypes.GENESIS,
            group=group_keypair.pubkey,
            author_keypair=founder.keypair,
            parents=(),
            content={
                "name": "Test",
                "description": "",
                "public": True,
                "founder": founder.pubkey,
                "mods": [founder.pubkey],
                "relays": ["ws://localhost:8765"],
            },
            group_keypair=group_keypair.keypair,
            ts=1,
        )

        publish = await server._handle_publish({"event": _event_to_json_dict(genesis)}, None)  # type: ignore[arg-type]
        assert publish is not None
        assert publish[0]["type"] == "receipt"

        ids = await server._handle_sync_ids({"group": group_keypair.pubkey})
        assert ids == [{"type": "ids", "group": group_keypair.pubkey, "ids": [genesis.id]}]

        monkeypatch.setattr(websocket_server.time, "time", lambda: 10.0)
        lock = await server._handle_sync_lock(
            {"group": group_keypair.pubkey, "client_id": alice.pubkey}
        )
        assert lock == [{"type": "sync_lock_granted", "group": group_keypair.pubkey, "ttl": 30}]
        monkeypatch.setattr(websocket_server.time, "time", lambda: 11.0)
        renewed = await server._handle_sync_lock(
            {"group": group_keypair.pubkey, "client_id": alice.pubkey}
        )
        assert renewed == [
            {"type": "sync_lock_granted", "group": group_keypair.pubkey, "ttl": 29}
        ]
        denied = await server._handle_sync_lock(
            {"group": group_keypair.pubkey, "client_id": founder.pubkey}
        )
        assert denied[0]["type"] == "sync_lock_denied"
        await server._handle_sync_unlock({"group": group_keypair.pubkey, "client_id": alice.pubkey})

        message = build_chat_message(
            user=alice,
            group=group_keypair.pubkey,
            parents=(genesis.id,),
            text="hello",
            ts=2,
        )
        broadcasts: list[Any] = []

        async def record_broadcast(event) -> None:  # type: ignore[no-untyped-def]
            broadcasts.append(event)

        server._broadcast_event = record_broadcast  # type: ignore[method-assign]
        backfill = await server._handle_backfill({"event": _event_to_json_dict(message)}, None)  # type: ignore[arg-type]
        assert backfill is not None
        assert backfill[0]["type"] == "receipt"
        assert await server._store.get_event(message.id) == message
        assert broadcasts == []
    finally:
        pass
