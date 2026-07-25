from __future__ import annotations

from dataclasses import replace

import pytest

from fern.bft.blocks import (
    Commit,
    sign_candidate,
    verify_block_evidence,
    verify_candidate,
    verify_commit_evidence,
)
from fern.bft.certificates import Vote, sign_vote
from fern.bft.chain import ChainVerificationError, verify_and_apply_commit
from fern.bft.consensus import (
    ConsensusCore,
    DoubleVoteError,
    MemorySafetyJournal,
    VoteSet,
)
from fern.bft.constants import PHASE_PREVOTE
from tests.bft.helpers import (
    genesis_fixture,
    message_fixture,
    proposal_fixture,
    validator_fixture,
    vote_quorum,
)


def test_verified_commit_advances_chain_and_finalizes_state() -> None:
    keys, validator_set = validator_fixture()
    _group, founder, _genesis, head, channel_id = genesis_fixture(validator_set)
    event = message_fixture(founder, head, channel_id)
    _proposal, block = proposal_fixture(head=head, event=event, validator_keys=keys)
    commit = Commit(block=block, round=0, precommits=vote_quorum(block=block, keys=keys))

    assert verify_commit_evidence(commit, validator_set)
    next_head = verify_and_apply_commit(head, commit)
    assert next_head.height == 1
    assert next_head.block_hash == block.id
    assert next_head.state.sequences[founder.pubkey_hex] == 1


def test_tampered_timestamp_or_state_root_is_rejected() -> None:
    keys, validator_set = validator_fixture()
    _group, founder, _genesis, head, channel_id = genesis_fixture(validator_set)
    event = message_fixture(founder, head, channel_id)
    _proposal, block = proposal_fixture(head=head, event=event, validator_keys=keys)

    assert not verify_block_evidence(
        replace(block, certified_times_ms=(block.certified_times_ms[0] + 1,)), validator_set
    )
    tampered = replace(block, state_root="0" * 64)
    votes = vote_quorum(block=tampered, keys=keys)
    with pytest.raises(ChainVerificationError):
        verify_and_apply_commit(head, Commit(block=tampered, round=0, precommits=votes))


def test_vote_set_excludes_equivocator_from_all_counts() -> None:
    keys, validator_set = validator_fixture()
    _group, founder, _genesis, head, channel_id = genesis_fixture(validator_set)
    event = message_fixture(founder, head, channel_id)
    _proposal, block = proposal_fixture(head=head, event=event, validator_keys=keys)
    vote_set = VoteSet(
        validator_set=validator_set,
        group=head.state.group,
        chain_id=head.state.chain_id,
        height=1,
        round=0,
        phase=PHASE_PREVOTE,
    )
    honest_vote = sign_vote(
        Vote(
            group=head.state.group,
            chain_id=head.state.chain_id,
            epoch=0,
            height=1,
            round=0,
            phase=PHASE_PREVOTE,
            block_id=block.id,
            validator=keys[0].pubkey_hex,
        ),
        keys[0],
    )
    conflicting_vote = sign_vote(replace(honest_vote, block_id="f" * 64, sig=""), keys[0])
    assert vote_set.add(honest_vote)
    assert not vote_set.add(conflicting_vote)
    assert keys[0].pubkey_hex in vote_set.equivocations
    assert not vote_set.votes_for(block.id)


def test_persisted_vote_prevents_double_signing() -> None:
    keys, validator_set = validator_fixture()
    _group, founder, _genesis, head, channel_id = genesis_fixture(validator_set)
    event = message_fixture(founder, head, channel_id)
    proposal, _block = proposal_fixture(head=head, event=event, validator_keys=keys)
    local = next(key for key in keys if key.pubkey_hex in validator_set.pubkeys)
    journal = MemorySafetyJournal()
    core = ConsensusCore(
        keypair=local,
        validator_set=validator_set,
        group=head.state.group,
        chain_id=head.state.chain_id,
        height=1,
        journal=journal,
        proposal_validator=lambda value: value.block.id == proposal.block.id,
    )

    first = core.prevote(proposal, 0)
    assert first.block_id == proposal.block.id
    with pytest.raises(DoubleVoteError):
        core.prevote(None, 0)


def test_lock_survives_round_and_rejects_unproven_conflict() -> None:
    keys, validator_set = validator_fixture()
    _group, founder, _genesis, head, channel_id = genesis_fixture(validator_set)
    first_event = message_fixture(founder, head, channel_id, "first")
    first, first_block = proposal_fixture(head=head, event=first_event, validator_keys=keys)
    local = keys[0]
    journal = MemorySafetyJournal()
    core = ConsensusCore(
        keypair=local,
        validator_set=validator_set,
        group=head.state.group,
        chain_id=head.state.chain_id,
        height=1,
        journal=journal,
        proposal_validator=lambda _value: True,
    )
    core.prevote(first, 0)
    prevotes = vote_quorum(block=first_block, keys=keys, phase=PHASE_PREVOTE, round=0)
    precommit = core.precommit_from_prevote_quorum(proposal=first, prevotes=prevotes, round=0)
    assert precommit.block_id == first_block.id

    conflicting_event = message_fixture(founder, head, channel_id, "conflict")
    conflicting, _ = proposal_fixture(
        head=head, event=conflicting_event, validator_keys=keys, round=1
    )
    core.enter_round(1)
    assert core.prevote(conflicting, 1).block_id is None


def test_quorums_intersect_in_at_least_f_plus_one_validators() -> None:
    for faults in range(1, 6):
        n = 3 * faults + 1
        quorum = 2 * faults + 1
        first = set(range(quorum))
        second = set(range(n - quorum, n))
        assert len(first & second) >= faults + 1


@pytest.mark.parametrize("validator_count", [1, 2, 3])
def test_small_validator_sets_are_unanimous(validator_count: int) -> None:
    keys, validator_set = validator_fixture(faults=0, validator_count=validator_count)
    _group, founder, _genesis, head, channel_id = genesis_fixture(validator_set)
    _proposal, block = proposal_fixture(
        head=head,
        event=message_fixture(founder, head, channel_id),
        validator_keys=keys,
    )
    commit = Commit(
        block=block,
        round=0,
        precommits=vote_quorum(block=block, keys=keys),
    )

    assert validator_set.is_small_unanimous
    assert validator_set.quorum == validator_count
    assert validator_set.propagation_threshold == 1
    assert validator_set.round_catchup_threshold == 1
    assert verify_commit_evidence(commit, validator_set)
    if validator_count > 1:
        assert not verify_commit_evidence(
            replace(commit, precommits=commit.precommits[:-1]), validator_set
        )


@pytest.mark.parametrize("validator_count", [1, 2, 3])
def test_small_validator_sets_require_zero_declared_faults(
    validator_count: int,
) -> None:
    _keys, validator_set = validator_fixture(faults=0, validator_count=validator_count)

    with pytest.raises(ValueError, match="fewer than 4"):
        replace(validator_set, fault_tolerance=1)


def test_standard_validator_set_uses_f_plus_one_round_catchup() -> None:
    _keys, validator_set = validator_fixture(faults=2)

    assert validator_set.propagation_threshold == 3
    assert validator_set.round_catchup_threshold == 3


def test_empty_candidates_are_invalid() -> None:
    keys, validator_set = validator_fixture()
    _group, founder, _genesis, head, channel_id = genesis_fixture(validator_set)
    proposal, _block = proposal_fixture(
        head=head,
        event=message_fixture(founder, head, channel_id),
        validator_keys=keys,
    )
    proposer_key = next(key for key in keys if key.pubkey_hex == proposal.block.candidate.proposer)
    empty = sign_candidate(
        replace(proposal.block.candidate, events=(), governance=None, sig=""),
        proposer_key,
    )
    assert not verify_candidate(empty, validator_set)
