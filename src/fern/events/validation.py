from __future__ import annotations


from fern.events.event import Event
from fern.events.limits import (
    MAX_TAG_ITEMS,
    MAX_TAG_STRING_BYTES,
    MAX_TAGS,
    MAX_TYPE_BYTES,
)
from fern.bft.canonical import canonical_json
from fern.bft.constants import MAX_EVENT_BYTES, PROTOCOL_VERSION
from fern.events.serialization import canonical_serialization, compute_id
from fern.crypto.encoding import (
    is_valid_event_id_hex,
    is_valid_pubkey_hex,
    is_valid_sig_hex,
)
from fern.errors import MalformedEventError, InvalidHashError, InvalidSignatureError


def _validate_structural(event: Event) -> None:
    if event.protocol != PROTOCOL_VERSION:
        raise MalformedEventError(f"unsupported protocol: {event.protocol}")
    if not event.type or not isinstance(event.type, str):
        raise MalformedEventError("Event type must be a non-empty string")
    if len(event.type.encode("utf-8")) > MAX_TYPE_BYTES:
        raise MalformedEventError("Event type exceeds maximum length")

    if not is_valid_pubkey_hex(event.group):
        raise MalformedEventError("group must be 64-char lowercase hex")

    if not is_valid_pubkey_hex(event.author):
        raise MalformedEventError("author must be 64-char lowercase hex")

    if event.id is None or not is_valid_event_id_hex(event.id):
        raise MalformedEventError("id must be 64-char lowercase hex")

    if event.sig is None or not is_valid_sig_hex(event.sig):
        raise MalformedEventError("sig must be 128-char lowercase hex")

    if not isinstance(event.seq, int) or isinstance(event.seq, bool):
        raise MalformedEventError("seq must be an integer")
    if event.type == "genesis":
        if event.seq != 0:
            raise MalformedEventError("genesis seq must be zero")
    elif event.seq < 1:
        raise MalformedEventError("non-genesis seq must be positive")

    if not isinstance(event.ts, int) or event.ts <= 0:
        raise MalformedEventError("ts must be a positive integer")

    if not isinstance(event.content, dict):
        raise MalformedEventError("content must be a JSON object (dict)")

    if len(event.tags) > MAX_TAGS:
        raise MalformedEventError("too many tags")

    for tag in event.tags:
        if not isinstance(tag, (tuple, list)):
            raise MalformedEventError("each tag must be an array")
        if len(tag) > MAX_TAG_ITEMS:
            raise MalformedEventError("tag has too many elements")
        for elem in tag:
            if not isinstance(elem, str):
                raise MalformedEventError("each tag element must be a string")
            if len(elem.encode("utf-8")) > MAX_TAG_STRING_BYTES:
                raise MalformedEventError("tag string exceeds maximum length")

    if len(canonical_json(event.to_dict())) > MAX_EVENT_BYTES:
        raise MalformedEventError("event exceeds maximum encoded size")


def verify_event(event: Event) -> None:
    _validate_structural(event)

    actual_id = compute_id(event)
    if actual_id != event.id:
        raise InvalidHashError(f"Event ID mismatch: expected {actual_id}, got {event.id}")

    canon_bytes = canonical_serialization(event)

    if event.type == "genesis":
        pubkey_hex = event.group
    else:
        pubkey_hex = event.author

    from fern.crypto.keys import Keypair

    try:
        pubkey_bytes = bytes.fromhex(pubkey_hex)
    except ValueError:
        raise MalformedEventError(f"Invalid pubkey hex: {pubkey_hex[:20]}...")

    sig_bytes = bytes.fromhex(event.sig) if event.sig else b""
    if len(sig_bytes) != 64:
        raise InvalidSignatureError("Signature must be 64 bytes")

    if not Keypair.verify_static(pubkey_bytes, canon_bytes, sig_bytes):
        raise InvalidSignatureError("Invalid signature")


def is_well_formed(event: Event) -> bool:
    try:
        verify_event(event)
        return True
    except (MalformedEventError, InvalidHashError, InvalidSignatureError):
        return False
