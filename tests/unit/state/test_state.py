
from fern.events.event import Event
from fern.events.types import ProtocolTypes
from fern.state.machine import derive_group_state


def make_genesis(
    group: str,
    founder: str,
    mods: list[str] | None = None,
    relays: list[str] | None = None,
    public: bool = True,
) -> Event:
    if mods is None:
        mods = [founder]
    if relays is None:
        relays = ["wss://relay.test"]
    return Event(
        id="0" * 64,
        type=ProtocolTypes.GENESIS,
        group=group,
        author=founder,
        parents=(),
        content={
            "name": "Test",
            "description": "",
            "public": public,
            "founder": founder,
            "mods": mods,
            "relays": relays,
        },
        ts=1,
        tags=(),
    )


def make_event(
    event_type: str,
    author: str,
    group: str,
    ts: int,
    content: dict,
    parents: tuple[str, ...] = ("0" * 64,),
    event_id: str | None = None,
    sig: str | None = None,
) -> Event:
    eid = event_id if event_id else f"{event_type[:4]}{author[:4]}{ts:08d}".ljust(64, "0")
    return Event(
        id=eid,
        type=event_type,
        group=group,
        author=author,
        parents=parents,
        content=content,
        ts=ts,
        tags=(),
        sig=sig,
    )


class TestGroupState:
    def test_genesis_initializes_state(self) -> None:
        genesis = make_genesis("0" * 64, "f" * 64)
        state, rejected = derive_group_state([genesis])
        assert "f" * 64 in state.members
        assert "f" * 64 in state.joined
        assert "f" * 64 in state.mods
        assert state.public is True
        assert len(rejected) == 0

    def test_join_adds_to_joined(self) -> None:
        genesis = make_genesis("0" * 64, "f" * 64)
        join = make_event(ProtocolTypes.JOIN, "a" * 64, "0" * 64, ts=2, content={})
        state, rejected = derive_group_state([genesis, join])
        assert ("a" * 64) in state.joined

    def test_ban_removes_from_joined(self) -> None:
        genesis = make_genesis("0" * 64, "f" * 64)
        ban = make_event(
            ProtocolTypes.BAN,
            "f" * 64,
            "0" * 64,
            ts=2,
            content={"target": "a" * 64, "until": None, "reason": "bad"},
        )
        state, rejected = derive_group_state([genesis, ban])
        assert "a" * 64 not in state.joined

    def test_events_sorted_by_ts_then_id(self) -> None:
        genesis = make_genesis("0" * 64, "f" * 64)
        ban = make_event(
            ProtocolTypes.BAN,
            "f" * 64,
            "0" * 64,
            ts=2,
            content={"target": "a" * 64, "until": None, "reason": "bad"},
            event_id="b" * 64,
        )
        unban = make_event(
            ProtocolTypes.UNBAN,
            "f" * 64,
            "0" * 64,
            ts=2,
            content={"target": "a" * 64},
            event_id="c" * 64,
        )
        join = make_event(ProtocolTypes.JOIN, "a" * 64, "0" * 64, ts=3, content={})
        state, rejected = derive_group_state([genesis, ban, unban, join])
        assert "a" * 64 in state.joined

    def test_kick_removes_from_joined_but_not_members(self) -> None:
        genesis = make_genesis("0" * 64, "f" * 64)
        join = make_event(ProtocolTypes.JOIN, "a" * 64, "0" * 64, ts=2, content={})
        kick = make_event(
            ProtocolTypes.KICK, "f" * 64, "0" * 64, ts=3, content={"target": "a" * 64}
        )
        state, rejected = derive_group_state([genesis, join, kick])
        assert "a" * 64 not in state.joined
        assert "a" * 64 not in state.mods
        assert "a" * 64 not in state.banned

    def test_mod_add_and_remove(self) -> None:
        genesis = make_genesis("0" * 64, "f" * 64)
        add = make_event(
            ProtocolTypes.MOD_ADD, "f" * 64, "0" * 64, ts=2, content={"target": "a" * 64}
        )
        remove = make_event(
            ProtocolTypes.MOD_REMOVE, "f" * 64, "0" * 64, ts=3, content={"target": "a" * 64}
        )
        state, rejected = derive_group_state([genesis, add, remove])
        assert "a" * 64 not in state.mods

    def test_metadata_update(self) -> None:
        genesis = make_genesis("0" * 64, "f" * 64)
        update = make_event(
            ProtocolTypes.METADATA_UPDATE, "f" * 64, "0" * 64, ts=2, content={"name": "New Name"}
        )
        state, rejected = derive_group_state([genesis, update])
        assert state.metadata["name"] == "New Name"
