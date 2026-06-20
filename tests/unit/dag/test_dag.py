
from fern.events.event import Event
from fern.dag.heads import compute_heads
from fern.dag.gaps import find_missing_parents
from fern.dag.cycle_check import has_cycle


class TestComputeHeads:
    def test_genesis_is_head_of_single_event(self) -> None:
        events = [
            Event(
                id="a" * 64,
                type="genesis",
                group="0" * 64,
                author="1" * 64,
                parents=(),
                content={},
                ts=1,
                tags=(),
            ),
        ]
        heads = compute_heads(events)
        assert "a" * 64 in heads
        assert len(heads) == 1

    def test_child_replaces_parent_as_head(self) -> None:
        events = [
            Event(
                id="a" * 64,
                type="genesis",
                group="0" * 64,
                author="1" * 64,
                parents=(),
                content={},
                ts=1,
                tags=(),
            ),
            Event(
                id="b" * 64,
                type="chat.message",
                group="0" * 64,
                author="1" * 64,
                parents=("a" * 64,),
                content={},
                ts=2,
                tags=(),
            ),
        ]
        heads = compute_heads(events)
        assert "b" * 64 in heads
        assert "a" * 64 not in heads

    def test_multiple_heads_when_no_common_child(self) -> None:
        events = [
            Event(
                id="a" * 64,
                type="genesis",
                group="0" * 64,
                author="1" * 64,
                parents=(),
                content={},
                ts=1,
                tags=(),
            ),
            Event(
                id="b1" + "b" * 62,
                type="chat.message",
                group="0" * 64,
                author="1" * 64,
                parents=("a" * 64,),
                content={},
                ts=2,
                tags=(),
            ),
            Event(
                id="b2" + "b" * 62,
                type="chat.message",
                group="0" * 64,
                author="1" * 64,
                parents=("a" * 64,),
                content={},
                ts=2,
                tags=(),
            ),
        ]
        heads = compute_heads(events)
        assert len(heads) == 2


class TestFindMissingParents:
    def test_no_missing_parents(self) -> None:
        events = [
            Event(
                id="a" * 64,
                type="genesis",
                group="0" * 64,
                author="1" * 64,
                parents=(),
                content={},
                ts=1,
                tags=(),
            ),
            Event(
                id="b" * 64,
                type="chat.message",
                group="0" * 64,
                author="1" * 64,
                parents=("a" * 64,),
                content={},
                ts=2,
                tags=(),
            ),
        ]
        missing = find_missing_parents(events)
        assert len(missing) == 0

    def test_missing_parent_detected(self) -> None:
        events = [
            Event(
                id="b" * 64,
                type="chat.message",
                group="0" * 64,
                author="1" * 64,
                parents=("a" * 64,),
                content={},
                ts=2,
                tags=(),
            ),
        ]
        missing = find_missing_parents(events)
        assert "a" * 64 in missing


class TestCycleCheck:
    def test_no_cycle(self) -> None:
        events = [
            Event(
                id="a" * 64,
                type="genesis",
                group="0" * 64,
                author="1" * 64,
                parents=(),
                content={},
                ts=1,
                tags=(),
            ),
            Event(
                id="b" * 64,
                type="chat.message",
                group="0" * 64,
                author="1" * 64,
                parents=("a" * 64,),
                content={},
                ts=2,
                tags=(),
            ),
        ]
        assert not has_cycle(events)

    def test_self_reference_is_cycle(self) -> None:
        events = [
            Event(
                id="a" * 64,
                type="chat.message",
                group="0" * 64,
                author="1" * 64,
                parents=("a" * 64,),
                content={},
                ts=1,
                tags=(),
            ),
        ]
        assert has_cycle(events)
