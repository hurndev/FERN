from __future__ import annotations

from fern.events.event import Event
from fern.state.types import GroupState
from fern.events.types import ChatTypes, ProtocolTypes


def is_authorised(state: GroupState, event: Event) -> bool:
    if event.type == ProtocolTypes.GENESIS:
        return True

    if event.type in (ProtocolTypes.JOIN, ProtocolTypes.LEAVE):
        return True

    if event.type in (
        ProtocolTypes.INVITE,
        ProtocolTypes.KICK,
        ProtocolTypes.BAN,
        ProtocolTypes.UNBAN,
        ProtocolTypes.ADMIN_ADD,
        ProtocolTypes.ADMIN_REMOVE,
        ProtocolTypes.RELAY_UPDATE,
        ProtocolTypes.METADATA_UPDATE,
    ):
        return event.author in state.admins

    if event.type in (
        ChatTypes.CHANNEL_CREATE,
        ChatTypes.CHANNEL_UPDATE,
        ChatTypes.CHANNEL_DELETE,
        ChatTypes.SETTINGS_UPDATE,
    ):
        return event.author in state.admins

    if event.type.startswith("chat."):
        return state.can_post(event.author, event.ts)

    return True
