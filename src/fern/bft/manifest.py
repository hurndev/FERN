from __future__ import annotations

import time
from dataclasses import dataclass, replace

from fern.bft.canonical import canonical_hash, sign_payload, strict_int, verify_payload_signature
from fern.crypto.encoding import is_valid_event_id_hex, is_valid_pubkey_hex, is_valid_sig_hex
from fern.crypto.keys import Keypair


@dataclass(frozen=True)
class AdmissionRecord:
    group: str
    chain_id: str
    epoch: int
    manifest_id: str
    checkpoint_height: int
    block_hash: str
    history_root: str
    logical_bytes: int
    admission_class: str
    witnesses: tuple[str, ...]
    accepted_at: int

    def to_dict(self) -> dict[str, object]:
        return {
            "group": self.group,
            "chain_id": self.chain_id,
            "epoch": self.epoch,
            "manifest_id": self.manifest_id,
            "checkpoint_height": self.checkpoint_height,
            "block_hash": self.block_hash,
            "history_root": self.history_root,
            "logical_bytes": self.logical_bytes,
            "admission_class": self.admission_class,
            "witnesses": list(self.witnesses),
            "accepted_at": self.accepted_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> AdmissionRecord:
        raw_witnesses = value.get("witnesses")
        if not isinstance(raw_witnesses, list) or not all(
            isinstance(item, str) for item in raw_witnesses
        ):
            raise ValueError("admission witnesses must be an array of keys")
        return cls(
            group=str(value.get("group", "")),
            chain_id=str(value.get("chain_id", "")),
            epoch=strict_int(value.get("epoch"), "admission epoch"),
            manifest_id=str(value.get("manifest_id", "")),
            checkpoint_height=strict_int(
                value.get("checkpoint_height"), "admission checkpoint_height"
            ),
            block_hash=str(value.get("block_hash", "")),
            history_root=str(value.get("history_root", "")),
            logical_bytes=strict_int(value.get("logical_bytes"), "admission logical_bytes"),
            admission_class=str(value.get("admission_class", "")),
            witnesses=tuple(str(item) for item in raw_witnesses),
            accepted_at=strict_int(value.get("accepted_at"), "admission accepted_at"),
        )


@dataclass(frozen=True)
class HistoryManifest:
    group: str
    chain_id: str
    epoch: int
    height: int
    block_hash: str
    history_root: str
    state_root: str
    event_count: int
    block_count: int
    logical_bytes: int
    max_block_bytes: int
    validator: str
    ts: int
    expires: int
    sig: str = ""

    def signing_payload(self) -> list[object]:
        return [
            "history_manifest",
            self.group,
            self.chain_id,
            self.epoch,
            self.height,
            self.block_hash,
            self.history_root,
            self.state_root,
            self.event_count,
            self.block_count,
            self.logical_bytes,
            self.max_block_bytes,
            self.validator,
            self.ts,
            self.expires,
        ]

    @property
    def id(self) -> str:
        return canonical_hash(self.signing_payload())

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "history_manifest",
            "id": self.id,
            "group": self.group,
            "chain_id": self.chain_id,
            "epoch": self.epoch,
            "height": self.height,
            "block_hash": self.block_hash,
            "history_root": self.history_root,
            "state_root": self.state_root,
            "event_count": self.event_count,
            "block_count": self.block_count,
            "logical_bytes": self.logical_bytes,
            "max_block_bytes": self.max_block_bytes,
            "validator": self.validator,
            "ts": self.ts,
            "expires": self.expires,
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> HistoryManifest:
        return cls(
            group=str(value.get("group", "")),
            chain_id=str(value.get("chain_id", "")),
            epoch=strict_int(value.get("epoch"), "manifest epoch"),
            height=strict_int(value.get("height"), "manifest height"),
            block_hash=str(value.get("block_hash", "")),
            history_root=str(value.get("history_root", "")),
            state_root=str(value.get("state_root", "")),
            event_count=strict_int(value.get("event_count"), "manifest event_count"),
            block_count=strict_int(value.get("block_count"), "manifest block_count"),
            logical_bytes=strict_int(value.get("logical_bytes"), "manifest logical_bytes"),
            max_block_bytes=strict_int(value.get("max_block_bytes"), "manifest max_block_bytes"),
            validator=str(value.get("validator", "")),
            ts=strict_int(value.get("ts"), "manifest ts"),
            expires=strict_int(value.get("expires"), "manifest expires"),
            sig=str(value.get("sig", "")),
        )


def sign_history_manifest(manifest: HistoryManifest, keypair: Keypair) -> HistoryManifest:
    if manifest.validator != keypair.pubkey_hex:
        raise ValueError("manifest validator does not match signing key")
    return replace(manifest, sig=sign_payload(keypair, manifest.signing_payload()))


def verify_history_manifest(manifest: HistoryManifest, *, now: int | None = None) -> bool:
    current = int(time.time()) if now is None else now
    return (
        is_valid_pubkey_hex(manifest.group)
        and is_valid_event_id_hex(manifest.chain_id)
        and manifest.epoch >= 0
        and manifest.height >= 0
        and all(
            is_valid_event_id_hex(value)
            for value in (manifest.block_hash, manifest.history_root, manifest.state_root)
        )
        and manifest.event_count >= 0
        and manifest.block_count == manifest.height
        and manifest.logical_bytes >= 0
        and manifest.max_block_bytes > 0
        and is_valid_pubkey_hex(manifest.validator)
        and 0 < manifest.ts <= current + 300
        and manifest.expires >= current
        and manifest.expires > manifest.ts
        and is_valid_sig_hex(manifest.sig)
        and verify_payload_signature(manifest.validator, manifest.signing_payload(), manifest.sig)
    )


@dataclass(frozen=True)
class HostingAttestation:
    group: str
    chain_id: str
    epoch: int
    manifest_id: str
    checkpoint_height: int
    block_hash: str
    history_root: str
    logical_bytes: int
    validator: str
    complete: bool
    admission_class: str
    ts: int
    expires: int
    sig: str = ""

    def signing_payload(self) -> list[object]:
        return [
            "hosting_attestation",
            self.group,
            self.chain_id,
            self.epoch,
            self.manifest_id,
            self.checkpoint_height,
            self.block_hash,
            self.history_root,
            self.logical_bytes,
            self.validator,
            self.complete,
            self.admission_class,
            self.ts,
            self.expires,
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "hosting_attestation",
            "group": self.group,
            "chain_id": self.chain_id,
            "epoch": self.epoch,
            "manifest_id": self.manifest_id,
            "checkpoint_height": self.checkpoint_height,
            "block_hash": self.block_hash,
            "history_root": self.history_root,
            "logical_bytes": self.logical_bytes,
            "validator": self.validator,
            "complete": self.complete,
            "admission_class": self.admission_class,
            "ts": self.ts,
            "expires": self.expires,
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> HostingAttestation:
        raw_complete = value.get("complete")
        if not isinstance(raw_complete, bool):
            raise ValueError("attestation complete must be a boolean")
        return cls(
            group=str(value.get("group", "")),
            chain_id=str(value.get("chain_id", "")),
            epoch=strict_int(value.get("epoch"), "attestation epoch"),
            manifest_id=str(value.get("manifest_id", "")),
            checkpoint_height=strict_int(
                value.get("checkpoint_height"), "attestation checkpoint_height"
            ),
            block_hash=str(value.get("block_hash", "")),
            history_root=str(value.get("history_root", "")),
            logical_bytes=strict_int(value.get("logical_bytes"), "attestation logical_bytes"),
            validator=str(value.get("validator", "")),
            complete=raw_complete,
            admission_class=str(value.get("admission_class", "")),
            ts=strict_int(value.get("ts"), "attestation ts"),
            expires=strict_int(value.get("expires"), "attestation expires"),
            sig=str(value.get("sig", "")),
        )


def sign_hosting_attestation(
    attestation: HostingAttestation, keypair: Keypair
) -> HostingAttestation:
    if attestation.validator != keypair.pubkey_hex:
        raise ValueError("attestation validator does not match signing key")
    return replace(attestation, sig=sign_payload(keypair, attestation.signing_payload()))


def verify_hosting_attestation(
    attestation: HostingAttestation, manifest: HistoryManifest, *, now: int | None = None
) -> bool:
    current = int(time.time()) if now is None else now
    return (
        attestation.complete
        and attestation.admission_class in {"normal", "manual", "open-genesis"}
        and attestation.group == manifest.group
        and attestation.chain_id == manifest.chain_id
        and attestation.epoch == manifest.epoch
        and attestation.manifest_id == manifest.id
        and attestation.checkpoint_height == manifest.height
        and attestation.block_hash == manifest.block_hash
        and attestation.history_root == manifest.history_root
        and attestation.logical_bytes == manifest.logical_bytes
        and is_valid_pubkey_hex(attestation.validator)
        and 0 < attestation.ts <= current + 300
        and attestation.expires >= current
        and attestation.expires > attestation.ts
        and is_valid_sig_hex(attestation.sig)
        and verify_payload_signature(
            attestation.validator, attestation.signing_payload(), attestation.sig
        )
    )


def hosting_threshold_met(
    *,
    manifest: HistoryManifest,
    attestations: tuple[HostingAttestation, ...],
    current_validator_keys: frozenset[str],
    trusted_operators: dict[str, str],
    minimum_operators: int = 2,
) -> bool:
    operators: set[str] = set()
    validators: set[str] = set()
    for attestation in attestations:
        if attestation.validator in validators:
            continue
        if attestation.validator not in current_validator_keys:
            continue
        operator = trusted_operators.get(attestation.validator)
        if not operator or not verify_hosting_attestation(attestation, manifest):
            continue
        validators.add(attestation.validator)
        operators.add(operator)
    return len(operators) >= minimum_operators


__all__ = [
    "AdmissionRecord",
    "HistoryManifest",
    "HostingAttestation",
    "hosting_threshold_met",
    "sign_history_manifest",
    "sign_hosting_attestation",
    "verify_history_manifest",
    "verify_hosting_attestation",
]
