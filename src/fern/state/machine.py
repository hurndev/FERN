from __future__ import annotations

from collections.abc import Iterable

from fern.events.event import Event
from fern.events.types import ProtocolTypes
from fern.state.authorization import is_authorised
from fern.state.types import BanEntry, GroupState


def _initialise_from_genesis(genesis: Event) -> GroupState:
    c = genesis.content
    founder = c["founder"]
    return GroupState(
        members=frozenset({founder}),
        joined=frozenset({founder}),
        banned={},
        mods=frozenset(c["mods"]),
        relays=tuple(c["relays"]),
        metadata={"name": c.get("name", ""), "description": c.get("description", "")},
        public=c.get("public", True),
    )


def apply_event(state: GroupState, event: Event) -> GroupState:
    c = event.content
    t = event.type

    members = set(state.members)
    joined = set(state.joined)
    banned = dict(state.banned)
    mods = set(state.mods)
    relays = list(state.relays)
    metadata = dict(state.metadata)

    if t == ProtocolTypes.INVITE:
        members.add(c["invitee"])

    elif t == ProtocolTypes.JOIN:
        if state.public or event.author in members:
            if not state.is_banned_at(event.author, event.ts):
                joined.add(event.author)

    elif t == ProtocolTypes.LEAVE:
        joined.discard(event.author)

    elif t == ProtocolTypes.KICK:
        target = c["target"]
        joined.discard(target)
        mods.discard(target)

    elif t == ProtocolTypes.BAN:
        target = c["target"]
        banned[target] = BanEntry(until=c.get("until"), reason=c.get("reason", ""))
        joined.discard(target)

    elif t == ProtocolTypes.UNBAN:
        banned.pop(c["target"], None)

    elif t == ProtocolTypes.MOD_ADD:
        mods.add(c["target"])

    elif t == ProtocolTypes.MOD_REMOVE:
        mods.discard(c["target"])

    elif t == ProtocolTypes.RELAY_UPDATE:
        relays[:] = c["relays"]

    elif t == ProtocolTypes.METADATA_UPDATE:
        for key in ("name", "description"):
            if key in c:
                metadata[key] = c[key]

    return GroupState(
        members=frozenset(members),
        joined=frozenset(joined),
        banned={k: v for k, v in banned.items()},
        mods=frozenset(mods),
        relays=tuple(relays),
        metadata={k: v for k, v in metadata.items()},
        public=state.public,
    )


def derive_group_state(events: Iterable[Event]) -> tuple[GroupState, list[Event]]:
    event_list = list(events)

    genesis_events = [e for e in event_list if e.type == ProtocolTypes.GENESIS]
    if not genesis_events:
        raise ValueError("No genesis event found in event list")
    genesis = genesis_events[0]

    state = _initialise_from_genesis(genesis)

    non_genesis = [e for e in event_list if e.type != ProtocolTypes.GENESIS]
    non_genesis.sort(key=lambda e: (e.ts, e.id))

    rejected: list[Event] = []

    for event in non_genesis:
        if not is_authorised(state, event):
            rejected.append(event)
            continue
        state = apply_event(state, event)

    return state, rejected
