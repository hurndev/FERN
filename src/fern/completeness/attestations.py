from __future__ import annotations

import json
from dataclasses import dataclass
from collections.abc import Iterable

from fern.crypto.hashes import sha256_hex
from fern.crypto.keys import Keypair


@dataclass(frozen=True)
class Attestation:
    group: str
    relay: str
    set_hash: str
    tips: tuple[str, ...]
    count: int
    prev: str | None
    ts: int
    sig: str


EMPTY_SET_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def compute_set_hash(event_ids: Iterable[str]) -> str:
    sorted_ids = sorted(event_ids)
    if not sorted_ids:
        return sha256_hex(b"")
    joined = "\n".join(sorted_ids)
    return sha256_hex(joined.encode("utf-8"))


def canonical_serialization_attestation(attestation: Attestation) -> bytes:
    array = [
        attestation.group,
        attestation.relay,
        attestation.set_hash,
        sorted(attestation.tips),
        attestation.count,
        attestation.prev,
        attestation.ts,
    ]
    return json.dumps(array, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def hash_attestation(attestation: Attestation) -> str:
    return sha256_hex(canonical_serialization_attestation(attestation))


def build_attestation(
    *,
    group: str,
    relay_keypair: Keypair,
    known_set: Iterable[str],
    tips: Iterable[str],
    count: int,
    prev: Attestation | None,
    ts: int,
) -> Attestation:
    prev_hash = hash_attestation(prev) if prev is not None else None

    attestation = Attestation(
        group=group,
        relay=relay_keypair.pubkey_hex,
        set_hash=compute_set_hash(known_set),
        tips=tuple(sorted(tips)),
        count=count,
        prev=prev_hash,
        ts=ts,
        sig="",
    )
    canon_bytes = canonical_serialization_attestation(attestation)
    sig = relay_keypair.sign_detached(canon_bytes)
    return Attestation(
        group=attestation.group,
        relay=attestation.relay,
        set_hash=attestation.set_hash,
        tips=attestation.tips,
        count=attestation.count,
        prev=attestation.prev,
        ts=attestation.ts,
        sig=sig,
    )


def verify_attestation(attestation: Attestation, prev: Attestation | None = None) -> bool:
    from fern.crypto.encoding import is_valid_pubkey_hex, is_valid_sig_hex

    if not is_valid_pubkey_hex(attestation.group):
        return False
    if not is_valid_pubkey_hex(attestation.relay):
        return False
    if not is_valid_sig_hex(attestation.sig):
        return False
    if not isinstance(attestation.ts, int) or attestation.ts <= 0:
        return False
    if not isinstance(attestation.count, int) or attestation.count < 0:
        return False

    if attestation.prev is not None:
        if (
            not all(c in "0123456789abcdef" for c in attestation.prev)
            or len(attestation.prev) != 64
        ):
            return False
    if prev is not None:
        expected_prev = hash_attestation(prev)
        if attestation.prev != expected_prev:
            return False

    tips_sorted = tuple(sorted(attestation.tips))
    if attestation.tips != tips_sorted:
        return False

    try:
        relay_pubkey = bytes.fromhex(attestation.relay)
    except ValueError:
        return False

    canon_bytes = canonical_serialization_attestation(attestation)
    try:
        sig_bytes = bytes.fromhex(attestation.sig)
    except ValueError:
        return False

    return Keypair.verify_static(relay_pubkey, canon_bytes, sig_bytes)
