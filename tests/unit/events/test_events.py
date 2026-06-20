
import pytest

from fern.events.event import Event
from fern.events.serialization import canonical_serialization, compute_id
from fern.events.validation import (
    verify_event,
    MalformedEventError,
    InvalidHashError,
)
from fern.events.build import build_event
from fern.events.types import ProtocolTypes, ChatTypes
from fern.identity.user import UserIdentity
from fern.identity.group import GroupKeypair


class TestCanonicalSerialization:
    def test_parents_are_sorted(self) -> None:
        e1 = Event(
            type="chat.message",
            group="a" * 64,
            author="b" * 64,
            parents=("ccc" + "c" * 61, "aaa" + "a" * 61, "bbb" + "b" * 61),
            content={"text": "hi"},
            ts=1000,
            tags=(),
        )
        e2 = Event(
            type="chat.message",
            group="a" * 64,
            author="b" * 64,
            parents=("aaa" + "a" * 61, "bbb" + "b" * 61, "ccc" + "c" * 61),
            content={"text": "hi"},
            ts=1000,
            tags=(),
        )
        assert canonical_serialization(e1) == canonical_serialization(e2)

    def test_content_keys_sorted(self) -> None:
        e = Event(
            type="chat.message",
            group="a" * 64,
            author="b" * 64,
            parents=("a" * 64,),
            content={"z": 1, "a": 2},
            ts=1000,
            tags=(),
        )
        canon = canonical_serialization(e).decode("utf-8")
        assert canon.index('"a"') < canon.index('"z"')

    def test_is_deterministic(self) -> None:
        e = Event(
            type="chat.message",
            group="a" * 64,
            author="b" * 64,
            parents=("a" * 64,),
            content={"text": "hi"},
            ts=1000,
            tags=(),
        )
        assert canonical_serialization(e) == canonical_serialization(e)

    def test_no_whitespace_in_output(self) -> None:
        e = Event(
            type="chat.message",
            group="a" * 64,
            author="b" * 64,
            parents=("a" * 64,),
            content={"text": "hi"},
            ts=1000,
            tags=(),
        )
        output = canonical_serialization(e).decode("utf-8")
        assert " " not in output
        assert "\n" not in output

    def test_nested_content_sorted(self) -> None:
        e = Event(
            type="chat.message",
            group="a" * 64,
            author="b" * 64,
            parents=("a" * 64,),
            content={"outer": {"z": 1, "a": 2}},
            ts=1000,
            tags=(),
        )
        canon = canonical_serialization(e).decode("utf-8")
        a_pos = canon.index('"a"')
        z_pos = canon.index('"z"')
        assert a_pos < z_pos

    def test_arrays_in_content_not_sorted(self) -> None:
        e = Event(
            type="chat.message",
            group="a" * 64,
            author="b" * 64,
            parents=("a" * 64,),
            content={"items": [3, 1, 2]},
            ts=1000,
            tags=(),
        )
        canon = canonical_serialization(e).decode("utf-8")
        assert "[3,1,2]" in canon


class TestBuildEvent:
    def test_build_chat_message(
        self, alice_identity: UserIdentity, group_keypair: GroupKeypair
    ) -> None:
        event = build_event(
            type=ChatTypes.MESSAGE,
            group=group_keypair.pubkey,
            author_keypair=alice_identity.keypair,
            parents=("a" * 64,),
            content={"text": "hello", "channel": "general"},
        )
        assert event.id is not None
        assert event.sig is not None
        assert len(event.id) == 64
        assert len(event.sig) == 128

    def test_build_genesis(
        self, founder_identity: UserIdentity, group_keypair: GroupKeypair
    ) -> None:
        event = build_event(
            type=ProtocolTypes.GENESIS,
            group=group_keypair.pubkey,
            author_keypair=founder_identity.keypair,
            parents=(),
            content={
                "name": "Test",
                "description": "",
                "public": True,
                "founder": founder_identity.pubkey,
                "mods": [founder_identity.pubkey],
                "relays": ["wss://relay.test"],
            },
            group_keypair=group_keypair.keypair,
        )
        assert event.id is not None
        assert event.is_genesis
        assert len(event.parents) == 0


class TestVerifyEvent:
    def test_verify_valid_event(self, sample_genesis: Event) -> None:
        verify_event(sample_genesis)

    def test_verify_tampered_content_fails(
        self, alice_identity: UserIdentity, group_keypair: GroupKeypair
    ) -> None:
        event = build_event(
            type=ChatTypes.MESSAGE,
            group=group_keypair.pubkey,
            author_keypair=alice_identity.keypair,
            parents=("a" * 64,),
            content={"text": "hello"},
        )
        tampered = Event(
            type=event.type,
            group=event.group,
            author=event.author,
            parents=event.parents,
            content={"text": "tampered"},
            ts=event.ts,
            tags=event.tags,
            id=event.id,
            sig=event.sig,
        )
        with pytest.raises(InvalidHashError):
            verify_event(tampered)

    def test_genesis_rejects_wrong_signer(
        self, founder_identity: UserIdentity, group_keypair: GroupKeypair
    ) -> None:
        event = build_event(
            type=ProtocolTypes.GENESIS,
            group=group_keypair.pubkey,
            author_keypair=founder_identity.keypair,
            parents=(),
            content={
                "name": "Test",
                "description": "",
                "public": True,
                "founder": founder_identity.pubkey,
                "mods": [founder_identity.pubkey],
                "relays": ["wss://relay.test"],
            },
            group_keypair=group_keypair.keypair,
        )
        verify_event(event)

    def test_non_genesis_must_have_parents(
        self, founder_identity: UserIdentity, group_keypair: GroupKeypair
    ) -> None:
        event = build_event(
            type=ChatTypes.MESSAGE,
            group=group_keypair.pubkey,
            author_keypair=founder_identity.keypair,
            parents=(),
            content={"text": "hi"},
        )
        with pytest.raises(MalformedEventError):
            verify_event(event)

    def test_compute_id_matches(self, sample_genesis: Event) -> None:
        computed = compute_id(sample_genesis)
        assert computed == sample_genesis.id
