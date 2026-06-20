from fern.crypto.keys import Keypair
from fern.events.event import Event
from fern.completeness.receipts import Receipt, build_receipt, verify_receipt
from fern.completeness.attestations import (
    build_attestation,
    verify_attestation,
    compute_set_hash,
    EMPTY_SET_HASH,
)


class TestReceipts:
    def test_build_and_verify_receipt(self) -> None:
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
        receipt = build_receipt(event=event, relay_keypair=relay_kp, ts=2000)
        assert verify_receipt(receipt)

    def test_tampered_receipt_fails(self) -> None:
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
        receipt = build_receipt(event=event, relay_keypair=relay_kp, ts=2000)
        tampered = Receipt(
            event_id=receipt.event_id,
            group=receipt.group,
            relay=receipt.relay,
            ts=receipt.ts + 1,
            sig=receipt.sig,
        )
        assert not verify_receipt(tampered)


class TestAttestations:
    def test_empty_set_hash(self) -> None:
        h = compute_set_hash([])
        assert h == EMPTY_SET_HASH

    def test_set_hash_deterministic(self) -> None:
        ids = ["b" * 64, "a" * 64, "c" * 64]
        h1 = compute_set_hash(ids)
        h2 = compute_set_hash(["a" * 64, "b" * 64, "c" * 64])
        assert h1 == h2

    def test_build_and_verify_attestation(self) -> None:
        relay_kp = Keypair.generate()
        known_set = ["a" * 64, "b" * 64]
        att = build_attestation(
            group="0" * 64,
            relay_keypair=relay_kp,
            known_set=known_set,
            tips=["b" * 64],
            count=2,
            prev=None,
            ts=1000,
        )
        assert verify_attestation(att)
        assert len(att.sig) == 128
