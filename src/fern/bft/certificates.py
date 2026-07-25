from __future__ import annotations

from dataclasses import dataclass, replace

from fern.bft.canonical import sign_payload, strict_int, verify_payload_signature
from fern.bft.constants import PHASE_PRECOMMIT, PHASE_PREVOTE, VOTE_PHASES
from fern.crypto.encoding import is_valid_event_id_hex, is_valid_pubkey_hex, is_valid_sig_hex
from fern.crypto.keys import Keypair


def _valid_position(epoch: int, height: int, round: int) -> bool:
    return epoch >= 0 and height > 0 and round >= 0


@dataclass(frozen=True)
class IngressReceipt:
    group: str
    chain_id: str
    epoch: int
    event_id: str
    validator: str
    first_seen_ms: int
    sig: str = ""

    def signing_payload(self) -> list[object]:
        return [
            "ingress_receipt",
            self.group,
            self.chain_id,
            self.epoch,
            self.event_id,
            self.validator,
            self.first_seen_ms,
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "ingress_receipt",
            "group": self.group,
            "chain_id": self.chain_id,
            "epoch": self.epoch,
            "event_id": self.event_id,
            "validator": self.validator,
            "first_seen_ms": self.first_seen_ms,
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> IngressReceipt:
        return cls(
            group=str(value.get("group", "")),
            chain_id=str(value.get("chain_id", "")),
            epoch=strict_int(value.get("epoch"), "receipt epoch"),
            event_id=str(value.get("event_id", "")),
            validator=str(value.get("validator", "")),
            first_seen_ms=strict_int(value.get("first_seen_ms"), "receipt first_seen_ms"),
            sig=str(value.get("sig", "")),
        )


def sign_ingress_receipt(receipt: IngressReceipt, keypair: Keypair) -> IngressReceipt:
    if receipt.validator != keypair.pubkey_hex:
        raise ValueError("receipt validator does not match signing key")
    return replace(receipt, sig=sign_payload(keypair, receipt.signing_payload()))


def verify_ingress_receipt(receipt: IngressReceipt) -> bool:
    return (
        is_valid_pubkey_hex(receipt.group)
        and is_valid_event_id_hex(receipt.chain_id)
        and receipt.epoch >= 0
        and is_valid_event_id_hex(receipt.event_id)
        and is_valid_pubkey_hex(receipt.validator)
        and receipt.first_seen_ms > 0
        and is_valid_sig_hex(receipt.sig)
        and verify_payload_signature(receipt.validator, receipt.signing_payload(), receipt.sig)
    )


@dataclass(frozen=True)
class TimestampObservation:
    group: str
    chain_id: str
    epoch: int
    height: int
    round: int
    candidate_id: str
    validator: str
    observed_ms: tuple[int, ...]
    sig: str = ""

    def signing_payload(self) -> list[object]:
        return [
            "timestamp_observation",
            self.group,
            self.chain_id,
            self.epoch,
            self.height,
            self.round,
            self.candidate_id,
            self.validator,
            list(self.observed_ms),
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "timestamp_observation",
            "group": self.group,
            "chain_id": self.chain_id,
            "epoch": self.epoch,
            "height": self.height,
            "round": self.round,
            "candidate_id": self.candidate_id,
            "validator": self.validator,
            "observed_ms": list(self.observed_ms),
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> TimestampObservation:
        raw_times = value.get("observed_ms", [])
        if not isinstance(raw_times, list):
            raise ValueError("observed_ms must be an array")
        return cls(
            group=str(value.get("group", "")),
            chain_id=str(value.get("chain_id", "")),
            epoch=strict_int(value.get("epoch"), "observation epoch"),
            height=strict_int(value.get("height"), "observation height"),
            round=strict_int(value.get("round"), "observation round"),
            candidate_id=str(value.get("candidate_id", "")),
            validator=str(value.get("validator", "")),
            observed_ms=tuple(strict_int(item, "observation timestamp") for item in raw_times),
            sig=str(value.get("sig", "")),
        )


def sign_timestamp_observation(
    observation: TimestampObservation, keypair: Keypair
) -> TimestampObservation:
    if observation.validator != keypair.pubkey_hex:
        raise ValueError("observation validator does not match signing key")
    return replace(observation, sig=sign_payload(keypair, observation.signing_payload()))


def verify_timestamp_observation(observation: TimestampObservation, *, event_count: int) -> bool:
    return (
        is_valid_pubkey_hex(observation.group)
        and is_valid_event_id_hex(observation.chain_id)
        and _valid_position(observation.epoch, observation.height, observation.round)
        and is_valid_event_id_hex(observation.candidate_id)
        and is_valid_pubkey_hex(observation.validator)
        and len(observation.observed_ms) == event_count
        and all(value > 0 for value in observation.observed_ms)
        and is_valid_sig_hex(observation.sig)
        and verify_payload_signature(
            observation.validator, observation.signing_payload(), observation.sig
        )
    )


@dataclass(frozen=True)
class Vote:
    group: str
    chain_id: str
    epoch: int
    height: int
    round: int
    phase: str
    block_id: str | None
    validator: str
    sig: str = ""

    def signing_payload(self) -> list[object]:
        return [
            "vote",
            self.group,
            self.chain_id,
            self.epoch,
            self.height,
            self.round,
            self.phase,
            self.block_id,
            self.validator,
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "vote",
            "group": self.group,
            "chain_id": self.chain_id,
            "epoch": self.epoch,
            "height": self.height,
            "round": self.round,
            "phase": self.phase,
            "block_id": self.block_id,
            "validator": self.validator,
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Vote:
        raw_block_id = value.get("block_id")
        return cls(
            group=str(value.get("group", "")),
            chain_id=str(value.get("chain_id", "")),
            epoch=strict_int(value.get("epoch"), "vote epoch"),
            height=strict_int(value.get("height"), "vote height"),
            round=strict_int(value.get("round"), "vote round"),
            phase=str(value.get("phase", "")),
            block_id=str(raw_block_id) if raw_block_id is not None else None,
            validator=str(value.get("validator", "")),
            sig=str(value.get("sig", "")),
        )


def sign_vote(vote: Vote, keypair: Keypair) -> Vote:
    if vote.validator != keypair.pubkey_hex:
        raise ValueError("vote validator does not match signing key")
    return replace(vote, sig=sign_payload(keypair, vote.signing_payload()))


def verify_vote(vote: Vote) -> bool:
    return (
        is_valid_pubkey_hex(vote.group)
        and is_valid_event_id_hex(vote.chain_id)
        and _valid_position(vote.epoch, vote.height, vote.round)
        and vote.phase in VOTE_PHASES
        and (vote.block_id is None or is_valid_event_id_hex(vote.block_id))
        and is_valid_pubkey_hex(vote.validator)
        and is_valid_sig_hex(vote.sig)
        and verify_payload_signature(vote.validator, vote.signing_payload(), vote.sig)
    )


@dataclass(frozen=True)
class SyncReady:
    group: str
    chain_id: str
    from_epoch: int
    to_epoch: int
    validator: str
    checkpoint_height: int
    checkpoint_block_hash: str
    history_root: str
    byte_count: int
    sig: str = ""

    def signing_payload(self) -> list[object]:
        return [
            "sync_ready",
            self.group,
            self.chain_id,
            self.from_epoch,
            self.to_epoch,
            self.validator,
            self.checkpoint_height,
            self.checkpoint_block_hash,
            self.history_root,
            self.byte_count,
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "sync_ready",
            "group": self.group,
            "chain_id": self.chain_id,
            "from_epoch": self.from_epoch,
            "to_epoch": self.to_epoch,
            "validator": self.validator,
            "checkpoint_height": self.checkpoint_height,
            "checkpoint_block_hash": self.checkpoint_block_hash,
            "history_root": self.history_root,
            "byte_count": self.byte_count,
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SyncReady:
        return cls(
            group=str(value.get("group", "")),
            chain_id=str(value.get("chain_id", "")),
            from_epoch=strict_int(value.get("from_epoch"), "readiness from_epoch"),
            to_epoch=strict_int(value.get("to_epoch"), "readiness to_epoch"),
            validator=str(value.get("validator", "")),
            checkpoint_height=strict_int(
                value.get("checkpoint_height"), "readiness checkpoint_height"
            ),
            checkpoint_block_hash=str(value.get("checkpoint_block_hash", "")),
            history_root=str(value.get("history_root", "")),
            byte_count=strict_int(value.get("byte_count"), "readiness byte_count"),
            sig=str(value.get("sig", "")),
        )


def sign_sync_ready(ready: SyncReady, keypair: Keypair) -> SyncReady:
    if ready.validator != keypair.pubkey_hex:
        raise ValueError("readiness validator does not match signing key")
    return replace(ready, sig=sign_payload(keypair, ready.signing_payload()))


def verify_sync_ready(ready: SyncReady) -> bool:
    return (
        is_valid_pubkey_hex(ready.group)
        and is_valid_event_id_hex(ready.chain_id)
        and ready.from_epoch >= 0
        and ready.to_epoch == ready.from_epoch + 1
        and is_valid_pubkey_hex(ready.validator)
        and ready.checkpoint_height >= 0
        and is_valid_event_id_hex(ready.checkpoint_block_hash)
        and is_valid_event_id_hex(ready.history_root)
        and ready.byte_count >= 0
        and is_valid_sig_hex(ready.sig)
        and verify_payload_signature(ready.validator, ready.signing_payload(), ready.sig)
    )


__all__ = [
    "IngressReceipt",
    "TimestampObservation",
    "Vote",
    "SyncReady",
    "PHASE_PREVOTE",
    "PHASE_PRECOMMIT",
    "sign_ingress_receipt",
    "sign_timestamp_observation",
    "sign_vote",
    "sign_sync_ready",
    "verify_ingress_receipt",
    "verify_timestamp_observation",
    "verify_vote",
    "verify_sync_ready",
]
