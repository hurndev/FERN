"""Core application state and the protocol/app boundary.

The core owns the universal group *facts* (identity, validator set, membership,
per-author sequences) and the consensus-critical *mechanisms* (deterministic
event execution and the ``validator_update`` transition). It has **no roles and
no authorization policy**: every policy decision is delegated to the group's
:class:`AppModule`, which also owns all application state (for chat: managers,
mods, channels, settings).

The combined state root hashes the core state merged with the app state, so the
root commits to both layers while each layer owns its own fields. App modules
are looked up by the ``app`` name committed in genesis via :mod:`fern.apps`; the
core never imports app code directly.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from fern.apps import get_app
from fern.bft.canonical import canonical_hash, canonical_json, strict_dict, strict_int
from fern.bft.certificates import SyncReady, verify_sync_ready
from fern.bft.constants import CORE_BOUNDARY_TYPES, CORE_EVENT_TYPES, PROTOCOL_VERSION
from fern.bft.validators import Validator, ValidatorSet, make_validator_set
from fern.events.event import Event
from fern.events.semantic import validate_core_event_semantics, validate_genesis_core
from fern.events.types import ProtocolTypes, is_app_type, namespace_of
from fern.events.validation import verify_event


class ApplicationError(ValueError):
    """A deterministic event or state-transition failure."""


@dataclass(frozen=True)
class BanRecord:
    until: int | None
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"until": self.until, "reason": self.reason}


@runtime_checkable
class AppState(Protocol):
    """Opaque, app-owned state. The core only serializes it via ``to_dict``."""

    def to_dict(self) -> dict[str, object]: ...


@runtime_checkable
class AppModule(Protocol):
    """An application the protocol core can host.

    The core delegates all policy and application-state transitions here. App
    modules live outside ``fern.bft`` and depend on the core, never the reverse.
    """

    name: str
    boundary_types: frozenset[str]

    def initial_state(self, genesis_content: dict[str, object]) -> AppState: ...

    def state_from_dict(self, value: dict[str, object]) -> AppState: ...

    def validate_genesis(self, genesis_content: dict[str, object]) -> None: ...

    def validate_semantics(self, event: Event) -> None: ...

    def validate_for_state(
        self, core: CoreState, app: AppState, event: Event, certified_time_ms: int
    ) -> None: ...

    def authorize(
        self, core: CoreState, app: AppState, event: Event, certified_time_ms: int
    ) -> bool: ...

    def apply_event(
        self, core: CoreState, app: AppState, event: Event, certified_time_ms: int
    ) -> AppState: ...


@dataclass(frozen=True)
class CoreState:
    """Universal group facts owned by the protocol core."""

    group: str
    chain_id: str
    validator_set: ValidatorSet
    members: frozenset[str]
    joined: frozenset[str]
    banned: dict[str, BanRecord]
    sequences: dict[str, int]
    metadata: dict[str, str]
    public: bool
    app: str

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
            "sequences": {key: self.sequences[key] for key in sorted(self.sequences)},
            "metadata": {key: self.metadata[key] for key in sorted(self.metadata)},
            "public": self.public,
            "app": self.app,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> CoreState:
        raw_banned = strict_dict(value.get("banned"), "state banned")
        raw_sequences = strict_dict(value.get("sequences"), "state sequences")
        raw_metadata = strict_dict(value.get("metadata"), "state metadata")
        raw_validator_set = strict_dict(value.get("validator_set"), "state validator_set")
        banned: dict[str, BanRecord] = {}
        for key, raw_item in raw_banned.items():
            item = strict_dict(raw_item, "ban record")
            raw_until = item.get("until")
            banned[key] = BanRecord(
                until=strict_int(raw_until, "ban until") if raw_until is not None else None,
                reason=str(item.get("reason", "")),
            )
        return cls(
            group=str(value.get("group", "")),
            chain_id=str(value.get("chain_id", "")),
            validator_set=ValidatorSet.from_dict(raw_validator_set),
            members=frozenset(str(item) for item in _list(value.get("members"), "members")),
            joined=frozenset(str(item) for item in _list(value.get("joined"), "joined")),
            banned=banned,
            sequences={
                key: strict_int(item, "author sequence") for key, item in raw_sequences.items()
            },
            metadata={str(key): str(item) for key, item in raw_metadata.items()},
            public=bool(value.get("public", False)),
            app=str(value.get("app", "")),
        )


def _list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array")
    return value


@dataclass(frozen=True)
class GroupState:
    """A group's complete deterministic state: core facts plus app state."""

    core: CoreState
    app: AppState

    def to_dict(self) -> dict[str, object]:
        combined = self.core.to_dict()
        combined.update(self.app.to_dict())
        return combined

    @property
    def root(self) -> str:
        return canonical_hash(["fern-bft-state", self.to_dict()])

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> GroupState:
        core = CoreState.from_dict(value)
        app_module = get_app(core.app)
        return cls(core=core, app=app_module.state_from_dict(value))

    # Convenience accessors delegating to the core; used widely by callers.
    @property
    def group(self) -> str:
        return self.core.group

    @property
    def chain_id(self) -> str:
        return self.core.chain_id

    @property
    def validator_set(self) -> ValidatorSet:
        return self.core.validator_set

    @property
    def sequences(self) -> dict[str, int]:
        return self.core.sequences

    @property
    def members(self) -> frozenset[str]:
        return self.core.members

    @property
    def joined(self) -> frozenset[str]:
        return self.core.joined

    @property
    def banned(self) -> dict[str, BanRecord]:
        return self.core.banned

    @property
    def metadata(self) -> dict[str, str]:
        return self.core.metadata

    @property
    def public(self) -> bool:
        return self.core.public

    def is_banned_at(self, pubkey: str, certified_seconds: int) -> bool:
        return self.core.is_banned_at(pubkey, certified_seconds)


@dataclass(frozen=True)
class ChainHead:
    height: int
    block_hash: str
    history_root: str
    logical_bytes: int
    state: GroupState


def boundary_types_for(app_name: str) -> frozenset[str]:
    """All boundary event types for a group: core plus the app's."""
    return CORE_BOUNDARY_TYPES | get_app(app_name).boundary_types


def initial_state_from_genesis(genesis: Event) -> GroupState:
    try:
        verify_event(genesis)
    except (ValueError, TypeError) as exc:
        raise ApplicationError(f"invalid genesis: {exc}") from exc
    if genesis.type != ProtocolTypes.GENESIS or genesis.id is None:
        raise ApplicationError("group trust anchor must be a genesis event")
    content = genesis.content
    try:
        validate_genesis_core(genesis)
    except ValueError as exc:
        raise ApplicationError(f"invalid genesis: {exc}") from exc
    app_name = str(content["app"])
    app_module = get_app(app_name)
    try:
        app_module.validate_genesis(content)
    except ValueError as exc:
        raise ApplicationError(f"invalid genesis: {exc}") from exc

    raw_validators = content["validators"]
    assert isinstance(raw_validators, list)
    validators = [Validator.from_dict(item) for item in raw_validators if isinstance(item, dict)]
    validator_set = make_validator_set(
        validators, epoch=0, fault_tolerance=int(content["fault_tolerance"])
    )
    founder = str(content["founder"])
    core = CoreState(
        group=genesis.group,
        chain_id=str(content["chain_id"]),
        validator_set=validator_set,
        members=frozenset({founder}),
        joined=frozenset({founder}),
        banned={},
        sequences={},
        metadata={"name": str(content["name"]), "description": str(content["description"])},
        public=bool(content["public"]),
        app=app_name,
    )
    return GroupState(core=core, app=app_module.initial_state(content))


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


def _validate_validator_update(
    core: CoreState,
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
        epoch=core.validator_set.epoch + 1,
        fault_tolerance=int(event.content["fault_tolerance"]),
    )
    added = next_set.pubkeys - core.validator_set.pubkeys
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
            ready.group == core.group
            and ready.chain_id == core.chain_id
            and ready.from_epoch == core.validator_set.epoch
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


def _apply_core_event(
    core: CoreState,
    event: Event,
    *,
    checkpoint_height: int,
    checkpoint_block_hash: str,
    history_root: str,
    logical_bytes: int,
) -> CoreState:
    content = event.content
    members = set(core.members)
    joined = set(core.joined)
    banned = dict(core.banned)
    metadata = dict(core.metadata)
    validator_set = core.validator_set

    event_type = event.type
    if event_type == ProtocolTypes.INVITE:
        members.add(str(content["invitee"]))
    elif event_type == ProtocolTypes.JOIN:
        joined.add(event.author)
        members.add(event.author)
    elif event_type == ProtocolTypes.LEAVE:
        joined.discard(event.author)
    elif event_type == ProtocolTypes.KICK:
        joined.discard(str(content["target"]))
    elif event_type == ProtocolTypes.BAN:
        target = str(content["target"])
        raw_until = content.get("until")
        banned[target] = BanRecord(
            until=int(raw_until) if raw_until is not None else None,
            reason=str(content.get("reason", "")),
        )
        joined.discard(target)
    elif event_type == ProtocolTypes.UNBAN:
        banned.pop(str(content["target"]), None)
    elif event_type == ProtocolTypes.METADATA_UPDATE:
        for key in ("name", "description"):
            if key in content:
                metadata[key] = str(content[key])
    elif event_type == ProtocolTypes.VALIDATOR_UPDATE:
        validator_set = _validate_validator_update(
            core,
            event,
            checkpoint_height=checkpoint_height,
            checkpoint_block_hash=checkpoint_block_hash,
            history_root=history_root,
            logical_bytes=logical_bytes,
        )
    else:
        raise ApplicationError(f"not a core event: {event_type}")

    return replace(
        core,
        validator_set=validator_set,
        members=frozenset(members),
        joined=frozenset(joined),
        banned=banned,
        metadata=metadata,
    )


def _authorize_core_event(
    app_module: AppModule,
    core: CoreState,
    app_state: AppState,
    event: Event,
    certified_seconds: int,
    certified_time_ms: int,
) -> None:
    event_type = event.type
    if event_type == ProtocolTypes.JOIN:
        if core.is_banned_at(event.author, certified_seconds) or not (
            core.public or event.author in core.members
        ):
            raise ApplicationError("event author is not authorized")
        return
    if event_type == ProtocolTypes.LEAVE:
        return
    # Every other core event is a policy decision delegated to the app.
    if not app_module.authorize(core, app_state, event, certified_time_ms):
        raise ApplicationError("event author is not authorized")


def validate_event_for_state(
    state: GroupState,
    event: Event,
    certified_time_ms: int,
    *,
    expected_seq: int | None = None,
) -> None:
    """Validate an event against the current state without applying it.

    Used at ingress to decide whether an event may enter the mempool. Does not
    check ``validator_update`` readiness, which requires checkpoint context and
    is verified when the transition is applied.
    """
    core = state.core
    app_module = get_app(core.app)
    try:
        verify_event(event)
    except (ValueError, TypeError) as exc:
        raise ApplicationError(str(exc)) from exc
    if event.group != core.group:
        raise ApplicationError("event belongs to another group")
    expected = (
        expected_seq if expected_seq is not None else core.sequences.get(event.author, 0) + 1
    )
    if event.seq != expected:
        raise ApplicationError(f"author sequence mismatch: expected {expected}, got {event.seq}")
    certified_seconds = certified_time_ms // 1000
    event_type = event.type
    if event_type in CORE_EVENT_TYPES:
        try:
            validate_core_event_semantics(event)
        except ValueError as exc:
            raise ApplicationError(str(exc)) from exc
        _authorize_core_event(
            app_module, core, state.app, event, certified_seconds, certified_time_ms
        )
    else:
        if not is_app_type(event_type) or namespace_of(event_type) != core.app:
            raise ApplicationError(f"event type not handled by app {core.app!r}: {event_type}")
        try:
            app_module.validate_semantics(event)
        except ValueError as exc:
            raise ApplicationError(str(exc)) from exc
        if not app_module.authorize(core, state.app, event, certified_time_ms):
            raise ApplicationError("event author is not authorized")
        app_module.validate_for_state(core, state.app, event, certified_time_ms)


def apply_event(
    state: GroupState,
    event: Event,
    certified_time_ms: int,
    *,
    checkpoint_height: int,
    checkpoint_block_hash: str,
    history_root: str,
    logical_bytes: int,
) -> GroupState:
    validate_event_for_state(state, event, certified_time_ms)
    core = state.core
    app_module = get_app(core.app)
    event_type = event.type

    if event_type in CORE_EVENT_TYPES:
        new_core = _apply_core_event(
            core,
            event,
            checkpoint_height=checkpoint_height,
            checkpoint_block_hash=checkpoint_block_hash,
            history_root=history_root,
            logical_bytes=logical_bytes,
        )
    else:
        new_core = core

    # The app owns every application-state transition, including side effects of
    # core events (e.g. dropping a banned or kicked user's roles).
    new_app = app_module.apply_event(new_core, state.app, event, certified_time_ms)
    new_core = replace(new_core, sequences={**new_core.sequences, event.author: event.seq})
    return GroupState(core=new_core, app=new_app)


def execute_events(
    state: GroupState,
    events: tuple[Event, ...],
    certified_times_ms: tuple[int, ...],
    *,
    governance: Event | None,
    checkpoint_height: int,
    checkpoint_block_hash: str,
    history_root: str,
    logical_bytes: int,
) -> GroupState:
    if len(certified_times_ms) != len(events) + (1 if governance is not None else 0):
        raise ApplicationError("certified timestamp count does not match events")
    boundary = boundary_types_for(state.core.app)
    if any(event.type in boundary for event in events):
        raise ApplicationError("boundary event must be in the governance slot")
    if governance is not None and governance.type not in boundary:
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
    "AppModule",
    "AppState",
    "ApplicationError",
    "BanRecord",
    "ChainHead",
    "CoreState",
    "GroupState",
    "apply_event",
    "boundary_types_for",
    "execute_events",
    "genesis_chain_head",
    "initial_state_from_genesis",
    "validate_event_for_state",
]
