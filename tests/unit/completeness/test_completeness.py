from fern.crypto.keys import Keypair
from fern.events.event import Event
from fern.completeness.event_receipts import EventReceipt, build_event_receipt, verify_event_receipt
from fern.completeness.group_statuses import (
    build_group_status,
    verify_group_status,
    compute_set_hash,
    EMPTY_SET_HASH,
)


class TestEventReceipts:
    def test_build_and_verify_event_receipt(self) -> None:
        relay_kp = Keypair.generate()
        event = Event(
            id="a" * 64,
            type="chat.message",
            group="0" * 64,
            author="0" * 64,
            parents=("0" * 64,),
            content={},
            ts=1000,
            tags=(),
        )
        event_receipt = build_event_receipt(event=event, relay_keypair=relay_kp, ts=2000)
        assert verify_event_receipt(event_receipt)

    def test_tampered_event_receipt_fails(self) -> None:
        relay_kp = Keypair.generate()
        event = Event(
            id="a" * 64,
            type="chat.message",
            group="0" * 64,
            author="0" * 64,
            parents=("0" * 64,),
            content={},
            ts=1000,
            tags=(),
        )
        event_receipt = build_event_receipt(event=event, relay_keypair=relay_kp, ts=2000)
        tampered = EventReceipt(
            event_id=event_receipt.event_id,
            group=event_receipt.group,
            relay=event_receipt.relay,
            ts=event_receipt.ts + 1,
            sig=event_receipt.sig,
        )
        assert not verify_event_receipt(tampered)


class TestGroupStatuses:
    def test_empty_set_hash(self) -> None:
        h = compute_set_hash([])
        assert h == EMPTY_SET_HASH

    def test_set_hash_deterministic(self) -> None:
        ids = ["b" * 64, "a" * 64, "c" * 64]
        h1 = compute_set_hash(ids)
        h2 = compute_set_hash(["a" * 64, "b" * 64, "c" * 64])
        assert h1 == h2

    def test_build_and_verify_group_status(self) -> None:
        relay_kp = Keypair.generate()
        known_set = ["a" * 64, "b" * 64]
        att = build_group_status(
            group="0" * 64,
            relay_keypair=relay_kp,
            known_set=known_set,
            tips=["b" * 64],
            count=2,
            prev=None,
            ts=1000,
        )
        assert verify_group_status(att)
        assert len(att.sig) == 128
