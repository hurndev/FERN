from __future__ import annotations

import pytest

from fern.completeness.heal_attestations import Threshold, TrustedWitness
from fern.crypto.keys import Keypair
from fern.events.build import build_event
from fern.events.types import ProtocolTypes
from fern.identity.user import UserIdentity
from fern.relay.trust_config import RelayTrustConfig
from fern.transport.fake import FakeRelay


@pytest.fixture
def founder():
    return UserIdentity(keypair=Keypair.from_privkey(b"f" + b"\x00" * 31))


@pytest.fixture
def group_kp():
    return Keypair.from_privkey(b"g" + b"\x00" * 31)


def _make_genesis(founder, gkp, relays):
    return build_event(
        type=ProtocolTypes.GENESIS,
        group=gkp.pubkey_hex,
        author_keypair=founder.keypair,
        parents=(),
        content={
            "name": "Test",
            "description": "",
            "public": True,
            "founder": founder.pubkey,
            "admins": [founder.pubkey],
            "relays": relays,
            "app": "chat",
            "chat.channels": [{"id": "general", "name": "general", "position": 0}],
            "chat.default_channel": "general",
            "chat.system_channel": "general",
        },
        group_keypair=gkp,
    )


@pytest.mark.asyncio
async def test_trusted_heal_seeds_missing_relay(founder, group_kp):
    r1 = FakeRelay(Keypair.from_privkey(b"1" + b"\x00" * 31))
    r2 = FakeRelay(Keypair.from_privkey(b"2" + b"\x00" * 31))
    r3 = FakeRelay(
        Keypair.from_privkey(b"3" + b"\x00" * 31),
        trust_config=RelayTrustConfig(
            trusted_witness_relays=(
                TrustedWitness(relay=r1.relay_pubkey, url=r1.url),
                TrustedWitness(relay=r2.relay_pubkey, url=r2.url),
            ),
            threshold=Threshold(),
        ),
    )

    genesis = _make_genesis(founder, group_kp, [r1.url, r2.url, r3.url])
    msg = build_event(
        type="chat.message",
        group=group_kp.pubkey_hex,
        author_keypair=founder.keypair,
        parents=(genesis.id,),
        content={"text": "hello", "channel": "general"},
    )

    await r1.publish(genesis)
    await r1.publish(msg)
    await r2.publish(genesis)
    await r2.publish(msg)

    assert not await r3._store.has_event(genesis.id)
    assert not await r3._store.has_event(msg.id)

    ids = [genesis.id, msg.id]
    challenge = await r3.get_heal_challenge(group_kp.pubkey_hex, ids)
    assert len(challenge.trusted_witnesses) == 2

    ha1 = await r1.get_group_host_attestation(challenge)
    ha2 = await r2.get_group_host_attestation(challenge)
    assert ha1.hosts is True
    assert ha2.hosts is True

    inv1 = await r1.get_inventory_attestation(challenge, ids)
    inv2 = await r2.get_inventory_attestation(challenge, ids)
    assert inv1.attestation is not None
    assert inv2.attestation is not None
    assert set(inv1.covered) == set(ids)
    assert set(inv2.covered) == set(ids)

    result = await r3.heal_batch(
        challenge=challenge,
        events=[genesis, msg],
        group_host_attestations=[ha1, ha2],
        inventory_attestations=[
            (inv1.attestation, inv1.covered),
            (inv2.attestation, inv2.covered),
        ],
    )
    assert set(result.stored) == set(ids)
    assert len(result.rejected) == 0

    assert await r3._store.has_event(genesis.id)
    assert await r3._store.has_event(msg.id)

    provenance = await r3._store.get_heal_provenance(msg.id)
    assert r1.relay_pubkey in provenance
    assert r2.relay_pubkey in provenance


@pytest.mark.asyncio
async def test_trusted_heal_insufficient_witnesses_falls_back(founder, group_kp):
    r1 = FakeRelay(Keypair.from_privkey(b"1" + b"\x00" * 31))
    r2 = FakeRelay(Keypair.from_privkey(b"2" + b"\x00" * 31))
    r3 = FakeRelay(
        Keypair.from_privkey(b"3" + b"\x00" * 31),
        trust_config=RelayTrustConfig(
            trusted_witness_relays=(
                TrustedWitness(relay=r1.relay_pubkey, url=r1.url),
                TrustedWitness(relay=r2.relay_pubkey, url=r2.url),
            ),
            threshold=Threshold(),
        ),
    )

    genesis = _make_genesis(founder, group_kp, [r1.url, r2.url, r3.url])
    msg = build_event(
        type="chat.message",
        group=group_kp.pubkey_hex,
        author_keypair=founder.keypair,
        parents=(genesis.id,),
        content={"text": "hello", "channel": "general"},
    )

    await r1.publish(genesis)
    await r1.publish(msg)
    await r2.publish(genesis)

    ids = [genesis.id, msg.id]
    challenge = await r3.get_heal_challenge(group_kp.pubkey_hex, ids)

    ha1 = await r1.get_group_host_attestation(challenge)
    ha2 = await r2.get_group_host_attestation(challenge)
    assert ha1.hosts is True
    assert ha2.hosts is True

    inv1 = await r1.get_inventory_attestation(challenge, ids)
    inv2 = await r2.get_inventory_attestation(challenge, ids)

    assert set(inv1.covered) == set(ids)
    assert set(inv2.covered) == {genesis.id}

    result = await r3.heal_batch(
        challenge=challenge,
        events=[genesis, msg],
        group_host_attestations=[ha1, ha2],
        inventory_attestations=[
            (inv1.attestation, inv1.covered),
            (inv2.attestation, inv2.covered),
        ],
    )

    assert genesis.id in result.stored
    assert msg.id in [r[0] for r in result.rejected]


@pytest.mark.asyncio
async def test_trusted_heal_one_witness_cannot_fast_heal(founder, group_kp):
    r1 = FakeRelay(Keypair.from_privkey(b"1" + b"\x00" * 31))
    r2 = FakeRelay(Keypair.from_privkey(b"2" + b"\x00" * 31))
    r3 = FakeRelay(
        Keypair.from_privkey(b"3" + b"\x00" * 31),
        trust_config=RelayTrustConfig(
            trusted_witness_relays=(
                TrustedWitness(relay=r1.relay_pubkey, url=r1.url),
                TrustedWitness(relay=r2.relay_pubkey, url=r2.url),
            ),
            threshold=Threshold(),
        ),
    )

    genesis = _make_genesis(founder, group_kp, [r1.url, r2.url, r3.url])
    msg = build_event(
        type="chat.message",
        group=group_kp.pubkey_hex,
        author_keypair=founder.keypair,
        parents=(genesis.id,),
        content={"text": "hello", "channel": "general"},
    )

    await r1.publish(genesis)
    await r1.publish(msg)

    ids = [genesis.id, msg.id]
    challenge = await r3.get_heal_challenge(group_kp.pubkey_hex, ids)

    ha1 = await r1.get_group_host_attestation(challenge)
    ha2 = await r2.get_group_host_attestation(challenge)
    assert ha1.hosts is True
    assert ha2.hosts is False

    inv1 = await r1.get_inventory_attestation(challenge, ids)
    assert inv1.attestation is not None

    result = await r3.heal_batch(
        challenge=challenge,
        events=[genesis, msg],
        group_host_attestations=[ha1, ha2],
        inventory_attestations=[(inv1.attestation, inv1.covered)],
    )

    for eid, reason in result.rejected:
        assert reason == "insufficient_trusted_witnesses"
    assert len(result.stored) == 0


@pytest.mark.asyncio
async def test_trusted_heal_does_not_broadcast(founder, group_kp):
    r1 = FakeRelay(Keypair.from_privkey(b"1" + b"\x00" * 31))
    r2 = FakeRelay(Keypair.from_privkey(b"2" + b"\x00" * 31))
    r3 = FakeRelay(
        Keypair.from_privkey(b"3" + b"\x00" * 31),
        trust_config=RelayTrustConfig(
            trusted_witness_relays=(
                TrustedWitness(relay=r1.relay_pubkey, url=r1.url),
                TrustedWitness(relay=r2.relay_pubkey, url=r2.url),
            ),
        ),
    )

    genesis = _make_genesis(founder, group_kp, [r1.url, r2.url, r3.url])
    msg = build_event(
        type="chat.message",
        group=group_kp.pubkey_hex,
        author_keypair=founder.keypair,
        parents=(genesis.id,),
        content={"text": "hello", "channel": "general"},
    )

    await r1.publish(genesis)
    await r1.publish(msg)
    await r2.publish(genesis)
    await r2.publish(msg)

    pushed_events: list = []
    r3.on_event(lambda e: pushed_events.append(e))

    ids = [genesis.id, msg.id]
    challenge = await r3.get_heal_challenge(group_kp.pubkey_hex, ids)
    ha1 = await r1.get_group_host_attestation(challenge)
    ha2 = await r2.get_group_host_attestation(challenge)
    inv1 = await r1.get_inventory_attestation(challenge, ids)
    inv2 = await r2.get_inventory_attestation(challenge, ids)

    await r3.heal_batch(
        challenge=challenge,
        events=[genesis, msg],
        group_host_attestations=[ha1, ha2],
        inventory_attestations=[
            (inv1.attestation, inv1.covered),
            (inv2.attestation, inv2.covered),
        ],
    )

    assert len(pushed_events) == 0


@pytest.mark.asyncio
async def test_trusted_heal_auto_hosts_on_genesis(founder, group_kp):
    r1 = FakeRelay(Keypair.from_privkey(b"1" + b"\x00" * 31))
    r2 = FakeRelay(Keypair.from_privkey(b"2" + b"\x00" * 31))
    r3 = FakeRelay(
        Keypair.from_privkey(b"3" + b"\x00" * 31),
        trust_config=RelayTrustConfig(
            trusted_witness_relays=(
                TrustedWitness(relay=r1.relay_pubkey, url=r1.url),
                TrustedWitness(relay=r2.relay_pubkey, url=r2.url),
            ),
        ),
    )

    genesis = _make_genesis(founder, group_kp, [r1.url, r2.url, r3.url])
    msg = build_event(
        type="chat.message",
        group=group_kp.pubkey_hex,
        author_keypair=founder.keypair,
        parents=(genesis.id,),
        content={"text": "hello", "channel": "general"},
    )

    await r1.publish(genesis)
    await r1.publish(msg)
    await r2.publish(genesis)
    await r2.publish(msg)

    assert group_kp.pubkey_hex not in r3.hosted_groups

    ids = [genesis.id, msg.id]
    challenge = await r3.get_heal_challenge(group_kp.pubkey_hex, ids)
    ha1 = await r1.get_group_host_attestation(challenge)
    ha2 = await r2.get_group_host_attestation(challenge)
    inv1 = await r1.get_inventory_attestation(challenge, ids)
    inv2 = await r2.get_inventory_attestation(challenge, ids)

    result = await r3.heal_batch(
        challenge=challenge,
        events=[genesis, msg],
        group_host_attestations=[ha1, ha2],
        inventory_attestations=[
            (inv1.attestation, inv1.covered),
            (inv2.attestation, inv2.covered),
        ],
    )

    assert len(result.stored) == 2
    assert group_kp.pubkey_hex in r3.hosted_groups