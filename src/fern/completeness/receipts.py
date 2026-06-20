from __future__ import annotations

import json
from dataclasses import dataclass

from fern.crypto.keys import Keypair
from fern.events.event import Event


@dataclass(frozen=True)
class Receipt:
    event_id: str
    group: str
    relay: str
    ts: int
    sig: str


def canonical_serialization_receipt(receipt: Receipt) -> bytes:
    array = [receipt.event_id, receipt.group, receipt.relay, receipt.ts]
    return json.dumps(array, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def build_receipt(*, event: Event, relay_keypair: Keypair, ts: int) -> Receipt:
    assert event.id is not None, "event must have an id"
    receipt = Receipt(
        event_id=event.id,
        group=event.group,
        relay=relay_keypair.pubkey_hex,
        ts=ts,
        sig="",
    )
    canon_bytes = canonical_serialization_receipt(receipt)
    sig = relay_keypair.sign_detached(canon_bytes)
    return Receipt(
        event_id=receipt.event_id,
        group=receipt.group,
        relay=receipt.relay,
        ts=receipt.ts,
        sig=sig,
    )


def verify_receipt(receipt: Receipt) -> bool:
    from fern.crypto.encoding import is_valid_event_id_hex, is_valid_pubkey_hex, is_valid_sig_hex

    if not is_valid_event_id_hex(receipt.event_id):
        return False
    if not is_valid_pubkey_hex(receipt.group):
        return False
    if not is_valid_pubkey_hex(receipt.relay):
        return False
    if not is_valid_sig_hex(receipt.sig):
        return False
    if not isinstance(receipt.ts, int) or receipt.ts <= 0:
        return False

    try:
        relay_pubkey = bytes.fromhex(receipt.relay)
    except ValueError:
        return False

    canon_bytes = canonical_serialization_receipt(receipt)
    try:
        sig_bytes = bytes.fromhex(receipt.sig)
    except ValueError:
        return False

    return Keypair.verify_static(relay_pubkey, canon_bytes, sig_bytes)
