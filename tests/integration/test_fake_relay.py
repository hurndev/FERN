import pytest

from fern.crypto.keys import Keypair
from fern.identity.user import UserIdentity
from fern.identity.group import GroupKeypair
from fern.events.build import build_event
from fern.events.types import ProtocolTypes
from fern.transport.fake import FakeRelayNetwork


@pytest.fixture
def founder_identity() -> UserIdentity:
    kp = Keypair.from_privkey(b"found" + b"\x00" * 27)
    return UserIdentity(keypair=kp)


@pytest.fixture
def alice_identity() -> UserIdentity:
    kp = Keypair.from_privkey(b"alice" + b"\x00" * 27)
    return UserIdentity(keypair=kp)


@pytest.fixture
def group_keypair() -> GroupKeypair:
    kp = Keypair.from_privkey(b"group" + b"\x00" * 27)
    return GroupKeypair(keypair=kp)


@pytest.fixture
def fake_relay_network() -> FakeRelayNetwork:
    return FakeRelayNetwork()


@pytest.mark.asyncio
async def test_publish_event_to_fake_relay(
    founder_identity: UserIdentity,
    group_keypair: GroupKeypair,
    fake_relay_network: FakeRelayNetwork,
) -> None:
    relays = fake_relay_network.spawn(count=3)
    relay = relays[0]

    genesis = build_event(
        type=ProtocolTypes.GENESIS,
        group=group_keypair.pubkey,
        author_keypair=founder_identity.keypair,
        parents=(),
        content={
            "name": "Test",
            "description": "",
            "public": True,
            "founder": founder_identity.pubkey,
            "admins": [founder_identity.pubkey],
            "relays": [relay.url],
            "app": "chat",
            "chat.channels": [{"id": "general", "name": "general", "position": 0}],
            "chat.default_channel": "general",
            "chat.system_channel": "general",
        },
        group_keypair=group_keypair.keypair,
    )

    event_receipt = await relay.publish(genesis)
    assert event_receipt is not None
    assert event_receipt.event_id == genesis.id

    stored = await relay.get(genesis.id)
    assert stored is not None
    assert stored.id == genesis.id


@pytest.mark.asyncio
async def test_fake_relay_group_status(
    founder_identity: UserIdentity,
    group_keypair: GroupKeypair,
    fake_relay_network: FakeRelayNetwork,
) -> None:
    relays = fake_relay_network.spawn(count=1)
    relay = relays[0]

    genesis = build_event(
        type=ProtocolTypes.GENESIS,
        group=group_keypair.pubkey,
        author_keypair=founder_identity.keypair,
        parents=(),
        content={
            "name": "Test",
            "description": "",
            "public": True,
            "founder": founder_identity.pubkey,
            "admins": [founder_identity.pubkey],
            "relays": [relay.url],
            "app": "chat",
            "chat.channels": [{"id": "general", "name": "general", "position": 0}],
            "chat.default_channel": "general",
            "chat.system_channel": "general",
        },
        group_keypair=group_keypair.keypair,
    )

    await relay.publish(genesis)

    group_status = await relay.request_group_status(group_keypair.pubkey)
    assert group_status is not None
    assert group_status.count == 1
    assert genesis.id in group_status.tips
