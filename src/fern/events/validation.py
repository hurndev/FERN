from __future__ import annotations


from fern.events.event import Event
from fern.events.serialization import canonical_serialization, compute_id
from fern.crypto.encoding import (
    is_valid_event_id_hex,
    is_valid_pubkey_hex,
    is_valid_sig_hex,
)
from fern.errors import MalformedEventError, InvalidHashError, InvalidSignatureError


def _validate_structural(event: Event) -> None:
    if not event.type or not isinstance(event.type, str):
        raise MalformedEventError("Event type must be a non-empty string")

    if not is_valid_pubkey_hex(event.group):
        raise MalformedEventError("group must be 64-char lowercase hex")

    if not is_valid_pubkey_hex(event.author):
        raise MalformedEventError("author must be 64-char lowercase hex")

    if event.id is not None and not is_valid_event_id_hex(event.id):
        raise MalformedEventError("id must be 64-char lowercase hex")

    if event.sig is not None and not is_valid_sig_hex(event.sig):
        raise MalformedEventError("sig must be 128-char lowercase hex")

    if not isinstance(event.ts, int) or event.ts <= 0:
        raise MalformedEventError("ts must be a positive integer")

    if not isinstance(event.content, dict):
        raise MalformedEventError("content must be a JSON object (dict)")

    if not isinstance(event.parents, tuple):
        raise MalformedEventError("parents must be a tuple")

    if event.type == "genesis":
        if len(event.parents) != 0:
            raise MalformedEventError("genesis event must have empty parents")
    else:
        if len(event.parents) == 0:
            raise MalformedEventError("non-genesis event must have at least one parent")

    unique_parents = set(event.parents)
    if len(unique_parents) != len(event.parents):
        raise MalformedEventError("parents must be unique")

    for p in event.parents:
        if not is_valid_event_id_hex(p):
            raise MalformedEventError(f"parent '{p[:20]}...' must be 64-char lowercase hex")

    for tag in event.tags:
        if not isinstance(tag, (tuple, list)):
            raise MalformedEventError("each tag must be an array")
        for elem in tag:
            if not isinstance(elem, str):
                raise MalformedEventError("each tag element must be a string")


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
