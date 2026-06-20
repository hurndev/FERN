
from fern.events.event import Event
from fern.events.types import ChatTypes
from fern.identity.user import UserIdentity
from fern.identity.group import GroupKeypair
from fern.chat.messages import build_chat_message, is_chat_message
from fern.chat.reactions import build_reaction
from fern.chat.nicknames import build_nickname_set, resolve_nickname


class TestChatMessages:
    def test_build_chat_message(
        self, alice_identity: UserIdentity, group_keypair: GroupKeypair
    ) -> None:
        event = build_chat_message(
            user=alice_identity,
            group=group_keypair.pubkey,
            parents=("p" * 64,),
            text="Hello!",
            channel="general",
        )
        assert event.type == ChatTypes.MESSAGE
        assert event.content["text"] == "Hello!"
        assert event.content["channel"] == "general"

    def test_is_chat_message(
        self, alice_identity: UserIdentity, group_keypair: GroupKeypair
    ) -> None:
        event = build_chat_message(
            user=alice_identity,
            group=group_keypair.pubkey,
            parents=("p" * 64,),
            text="Hi",
        )
        assert is_chat_message(event)


class TestReactions:
    def test_build_reaction(
        self, alice_identity: UserIdentity, group_keypair: GroupKeypair
    ) -> None:
        event = build_reaction(
            user=alice_identity,
            group=group_keypair.pubkey,
            parents=("p" * 64,),
            target="t" * 64,
            emoji="+1",
        )
        assert event.type == ChatTypes.REACTION
        assert event.content["target"] == "t" * 64
        assert event.content["emoji"] == "+1"


class TestNicknames:
    def test_build_nickname(
        self, alice_identity: UserIdentity, group_keypair: GroupKeypair
    ) -> None:
        event = build_nickname_set(
            user=alice_identity,
            group=group_keypair.pubkey,
            parents=("p" * 64,),
            nickname="Alice",
        )
        assert event.type == ChatTypes.NICKNAME_SET
        assert event.content["nickname"] == "Alice"

    def test_resolve_nickname(self) -> None:
        events = [
            Event(
                id="a" * 64,
                type=ChatTypes.NICKNAME_SET,
                group="0" * 64,
                author="1" * 64,
                parents=("p" * 64,),
                content={"nickname": "OldNick"},
                ts=1,
                tags=(),
            ),
            Event(
                id="b" * 64,
                type=ChatTypes.NICKNAME_SET,
                group="0" * 64,
                author="1" * 64,
                parents=("p" * 64,),
                content={"nickname": "NewNick"},
                ts=2,
                tags=(),
            ),
        ]
        assert resolve_nickname("1" * 64, events) == "NewNick"
