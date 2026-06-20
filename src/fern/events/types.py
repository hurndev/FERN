

class ProtocolTypes:
    GENESIS = "genesis"
    JOIN = "join"
    LEAVE = "leave"
    INVITE = "invite"
    KICK = "kick"
    BAN = "ban"
    UNBAN = "unban"
    ADMIN_ADD = "admin_add"
    ADMIN_REMOVE = "admin_remove"
    RELAY_UPDATE = "relay_update"
    METADATA_UPDATE = "metadata_update"


class ChatTypes:
    MESSAGE = "chat.message"
    REACTION = "chat.reaction"
    NICKNAME_SET = "chat.nickname_set"
    CHANNEL_CREATE = "chat.channel_create"
    CHANNEL_UPDATE = "chat.channel_update"
    CHANNEL_DELETE = "chat.channel_delete"
    SETTINGS_UPDATE = "chat.settings_update"


PROTOCOL_TYPES: frozenset[str] = frozenset(
    {
        ProtocolTypes.GENESIS,
        ProtocolTypes.JOIN,
        ProtocolTypes.LEAVE,
        ProtocolTypes.INVITE,
        ProtocolTypes.KICK,
        ProtocolTypes.BAN,
        ProtocolTypes.UNBAN,
        ProtocolTypes.ADMIN_ADD,
        ProtocolTypes.ADMIN_REMOVE,
        ProtocolTypes.RELAY_UPDATE,
        ProtocolTypes.METADATA_UPDATE,
    }
)

CHAT_TYPES: frozenset[str] = frozenset(
    {
        ChatTypes.MESSAGE,
        ChatTypes.REACTION,
        ChatTypes.NICKNAME_SET,
        ChatTypes.CHANNEL_CREATE,
        ChatTypes.CHANNEL_UPDATE,
        ChatTypes.CHANNEL_DELETE,
        ChatTypes.SETTINGS_UPDATE,
    }
)

STATE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        ProtocolTypes.JOIN,
        ProtocolTypes.LEAVE,
        ProtocolTypes.INVITE,
        ProtocolTypes.KICK,
        ProtocolTypes.BAN,
        ProtocolTypes.UNBAN,
        ProtocolTypes.ADMIN_ADD,
        ProtocolTypes.ADMIN_REMOVE,
        ProtocolTypes.RELAY_UPDATE,
        ProtocolTypes.METADATA_UPDATE,
        ChatTypes.CHANNEL_CREATE,
        ChatTypes.CHANNEL_UPDATE,
        ChatTypes.CHANNEL_DELETE,
        ChatTypes.SETTINGS_UPDATE,
    }
)


def is_protocol_type(t: str) -> bool:
    return "." not in t


def is_app_type(t: str) -> bool:
    return "." in t


def is_state_event_type(t: str) -> bool:
    return t in STATE_EVENT_TYPES


def namespace_of(t: str) -> str:
    return t.split(".")[0] if "." in t else ""
