class ProtocolTypes:
    """Core (bare) event types owned by the protocol."""

    GENESIS = "genesis"
    JOIN = "join"
    LEAVE = "leave"
    INVITE = "invite"
    KICK = "kick"
    BAN = "ban"
    UNBAN = "unban"
    VALIDATOR_UPDATE = "validator_update"
    METADATA_UPDATE = "metadata_update"


class ChatTypes:
    """Chat app (dotted) event types."""

    MESSAGE = "chat.message"
    REACTION = "chat.reaction"
    NICKNAME_SET = "chat.nickname_set"
    CHANNEL_CREATE = "chat.channel_create"
    CHANNEL_UPDATE = "chat.channel_update"
    CHANNEL_DELETE = "chat.channel_delete"
    SETTINGS_UPDATE = "chat.settings_update"
    MANAGER_ADD = "chat.manager_add"
    MANAGER_REMOVE = "chat.manager_remove"
    MOD_ADD = "chat.mod_add"
    MOD_REMOVE = "chat.mod_remove"


PROTOCOL_TYPES: frozenset[str] = frozenset(
    {
        ProtocolTypes.GENESIS,
        ProtocolTypes.JOIN,
        ProtocolTypes.LEAVE,
        ProtocolTypes.INVITE,
        ProtocolTypes.KICK,
        ProtocolTypes.BAN,
        ProtocolTypes.UNBAN,
        ProtocolTypes.VALIDATOR_UPDATE,
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
        ChatTypes.MANAGER_ADD,
        ChatTypes.MANAGER_REMOVE,
        ChatTypes.MOD_ADD,
        ChatTypes.MOD_REMOVE,
    }
)

# Events that mutate deterministic group state (core or app), as opposed to
# pure content (message/reaction/nickname). Used for display/filtering.
STATE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        ProtocolTypes.JOIN,
        ProtocolTypes.LEAVE,
        ProtocolTypes.INVITE,
        ProtocolTypes.KICK,
        ProtocolTypes.BAN,
        ProtocolTypes.UNBAN,
        ProtocolTypes.VALIDATOR_UPDATE,
        ProtocolTypes.METADATA_UPDATE,
        ChatTypes.CHANNEL_CREATE,
        ChatTypes.CHANNEL_UPDATE,
        ChatTypes.CHANNEL_DELETE,
        ChatTypes.SETTINGS_UPDATE,
        ChatTypes.MANAGER_ADD,
        ChatTypes.MANAGER_REMOVE,
        ChatTypes.MOD_ADD,
        ChatTypes.MOD_REMOVE,
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
