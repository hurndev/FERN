

class ProtocolTypes:
    GENESIS = "genesis"
    JOIN = "join"
    LEAVE = "leave"
    INVITE = "invite"
    KICK = "kick"
    BAN = "ban"
    UNBAN = "unban"
    MOD_ADD = "mod_add"
    MOD_REMOVE = "mod_remove"
    RELAY_UPDATE = "relay_update"
    METADATA_UPDATE = "metadata_update"


class ChatTypes:
    MESSAGE = "chat.message"
    REACTION = "chat.reaction"
    NICKNAME_SET = "chat.nickname_set"
    CHANNEL_CREATE = "chat.channel_create"
    CHANNEL_DELETE = "chat.channel_delete"


PROTOCOL_TYPES: frozenset[str] = frozenset(
    {
        ProtocolTypes.GENESIS,
        ProtocolTypes.JOIN,
        ProtocolTypes.LEAVE,
        ProtocolTypes.INVITE,
        ProtocolTypes.KICK,
        ProtocolTypes.BAN,
        ProtocolTypes.UNBAN,
        ProtocolTypes.MOD_ADD,
        ProtocolTypes.MOD_REMOVE,
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
        ChatTypes.CHANNEL_DELETE,
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
        ProtocolTypes.MOD_ADD,
        ProtocolTypes.MOD_REMOVE,
        ProtocolTypes.RELAY_UPDATE,
        ProtocolTypes.METADATA_UPDATE,
        ChatTypes.CHANNEL_CREATE,
        ChatTypes.CHANNEL_DELETE,
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
