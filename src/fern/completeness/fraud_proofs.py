from __future__ import annotations

import json
from dataclasses import dataclass

from fern.crypto.hashes import sha256_hex
from fern.events.event import Event
from fern.completeness.receipts import Receipt, canonical_serialization_receipt
from fern.events.serialization import canonical_serialization as event_canonical
from fern.events.validation import verify_event


@dataclass(frozen=True)
class FraudProof:
    type: str = "fraud_proof"
    group: str = ""
    relay: str = ""
    event_id: str = ""
    event: Event | None = None
    receipt: Receipt | None = None
    evidence: str = ""


def canonical_serialization_fraud_proof(proof: FraudProof) -> bytes:
    event_array = json.loads(event_canonical(proof.event).decode("utf-8")) if proof.event else None
    receipt_array = (
        json.loads(canonical_serialization_receipt(proof.receipt).decode("utf-8"))
        if proof.receipt
        else None
    )
    array = [
        proof.type,
        proof.group,
        proof.relay,
        proof.event_id,
        event_array,
        receipt_array,
        proof.evidence,
    ]
    return json.dumps(array, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_fraud_proof_id(proof: FraudProof) -> str:
    return sha256_hex(canonical_serialization_fraud_proof(proof))


def build_fraud_proof(*, relay: str, event: Event, receipt: Receipt, evidence: str) -> FraudProof:
    assert event.id is not None, "event must have an id"
    return FraudProof(
        type="fraud_proof",
        group=event.group,
        relay=relay,
        event_id=event.id,
        event=event,
        receipt=receipt,
        evidence=evidence,
    )


def verify_fraud_proof(proof: FraudProof) -> bool:
    if proof.event is None or proof.receipt is None:
        return False

    try:
        verify_event(proof.event)
    except Exception:
        return False

    from fern.completeness.receipts import verify_receipt

    if not verify_receipt(proof.receipt):
        return False

    if proof.receipt.event_id != proof.event_id:
        return False

    return True
