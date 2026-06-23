from __future__ import annotations

import json
from dataclasses import dataclass

from fern.crypto.keys import Keypair
from fern.events.event import Event


@dataclass(frozen=True)
class EventReceipt:
    event_id: str
    group: str
    relay: str
    ts: int
    sig: str


def canonical_serialization_event_receipt(event_receipt: EventReceipt) -> bytes:
    array = [event_receipt.event_id, event_receipt.group, event_receipt.relay, event_receipt.ts]
    return json.dumps(array, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def build_event_receipt(*, event: Event, relay_keypair: Keypair, ts: int) -> EventReceipt:
    assert event.id is not None, "event must have an id"
    event_receipt = EventReceipt(
        event_id=event.id,
        group=event.group,
        relay=relay_keypair.pubkey_hex,
        ts=ts,
        sig="",
    )
    canon_bytes = canonical_serialization_event_receipt(event_receipt)
    sig = relay_keypair.sign_detached(canon_bytes)
    return EventReceipt(
        event_id=event_receipt.event_id,
        group=event_receipt.group,
        relay=event_receipt.relay,
        ts=event_receipt.ts,
        sig=sig,
    )


def verify_event_receipt(event_receipt: EventReceipt) -> bool:
    from fern.crypto.encoding import is_valid_event_id_hex, is_valid_pubkey_hex, is_valid_sig_hex

    if not is_valid_event_id_hex(event_receipt.event_id):
        return False
    if not is_valid_pubkey_hex(event_receipt.group):
        return False
    if not is_valid_pubkey_hex(event_receipt.relay):
        return False
    if not is_valid_sig_hex(event_receipt.sig):
        return False
    if not isinstance(event_receipt.ts, int) or event_receipt.ts <= 0:
        return False

    try:
        relay_pubkey = bytes.fromhex(event_receipt.relay)
    except ValueError:
        return False

    canon_bytes = canonical_serialization_event_receipt(event_receipt)
    try:
        sig_bytes = bytes.fromhex(event_receipt.sig)
    except ValueError:
        return False

    return Keypair.verify_static(relay_pubkey, canon_bytes, sig_bytes)
