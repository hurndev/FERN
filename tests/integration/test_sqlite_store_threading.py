from __future__ import annotations

import asyncio

import pytest

from fern.chat.messages import build_chat_message
from fern.crypto.keys import Keypair
from fern.events.build import build_event
from fern.events.types import ProtocolTypes
from fern.identity.group import GroupKeypair
from fern.identity.user import UserIdentity
from fern.storage.sqlite_store import SqliteStore


@pytest.mark.asyncio
async def test_sqlite_store_allows_concurrent_to_thread_calls(tmp_path) -> None:
    founder = UserIdentity(keypair=Keypair.from_privkey(b"found" + b"\x00" * 27))
    group_keypair = GroupKeypair(keypair=Keypair.from_privkey(b"group" + b"\x00" * 27))
    store = SqliteStore(str(tmp_path / "store.sqlite"))
    await store.open()
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
                "admins": [founder.pubkey],
                "relays": ["ws://localhost:8765"],
                "app": "chat",
                "chat.channels": [{"id": "general", "name": "general", "position": 0}],
            "chat.default_channel": "general",
            "chat.system_channel": "general",
            },
            group_keypair=group_keypair.keypair,
            ts=1,
        )
        await store.put_event(genesis)

        events = [
            build_chat_message(
                user=founder,
                group=group_keypair.pubkey,
                parents=(genesis.id,),
                text=f"message {i}",
                ts=2 + i,
            )
            for i in range(20)
        ]

        await asyncio.gather(*(store.put_event(event) for event in events))
        results = await asyncio.gather(*(store.get_event(event.id) for event in events))

        assert results == events
        assert await store.count_events(group_keypair.pubkey) == 21
    finally:
        await store.close()
