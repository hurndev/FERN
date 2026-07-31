from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median_low

from fern.bft.canonical import canonical_hash, sign_payload, strict_int, verify_payload_signature
from fern.bft.certificates import (
    TimestampObservation,
    Vote,
    verify_timestamp_observation,
    verify_vote,
)
from fern.bft.constants import (
    MAX_BLOCK_BYTES,
    MAX_BLOCK_EVENTS,
    PHASE_PRECOMMIT,
    PHASE_PREVOTE,
)
from fern.bft.validators import ValidatorSet
from fern.crypto.encoding import is_valid_event_id_hex, is_valid_pubkey_hex, is_valid_sig_hex
from fern.crypto.keys import Keypair
from fern.events.event import Event
from fern.events.validation import verify_event


@dataclass(frozen=True)
class Candidate:
    group: str
    chain_id: str
    epoch: int
    height: int
    round: int
    previous_block_hash: str
    previous_state_root: str
    events: tuple[Event, ...]
    governance: Event | None
    proposer: str
    sig: str = ""

    @property
    def event_ids(self) -> tuple[str, ...]:
        ids = [event.id or "" for event in self.events]
        if self.governance is not None:
            ids.append(self.governance.id or "")
        return tuple(ids)

    @property
    def all_events(self) -> tuple[Event, ...]:
        if self.governance is None:
            return self.events
        return self.events + (self.governance,)

    def signing_payload(self) -> list[object]:
        return [
            "candidate",
            self.group,
            self.chain_id,
            self.epoch,
            self.height,
            self.round,
            self.previous_block_hash,
            self.previous_state_root,
            [event.id for event in self.events],
            self.governance.id if self.governance is not None else None,
            self.proposer,
        ]

    @property
    def id(self) -> str:
        return canonical_hash(self.signing_payload())

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "candidate",
            "id": self.id,
            "group": self.group,
            "chain_id": self.chain_id,
            "epoch": self.epoch,
            "height": self.height,
            "round": self.round,
            "previous_block_hash": self.previous_block_hash,
            "previous_state_root": self.previous_state_root,
            "events": [event.to_dict() for event in self.events],
            "governance": self.governance.to_dict() if self.governance is not None else None,
            "proposer": self.proposer,
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Candidate:
        raw_events = value.get("events", [])
        if not isinstance(raw_events, list):
            raise ValueError("candidate events must be an array")
        events = tuple(Event.from_dict(item) for item in raw_events if isinstance(item, dict))
        if len(events) != len(raw_events):
            raise ValueError("invalid candidate event")
        raw_governance = value.get("governance")
        governance = Event.from_dict(raw_governance) if isinstance(raw_governance, dict) else None
        return cls(
            group=str(value.get("group", "")),
            chain_id=str(value.get("chain_id", "")),
            epoch=strict_int(value.get("epoch"), "candidate epoch"),
            height=strict_int(value.get("height"), "candidate height"),
            round=strict_int(value.get("round"), "candidate round"),
            previous_block_hash=str(value.get("previous_block_hash", "")),
            previous_state_root=str(value.get("previous_state_root", "")),
            events=events,
            governance=governance,
            proposer=str(value.get("proposer", "")),
            sig=str(value.get("sig", "")),
        )


def sign_candidate(candidate: Candidate, keypair: Keypair) -> Candidate:
    if candidate.proposer != keypair.pubkey_hex:
        raise ValueError("candidate proposer does not match signing key")
    return replace(candidate, sig=sign_payload(keypair, candidate.signing_payload()))


def verify_candidate(candidate: Candidate, validator_set: ValidatorSet) -> bool:
    if not (
        is_valid_pubkey_hex(candidate.group)
        and is_valid_event_id_hex(candidate.chain_id)
        and candidate.epoch == validator_set.epoch
        and candidate.height > 0
        and candidate.round >= 0
        and is_valid_event_id_hex(candidate.previous_block_hash)
        and is_valid_event_id_hex(candidate.previous_state_root)
        and candidate.proposer == validator_set.proposer(candidate.height, candidate.round).pubkey
        and is_valid_sig_hex(candidate.sig)
        and 0 < len(candidate.all_events) <= MAX_BLOCK_EVENTS
        and len(candidate.all_events) == len(set(candidate.event_ids))
        and verify_payload_signature(candidate.proposer, candidate.signing_payload(), candidate.sig)
    ):
        return False
    try:
        for event in candidate.all_events:
            verify_event(event)
            if event.group != candidate.group or event.type == "genesis":
                return False
    except (ValueError, TypeError):
        return False
    return True


@dataclass(frozen=True)
class Block:
    candidate: Candidate
    observations: tuple[TimestampObservation, ...]
    certified_times_ms: tuple[int, ...]
    previous_history_root: str
    state_root: str
    history_root: str

    @property
    def group(self) -> str:
        return self.candidate.group

    @property
    def chain_id(self) -> str:
        return self.candidate.chain_id

    @property
    def epoch(self) -> int:
        return self.candidate.epoch

    @property
    def height(self) -> int:
        return self.candidate.height

    def hash_payload(self) -> list[object]:
        return [
            "block",
            self.candidate.to_dict(),
            [observation.to_dict() for observation in self.observations],
            list(self.certified_times_ms),
            self.previous_history_root,
            self.state_root,
            self.history_root,
        ]

    @property
    def id(self) -> str:
        return canonical_hash(self.hash_payload())

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "block",
            "id": self.id,
            "group": self.group,
            "chain_id": self.chain_id,
            "epoch": self.epoch,
            "height": self.height,
            "candidate": self.candidate.to_dict(),
            "observations": [observation.to_dict() for observation in self.observations],
            "certified_times_ms": list(self.certified_times_ms),
            "previous_history_root": self.previous_history_root,
            "state_root": self.state_root,
            "history_root": self.history_root,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Block:
        raw_candidate = value.get("candidate")
        raw_observations = value.get("observations", [])
        raw_times = value.get("certified_times_ms", [])
        if not isinstance(raw_candidate, dict):
            raise ValueError("block candidate must be an object")
        if not isinstance(raw_observations, list) or not isinstance(raw_times, list):
            raise ValueError("invalid block arrays")
        observations = tuple(
            TimestampObservation.from_dict(item)
            for item in raw_observations
            if isinstance(item, dict)
        )
        if len(observations) != len(raw_observations):
            raise ValueError("invalid block observation")
        return cls(
            candidate=Candidate.from_dict(raw_candidate),
            observations=observations,
            certified_times_ms=tuple(strict_int(item, "certified timestamp") for item in raw_times),
            previous_history_root=str(value.get("previous_history_root", "")),
            state_root=str(value.get("state_root", "")),
            history_root=str(value.get("history_root", "")),
        )


def compute_history_root(previous_history_root: str, event_ids: tuple[str, ...]) -> str:
    return canonical_hash(["fern-bft-history", previous_history_root, list(event_ids)])


def observation_medians(
    observations: tuple[TimestampObservation, ...], event_count: int
) -> tuple[int, ...]:
    if not observations:
        raise ValueError("observation quorum is empty")
    return tuple(
        int(median_low([observation.observed_ms[index] for observation in observations]))
        for index in range(event_count)
    )


def build_block(
    *,
    candidate: Candidate,
    observations: tuple[TimestampObservation, ...],
    validator_set: ValidatorSet,
    previous_history_root: str,
    state_root: str,
) -> Block:
    ordered = tuple(sorted(observations, key=lambda observation: observation.validator))
    if len(ordered) != validator_set.quorum:
        raise ValueError("block requires exactly one observation quorum")
    event_count = len(candidate.all_events)
    certified = observation_medians(ordered, event_count)
    return Block(
        candidate=candidate,
        observations=ordered,
        certified_times_ms=certified,
        previous_history_root=previous_history_root,
        state_root=state_root,
        history_root=compute_history_root(previous_history_root, candidate.event_ids),
    )


def verify_block_evidence(block: Block, validator_set: ValidatorSet) -> bool:
    candidate = block.candidate
    if not verify_candidate(candidate, validator_set):
        return False
    if len(block.observations) != validator_set.quorum:
        return False
    validators = [observation.validator for observation in block.observations]
    if validators != sorted(validators) or len(validators) != len(set(validators)):
        return False
    if not set(validators).issubset(validator_set.pubkeys):
        return False
    event_count = len(candidate.all_events)
    for observation in block.observations:
        if not (
            observation.group == candidate.group
            and observation.chain_id == candidate.chain_id
            and observation.epoch == candidate.epoch
            and observation.height == candidate.height
            and observation.round == candidate.round
            and observation.candidate_id == candidate.id
            and verify_timestamp_observation(observation, event_count=event_count)
        ):
            return False
    if block.certified_times_ms != observation_medians(block.observations, event_count):
        return False
    if not all(
        is_valid_event_id_hex(value)
        for value in (block.previous_history_root, block.state_root, block.history_root)
    ):
        return False
    if block.history_root != compute_history_root(block.previous_history_root, candidate.event_ids):
        return False
    from fern.bft.canonical import canonical_json

    if len(canonical_json(block.to_dict())) > MAX_BLOCK_BYTES:
        return False
    return True


@dataclass(frozen=True)
class Proposal:
    block: Block
    round: int
    valid_round: int | None
    valid_round_votes: tuple[Vote, ...]
    proposer: str
    sig: str = ""

    @property
    def group(self) -> str:
        return self.block.group

    @property
    def chain_id(self) -> str:
        return self.block.chain_id

    @property
    def epoch(self) -> int:
        return self.block.epoch

    @property
    def height(self) -> int:
        return self.block.height

    def signing_payload(self) -> list[object]:
        return [
            "proposal",
            self.group,
            self.chain_id,
            self.epoch,
            self.height,
            self.round,
            self.block.id,
            self.valid_round,
            [vote.to_dict() for vote in self.valid_round_votes],
            self.proposer,
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "proposal",
            "group": self.group,
            "chain_id": self.chain_id,
            "epoch": self.epoch,
            "height": self.height,
            "round": self.round,
            "block": self.block.to_dict(),
            "valid_round": self.valid_round,
            "valid_round_votes": [vote.to_dict() for vote in self.valid_round_votes],
            "proposer": self.proposer,
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Proposal:
        raw_block = value.get("block")
        raw_votes = value.get("valid_round_votes", [])
        raw_valid_round = value.get("valid_round")
        if not isinstance(raw_block, dict) or not isinstance(raw_votes, list):
            raise ValueError("invalid proposal")
        votes = tuple(Vote.from_dict(item) for item in raw_votes if isinstance(item, dict))
        if len(votes) != len(raw_votes):
            raise ValueError("invalid valid-round vote")
        return cls(
            block=Block.from_dict(raw_block),
            round=strict_int(value.get("round"), "proposal round"),
            valid_round=(
                strict_int(raw_valid_round, "proposal valid_round")
                if raw_valid_round is not None
                else None
            ),
            valid_round_votes=votes,
            proposer=str(value.get("proposer", "")),
            sig=str(value.get("sig", "")),
        )


def sign_proposal(proposal: Proposal, keypair: Keypair) -> Proposal:
    if proposal.proposer != keypair.pubkey_hex:
        raise ValueError("proposal proposer does not match signing key")
    return replace(proposal, sig=sign_payload(keypair, proposal.signing_payload()))


def _verify_vote_quorum(
    votes: tuple[Vote, ...],
    *,
    validator_set: ValidatorSet,
    phase: str,
    height: int,
    round: int,
    block_id: str | None,
) -> bool:
    if len(votes) < validator_set.quorum:
        return False
    validators: set[str] = set()
    for vote in votes:
        if not (
            verify_vote(vote)
            and vote.validator in validator_set.pubkeys
            and vote.validator not in validators
            and vote.epoch == validator_set.epoch
            and vote.height == height
            and vote.round == round
            and vote.phase == phase
            and vote.block_id == block_id
        ):
            return False
        validators.add(vote.validator)
    return True


def verify_proposal(proposal: Proposal, validator_set: ValidatorSet) -> bool:
    if not (
        proposal.round >= 0
        and proposal.proposer == validator_set.proposer(proposal.height, proposal.round).pubkey
        and is_valid_sig_hex(proposal.sig)
        and verify_payload_signature(proposal.proposer, proposal.signing_payload(), proposal.sig)
        and verify_block_evidence(proposal.block, validator_set)
    ):
        return False
    if proposal.valid_round is None:
        return not proposal.valid_round_votes
    if proposal.valid_round < 0 or proposal.valid_round >= proposal.round:
        return False
    return _verify_vote_quorum(
        proposal.valid_round_votes,
        validator_set=validator_set,
        phase=PHASE_PREVOTE,
        height=proposal.height,
        round=proposal.valid_round,
        block_id=proposal.block.id,
    )


@dataclass(frozen=True)
class Commit:
    block: Block
    round: int
    precommits: tuple[Vote, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "commit",
            "group": self.block.group,
            "chain_id": self.block.chain_id,
            "epoch": self.block.epoch,
            "height": self.block.height,
            "round": self.round,
            "block": self.block.to_dict(),
            "precommits": [vote.to_dict() for vote in self.precommits],
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Commit:
        raw_block = value.get("block")
        raw_votes = value.get("precommits", [])
        if not isinstance(raw_block, dict) or not isinstance(raw_votes, list):
            raise ValueError("invalid commit")
        votes = tuple(Vote.from_dict(item) for item in raw_votes if isinstance(item, dict))
        if len(votes) != len(raw_votes):
            raise ValueError("invalid commit vote")
        return cls(
            block=Block.from_dict(raw_block),
            round=strict_int(value.get("round"), "commit round"),
            precommits=votes,
        )


def verify_commit_evidence(commit: Commit, validator_set: ValidatorSet) -> bool:
    return verify_block_evidence(commit.block, validator_set) and _verify_vote_quorum(
        commit.precommits,
        validator_set=validator_set,
        phase=PHASE_PRECOMMIT,
        height=commit.block.height,
        round=commit.round,
        block_id=commit.block.id,
    )


__all__ = [
    "Block",
    "Candidate",
    "Commit",
    "Proposal",
    "build_block",
    "compute_history_root",
    "observation_medians",
    "sign_candidate",
    "sign_proposal",
    "verify_block_evidence",
    "verify_candidate",
    "verify_commit_evidence",
    "verify_proposal",
]
