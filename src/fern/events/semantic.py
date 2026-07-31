"""Context-free semantic validation for core (protocol) events.

Core events are the bare types the protocol owns (membership, validator set,
group metadata). Application events (dotted types) are validated by their app
module, which reuses the generic field helpers exported here.

Validation here is context-free: membership, authorization, expected sequence
and certified-time bans depend on a particular finalized state and live in
``fern.bft.app`` / the app module.
"""
from __future__ import annotations

from fern.bft.validators import Validator, make_validator_set
from fern.crypto.encoding import is_valid_event_id_hex, is_valid_pubkey_hex
from fern.events.event import Event
from fern.events.limits import (
    MAX_APP_NAME_BYTES,
    MAX_BAN_REASON_BYTES,
    MAX_GROUP_DESCRIPTION_BYTES,
    MAX_GROUP_NAME_BYTES,
)
from fern.events.types import ProtocolTypes


class SemanticValidationError(ValueError):
    pass


# --- Generic content-field helpers (shared with app modules) -----------------


def byte_len(value: str) -> int:
    return len(value.encode("utf-8"))


def string_field(value: object, field: str, *, min_bytes: int = 0, max_bytes: int) -> str:
    if not isinstance(value, str):
        raise SemanticValidationError(f"{field} must be a string")
    size = byte_len(value)
    if size < min_bytes:
        raise SemanticValidationError(f"{field} is too short")
    if size > max_bytes:
        raise SemanticValidationError(f"{field} exceeds maximum length")
    return value


def pubkey_field(value: object, field: str) -> str:
    if not isinstance(value, str) or not is_valid_pubkey_hex(value):
        raise SemanticValidationError(f"{field} must be a pubkey")
    return value


def event_id_field(value: object, field: str) -> str:
    if not isinstance(value, str) or not is_valid_event_id_hex(value):
        raise SemanticValidationError(f"{field} must be an event id")
    return value


def int_field(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SemanticValidationError(f"{field} must be an integer")
    return value


def only_fields(content: dict[str, object], allowed: set[str]) -> None:
    extra = set(content) - allowed
    if extra:
        raise SemanticValidationError(f"unexpected content field: {sorted(extra)[0]}")


# --- Core event validation ---------------------------------------------------


def _validate_validator_list(content: dict[str, object]) -> None:
    raw_validators = content.get("validators")
    if not isinstance(raw_validators, list):
        raise SemanticValidationError("validators must be an array")
    try:
        validators = [Validator.from_dict(raw) for raw in raw_validators if isinstance(raw, dict)]
        if len(validators) != len(raw_validators):
            raise ValueError("invalid validator entry")
        fault_tolerance = int_field(content["fault_tolerance"], "fault_tolerance")
        make_validator_set(validators, epoch=0, fault_tolerance=fault_tolerance)
    except ValueError as exc:
        raise SemanticValidationError(str(exc)) from exc


def validate_genesis_core(event: Event) -> None:
    """Validate the core (bare) fields of a genesis event.

    App-namespaced (dotted) fields are validated by the app module; this only
    checks that they belong to the declared app's namespace.
    """
    c = event.content
    required = {
        "chain_id",
        "name",
        "description",
        "public",
        "founder",
        "validators",
        "fault_tolerance",
        "app",
    }
    missing = required - set(c)
    if missing:
        raise SemanticValidationError(f"missing genesis field: {sorted(missing)[0]}")
    for key in c:
        if "." not in key and key not in required:
            raise SemanticValidationError(f"unexpected genesis protocol field: {key}")
    string_field(c["name"], "name", min_bytes=1, max_bytes=MAX_GROUP_NAME_BYTES)
    string_field(c["description"], "description", max_bytes=MAX_GROUP_DESCRIPTION_BYTES)
    if not isinstance(c["public"], bool):
        raise SemanticValidationError("public must be a boolean")
    founder = pubkey_field(c["founder"], "founder")
    if founder != event.author:
        raise SemanticValidationError("founder must equal author")
    chain_id = c["chain_id"]
    if not isinstance(chain_id, str) or not is_valid_event_id_hex(chain_id):
        raise SemanticValidationError("chain_id must be 64-char lowercase hex")
    _validate_validator_list(c)
    app = string_field(c["app"], "app", min_bytes=1, max_bytes=MAX_APP_NAME_BYTES)
    prefix = app + "."
    for key in c:
        if "." in key and not key.startswith(prefix):
            raise SemanticValidationError(f"genesis field for foreign app namespace: {key}")


def validate_core_event_semantics(event: Event) -> None:
    c = event.content
    t = event.type

    if t in (ProtocolTypes.JOIN, ProtocolTypes.LEAVE):
        only_fields(c, set())
    elif t == ProtocolTypes.INVITE:
        only_fields(c, {"invitee", "role"})
        pubkey_field(c.get("invitee"), "invitee")
        if c.get("role") != "member":
            raise SemanticValidationError("role must be member")
    elif t in (ProtocolTypes.KICK, ProtocolTypes.UNBAN):
        only_fields(c, {"target"})
        pubkey_field(c.get("target"), "target")
    elif t == ProtocolTypes.BAN:
        only_fields(c, {"target", "until", "reason"})
        pubkey_field(c.get("target"), "target")
        if c.get("until") is not None:
            until = int_field(c.get("until"), "until")
            if until <= 0:
                raise SemanticValidationError("until must be positive")
        string_field(c.get("reason", ""), "reason", max_bytes=MAX_BAN_REASON_BYTES)
    elif t == ProtocolTypes.VALIDATOR_UPDATE:
        only_fields(c, {"validators", "fault_tolerance", "readiness"})
        _validate_validator_list(c)
        if not isinstance(c.get("readiness"), list):
            raise SemanticValidationError("readiness must be an array")
    elif t == ProtocolTypes.METADATA_UPDATE:
        only_fields(c, {"name", "description"})
        if "name" not in c and "description" not in c:
            raise SemanticValidationError("metadata_update must include a field")
        if "name" in c:
            string_field(c["name"], "name", min_bytes=1, max_bytes=MAX_GROUP_NAME_BYTES)
        if "description" in c:
            string_field(c["description"], "description", max_bytes=MAX_GROUP_DESCRIPTION_BYTES)
    else:
        raise SemanticValidationError(f"unknown core event type: {t}")


__all__ = [
    "SemanticValidationError",
    "byte_len",
    "event_id_field",
    "int_field",
    "only_fields",
    "pubkey_field",
    "string_field",
    "validate_core_event_semantics",
    "validate_genesis_core",
]
