import json


from fern.events.event import Event
from fern.events.serialization import canonical_serialization, compute_id


class TestSerializationPropertyBased:
    def test_compute_id_is_deterministic(self) -> None:
        event = Event(
            type="chat.message",
            group="0" * 64,
            author="1" * 64,
            parents=("2" * 64, "3" * 64),
            content={"text": "hi", "channel": "general"},
            ts=1000,
            tags=(),
        )
        id1 = compute_id(event)
        id2 = compute_id(event)
        assert id1 == id2

    def test_unicode_content_roundtrips(self) -> None:
        event = Event(
            type="chat.message",
            group="0" * 64,
            author="1" * 64,
            parents=("p" * 64,),
            content={"text": "hello \U0001f60a world", "channel": "general"},
            ts=1000,
            tags=(),
        )

        parsed = json.loads(canonical_serialization(event))
        assert parsed[4]["text"] == "hello \U0001f60a world"

    def test_tags_sorted(self) -> None:
        e1 = Event(
            type="chat.message",
            group="0" * 64,
            author="1" * 64,
            parents=("p" * 64,),
            content={"text": "hi"},
            ts=1000,
            tags=(("b",), ("a",)),
        )
        e2 = Event(
            type="chat.message",
            group="0" * 64,
            author="1" * 64,
            parents=("p" * 64,),
            content={"text": "hi"},
            ts=1000,
            tags=(("a",), ("b",)),
        )
        assert canonical_serialization(e1) == canonical_serialization(e2)
