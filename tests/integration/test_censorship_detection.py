import pytest

from fern.crypto.keys import Keypair
from fern.identity.user import UserIdentity
from fern.events.build import build_event
from fern.events.types import ProtocolTypes
from fern.completeness.event_receipts import EventReceipt
from fern.completeness.trust_ledger import TrustLedger
from fern.client.monitor_runner import run_monitor_pass
from fern.storage.memory import MemoryStore
from fern.transport.fake import FakeRelayNetwork


@pytest.mark.asyncio
async def test_censorship_detection_via_group_status_divergence() -> None:
    network = FakeRelayNetwork()
    relay_a, relay_b, relay_c = network.spawn(count=3)

    founder_kp = Keypair.from_privkey(b"found" + b"\x00" * 27)
    founder = UserIdentity(keypair=founder_kp)
    group_kp = Keypair.from_privkey(b"group" + b"\x00" * 27)

    genesis = build_event(
        type=ProtocolTypes.GENESIS,
        group=group_kp.pubkey_hex,
        author_keypair=founder.keypair,
        parents=(),
        content={
            "name": "Test",
            "description": "",
            "public": True,
            "founder": founder.pubkey,
            "admins": [founder.pubkey],
            "relays": [relay_a.url, relay_b.url, relay_c.url],
        "app": "chat",
        "chat.channels": [{"id": "general", "name": "general", "position": 0}],
            "chat.default_channel": "general",
            "chat.system_channel": "general",
        },
        group_keypair=group_kp,
    )

    await relay_a.publish(genesis)
    await relay_b.publish(genesis)
    await relay_c.publish(genesis)

    msg = build_event(
        type="chat.message",
        group=group_kp.pubkey_hex,
        author_keypair=founder.keypair,
        parents=(genesis.id,),
        content={"text": "hello", "channel": "general"},
    )

    await relay_a.publish(msg)
    await relay_b.publish(msg)
    await relay_c.publish(msg)

    relay_a.drop_event(msg.id)

    group_status_a = await relay_a.request_group_status(group_kp.pubkey_hex)
    group_status_b = await relay_b.request_group_status(group_kp.pubkey_hex)

    assert group_status_a.set_hash != group_status_b.set_hash


@pytest.mark.asyncio
async def test_monitor_pass_detects_missing_event_with_event_receipt() -> None:
    network = FakeRelayNetwork()
    relay_a, relay_b, relay_c = network.spawn(count=3)

    founder_kp = Keypair.from_privkey(b"found" + b"\x00" * 27)
    founder = UserIdentity(keypair=founder_kp)
    group_kp = Keypair.from_privkey(b"group" + b"\x00" * 27)

    genesis = build_event(
        type=ProtocolTypes.GENESIS,
        group=group_kp.pubkey_hex,
        author_keypair=founder.keypair,
        parents=(),
        content={
            "name": "Test",
            "description": "",
            "public": True,
            "founder": founder.pubkey,
            "admins": [founder.pubkey],
            "relays": [relay_a.url, relay_b.url, relay_c.url],
        "app": "chat",
        "chat.channels": [{"id": "general", "name": "general", "position": 0}],
            "chat.default_channel": "general",
            "chat.system_channel": "general",
        },
        group_keypair=group_kp,
    )

    await relay_a.publish(genesis)
    await relay_b.publish(genesis)
    await relay_c.publish(genesis)

    msg = build_event(
        type="chat.message",
        group=group_kp.pubkey_hex,
        author_keypair=founder.keypair,
        parents=(genesis.id,),
        content={"text": "hello", "channel": "general"},
    )

    event_receipt_a = await relay_a.publish(msg)
    await relay_b.publish(msg)
    await relay_c.publish(msg)

    relay_a.drop_event(msg.id)

    group_status_a = await relay_a.request_group_status(group_kp.pubkey_hex)
    group_status_b = await relay_b.request_group_status(group_kp.pubkey_hex)

    store = MemoryStore()
    await store.put_event(genesis)
    await store.put_event(msg)

    known_set = await store.get_known_set(group_kp.pubkey_hex)

    event_receipts_for_relay: dict[str, EventReceipt] = {}
    assert msg.id is not None
    event_receipts_for_relay[msg.id] = event_receipt_a

    trust_ledger = TrustLedger()
    sibling_group_statuses = {relay_b.relay_pubkey: group_status_b}

    result = await run_monitor_pass(
        relay=relay_a,
        group_status=group_status_a,
        local_known_set=known_set,
        event_receipts_for_relay=event_receipts_for_relay,
        trust_ledger=trust_ledger,
        sibling_group_statuses=sibling_group_statuses,
    )

    assert not result.in_sync
    fault_kinds = [f.kind for f in result.faults]
    assert "missing_event_with_event_receipt" in fault_kinds

    entry = trust_ledger.entries[relay_a.relay_pubkey]
    assert any(f.kind == "missing_event_with_event_receipt" for f in entry.observed_faults)
