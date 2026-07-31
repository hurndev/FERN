"""The built-in ``chat`` application module.

Implements :class:`~fern.bft.app.AppModule` for the chat app: it owns all chat
state (managers, mods, channels, settings), validates chat event content, and
makes every chat policy decision (who may moderate, who may manage roles, who
may post). The protocol core delegates authorization here and never encodes
chat rules itself.

Role model: **managers** are supreme (they may change validators, manage roles,
and moderate); **mods** are a delegated moderation tier (ban/kick/invite and
channel management, but not validators or role management). Both are chat-app
state, not protocol state.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from fern.bft.app import ApplicationError, AppState, CoreState
from fern.bft.canonical import strict_dict, strict_int
from fern.crypto.encoding import is_valid_event_id_hex
from fern.events.event import Event
from fern.events.limits import (
    MAX_CHANNEL_DESCRIPTION_BYTES,
    MAX_CHANNEL_ID_BYTES,
    MAX_CHANNEL_NAME_BYTES,
    MAX_MESSAGE_TEXT_BYTES,
    MAX_NICKNAME_BYTES,
    MAX_REACTION_BYTES,
    MAX_ROLE_ENTRIES,
)
from fern.events.semantic import (
    SemanticValidationError,
    event_id_field,
    int_field,
    only_fields,
    pubkey_field,
    string_field,
)
from fern.events.types import ChatTypes, ProtocolTypes

# Events any joined, non-banned member may send.
_CONTENT_TYPES = frozenset(
    {ChatTypes.MESSAGE, ChatTypes.REACTION, ChatTypes.NICKNAME_SET}
)

# Events a mod (not just a manager) may perform. Managers can do everything.
_MOD_ALLOWED = frozenset(
    {
        ProtocolTypes.INVITE,
        ProtocolTypes.KICK,
        ProtocolTypes.BAN,
        ProtocolTypes.UNBAN,
        ChatTypes.CHANNEL_CREATE,
        ChatTypes.CHANNEL_UPDATE,
        ChatTypes.CHANNEL_DELETE,
        ChatTypes.SETTINGS_UPDATE,
    }
)


def channel_id_field(value: object, field: str = "channel id") -> str:
    s = string_field(value, field, min_bytes=MAX_CHANNEL_ID_BYTES, max_bytes=MAX_CHANNEL_ID_BYTES)
    if not is_valid_event_id_hex(s):
        raise SemanticValidationError(f"{field} must be 64-char lowercase hex")
    return s


@dataclass(frozen=True)
class ChannelRecord:
    id: str
    name: str
    description: str = ""
    position: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "position": self.position,
        }


@dataclass(frozen=True)
class ChatState:
    managers: frozenset[str]
    mods: frozenset[str]
    channels: dict[str, ChannelRecord]
    settings: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "managers": sorted(self.managers),
            "mods": sorted(self.mods),
            "channels": {key: self.channels[key].to_dict() for key in sorted(self.channels)},
            "chat_settings": {key: self.settings[key] for key in sorted(self.settings)},
        }


def _list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    return value


class ChatApp:
    name = "chat"
    boundary_types = frozenset(
        {
            ChatTypes.CHANNEL_CREATE,
            ChatTypes.CHANNEL_DELETE,
            ChatTypes.MANAGER_ADD,
            ChatTypes.MANAGER_REMOVE,
            ChatTypes.MOD_ADD,
            ChatTypes.MOD_REMOVE,
        }
    )

    # --- state construction / serialization --------------------------------

    def initial_state(self, genesis_content: dict[str, object]) -> ChatState:
        channels: dict[str, ChannelRecord] = {}
        raw_channels = genesis_content["chat.channels"]
        assert isinstance(raw_channels, list)
        for index, item in enumerate(raw_channels):
            assert isinstance(item, dict)
            channel_id = str(item["id"])
            channels[channel_id] = ChannelRecord(
                id=channel_id,
                name=str(item["name"]),
                description=str(item.get("description", "")),
                position=int(item.get("position", index)),
            )
        first_channel = next(iter(channels), "")
        founder = str(genesis_content["founder"])
        raw_managers = genesis_content.get("chat.managers")
        managers = (
            frozenset(str(key) for key in raw_managers)
            if isinstance(raw_managers, list) and raw_managers
            else frozenset({founder})
        )
        raw_mods = genesis_content.get("chat.mods")
        mods = (
            frozenset(str(key) for key in raw_mods)
            if isinstance(raw_mods, list)
            else frozenset()
        )
        settings = (
            {
                "default_channel": str(genesis_content.get("chat.default_channel", first_channel)),
                "system_channel": str(genesis_content.get("chat.system_channel", first_channel)),
            }
            if channels
            else {}
        )
        return ChatState(managers=managers, mods=mods, channels=channels, settings=settings)

    def state_from_dict(self, value: dict[str, object]) -> ChatState:
        raw_channels = strict_dict(value.get("channels"), "state channels")
        channels = {
            str(key): ChannelRecord(
                id=str(item.get("id", key)),
                name=str(item.get("name", "")),
                description=str(item.get("description", "")),
                position=strict_int(item.get("position"), "channel position"),
            )
            for key, item in raw_channels.items()
            if isinstance(item, dict)
        }
        if len(channels) != len(raw_channels):
            raise ValueError("invalid channel state")
        raw_settings = strict_dict(value.get("chat_settings"), "state chat_settings")
        return ChatState(
            managers=frozenset(str(key) for key in _list(value.get("managers"), "managers")),
            mods=frozenset(str(key) for key in _list(value.get("mods"), "mods")),
            channels=channels,
            settings={str(key): str(item) for key, item in raw_settings.items()},
        )

    # --- validation --------------------------------------------------------

    def validate_genesis(self, genesis_content: dict[str, object]) -> None:
        channels = genesis_content.get("chat.channels")
        if not isinstance(channels, list) or not channels:
            raise SemanticValidationError("chat.channels must be a non-empty array")
        channel_ids = [_validate_chat_channel(raw) for raw in channels]
        if len(channel_ids) != len(set(channel_ids)):
            raise SemanticValidationError("chat channel ids must be unique")
        channel_names = [str(raw["name"]) for raw in channels if isinstance(raw, dict)]
        if len(channel_names) != len(set(channel_names)):
            raise SemanticValidationError("chat channel names must be unique")
        if "chat.default_channel" in genesis_content:
            default = channel_id_field(genesis_content["chat.default_channel"], "chat.default_channel")
            if default not in channel_ids:
                raise SemanticValidationError("chat.default_channel is unknown")
        if "chat.system_channel" in genesis_content:
            system = channel_id_field(genesis_content["chat.system_channel"], "chat.system_channel")
            if system not in channel_ids:
                raise SemanticValidationError("chat.system_channel is unknown")
        if "chat.managers" in genesis_content:
            managers = genesis_content["chat.managers"]
            if not isinstance(managers, list) or not managers or len(managers) > MAX_ROLE_ENTRIES:
                raise SemanticValidationError("chat.managers must be a non-empty bounded array")
            for key in managers:
                pubkey_field(key, "chat.managers entry")
        if "chat.mods" in genesis_content:
            mods = genesis_content["chat.mods"]
            if not isinstance(mods, list) or len(mods) > MAX_ROLE_ENTRIES:
                raise SemanticValidationError("chat.mods must be a bounded array")
            for key in mods:
                pubkey_field(key, "chat.mods entry")
        known = {
            "chat.channels",
            "chat.default_channel",
            "chat.system_channel",
            "chat.managers",
            "chat.mods",
        }
        for key in genesis_content:
            if key.startswith("chat.") and key not in known:
                raise SemanticValidationError(f"unexpected chat genesis field: {key}")

    def validate_semantics(self, event: Event) -> None:
        c = event.content
        t = event.type
        if t == ChatTypes.MESSAGE:
            only_fields(c, {"text", "channel", "reply_to"})
            string_field(c.get("text"), "text", min_bytes=1, max_bytes=MAX_MESSAGE_TEXT_BYTES)
            channel_id_field(c.get("channel"), "channel")
            if c.get("reply_to") is not None:
                event_id_field(c.get("reply_to"), "reply_to")
        elif t == ChatTypes.REACTION:
            only_fields(c, {"target", "emoji"})
            event_id_field(c.get("target"), "target")
            string_field(c.get("emoji"), "emoji", min_bytes=1, max_bytes=MAX_REACTION_BYTES)
        elif t == ChatTypes.NICKNAME_SET:
            only_fields(c, {"nickname"})
            string_field(c.get("nickname"), "nickname", min_bytes=1, max_bytes=MAX_NICKNAME_BYTES)
        elif t == ChatTypes.CHANNEL_CREATE:
            only_fields(c, {"id", "name", "description", "position"})
            channel_id_field(c.get("id"), "id")
            string_field(c.get("name"), "name", min_bytes=1, max_bytes=MAX_CHANNEL_NAME_BYTES)
            if "description" in c:
                string_field(
                    c["description"], "channel.description", max_bytes=MAX_CHANNEL_DESCRIPTION_BYTES
                )
            if "position" in c:
                int_field(c["position"], "position")
        elif t == ChatTypes.CHANNEL_UPDATE:
            only_fields(c, {"id", "name", "description", "position"})
            channel_id_field(c.get("id"), "id")
            if not any(key in c for key in ("name", "description", "position")):
                raise SemanticValidationError("channel_update must include an update")
            if "name" in c:
                string_field(c["name"], "name", min_bytes=1, max_bytes=MAX_CHANNEL_NAME_BYTES)
            if "description" in c:
                string_field(
                    c["description"], "channel.description", max_bytes=MAX_CHANNEL_DESCRIPTION_BYTES
                )
            if "position" in c:
                int_field(c["position"], "position")
        elif t == ChatTypes.CHANNEL_DELETE:
            only_fields(c, {"id", "name"})
            channel_id_field(c.get("id"), "id")
            if "name" in c:
                string_field(c["name"], "name", min_bytes=1, max_bytes=MAX_CHANNEL_NAME_BYTES)
        elif t == ChatTypes.SETTINGS_UPDATE:
            only_fields(c, {"default_channel", "system_channel"})
            if "default_channel" not in c and "system_channel" not in c:
                raise SemanticValidationError("settings_update must include a field")
            if "default_channel" in c:
                channel_id_field(c["default_channel"], "default_channel")
            if "system_channel" in c:
                channel_id_field(c["system_channel"], "system_channel")
        elif t in (
            ChatTypes.MANAGER_ADD,
            ChatTypes.MANAGER_REMOVE,
            ChatTypes.MOD_ADD,
            ChatTypes.MOD_REMOVE,
        ):
            only_fields(c, {"target"})
            pubkey_field(c.get("target"), "target")
        else:
            raise SemanticValidationError(f"unknown chat event type: {t}")

    def validate_for_state(
        self, core: CoreState, app: AppState, event: Event, certified_time_ms: int
    ) -> None:
        assert isinstance(app, ChatState)
        content = event.content
        t = event.type
        if t == ChatTypes.MESSAGE and str(content["channel"]) not in app.channels:
            raise ApplicationError("message channel does not exist")
        if t == ChatTypes.CHANNEL_CREATE:
            channel_id = str(content["id"])
            if channel_id in app.channels:
                raise ApplicationError("channel id already exists")
            if any(channel.name == content["name"] for channel in app.channels.values()):
                raise ApplicationError("channel name already exists")
        if t in (ChatTypes.CHANNEL_UPDATE, ChatTypes.CHANNEL_DELETE):
            if str(content["id"]) not in app.channels:
                raise ApplicationError("channel does not exist")
        if t == ChatTypes.CHANNEL_DELETE:
            if str(content["id"]) == app.settings.get("default_channel"):
                raise ApplicationError("cannot delete the default channel")
        if t == ChatTypes.SETTINGS_UPDATE:
            for key in ("default_channel", "system_channel"):
                if key in content and str(content[key]) not in app.channels:
                    raise ApplicationError("chat setting references an unknown channel")

    # --- authorization -----------------------------------------------------

    def authorize(
        self, core: CoreState, app: AppState, event: Event, certified_time_ms: int
    ) -> bool:
        assert isinstance(app, ChatState)
        signer = event.author
        t = event.type
        if t in _CONTENT_TYPES:
            certified_seconds = certified_time_ms // 1000
            return signer in core.joined and not core.is_banned_at(signer, certified_seconds)
        if signer in app.managers:
            return True
        if signer in app.mods:
            return t in _MOD_ALLOWED
        return False

    # --- state transitions -------------------------------------------------

    def apply_event(
        self, core: CoreState, app: AppState, event: Event, certified_time_ms: int
    ) -> ChatState:
        assert isinstance(app, ChatState)
        content = event.content
        t = event.type
        managers = set(app.managers)
        mods = set(app.mods)
        channels = dict(app.channels)
        settings = dict(app.settings)

        if t in (ProtocolTypes.BAN, ProtocolTypes.KICK):
            # A removed user loses any chat roles they held.
            target = str(content["target"])
            managers.discard(target)
            mods.discard(target)
        elif t == ChatTypes.CHANNEL_CREATE:
            channel_id = str(content["id"])
            channels[channel_id] = ChannelRecord(
                id=channel_id,
                name=str(content["name"]),
                description=str(content.get("description", "")),
                position=int(content.get("position", len(channels))),
            )
        elif t == ChatTypes.CHANNEL_UPDATE:
            channel_id = str(content["id"])
            current = channels[channel_id]
            channels[channel_id] = ChannelRecord(
                id=channel_id,
                name=str(content.get("name", current.name)),
                description=str(content.get("description", current.description)),
                position=int(content.get("position", current.position)),
            )
        elif t == ChatTypes.CHANNEL_DELETE:
            channel_id = str(content["id"])
            channels.pop(channel_id)
            if settings.get("system_channel") == channel_id:
                settings["system_channel"] = next(iter(channels), "")
        elif t == ChatTypes.SETTINGS_UPDATE:
            for key in ("default_channel", "system_channel"):
                if key in content:
                    settings[key] = str(content[key])
        elif t == ChatTypes.MANAGER_ADD:
            managers.add(str(content["target"]))
        elif t == ChatTypes.MANAGER_REMOVE:
            managers.discard(str(content["target"]))
        elif t == ChatTypes.MOD_ADD:
            mods.add(str(content["target"]))
        elif t == ChatTypes.MOD_REMOVE:
            mods.discard(str(content["target"]))
        # Content events and core events not handled above leave chat state
        # unchanged (the sequence counter is core state, updated by the core).

        return replace(
            app,
            managers=frozenset(managers),
            mods=frozenset(mods),
            channels=channels,
            settings=settings,
        )


def _validate_chat_channel(raw: object) -> str:
    if not isinstance(raw, dict):
        raise SemanticValidationError("chat channel must be an object")
    only_fields(raw, {"id", "name", "description", "position"})
    channel_id = channel_id_field(raw.get("id"), "channel.id")
    string_field(raw.get("name"), "channel.name", min_bytes=1, max_bytes=MAX_CHANNEL_NAME_BYTES)
    if "description" in raw:
        string_field(
            raw["description"], "channel.description", max_bytes=MAX_CHANNEL_DESCRIPTION_BYTES
        )
    if "position" in raw:
        int_field(raw["position"], "channel.position")
    return channel_id


CHAT_APP = ChatApp()


__all__ = ["CHAT_APP", "ChannelRecord", "ChatApp", "ChatState"]
