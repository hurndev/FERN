from __future__ import annotations

import json
from dataclasses import dataclass
from collections.abc import Iterable

from fern.crypto.hashes import sha256_hex
from fern.crypto.keys import Keypair


@dataclass(frozen=True)
class GroupStatus:
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


def canonical_serialization_group_status(group_status: GroupStatus) -> bytes:
    array = [
        group_status.group,
        group_status.relay,
        group_status.set_hash,
        sorted(group_status.tips),
        group_status.count,
        group_status.prev,
        group_status.ts,
    ]
    return json.dumps(array, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def hash_group_status(group_status: GroupStatus) -> str:
    return sha256_hex(canonical_serialization_group_status(group_status))


def build_group_status(
    *,
    group: str,
    relay_keypair: Keypair,
    known_set: Iterable[str],
    tips: Iterable[str],
    count: int,
    prev: GroupStatus | None,
    ts: int,
) -> GroupStatus:
    prev_hash = hash_group_status(prev) if prev is not None else None

    group_status = GroupStatus(
        group=group,
        relay=relay_keypair.pubkey_hex,
        set_hash=compute_set_hash(known_set),
        tips=tuple(sorted(tips)),
        count=count,
        prev=prev_hash,
        ts=ts,
        sig="",
    )
    canon_bytes = canonical_serialization_group_status(group_status)
    sig = relay_keypair.sign_detached(canon_bytes)
    return GroupStatus(
        group=group_status.group,
        relay=group_status.relay,
        set_hash=group_status.set_hash,
        tips=group_status.tips,
        count=group_status.count,
        prev=group_status.prev,
        ts=group_status.ts,
        sig=sig,
    )


def verify_group_status(group_status: GroupStatus, prev: GroupStatus | None = None) -> bool:
    from fern.crypto.encoding import is_valid_pubkey_hex, is_valid_sig_hex

    if not is_valid_pubkey_hex(group_status.group):
        return False
    if not is_valid_pubkey_hex(group_status.relay):
        return False
    if not is_valid_sig_hex(group_status.sig):
        return False
    if not isinstance(group_status.ts, int) or group_status.ts <= 0:
        return False
    if not isinstance(group_status.count, int) or group_status.count < 0:
        return False

    if group_status.prev is not None:
        if (
            not all(c in "0123456789abcdef" for c in group_status.prev)
            or len(group_status.prev) != 64
        ):
            return False
    if prev is not None:
        expected_prev = hash_group_status(prev)
        if group_status.prev != expected_prev:
            return False

    tips_sorted = tuple(sorted(group_status.tips))
    if group_status.tips != tips_sorted:
        return False

    try:
        relay_pubkey = bytes.fromhex(group_status.relay)
    except ValueError:
        return False

    canon_bytes = canonical_serialization_group_status(group_status)
    try:
        sig_bytes = bytes.fromhex(group_status.sig)
    except ValueError:
        return False

    return Keypair.verify_static(relay_pubkey, canon_bytes, sig_bytes)
