from __future__ import annotations

import pytest

from fern.bft.certificates import Vote, sign_vote
from fern.bft.consensus import DoubleVoteError
from fern.bft.constants import PHASE_PREVOTE
from fern.bft.store import BFTStore
from tests.bft.helpers import genesis_fixture, validator_fixture


def test_sqlite_journal_refuses_conflicting_vote_after_restart(tmp_path) -> None:
    keys, validator_set = validator_fixture()
    _group, _founder, genesis, head, _channel = genesis_fixture(validator_set)
    path = tmp_path / "validator.sqlite"
    first = Vote(
        group=head.state.group,
        chain_id=head.state.chain_id,
        epoch=0,
        height=1,
        round=0,
        phase=PHASE_PREVOTE,
        block_id="a" * 64,
        validator=keys[0].pubkey_hex,
    )
    first = sign_vote(first, keys[0])

    store = BFTStore(path)
    store.bootstrap_genesis(genesis)
    store.record_own_vote(first)
    store.close()

    reopened = BFTStore(path)
    conflicting = sign_vote(
        Vote(
            group=first.group,
            chain_id=first.chain_id,
            epoch=first.epoch,
            height=first.height,
            round=first.round,
            phase=first.phase,
            block_id="b" * 64,
            validator=first.validator,
        ),
        keys[0],
    )
    with pytest.raises(DoubleVoteError):
        reopened.record_own_vote(conflicting)
    reopened.close()


def test_nil_and_block_votes_are_persisted_as_equivocation(tmp_path) -> None:
    keys, validator_set = validator_fixture()
    _group, _founder, genesis, head, _channel = genesis_fixture(validator_set)
    store = BFTStore(tmp_path / "validator.sqlite")
    store.bootstrap_genesis(genesis)
    base = Vote(
        group=head.state.group,
        chain_id=head.state.chain_id,
        epoch=0,
        height=1,
        round=0,
        phase=PHASE_PREVOTE,
        block_id=None,
        validator=keys[0].pubkey_hex,
    )
    store.save_vote(sign_vote(base, keys[0]))
    store.save_vote(sign_vote(Vote(**{**base.__dict__, "block_id": "a" * 64}), keys[0]))
    votes = store.votes(head.state.group, 0, 1, 0, PHASE_PREVOTE)
    assert {vote.block_id for vote in votes} == {None, "a" * 64}
    store.close()
