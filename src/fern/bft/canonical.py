from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from fern.crypto.hashes import sha256_hex
from fern.crypto.keys import Keypair


JsonValue = None | bool | int | str | list["JsonValue"] | dict[str, "JsonValue"]

MAX_SAFE_INTEGER = 2**53 - 1


def strict_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
        raise ValueError(f"{field_name} exceeds the safe integer range")
    return value


def strict_dict(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} must be an object with string keys")
    return {str(key): item for key, item in value.items()}


def strict_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    return value


def normalize_json(value: Any) -> JsonValue:
    """Return a recursively deterministic, JSON-compatible value.

    Consensus payloads reject non-string mapping keys and non-JSON values
    rather than relying on implementation-specific encoder behavior.
    """

    if value is None or isinstance(value, (bool, str)):
        return value
    # Python and JavaScript do not render every numeric value identically.
    # Consensus JSON therefore uses only their shared exact integer range.
    if isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise TypeError("canonical JSON integer exceeds the safe range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("canonical JSON does not permit non-finite numbers")
        raise TypeError("canonical JSON does not permit floating-point numbers")
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        for key in sorted(value):
            assert isinstance(key, str)
            normalized[key] = normalize_json(value[key])
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [normalize_json(item) for item in value]
    raise TypeError(f"value is not canonical JSON: {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        normalize_json(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return sha256_hex(canonical_json(value))


def sign_payload(keypair: Keypair, payload: Any) -> str:
    return keypair.sign_detached(canonical_json(payload))


def verify_payload_signature(pubkey: str, payload: Any, signature: str) -> bool:
    try:
        pubkey_bytes = bytes.fromhex(pubkey)
        signature_bytes = bytes.fromhex(signature)
    except ValueError:
        return False
    if len(pubkey_bytes) != 32 or len(signature_bytes) != 64:
        return False
    return Keypair.verify_static(pubkey_bytes, canonical_json(payload), signature_bytes)
