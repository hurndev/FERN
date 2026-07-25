from __future__ import annotations

import pytest

from fern.bft.application import ApplicationError, ApplicationState, ChainHead, execute_events
from fern.events.build import build_event
from fern.events.event import Event
from tests.bft.helpers import genesis_fixture, validator_fixture


def _execute(
    head: ChainHead,
    *,
    events: tuple[Event, ...] = (),
    governance: Event | None = None,
    times: tuple[int, ...] = (),
) -> ApplicationState:
    return execute_events(
        head.state,
        events,
        times,
        governance=governance,
        checkpoint_height=head.height,
        checkpoint_block_hash=head.block_hash,
        history_root=head.history_root,
        logical_bytes=head.logical_bytes,
    )


def test_certified_time_and_finalized_ban_override_author_time() -> None:
    _keys, validator_set = validator_fixture(faults=0)
    _group, founder, _genesis, head, channel_id = genesis_fixture(validator_set)
    ban = build_event(
        type="ban",
        group=head.state.group,
        author_keypair=founder,
        seq=1,
        content={"target": founder.pubkey_hex, "until": None, "reason": "test"},
        ts=2_000_000_000,
    )
    banned = _execute(head, governance=ban, times=(2_000_000_000_000,))
    backdated_message = build_event(
        type="chat.message",
        group=head.state.group,
        author_keypair=founder,
        seq=2,
        content={"text": "backdated", "channel": channel_id, "reply_to": None},
        ts=1,
    )

    with pytest.raises(ApplicationError, match="not authorized"):
        execute_events(
            banned,
            (backdated_message,),
            (2_000_000_001_000,),
            governance=None,
            checkpoint_height=1,
            checkpoint_block_hash="a" * 64,
            history_root="b" * 64,
            logical_bytes=head.logical_bytes,
        )


def test_governance_event_is_rejected_from_ordinary_batch() -> None:
    _keys, validator_set = validator_fixture(faults=0)
    _group, founder, _genesis, head, _channel_id = genesis_fixture(validator_set)
    invite = build_event(
        type="invite",
        group=head.state.group,
        author_keypair=founder,
        seq=1,
        content={"invitee": "1" * 64, "role": "member"},
    )

    with pytest.raises(ApplicationError, match="governance slot"):
        _execute(head, events=(invite,), times=(1_711_234_567_000,))


def test_default_channel_cannot_be_deleted() -> None:
    _keys, validator_set = validator_fixture(faults=0)
    _group, founder, _genesis, head, channel_id = genesis_fixture(validator_set)
    deletion = build_event(
        type="chat.channel_delete",
        group=head.state.group,
        author_keypair=founder,
        seq=1,
        content={"id": channel_id},
    )

    with pytest.raises(ApplicationError, match="default channel"):
        _execute(head, governance=deletion, times=(1_711_234_567_000,))
