import json

from fern.events.event import Event


class TestEventRoundTrip:
    def test_event_to_json_and_back(self) -> None:
        from fern.events.serialization import canonical_serialization

        event = Event(
            type="chat.message",
            group="0" * 64,
            author="1" * 64,
            parents=("2" * 64,),
            content={"text": "hi"},
            ts=1,
            tags=(),
            id="a" * 64,
            sig="b" * 128,
        )
        canon = canonical_serialization(event)
        reparsed = json.loads(canon)
        assert reparsed[0] == "chat.message"
        assert reparsed[1] == "0" * 64
