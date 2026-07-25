from __future__ import annotations

from dataclasses import dataclass, field, replace
from fern.bft.canonical import canonical_hash, canonical_json, strict_dict, strict_int
from fern.bft.certificates import SyncReady, verify_sync_ready
from fern.bft.constants import GOVERNANCE_TYPES, PROTOCOL_VERSION
from fern.bft.validators import Validator, ValidatorSet, make_validator_set
from fern.events.event import Event
from fern.events.semantic import SemanticValidationError, validate_event_semantics
from fern.events.types import ChatTypes, ProtocolTypes
from fern.events.validation import verify_event


class ApplicationError(ValueError):
    """A deterministic event or state-transition failure."""


@dataclass(frozen=True)
class BanRecord:
    until: int | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"until": self.until, "reason": self.reason}


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
class ApplicationState:
    group: str
    chain_id: str
    validator_set: ValidatorSet
    members: frozenset[str]
    joined: frozenset[str]
    banned: dict[str, BanRecord]
    admins: frozenset[str]
    sequences: dict[str, int]
    metadata: dict[str, str]
    public: bool
    app: str
    channels: dict[str, ChannelRecord] = field(default_factory=dict)
    chat_settings: dict[str, str] = field(default_factory=dict)

    def is_banned_at(self, pubkey: str, certified_seconds: int) -> bool:
        record = self.banned.get(pubkey)
        return record is not None and (record.until is None or record.until > certified_seconds)

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol": PROTOCOL_VERSION,
            "group": self.group,
            "chain_id": self.chain_id,
            "validator_set": self.validator_set.to_dict(),
            "members": sorted(self.members),
            "joined": sorted(self.joined),
            "banned": {key: self.banned[key].to_dict() for key in sorted(self.banned)},
            "admins": sorted(self.admins),
            "sequences": {key: self.sequences[key] for key in sorted(self.sequences)},
            "metadata": {key: self.metadata[key] for key in sorted(self.metadata)},
            "public": self.public,
            "app": self.app,
            "channels": {key: self.channels[key].to_dict() for key in sorted(self.channels)},
            "chat_settings": {key: self.chat_settings[key] for key in sorted(self.chat_settings)},
        }

    @property
    def root(self) -> str:
        return canonical_hash(["fern-bft-state", self.to_dict()])

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ApplicationState:
        raw_banned = strict_dict(value.get("banned"), "state banned")
        raw_channels = strict_dict(value.get("channels"), "state channels")
        raw_sequences = strict_dict(value.get("sequences"), "state sequences")
        raw_metadata = strict_dict(value.get("metadata"), "state metadata")
        raw_settings = strict_dict(value.get("chat_settings"), "state chat_settings")
        raw_validator_set = strict_dict(value.get("validator_set"), "state validator_set")
        banned: dict[str, BanRecord] = {}
        for key, raw_item in raw_banned.items():
            item = strict_dict(raw_item, "ban record")
            raw_until = item.get("until")
            banned[key] = BanRecord(
                until=strict_int(raw_until, "ban until") if raw_until is not None else None,
                reason=str(item.get("reason", "")),
            )
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
        return cls(
            group=str(value.get("group", "")),
            chain_id=str(value.get("chain_id", "")),
            validator_set=ValidatorSet.from_dict(raw_validator_set),
            members=frozenset(str(item) for item in _list(value.get("members"), "members")),
            joined=frozenset(str(item) for item in _list(value.get("joined"), "joined")),
            banned=banned,
            admins=frozenset(str(item) for item in _list(value.get("admins"), "admins")),
            sequences={
                key: strict_int(item, "author sequence") for key, item in raw_sequences.items()
            },
            metadata={str(key): str(item) for key, item in raw_metadata.items()},
            public=bool(value.get("public", False)),
            app=str(value.get("app", "")),
            channels=channels,
            chat_settings={str(key): str(item) for key, item in raw_settings.items()},
        )


def _list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    return value


@dataclass(frozen=True)
class ChainHead:
    height: int
    block_hash: str
    history_root: str
    logical_bytes: int
    state: ApplicationState


def initial_state_from_genesis(genesis: Event) -> ApplicationState:
    try:
        verify_event(genesis)
        validate_event_semantics(genesis)
    except (ValueError, TypeError) as exc:
        raise ApplicationError(f"invalid genesis: {exc}") from exc
    if genesis.type != ProtocolTypes.GENESIS or genesis.id is None:
        raise ApplicationError("group trust anchor must be a genesis event")
    content = genesis.content
    raw_validators = content["validators"]
    assert isinstance(raw_validators, list)
    validators = [Validator.from_dict(item) for item in raw_validators if isinstance(item, dict)]
    validator_set = make_validator_set(
        validators,
        epoch=0,
        fault_tolerance=int(content["fault_tolerance"]),
    )
    channels: dict[str, ChannelRecord] = {}
    if content["app"] == "chat":
        raw_channels = content["chat.channels"]
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
    founder = str(content["founder"])
    return ApplicationState(
        group=genesis.group,
        chain_id=str(content["chain_id"]),
        validator_set=validator_set,
        members=frozenset({founder}),
        joined=frozenset({founder}),
        banned={},
        admins=frozenset(str(item) for item in content["admins"]),
        sequences={},
        metadata={
            "name": str(content["name"]),
            "description": str(content["description"]),
        },
        public=bool(content["public"]),
        app=str(content["app"]),
        channels=channels,
        chat_settings={
            "default_channel": str(content.get("chat.default_channel", first_channel)),
            "system_channel": str(content.get("chat.system_channel", first_channel)),
        }
        if channels
        else {},
    )


def genesis_chain_head(genesis: Event) -> ChainHead:
    if genesis.id is None:
        raise ApplicationError("genesis has no id")
    state = initial_state_from_genesis(genesis)
    return ChainHead(
        height=0,
        block_hash=genesis.id,
        history_root=canonical_hash(["fern-bft-history", genesis.id]),
        logical_bytes=len(canonical_json(genesis.to_dict())),
        state=state,
    )


def _validate_known_or_opaque_semantics(event: Event) -> None:
    try:
        validate_event_semantics(event)
    except SemanticValidationError:
        if "." not in event.type or event.type.startswith("chat."):
            raise


def _is_authorized(state: ApplicationState, event: Event, certified_seconds: int) -> bool:
    event_type = event.type
    if event_type == ProtocolTypes.JOIN:
        return (state.public or event.author in state.members) and not state.is_banned_at(
            event.author, certified_seconds
        )
    if event_type == ProtocolTypes.LEAVE:
        return True
    if event_type in GOVERNANCE_TYPES:
        return event.author in state.admins
    if "." in event_type:
        if event_type.split(".", 1)[0] != state.app:
            return False
        return event.author in state.joined and not state.is_banned_at(
            event.author, certified_seconds
        )
    return False


def _validate_state_dependent(state: ApplicationState, event: Event) -> None:
    content = event.content
    if event.type == ChatTypes.MESSAGE and str(content["channel"]) not in state.channels:
        raise ApplicationError("message channel does not exist")
    if event.type == ChatTypes.CHANNEL_CREATE:
        channel_id = str(content["id"])
        if channel_id in state.channels:
            raise ApplicationError("channel id already exists")
        if any(channel.name == content["name"] for channel in state.channels.values()):
            raise ApplicationError("channel name already exists")
    if event.type in {ChatTypes.CHANNEL_UPDATE, ChatTypes.CHANNEL_DELETE}:
        if str(content["id"]) not in state.channels:
            raise ApplicationError("channel does not exist")
    if event.type == ChatTypes.CHANNEL_DELETE:
        if str(content["id"]) == state.chat_settings.get("default_channel"):
            raise ApplicationError("cannot delete the default channel")


def validate_event_for_state(
    state: ApplicationState,
    event: Event,
    certified_time_ms: int,
    *,
    expected_seq: int | None = None,
) -> None:
    try:
        verify_event(event)
        _validate_known_or_opaque_semantics(event)
    except (ValueError, TypeError) as exc:
        raise ApplicationError(str(exc)) from exc
    if event.group != state.group:
        raise ApplicationError("event belongs to another group")
    expected_seq = expected_seq or state.sequences.get(event.author, 0) + 1
    if event.seq != expected_seq:
        raise ApplicationError(
            f"author sequence mismatch: expected {expected_seq}, got {event.seq}"
        )
    certified_seconds = certified_time_ms // 1000
    if not _is_authorized(state, event, certified_seconds):
        raise ApplicationError("event author is not authorized")
    _validate_state_dependent(state, event)


def _validate_validator_update(
    state: ApplicationState,
    event: Event,
    *,
    checkpoint_height: int,
    checkpoint_block_hash: str,
    history_root: str,
    logical_bytes: int,
) -> ValidatorSet:
    raw_validators = event.content["validators"]
    assert isinstance(raw_validators, list)
    validators = [Validator.from_dict(item) for item in raw_validators if isinstance(item, dict)]
    next_set = make_validator_set(
        validators,
        epoch=state.validator_set.epoch + 1,
        fault_tolerance=int(event.content["fault_tolerance"]),
    )
    added = next_set.pubkeys - state.validator_set.pubkeys
    raw_readiness = event.content["readiness"]
    assert isinstance(raw_readiness, list)
    readiness = [SyncReady.from_dict(item) for item in raw_readiness if isinstance(item, dict)]
    if len(readiness) != len(raw_readiness):
        raise ApplicationError("invalid readiness entry")
    by_validator: dict[str, SyncReady] = {}
    for ready in readiness:
        if ready.validator in by_validator:
            raise ApplicationError("duplicate readiness validator")
        if not verify_sync_ready(ready):
            raise ApplicationError("invalid readiness signature")
        if not (
            ready.group == state.group
            and ready.chain_id == state.chain_id
            and ready.from_epoch == state.validator_set.epoch
            and ready.to_epoch == next_set.epoch
            and ready.checkpoint_height == checkpoint_height
            and ready.checkpoint_block_hash == checkpoint_block_hash
            and ready.history_root == history_root
            and ready.byte_count == logical_bytes
        ):
            raise ApplicationError("readiness does not match transition checkpoint")
        by_validator[ready.validator] = ready
    if set(by_validator) != added:
        raise ApplicationError("readiness must cover exactly the newly added validators")
    return next_set


def apply_event(
    state: ApplicationState,
    event: Event,
    certified_time_ms: int,
    *,
    checkpoint_height: int,
    checkpoint_block_hash: str,
    history_root: str,
    logical_bytes: int,
) -> ApplicationState:
    validate_event_for_state(state, event, certified_time_ms)
    content = event.content
    members = set(state.members)
    joined = set(state.joined)
    banned = dict(state.banned)
    admins = set(state.admins)
    sequences = dict(state.sequences)
    metadata = dict(state.metadata)
    channels = dict(state.channels)
    settings = dict(state.chat_settings)
    validator_set = state.validator_set

    event_type = event.type
    if event_type == ProtocolTypes.INVITE:
        members.add(str(content["invitee"]))
    elif event_type == ProtocolTypes.JOIN:
        joined.add(event.author)
        members.add(event.author)
    elif event_type == ProtocolTypes.LEAVE:
        joined.discard(event.author)
    elif event_type == ProtocolTypes.KICK:
        target = str(content["target"])
        joined.discard(target)
        admins.discard(target)
    elif event_type == ProtocolTypes.BAN:
        target = str(content["target"])
        raw_until = content.get("until")
        banned[target] = BanRecord(
            until=int(raw_until) if raw_until is not None else None,
            reason=str(content.get("reason", "")),
        )
        joined.discard(target)
        admins.discard(target)
    elif event_type == ProtocolTypes.UNBAN:
        banned.pop(str(content["target"]), None)
    elif event_type == ProtocolTypes.ADMIN_ADD:
        admins.add(str(content["target"]))
    elif event_type == ProtocolTypes.ADMIN_REMOVE:
        admins.discard(str(content["target"]))
    elif event_type == ProtocolTypes.METADATA_UPDATE:
        for key in ("name", "description"):
            if key in content:
                metadata[key] = str(content[key])
    elif event_type == ProtocolTypes.VALIDATOR_UPDATE:
        validator_set = _validate_validator_update(
            state,
            event,
            checkpoint_height=checkpoint_height,
            checkpoint_block_hash=checkpoint_block_hash,
            history_root=history_root,
            logical_bytes=logical_bytes,
        )
    elif event_type == ChatTypes.CHANNEL_CREATE:
        channel_id = str(content["id"])
        channels[channel_id] = ChannelRecord(
            id=channel_id,
            name=str(content["name"]),
            description=str(content.get("description", "")),
            position=int(content.get("position", len(channels))),
        )
    elif event_type == ChatTypes.CHANNEL_UPDATE:
        channel_id = str(content["id"])
        current = channels[channel_id]
        channels[channel_id] = ChannelRecord(
            id=channel_id,
            name=str(content.get("name", current.name)),
            description=str(content.get("description", current.description)),
            position=int(content.get("position", current.position)),
        )
    elif event_type == ChatTypes.CHANNEL_DELETE:
        channel_id = str(content["id"])
        channels.pop(channel_id)
        if settings.get("system_channel") == channel_id:
            settings["system_channel"] = next(iter(channels), "")
    elif event_type == ChatTypes.SETTINGS_UPDATE:
        for key in ("default_channel", "system_channel"):
            if key in content:
                channel_id = str(content[key])
                if channel_id not in channels:
                    raise ApplicationError("chat setting references an unknown channel")
                settings[key] = channel_id

    sequences[event.author] = event.seq
    return replace(
        state,
        validator_set=validator_set,
        members=frozenset(members),
        joined=frozenset(joined),
        banned=banned,
        admins=frozenset(admins),
        sequences=sequences,
        metadata=metadata,
        channels=channels,
        chat_settings=settings,
    )


def execute_events(
    state: ApplicationState,
    events: tuple[Event, ...],
    certified_times_ms: tuple[int, ...],
    *,
    governance: Event | None,
    checkpoint_height: int,
    checkpoint_block_hash: str,
    history_root: str,
    logical_bytes: int,
) -> ApplicationState:
    if len(certified_times_ms) != len(events) + (1 if governance is not None else 0):
        raise ApplicationError("certified timestamp count does not match events")
    if any(event.type in GOVERNANCE_TYPES for event in events):
        raise ApplicationError("governance event must be in the governance slot")
    if governance is not None and governance.type not in GOVERNANCE_TYPES:
        raise ApplicationError("governance slot contains an ordinary event")
    result = state
    for index, event in enumerate(events):
        result = apply_event(
            result,
            event,
            certified_times_ms[index],
            checkpoint_height=checkpoint_height,
            checkpoint_block_hash=checkpoint_block_hash,
            history_root=history_root,
            logical_bytes=logical_bytes,
        )
    if governance is not None:
        result = apply_event(
            result,
            governance,
            certified_times_ms[-1],
            checkpoint_height=checkpoint_height,
            checkpoint_block_hash=checkpoint_block_hash,
            history_root=history_root,
            logical_bytes=logical_bytes,
        )
    return result


__all__ = [
    "ApplicationError",
    "ApplicationState",
    "BanRecord",
    "ChainHead",
    "ChannelRecord",
    "apply_event",
    "execute_events",
    "genesis_chain_head",
    "initial_state_from_genesis",
    "validate_event_for_state",
]
