from __future__ import annotations

from fern.events.event import Event
from fern.state.types import GroupState
from fern.events.types import ProtocolTypes


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
        ProtocolTypes.MOD_ADD,
        ProtocolTypes.MOD_REMOVE,
        ProtocolTypes.RELAY_UPDATE,
        ProtocolTypes.METADATA_UPDATE,
    ):
        return event.author in state.mods

    if event.type.startswith("chat."):
        return state.can_post(event.author, event.ts)

    return True
