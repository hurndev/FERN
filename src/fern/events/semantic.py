from __future__ import annotations

from urllib.parse import urlparse

from fern.crypto.encoding import is_valid_event_id_hex, is_valid_pubkey_hex
from fern.events.event import Event
from fern.events.limits import (
    MAX_ADMINS,
    MAX_APP_NAME_BYTES,
    MAX_BAN_REASON_BYTES,
    MAX_CHANNEL_DESCRIPTION_BYTES,
    MAX_CHANNEL_ID_BYTES,
    MAX_CHANNEL_NAME_BYTES,
    MAX_GROUP_DESCRIPTION_BYTES,
    MAX_GROUP_NAME_BYTES,
    MAX_MESSAGE_TEXT_BYTES,
    MAX_NICKNAME_BYTES,
    MAX_REACTION_BYTES,
    MAX_RELAYS,
    MAX_RELAY_URL_BYTES,
)
from fern.events.types import ChatTypes, ProtocolTypes


class SemanticValidationError(ValueError):
    pass


def _byte_len(value: str) -> int:
    return len(value.encode("utf-8"))


def _string(value: object, field: str, *, min_bytes: int = 0, max_bytes: int) -> str:
    if not isinstance(value, str):
        raise SemanticValidationError(f"{field} must be a string")
    size = _byte_len(value)
    if size < min_bytes:
        raise SemanticValidationError(f"{field} is too short")
    if size > max_bytes:
        raise SemanticValidationError(f"{field} exceeds maximum length")
    return value


def _pubkey(value: object, field: str) -> str:
    if not isinstance(value, str) or not is_valid_pubkey_hex(value):
        raise SemanticValidationError(f"{field} must be a pubkey")
    return value


def _event_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not is_valid_event_id_hex(value):
        raise SemanticValidationError(f"{field} must be an event id")
    return value


def _int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SemanticValidationError(f"{field} must be an integer")
    return value


def _only(content: dict[str, object], allowed: set[str]) -> None:
    extra = set(content) - allowed
    if extra:
        raise SemanticValidationError(f"unexpected content field: {sorted(extra)[0]}")


def _relay_url(value: object) -> str:
    url = _string(value, "relay", min_bytes=1, max_bytes=MAX_RELAY_URL_BYTES)
    parsed = urlparse(url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
        raise SemanticValidationError("relay must be a ws:// or wss:// URL")
    return url


def _channel_id(value: object, field: str = "channel id") -> str:
    s = _string(value, field, min_bytes=MAX_CHANNEL_ID_BYTES, max_bytes=MAX_CHANNEL_ID_BYTES)
    if not is_valid_event_id_hex(s):
        raise SemanticValidationError(f"{field} must be 64-char lowercase hex")
    return s


def _validate_chat_channel(raw: object) -> str:
    if not isinstance(raw, dict):
        raise SemanticValidationError("chat channel must be an object")
    _only(raw, {"id", "name", "description", "position"})
    channel_id = _channel_id(raw.get("id"), "channel.id")
    _string(raw.get("name"), "channel.name", min_bytes=1, max_bytes=MAX_CHANNEL_NAME_BYTES)
    if "description" in raw:
        _string(raw["description"], "channel.description", max_bytes=MAX_CHANNEL_DESCRIPTION_BYTES)
    if "position" in raw:
        _int(raw["position"], "channel.position")
    return channel_id


def validate_event_semantics(event: Event) -> None:
    c = event.content
    t = event.type

    if t == ProtocolTypes.GENESIS:
        required = {"name", "description", "public", "founder", "admins", "relays", "app"}
        missing = required - set(c)
        if missing:
            raise SemanticValidationError(f"missing genesis field: {sorted(missing)[0]}")
        known_bare = required
        for key in c:
            if "." not in key and key not in known_bare:
                raise SemanticValidationError(f"unexpected genesis protocol field: {key}")
        _string(c["name"], "name", min_bytes=1, max_bytes=MAX_GROUP_NAME_BYTES)
        _string(c["description"], "description", max_bytes=MAX_GROUP_DESCRIPTION_BYTES)
        if not isinstance(c["public"], bool):
            raise SemanticValidationError("public must be a boolean")
        founder = _pubkey(c["founder"], "founder")
        if founder != event.author:
            raise SemanticValidationError("founder must equal author")
        admins = c["admins"]
        if not isinstance(admins, list) or not 1 <= len(admins) <= MAX_ADMINS:
            raise SemanticValidationError("admins must be a non-empty bounded array")
        for admin in admins:
            _pubkey(admin, "admin")
        if founder not in admins:
            raise SemanticValidationError("admins must include founder")
        relays = c["relays"]
        if not isinstance(relays, list) or not 1 <= len(relays) <= MAX_RELAYS:
            raise SemanticValidationError("relays must be a non-empty bounded array")
        for relay in relays:
            _relay_url(relay)
        app = _string(c["app"], "app", min_bytes=1, max_bytes=MAX_APP_NAME_BYTES)
        if app == "chat":
            channels = c.get("chat.channels")
            if not isinstance(channels, list) or not channels:
                raise SemanticValidationError("chat.channels must be a non-empty array")
            for raw in channels:
                _validate_chat_channel(raw)
            if "chat.default_channel" in c:
                _channel_id(c["chat.default_channel"], "chat.default_channel")
            if "chat.system_channel" in c:
                _channel_id(c["chat.system_channel"], "chat.system_channel")
        return

    if t in (ProtocolTypes.JOIN, ProtocolTypes.LEAVE):
        _only(c, set())
    elif t == ProtocolTypes.INVITE:
        _only(c, {"invitee", "role"})
        _pubkey(c.get("invitee"), "invitee")
        if c.get("role") != "member":
            raise SemanticValidationError("role must be member")
    elif t in (ProtocolTypes.KICK, ProtocolTypes.UNBAN, ProtocolTypes.ADMIN_ADD, ProtocolTypes.ADMIN_REMOVE):
        _only(c, {"target"})
        _pubkey(c.get("target"), "target")
    elif t == ProtocolTypes.BAN:
        _only(c, {"target", "until", "reason"})
        _pubkey(c.get("target"), "target")
        if c.get("until") is not None:
            until = _int(c.get("until"), "until")
            if until <= 0:
                raise SemanticValidationError("until must be positive")
        _string(c.get("reason", ""), "reason", max_bytes=MAX_BAN_REASON_BYTES)
    elif t == ProtocolTypes.RELAY_UPDATE:
        _only(c, {"relays"})
        relays = c.get("relays")
        if not isinstance(relays, list) or not 1 <= len(relays) <= MAX_RELAYS:
            raise SemanticValidationError("relays must be a non-empty bounded array")
        for relay in relays:
            _relay_url(relay)
    elif t == ProtocolTypes.METADATA_UPDATE:
        _only(c, {"name", "description"})
        if "name" not in c and "description" not in c:
            raise SemanticValidationError("metadata_update must include a field")
        if "name" in c:
            _string(c["name"], "name", min_bytes=1, max_bytes=MAX_GROUP_NAME_BYTES)
        if "description" in c:
            _string(c["description"], "description", max_bytes=MAX_GROUP_DESCRIPTION_BYTES)
    elif t == ChatTypes.MESSAGE:
        _only(c, {"text", "channel", "reply_to"})
        _string(c.get("text"), "text", min_bytes=1, max_bytes=MAX_MESSAGE_TEXT_BYTES)
        _channel_id(c.get("channel"), "channel")
        if c.get("reply_to") is not None:
            _event_id(c.get("reply_to"), "reply_to")
    elif t == ChatTypes.REACTION:
        _only(c, {"target", "emoji"})
        _event_id(c.get("target"), "target")
        _string(c.get("emoji"), "emoji", min_bytes=1, max_bytes=MAX_REACTION_BYTES)
    elif t == ChatTypes.NICKNAME_SET:
        _only(c, {"nickname"})
        _string(c.get("nickname"), "nickname", min_bytes=1, max_bytes=MAX_NICKNAME_BYTES)
    elif t == ChatTypes.CHANNEL_CREATE:
        _only(c, {"id", "name", "description", "position"})
        _channel_id(c.get("id"), "id")
        _string(c.get("name"), "name", min_bytes=1, max_bytes=MAX_CHANNEL_NAME_BYTES)
        if "description" in c:
            _string(c["description"], "description", max_bytes=MAX_CHANNEL_DESCRIPTION_BYTES)
        if "position" in c:
            _int(c["position"], "position")
    elif t == ChatTypes.CHANNEL_UPDATE:
        _only(c, {"id", "name", "description", "position"})
        _channel_id(c.get("id"), "id")
        if not any(key in c for key in ("name", "description", "position")):
            raise SemanticValidationError("channel_update must include an update")
        if "name" in c:
            _string(c["name"], "name", min_bytes=1, max_bytes=MAX_CHANNEL_NAME_BYTES)
        if "description" in c:
            _string(c["description"], "description", max_bytes=MAX_CHANNEL_DESCRIPTION_BYTES)
        if "position" in c:
            _int(c["position"], "position")
    elif t == ChatTypes.CHANNEL_DELETE:
        _only(c, {"id", "name"})
        _channel_id(c.get("id"), "id")
        if "name" in c:
            _string(c["name"], "name", min_bytes=1, max_bytes=MAX_CHANNEL_NAME_BYTES)
    elif t == ChatTypes.SETTINGS_UPDATE:
        _only(c, {"default_channel", "system_channel"})
        if "default_channel" not in c and "system_channel" not in c:
            raise SemanticValidationError("settings_update must include a field")
        if "default_channel" in c:
            _channel_id(c["default_channel"], "default_channel")
        if "system_channel" in c:
            _channel_id(c["system_channel"], "system_channel")
    else:
        raise SemanticValidationError(f"unknown event type: {t}")
