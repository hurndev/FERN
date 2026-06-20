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
            "mods": [founder_identity.pubkey],
            "relays": [relay.url],
        },
        group_keypair=group_keypair.keypair,
    )

    receipt = await relay.publish(genesis)
    assert receipt is not None
    assert receipt.event_id == genesis.id

    stored = await relay.get(genesis.id)
    assert stored is not None
    assert stored.id == genesis.id


@pytest.mark.asyncio
async def test_fake_relay_attestation(
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
            "mods": [founder_identity.pubkey],
            "relays": [relay.url],
        },
        group_keypair=group_keypair.keypair,
    )

    await relay.publish(genesis)

    attestation = await relay.request_attestation(group_keypair.pubkey)
    assert attestation is not None
    assert attestation.count == 1
    assert genesis.id in attestation.tips
