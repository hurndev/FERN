from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol

from fern.bft.blocks import Proposal, verify_proposal
from fern.bft.certificates import Vote, sign_vote, verify_vote
from fern.bft.constants import PHASE_PRECOMMIT, PHASE_PREVOTE
from fern.bft.validators import ValidatorSet
from fern.crypto.keys import Keypair


class ConsensusSafetyError(RuntimeError):
    pass


class DoubleVoteError(ConsensusSafetyError):
    pass


@dataclass(frozen=True)
class SafetyState:
    group: str
    chain_id: str
    epoch: int
    height: int
    round: int = 0
    locked_round: int | None = None
    locked_block_id: str | None = None
    locked_proposal: Proposal | None = None
    valid_round: int | None = None
    valid_proposal: Proposal | None = None


class SafetyJournal(Protocol):
    def load_safety_state(
        self, group: str, chain_id: str, epoch: int, height: int
    ) -> SafetyState | None: ...

    def save_safety_state(self, state: SafetyState) -> None: ...

    def record_own_vote(self, vote: Vote) -> Vote: ...


class MemorySafetyJournal:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str, int, int], SafetyState] = {}
        self.votes: dict[tuple[str, str, int, int, int, str], Vote] = {}

    def load_safety_state(
        self, group: str, chain_id: str, epoch: int, height: int
    ) -> SafetyState | None:
        return self.states.get((group, chain_id, epoch, height))

    def save_safety_state(self, state: SafetyState) -> None:
        self.states[(state.group, state.chain_id, state.epoch, state.height)] = state

    def record_own_vote(self, vote: Vote) -> Vote:
        key = (vote.group, vote.chain_id, vote.epoch, vote.height, vote.round, vote.phase)
        existing = self.votes.get(key)
        if existing is not None:
            if existing.block_id != vote.block_id:
                raise DoubleVoteError("refusing to sign a conflicting vote")
            return existing
        self.votes[key] = vote
        return vote


class VoteSet:
    """Collect one validator vote per position and expose conservative quorums.

    A validator observed equivocating is removed from every count. This is
    stricter than Tendermint requires and can reduce liveness, but it never
    manufactures a quorum from contradictory evidence.
    """

    def __init__(
        self,
        *,
        validator_set: ValidatorSet,
        group: str,
        chain_id: str,
        height: int,
        round: int,
        phase: str,
    ) -> None:
        self.validator_set = validator_set
        self.group = group
        self.chain_id = chain_id
        self.height = height
        self.round = round
        self.phase = phase
        self._votes: dict[str, Vote] = {}
        self._equivocators: set[str] = set()

    @property
    def equivocations(self) -> frozenset[str]:
        return frozenset(self._equivocators)

    def add(self, vote: Vote) -> bool:
        if not (
            verify_vote(vote)
            and vote.group == self.group
            and vote.chain_id == self.chain_id
            and vote.epoch == self.validator_set.epoch
            and vote.height == self.height
            and vote.round == self.round
            and vote.phase == self.phase
            and vote.validator in self.validator_set.pubkeys
        ):
            return False
        existing = self._votes.get(vote.validator)
        if existing is not None and existing.block_id != vote.block_id:
            self._equivocators.add(vote.validator)
            return False
        self._votes[vote.validator] = vote
        return True

    def votes_for(self, block_id: str | None) -> tuple[Vote, ...]:
        return tuple(
            sorted(
                (
                    vote
                    for validator, vote in self._votes.items()
                    if validator not in self._equivocators and vote.block_id == block_id
                ),
                key=lambda vote: vote.validator,
            )
        )

    def quorum_for(self, block_id: str | None) -> tuple[Vote, ...] | None:
        votes = self.votes_for(block_id)
        return votes if len(votes) >= self.validator_set.quorum else None

    def any_quorum(self) -> tuple[str | None, tuple[Vote, ...]] | None:
        values = {vote.block_id for vote in self._votes.values()}
        for value in sorted(values, key=lambda item: "" if item is None else item):
            quorum = self.quorum_for(value)
            if quorum is not None:
                return value, quorum
        return None


class ConsensusCore:
    """Crash-safe Tendermint voting and locking rules for one height.

    Networking, timeouts and proposal construction live outside this class.
    Every method returning a local vote has already persisted it.
    """

    def __init__(
        self,
        *,
        keypair: Keypair,
        validator_set: ValidatorSet,
        group: str,
        chain_id: str,
        height: int,
        journal: SafetyJournal,
        proposal_validator: Callable[[Proposal], bool],
    ) -> None:
        if keypair.pubkey_hex not in validator_set.pubkeys:
            raise ValueError("local validator is not active in this epoch")
        self.keypair = keypair
        self.validator_set = validator_set
        self.journal = journal
        self.proposal_validator = proposal_validator
        loaded = journal.load_safety_state(group, chain_id, validator_set.epoch, height)
        self.state = loaded or SafetyState(
            group=group,
            chain_id=chain_id,
            epoch=validator_set.epoch,
            height=height,
        )
        if loaded is None:
            journal.save_safety_state(self.state)

    def enter_round(self, round: int) -> None:
        if round < self.state.round:
            return
        self.state = SafetyState(
            group=self.state.group,
            chain_id=self.state.chain_id,
            epoch=self.state.epoch,
            height=self.state.height,
            round=round,
            locked_round=self.state.locked_round,
            locked_block_id=self.state.locked_block_id,
            locked_proposal=self.state.locked_proposal,
            valid_round=self.state.valid_round,
            valid_proposal=self.state.valid_proposal,
        )
        self.journal.save_safety_state(self.state)

    def _vote(self, phase: str, round: int, block_id: str | None) -> Vote:
        unsigned = Vote(
            group=self.state.group,
            chain_id=self.state.chain_id,
            epoch=self.state.epoch,
            height=self.state.height,
            round=round,
            phase=phase,
            block_id=block_id,
            validator=self.keypair.pubkey_hex,
        )
        signed = sign_vote(unsigned, self.keypair)
        return self.journal.record_own_vote(signed)

    def prevote(self, proposal: Proposal | None, round: int) -> Vote:
        if round != self.state.round:
            raise ConsensusSafetyError("cannot prevote outside the current round")
        block_id: str | None = None
        if proposal is not None and self._proposal_unlocks(proposal, round):
            block_id = proposal.block.id
        return self._vote(PHASE_PREVOTE, round, block_id)

    def _proposal_unlocks(self, proposal: Proposal, round: int) -> bool:
        if not (
            proposal.round == round
            and proposal.height == self.state.height
            and proposal.epoch == self.state.epoch
            and proposal.group == self.state.group
            and proposal.chain_id == self.state.chain_id
            and verify_proposal(proposal, self.validator_set)
            and self.proposal_validator(proposal)
        ):
            return False
        if self.state.locked_block_id is None:
            return True
        if self.state.locked_block_id == proposal.block.id:
            return True
        return (
            proposal.valid_round is not None
            and self.state.locked_round is not None
            and proposal.valid_round >= self.state.locked_round
        )

    def precommit_from_prevote_quorum(
        self,
        *,
        proposal: Proposal | None,
        prevotes: tuple[Vote, ...],
        round: int,
    ) -> Vote:
        if round != self.state.round:
            raise ConsensusSafetyError("cannot precommit outside the current round")
        vote_set = VoteSet(
            validator_set=self.validator_set,
            group=self.state.group,
            chain_id=self.state.chain_id,
            height=self.state.height,
            round=round,
            phase=PHASE_PREVOTE,
        )
        for vote in prevotes:
            vote_set.add(vote)
        if proposal is None:
            if vote_set.quorum_for(None) is None:
                raise ConsensusSafetyError("nil precommit requires a nil prevote quorum")
            return self._vote(PHASE_PRECOMMIT, round, None)
        block_id = proposal.block.id
        if vote_set.quorum_for(block_id) is None:
            raise ConsensusSafetyError("block precommit requires a matching prevote quorum")
        if not self._proposal_unlocks(proposal, round):
            raise ConsensusSafetyError("cannot lock an invalid proposal")

        # The lock and valid-block evidence must reach durable storage before
        # the precommit signature can escape this process.
        self.state = SafetyState(
            group=self.state.group,
            chain_id=self.state.chain_id,
            epoch=self.state.epoch,
            height=self.state.height,
            round=self.state.round,
            locked_round=round,
            locked_block_id=block_id,
            locked_proposal=proposal,
            valid_round=round,
            valid_proposal=proposal,
        )
        self.journal.save_safety_state(self.state)
        return self._vote(PHASE_PRECOMMIT, round, block_id)

    def precommit_nil(self, round: int) -> Vote:
        """Persist a nil precommit after the local prevote phase times out."""

        if round != self.state.round:
            raise ConsensusSafetyError("cannot precommit outside the current round")
        return self._vote(PHASE_PRECOMMIT, round, None)


__all__ = [
    "ConsensusCore",
    "ConsensusSafetyError",
    "DoubleVoteError",
    "MemorySafetyJournal",
    "SafetyJournal",
    "SafetyState",
    "VoteSet",
]
