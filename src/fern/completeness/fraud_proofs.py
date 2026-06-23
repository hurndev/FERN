from __future__ import annotations

import json
from dataclasses import dataclass

from fern.crypto.hashes import sha256_hex
from fern.events.event import Event
from fern.completeness.event_receipts import EventReceipt, canonical_serialization_event_receipt
from fern.events.serialization import canonical_serialization as event_canonical
from fern.events.validation import verify_event


@dataclass(frozen=True)
class FraudProof:
    type: str = "fraud_proof"
    group: str = ""
    relay: str = ""
    event_id: str = ""
    event: Event | None = None
    event_receipt: EventReceipt | None = None
    evidence: str = ""


def canonical_serialization_fraud_proof(proof: FraudProof) -> bytes:
    event_array = json.loads(event_canonical(proof.event).decode("utf-8")) if proof.event else None
    event_receipt_array = (
        json.loads(canonical_serialization_event_receipt(proof.event_receipt).decode("utf-8"))
        if proof.event_receipt
        else None
    )
    array = [
        proof.type,
        proof.group,
        proof.relay,
        proof.event_id,
        event_array,
        event_receipt_array,
        proof.evidence,
    ]
    return json.dumps(array, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_fraud_proof_id(proof: FraudProof) -> str:
    return sha256_hex(canonical_serialization_fraud_proof(proof))


def build_fraud_proof(*, relay: str, event: Event, event_receipt: EventReceipt, evidence: str) -> FraudProof:
    assert event.id is not None, "event must have an id"
    return FraudProof(
        type="fraud_proof",
        group=event.group,
        relay=relay,
        event_id=event.id,
        event=event,
        event_receipt=event_receipt,
        evidence=evidence,
    )


def verify_fraud_proof(proof: FraudProof) -> bool:
    if proof.event is None or proof.event_receipt is None:
        return False

    try:
        verify_event(proof.event)
    except Exception:
        return False

    from fern.completeness.event_receipts import verify_event_receipt

    if not verify_event_receipt(proof.event_receipt):
        return False

    if proof.event_receipt.event_id != proof.event_id:
        return False

    return True
