from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from fern.crypto.hashes import sha256_hex
from fern.crypto.keys import Keypair
from fern.events.serialization import sort_keys_recursive


@dataclass(frozen=True)
class TrustedWitness:
    relay: str
    url: str


@dataclass(frozen=True)
class Threshold:
    kind: str = "ratio"
    num: int = 2
    den: int = 3
    min: int = 2


_HEAL_CHALLENGE = "heal_challenge"
_GROUP_HOST_ATTESTATION = "group_host_attestation"
_INVENTORY_ATTESTATION = "inventory_attestation"


@dataclass(frozen=True)
class HealChallenge:
    type: str = _HEAL_CHALLENGE
    group: str = ""
    receiver: str = ""
    ids_hash: str = ""
    count: int = 0
    trusted_witnesses: tuple[TrustedWitness, ...] = ()
    threshold: Threshold = field(default_factory=Threshold)
    nonce: str = ""
    ts: int = 0
    expires: int = 0
    sig: str = ""


@dataclass(frozen=True)
class GroupHostAttestation:
    type: str = _GROUP_HOST_ATTESTATION
    group: str = ""
    relay: str = ""
    receiver: str = ""
    challenge: str = ""
    hosts: bool = True
    ts: int = 0
    expires: int = 0
    sig: str = ""


@dataclass(frozen=True)
class InventoryAttestation:
    type: str = _INVENTORY_ATTESTATION
    group: str = ""
    relay: str = ""
    receiver: str = ""
    challenge: str = ""
    ids_hash: str = ""
    count: int = 0
    ts: int = 0
    expires: int = 0
    sig: str = ""


def _witness_to_obj(w: TrustedWitness) -> dict[str, str]:
    return {"relay": w.relay, "url": w.url}


def _threshold_to_obj(t: Threshold) -> dict[str, Any]:
    return {"kind": t.kind, "num": t.num, "den": t.den, "min": t.min}


def canonical_serialization_heal_challenge(challenge: HealChallenge) -> bytes:
    witnesses = [_witness_to_obj(w) for w in challenge.trusted_witnesses]
    array: list[Any] = [
        challenge.type,
        challenge.group,
        challenge.receiver,
        challenge.ids_hash,
        challenge.count,
        witnesses,
        _threshold_to_obj(challenge.threshold),
        challenge.nonce,
        challenge.ts,
        challenge.expires,
    ]
    array = sort_keys_recursive(array)  # type: ignore[assignment]
    return json.dumps(array, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_serialization_group_host_attestation(att: GroupHostAttestation) -> bytes:
    array: list[Any] = [
        att.type,
        att.group,
        att.relay,
        att.receiver,
        att.challenge,
        att.hosts,
        att.ts,
        att.expires,
    ]
    array = sort_keys_recursive(array)  # type: ignore[assignment]
    return json.dumps(array, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_serialization_inventory_attestation(att: InventoryAttestation) -> bytes:
    array: list[Any] = [
        att.type,
        att.group,
        att.relay,
        att.receiver,
        att.challenge,
        att.ids_hash,
        att.count,
        att.ts,
        att.expires,
    ]
    array = sort_keys_recursive(array)  # type: ignore[assignment]
    return json.dumps(array, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_challenge_id(challenge: HealChallenge) -> str:
    return sha256_hex(canonical_serialization_heal_challenge(challenge))


def build_heal_challenge(
    *,
    group: str,
    receiver_keypair: Keypair,
    ids: list[str],
    trusted_witnesses: tuple[TrustedWitness, ...],
    threshold: Threshold,
    ts: int,
    expires: int,
    nonce: str | None = None,
) -> HealChallenge:
    from fern.completeness.group_statuses import compute_set_hash

    if nonce is None:
        nonce = os.urandom(32).hex()

    sorted_witnesses = tuple(sorted(trusted_witnesses, key=lambda w: w.relay))
    challenge = HealChallenge(
        type=_HEAL_CHALLENGE,
        group=group,
        receiver=receiver_keypair.pubkey_hex,
        ids_hash=compute_set_hash(ids),
        count=len(ids),
        trusted_witnesses=sorted_witnesses,
        threshold=threshold,
        nonce=nonce,
        ts=ts,
        expires=expires,
        sig="",
    )
    canon_bytes = canonical_serialization_heal_challenge(challenge)
    sig = receiver_keypair.sign_detached(canon_bytes)
    return HealChallenge(
        type=challenge.type,
        group=challenge.group,
        receiver=challenge.receiver,
        ids_hash=challenge.ids_hash,
        count=challenge.count,
        trusted_witnesses=challenge.trusted_witnesses,
        threshold=challenge.threshold,
        nonce=challenge.nonce,
        ts=challenge.ts,
        expires=challenge.expires,
        sig=sig,
    )


def build_group_host_attestation(
    *,
    group: str,
    witness_keypair: Keypair,
    receiver: str,
    challenge_id: str,
    hosts: bool,
    ts: int,
    expires: int,
) -> GroupHostAttestation:
    att = GroupHostAttestation(
        type=_GROUP_HOST_ATTESTATION,
        group=group,
        relay=witness_keypair.pubkey_hex,
        receiver=receiver,
        challenge=challenge_id,
        hosts=hosts,
        ts=ts,
        expires=expires,
        sig="",
    )
    canon_bytes = canonical_serialization_group_host_attestation(att)
    sig = witness_keypair.sign_detached(canon_bytes)
    return GroupHostAttestation(
        type=att.type,
        group=att.group,
        relay=att.relay,
        receiver=att.receiver,
        challenge=att.challenge,
        hosts=att.hosts,
        ts=att.ts,
        expires=att.expires,
        sig=sig,
    )


def build_inventory_attestation(
    *,
    group: str,
    witness_keypair: Keypair,
    receiver: str,
    challenge_id: str,
    covered_ids: list[str],
    ts: int,
    expires: int,
) -> InventoryAttestation:
    from fern.completeness.group_statuses import compute_set_hash

    att = InventoryAttestation(
        type=_INVENTORY_ATTESTATION,
        group=group,
        relay=witness_keypair.pubkey_hex,
        receiver=receiver,
        challenge=challenge_id,
        ids_hash=compute_set_hash(covered_ids),
        count=len(covered_ids),
        ts=ts,
        expires=expires,
        sig="",
    )
    canon_bytes = canonical_serialization_inventory_attestation(att)
    sig = witness_keypair.sign_detached(canon_bytes)
    return InventoryAttestation(
        type=att.type,
        group=att.group,
        relay=att.relay,
        receiver=att.receiver,
        challenge=att.challenge,
        ids_hash=att.ids_hash,
        count=att.count,
        ts=att.ts,
        expires=att.expires,
        sig=sig,
    )


def verify_heal_challenge(
    challenge: HealChallenge,
    *,
    receiver_pubkey: str | None = None,
    now_ts: int,
) -> bool:
    from fern.crypto.encoding import is_valid_pubkey_hex, is_valid_sig_hex
    from fern.completeness.group_statuses import EMPTY_SET_HASH

    if challenge.type != _HEAL_CHALLENGE:
        return False
    if not is_valid_pubkey_hex(challenge.group):
        return False
    if not is_valid_pubkey_hex(challenge.receiver):
        return False
    if not is_valid_sig_hex(challenge.sig):
        return False
    if len(challenge.ids_hash) != 64 or any(c not in "0123456789abcdef" for c in challenge.ids_hash):
        return False
    if not isinstance(challenge.count, int) or challenge.count < 0:
        return False
    if not isinstance(challenge.ts, int) or challenge.ts <= 0:
        return False
    if not isinstance(challenge.expires, int) or challenge.expires <= 0:
        return False
    if challenge.expires <= now_ts:
        return False
    if challenge.threshold.kind != "ratio":
        return False
    if not isinstance(challenge.threshold.num, int) or challenge.threshold.num <= 0:
        return False
    if not isinstance(challenge.threshold.den, int) or challenge.threshold.den <= 0:
        return False
    if not isinstance(challenge.threshold.min, int) or challenge.threshold.min <= 0:
        return False
    if len(challenge.nonce) == 0:
        return False
    if challenge.count == 0 and challenge.ids_hash != EMPTY_SET_HASH:
        return False

    if receiver_pubkey is not None and challenge.receiver != receiver_pubkey:
        return False

    if challenge.trusted_witnesses:
        witness_pubkeys = [w.relay for w in challenge.trusted_witnesses]
        if witness_pubkeys != sorted(witness_pubkeys):
            return False
        for w in challenge.trusted_witnesses:
            if not is_valid_pubkey_hex(w.relay):
                return False
            if not w.url:
                return False

    try:
        receiver_bytes = bytes.fromhex(challenge.receiver)
    except ValueError:
        return False

    canon_bytes = canonical_serialization_heal_challenge(challenge)
    try:
        sig_bytes = bytes.fromhex(challenge.sig)
    except ValueError:
        return False

    return Keypair.verify_static(receiver_bytes, canon_bytes, sig_bytes)


def verify_group_host_attestation(
    att: GroupHostAttestation,
    *,
    challenge_id: str | None = None,
    witness_pubkey: str | None = None,
    now_ts: int,
) -> bool:
    from fern.crypto.encoding import is_valid_pubkey_hex, is_valid_sig_hex

    if att.type != _GROUP_HOST_ATTESTATION:
        return False
    if not is_valid_pubkey_hex(att.group):
        return False
    if not is_valid_pubkey_hex(att.relay):
        return False
    if not is_valid_pubkey_hex(att.receiver):
        return False
    if not is_valid_sig_hex(att.sig):
        return False
    if len(att.challenge) != 64 or any(c not in "0123456789abcdef" for c in att.challenge):
        return False
    if not isinstance(att.hosts, bool):
        return False
    if not isinstance(att.ts, int) or att.ts <= 0:
        return False
    if not isinstance(att.expires, int) or att.expires <= 0:
        return False
    if att.expires <= now_ts:
        return False

    if challenge_id is not None and att.challenge != challenge_id:
        return False
    if witness_pubkey is not None and att.relay != witness_pubkey:
        return False

    try:
        relay_bytes = bytes.fromhex(att.relay)
    except ValueError:
        return False

    canon_bytes = canonical_serialization_group_host_attestation(att)
    try:
        sig_bytes = bytes.fromhex(att.sig)
    except ValueError:
        return False

    return Keypair.verify_static(relay_bytes, canon_bytes, sig_bytes)


def verify_inventory_attestation(
    att: InventoryAttestation,
    *,
    challenge_id: str | None = None,
    witness_pubkey: str | None = None,
    now_ts: int,
    covered_ids: list[str] | None = None,
) -> bool:
    from fern.crypto.encoding import is_valid_pubkey_hex, is_valid_sig_hex
    from fern.completeness.group_statuses import compute_set_hash

    if att.type != _INVENTORY_ATTESTATION:
        return False
    if not is_valid_pubkey_hex(att.group):
        return False
    if not is_valid_pubkey_hex(att.relay):
        return False
    if not is_valid_pubkey_hex(att.receiver):
        return False
    if not is_valid_sig_hex(att.sig):
        return False
    if len(att.challenge) != 64 or any(c not in "0123456789abcdef" for c in att.challenge):
        return False
    if len(att.ids_hash) != 64 or any(c not in "0123456789abcdef" for c in att.ids_hash):
        return False
    if not isinstance(att.count, int) or att.count < 0:
        return False
    if not isinstance(att.ts, int) or att.ts <= 0:
        return False
    if not isinstance(att.expires, int) or att.expires <= 0:
        return False
    if att.expires <= now_ts:
        return False

    if challenge_id is not None and att.challenge != challenge_id:
        return False
    if witness_pubkey is not None and att.relay != witness_pubkey:
        return False

    if covered_ids is not None:
        if compute_set_hash(covered_ids) != att.ids_hash:
            return False
        if att.count != len(covered_ids):
            return False

    try:
        relay_bytes = bytes.fromhex(att.relay)
    except ValueError:
        return False

    canon_bytes = canonical_serialization_inventory_attestation(att)
    try:
        sig_bytes = bytes.fromhex(att.sig)
    except ValueError:
        return False

    return Keypair.verify_static(relay_bytes, canon_bytes, sig_bytes)


def threshold_required(n: int, threshold: Threshold) -> int:
    if n <= 0:
        return 1
    ceil_div = (threshold.num * n + threshold.den - 1) // threshold.den
    return max(threshold.min, ceil_div)