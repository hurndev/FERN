from __future__ import annotations

from dataclasses import dataclass

from fern.events.event import Event
from fern.completeness.receipts import Receipt
from fern.completeness.attestations import Attestation
from fern.completeness.fraud_proofs import FraudProof


@dataclass(frozen=True)
class SubscribeMessage:
    action: str = "subscribe"
    group: str = ""


@dataclass(frozen=True)
class PublishMessage:
    action: str = "publish"
    event: Event | None = None


@dataclass(frozen=True)
class GetMessage:
    action: str = "get"
    id: str = ""


@dataclass(frozen=True)
class SyncMessage:
    action: str = "sync"
    group: str = ""
    since: int | None = None


@dataclass(frozen=True)
class AttestationRequest:
    action: str = "attestation"
    group: str = ""


@dataclass(frozen=True)
class UnsubscribeMessage:
    action: str = "unsubscribe"
    group: str = ""


@dataclass(frozen=True)
class SubmitFraudProofMessage:
    action: str = "submit_fraud_proof"
    fraud_proof: FraudProof | None = None


@dataclass(frozen=True)
class QueryFraudProofsMessage:
    action: str = "query_fraud_proofs"
    relay: str | None = None
    group: str | None = None


@dataclass(frozen=True)
class EventMessage:
    type: str = "event"
    event: Event | None = None


@dataclass(frozen=True)
class ReceiptMessage:
    type: str = "receipt"
    receipt: Receipt | None = None


@dataclass(frozen=True)
class AttestationMessage:
    type: str = "attestation"
    attestation: Attestation | None = None


@dataclass(frozen=True)
class NotFoundMessage:
    type: str = "not_found"
    id: str = ""


@dataclass(frozen=True)
class SyncCompleteMessage:
    type: str = "sync_complete"
    group: str = ""
    count: int = 0


@dataclass(frozen=True)
class ErrorMessage:
    type: str = "error"
    message: str = ""


@dataclass(frozen=True)
class OkMessage:
    type: str = "ok"
    id: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class FraudProofMessage:
    type: str = "fraud_proof"
    fraud_proof: FraudProof | None = None


@dataclass(frozen=True)
class QueryCompleteMessage:
    type: str = "query_complete"
    count: int = 0
