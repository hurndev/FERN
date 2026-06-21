from __future__ import annotations

from collections.abc import Iterable

from fern.events.semantic import SemanticValidationError, validate_event_semantics
from fern.events.event import Event
from fern.events.types import ChatTypes, ProtocolTypes
from fern.state.authorization import is_authorised
from fern.state.types import BanEntry, Channel, GroupState

_GENERAL = "general"


def _channel_from_config(raw: object, position: int) -> Channel:
    if isinstance(raw, dict):
        channel_id = str(raw.get("id", "")).strip()
        name = str(raw.get("name", "")).strip()
        description = str(raw.get("description", ""))
        raw_position = raw.get("position", position)
        pos = raw_position if isinstance(raw_position, int) else position
        if not channel_id:
            channel_id = name
        return Channel(id=channel_id, name=name or channel_id, description=description, position=pos)
    name = str(raw).strip()
    return Channel(id=name, name=name, position=position)


def _initialise_from_genesis(genesis: Event) -> GroupState:
    c = genesis.content
    founder = c["founder"]
    app = c["app"]
    channels: dict[str, Channel] = {}
    chat_settings: dict[str, str] = {}
    if app == "chat":
        raw_channels = c["chat.channels"]
        for idx, raw in enumerate(raw_channels):
            channel = _channel_from_config(raw, idx)
            if channel.id:
                channels[channel.id] = channel
        if _GENERAL not in channels:
            channels[_GENERAL] = Channel(id=_GENERAL, name=_GENERAL, position=0)
        chat_settings = {
            "default_channel": str(c.get("chat.default_channel", _GENERAL)),
            "system_channel": str(c.get("chat.system_channel", _GENERAL)),
        }
    return GroupState(
        members=frozenset({founder}),
        joined=frozenset({founder}),
        banned={},
        admins=frozenset(c["admins"]),
        relays=tuple(c["relays"]),
        metadata={"name": c.get("name", ""), "description": c.get("description", "")},
        public=c.get("public", True),
        app=app,
        channels=channels,
        chat_settings=chat_settings,
    )


def apply_event(state: GroupState, event: Event) -> GroupState:
    c = event.content
    t = event.type

    members = set(state.members)
    joined = set(state.joined)
    banned = dict(state.banned)
    admins = set(state.admins)
    relays = list(state.relays)
    metadata = dict(state.metadata)
    channels = dict(state.channels)
    chat_settings = dict(state.chat_settings)

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
        admins.discard(target)

    elif t == ProtocolTypes.BAN:
        target = c["target"]
        banned[target] = BanEntry(until=c.get("until"), reason=c.get("reason", ""))
        joined.discard(target)
        admins.discard(target)

    elif t == ProtocolTypes.UNBAN:
        banned.pop(c["target"], None)

    elif t == ProtocolTypes.ADMIN_ADD:
        admins.add(c["target"])

    elif t == ProtocolTypes.ADMIN_REMOVE:
        admins.discard(c["target"])

    elif t == ProtocolTypes.RELAY_UPDATE:
        relays[:] = c["relays"]

    elif t == ProtocolTypes.METADATA_UPDATE:
        for key in ("name", "description"):
            if key in c:
                metadata[key] = c[key]

    elif t == ChatTypes.CHANNEL_CREATE:
        name = c["name"]
        description = str(c.get("description", ""))
        raw_position = c.get("position", len(channels))
        position = raw_position if isinstance(raw_position, int) else len(channels)
        if event.id and not any(ch.name == name for ch in channels.values()):
            channels[event.id] = Channel(
                id=event.id,
                name=str(name),
                description=description,
                position=position,
            )

    elif t == ChatTypes.CHANNEL_UPDATE:
        channel_id = str(c["id"])
        existing = channels.get(channel_id)
        if existing is not None:
            raw_position = c.get("position", existing.position)
            position = raw_position if isinstance(raw_position, int) else existing.position
            channels[channel_id] = Channel(
                id=channel_id,
                name=str(c.get("name", existing.name)),
                description=str(c.get("description", existing.description)),
                position=position,
            )

    elif t == ChatTypes.CHANNEL_DELETE:
        channel_id = str(c["id"])
        if channel_id != _GENERAL:
            channels.pop(channel_id, None)
            for key in ("default_channel", "system_channel"):
                if chat_settings.get(key) == channel_id:
                    chat_settings[key] = _GENERAL

    elif t == ChatTypes.SETTINGS_UPDATE:
        for key in ("default_channel", "system_channel"):
            if key in c and str(c[key]) in channels:
                chat_settings[key] = str(c[key])

    return GroupState(
        members=frozenset(members),
        joined=frozenset(joined),
        banned={k: v for k, v in banned.items()},
        admins=frozenset(admins),
        relays=tuple(relays),
        metadata={k: v for k, v in metadata.items()},
        public=state.public,
        app=state.app,
        channels={k: v for k, v in channels.items()},
        chat_settings={k: v for k, v in chat_settings.items()},
    )


def _validate_state_dependent_semantics(state: GroupState, event: Event) -> None:
    c = event.content
    if event.type == ChatTypes.MESSAGE and c["channel"] not in state.channels:
        raise SemanticValidationError("message channel does not exist")
    if event.type == ChatTypes.CHANNEL_CREATE and any(
        channel.name == c["name"] for channel in state.channels.values()
    ):
        raise SemanticValidationError("channel name already exists")


def derive_group_state_details(events: Iterable[Event]) -> tuple[GroupState, list[Event], frozenset[str]]:
    event_list = list(events)

    genesis_events = [e for e in event_list if e.type == ProtocolTypes.GENESIS]
    if not genesis_events:
        raise ValueError("No genesis event found in event list")
    genesis = genesis_events[0]
    try:
        validate_event_semantics(genesis)
    except SemanticValidationError as exc:
        raise ValueError(f"Invalid genesis event: {exc}") from exc

    state = _initialise_from_genesis(genesis)
    accepted_ids = {genesis.id} if genesis.id else set()

    non_genesis = [e for e in event_list if e.type != ProtocolTypes.GENESIS]
    non_genesis.sort(key=lambda e: (e.ts, e.id))

    rejected: list[Event] = []

    for event in non_genesis:
        if any(parent not in accepted_ids for parent in event.parents):
            rejected.append(event)
            continue
        try:
            validate_event_semantics(event)
            _validate_state_dependent_semantics(state, event)
        except SemanticValidationError:
            rejected.append(event)
            continue
        if not is_authorised(state, event):
            rejected.append(event)
            continue
        state = apply_event(state, event)
        if event.id:
            accepted_ids.add(event.id)

    return state, rejected, frozenset(accepted_ids)


def derive_group_state(events: Iterable[Event]) -> tuple[GroupState, list[Event]]:
    state, rejected, _accepted_ids = derive_group_state_details(events)
    return state, rejected


def compute_accepted_heads(events: Iterable[Event]) -> frozenset[str]:
    event_list = list(events)
    _state, _rejected, accepted_ids = derive_group_state_details(event_list)
    referenced: set[str] = set()
    for event in event_list:
        if event.id not in accepted_ids:
            continue
        for parent in event.parents:
            if parent in accepted_ids:
                referenced.add(parent)
    return frozenset(accepted_ids - referenced)
