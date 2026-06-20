from __future__ import annotations

import asyncio

import pytest

from fern.chat.messages import build_chat_message
from fern.client.sync import sync_diff
from fern.crypto.keys import Keypair
from fern.events.build import build_event
from fern.events.types import ProtocolTypes
from fern.identity.group import GroupKeypair
from fern.identity.user import UserIdentity
from fern.storage.memory import MemoryStore
from fern.transport.fake import FakeRelay


@pytest.fixture
def founder() -> UserIdentity:
    return UserIdentity(keypair=Keypair.from_privkey(b"found" + b"\x00" * 27))


@pytest.fixture
def alice() -> UserIdentity:
    return UserIdentity(keypair=Keypair.from_privkey(b"alice" + b"\x00" * 27))


@pytest.fixture
def group_keypair() -> GroupKeypair:
    return GroupKeypair(keypair=Keypair.from_privkey(b"group" + b"\x00" * 27))


def make_genesis(founder: UserIdentity, group_keypair: GroupKeypair, relay: FakeRelay):
    return build_event(
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
            "relays": [relay.url],
            "app": "chat",
            "chat.channels": ["general"],
        },
        group_keypair=group_keypair.keypair,
        ts=1,
    )


@pytest.mark.asyncio
async def test_backfill_stores_without_broadcast(
    founder: UserIdentity,
    alice: UserIdentity,
    group_keypair: GroupKeypair,
) -> None:
    relay = FakeRelay()
    genesis = make_genesis(founder, group_keypair, relay)
    await relay.publish(genesis)

    delivered = []

    async def on_event(event):
        delivered.append(event)

    relay.on_event(on_event)
    await relay.subscribe(group_keypair.pubkey)

    message = build_chat_message(
        user=alice,
        group=group_keypair.pubkey,
        parents=(genesis.id,),
        text="hello",
        ts=2,
    )
    await relay.backfill(message)
    await asyncio.sleep(0)

    assert await relay.get(message.id) == message
    assert delivered == []


@pytest.mark.asyncio
async def test_sync_diff_fetches_events_missing_locally(
    founder: UserIdentity,
    alice: UserIdentity,
    group_keypair: GroupKeypair,
) -> None:
    relay = FakeRelay()
    genesis = make_genesis(founder, group_keypair, relay)
    message = build_chat_message(
        user=alice,
        group=group_keypair.pubkey,
        parents=(genesis.id,),
        text="hello",
        ts=2,
    )
    await relay.publish(genesis)
    await relay.publish(message)

    store = MemoryStore()
    await store.put_event(genesis)

    result = await sync_diff(
        transport=relay,
        group=group_keypair.pubkey,
        store=store,
        client_id=alice.pubkey,
    )

    assert result.fetched == 1
    assert await store.get_event(message.id) == message


@pytest.mark.asyncio
async def test_sync_diff_backfills_events_missing_on_relay(
    founder: UserIdentity,
    alice: UserIdentity,
    group_keypair: GroupKeypair,
) -> None:
    relay = FakeRelay()
    genesis = make_genesis(founder, group_keypair, relay)
    message = build_chat_message(
        user=alice,
        group=group_keypair.pubkey,
        parents=(genesis.id,),
        text="hello",
        ts=2,
    )
    await relay.publish(genesis)

    store = MemoryStore()
    await store.put_event(genesis)
    await store.put_event(message)

    result = await sync_diff(
        transport=relay,
        group=group_keypair.pubkey,
        store=store,
        client_id=alice.pubkey,
    )

    assert result.backfilled == 1
    assert await relay.get(message.id) == message


@pytest.mark.asyncio
async def test_sync_diff_skips_locked_relay_without_waiting(
    founder: UserIdentity,
    group_keypair: GroupKeypair,
) -> None:
    relay = FakeRelay()
    genesis = make_genesis(founder, group_keypair, relay)
    await relay.publish(genesis)

    store = MemoryStore()
    await store.put_event(genesis)
    await relay.sync_lock(group_keypair.pubkey, "a" * 64)

    result = await sync_diff(
        transport=relay,
        group=group_keypair.pubkey,
        store=store,
        client_id="b" * 64,
        wait_on_lock=False,
    )

    assert result.skipped_locked is False

    extra = build_chat_message(
        user=founder,
        group=group_keypair.pubkey,
        parents=(genesis.id,),
        text="local only",
        ts=3,
    )
    await store.put_event(extra)

    result = await sync_diff(
        transport=relay,
        group=group_keypair.pubkey,
        store=store,
        client_id="b" * 64,
        wait_on_lock=False,
    )
    assert result.skipped_locked is True
    assert await relay.get(extra.id) is None
