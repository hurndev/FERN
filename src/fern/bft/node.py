from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from fern.bft.app import (
    ApplicationError,
    ChainHead,
    apply_event,
    boundary_types_for,
    execute_events,
    genesis_chain_head,
    validate_event_for_state,
)
from fern.bft.blocks import (
    Candidate,
    Commit,
    Proposal,
    build_block,
    observation_medians,
    sign_candidate,
    sign_proposal,
    verify_candidate,
    verify_commit_evidence,
    verify_proposal,
)
from fern.bft.certificates import (
    IngressReceipt,
    TimestampObservation,
    Vote,
    sign_ingress_receipt,
    sign_timestamp_observation,
    verify_timestamp_observation,
    verify_vote,
)
from fern.bft.consensus import ConsensusCore, ConsensusSafetyError, DoubleVoteError, VoteSet
from fern.bft.canonical import canonical_json
from fern.bft.constants import (
    CORE_EVENT_TYPES,
    MAX_BLOCK_EVENTS,
    MAX_CANDIDATE_EVENT_BYTES,
    PHASE_PRECOMMIT,
    PHASE_PREVOTE,
)
from fern.bft.store import BFTStore
from fern.bft.validators import ValidatorSet
from fern.crypto.keys import Keypair
from fern.apps import get_app
from fern.events.event import Event
from fern.events.semantic import validate_core_event_semantics
from fern.events.validation import verify_event


logger = logging.getLogger(__name__)

Broadcast = Callable[[dict[str, object], ValidatorSet], Awaitable[None]]
CommitListener = Callable[[Commit], Awaitable[None]]
PendingListener = Callable[[Event, IngressReceipt], Awaitable[None]]
Synchronize = Callable[[], Awaitable[object]]
_AdmissionResult = TypeVar("_AdmissionResult")


def _short(value: str, length: int = 12) -> str:
    return value if len(value) <= length else f"{value[:length]}…"


def _vote_value(block_id: str | None) -> str:
    return "nil" if block_id is None else _short(block_id)


async def _discard_broadcast(_message: dict[str, object], _validators: ValidatorSet) -> None:
    return None


@dataclass(frozen=True)
class ConsensusTiming:
    block_interval: float = 20.0
    observation_timeout: float = 3.0
    observation_timeout_delta: float = 0.5
    proposal_timeout: float = 3.0
    proposal_timeout_delta: float = 0.5
    prevote_timeout: float = 3.0
    prevote_timeout_delta: float = 0.5
    precommit_timeout: float = 3.0
    precommit_timeout_delta: float = 0.5
    round_backoff: float = 1.0

    def observe_timeout(self, round: int) -> float:
        return self.observation_timeout + self.observation_timeout_delta * round

    def propose_timeout(self, round: int) -> float:
        return self.proposal_timeout + self.proposal_timeout_delta * round

    def prevote_timeout_for(self, round: int) -> float:
        return self.prevote_timeout + self.prevote_timeout_delta * round

    def precommit_timeout_for(self, round: int) -> float:
        return self.precommit_timeout + self.precommit_timeout_delta * round


class GroupEngine:
    """One validator's consensus runtime for one FERN group."""

    def __init__(
        self,
        *,
        keypair: Keypair,
        store: BFTStore,
        group: str,
        broadcast: Broadcast = _discard_broadcast,
        timing: ConsensusTiming | None = None,
    ) -> None:
        self.keypair = keypair
        self.store = store
        self.group = group
        self.broadcast = broadcast
        self.timing = timing or ConsensusTiming()
        self._lock = asyncio.Lock()
        self._changed = asyncio.Event()
        self._stop = asyncio.Event()
        self._committed = asyncio.Event()
        self._start_consensus = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._core: ConsensusCore | None = None
        self._proposals: dict[int, Proposal] = {}
        self._candidates: dict[int, Candidate] = {}
        self._sent_phases: set[tuple[int, str]] = set()
        self._target_round = 0
        self._commit_listeners: list[CommitListener] = []
        self._pending_listeners: list[PendingListener] = []

        head = self.store.get_chain_head(group)
        if self.keypair.pubkey_hex not in head.state.validator_set.pubkeys:
            raise ValueError("local node is not a validator for this group")
        # The app is fixed at genesis, so the boundary-event set is constant for
        # the life of this engine.
        self._boundary_types = boundary_types_for(head.state.core.app)

    @property
    def head(self) -> ChainHead:
        return self.store.get_chain_head(self.group)

    def add_commit_listener(self, listener: CommitListener) -> None:
        self._commit_listeners.append(listener)

    def add_pending_listener(self, listener: PendingListener) -> None:
        self._pending_listeners.append(listener)

    def start(self) -> None:
        if self._task is None or self._task.done():
            head = self.head
            logger.debug(
                "engine starting group=%s epoch=%d height=%d",
                _short(self.group),
                head.state.validator_set.epoch,
                head.height,
            )
            self._task = asyncio.create_task(self._run(), name=f"fern-bft-{self.group[:8]}")

    async def stop(self) -> None:
        self._stop.set()
        self._changed.set()
        self._start_consensus.set()
        if self._task is not None:
            await self._task
        logger.debug("engine stopped group=%s", _short(self.group))

    async def submit_event(self, event: Event, *, gossip: bool = True) -> IngressReceipt:
        # Admission of an author sequence is one critical section. Without it,
        # concurrent WebSocket requests could both observe an empty slot and
        # receive receipts for conflicting events at that sequence.
        async with self._lock:
            head = self.head
            if self.keypair.pubkey_hex not in head.state.validator_set.pubkeys:
                raise ValueError("local validator is not active in the current epoch")
            finalized_seq = head.state.sequences.get(event.author, 0)
            expected_seq = self.store.next_pending_sequence(self.group, event.author, finalized_seq)
            existing = self.store.pending_at_sequence(self.group, event.author, event.seq)
            if existing is not None and existing.id != event.id:
                raise ValueError("a different event already occupies this author sequence")
            is_new = existing is None
            if existing is not None:
                first_seen = self.store.first_seen_ms(event.id or "")
                assert first_seen is not None
            else:
                try:
                    validate_event_for_state(
                        head.state,
                        event,
                        int(time.time() * 1000),
                        expected_seq=expected_seq,
                    )
                except ApplicationError as exc:
                    raise ValueError(f"event rejected by current finalized state: {exc}") from exc
                first_seen = self.store.add_pending(event)
            receipt = sign_ingress_receipt(
                IngressReceipt(
                    group=self.group,
                    chain_id=head.state.chain_id,
                    epoch=head.state.validator_set.epoch,
                    event_id=event.id or "",
                    validator=self.keypair.pubkey_hex,
                    first_seen_ms=first_seen,
                ),
                self.keypair,
            )
            pending_count = len(self.store.pending_events(self.group))
        if is_new:
            logger.debug(
                "event accepted group=%s event=%s type=%s author=%s seq=%d source=%s pending=%d",
                _short(self.group),
                _short(event.id or ""),
                event.type,
                _short(event.author),
                event.seq,
                "client" if gossip else "peer",
                pending_count,
            )
        else:
            logger.debug(
                "event already pending group=%s event=%s author=%s seq=%d source=%s",
                _short(self.group),
                _short(event.id or ""),
                _short(event.author),
                event.seq,
                "client" if gossip else "peer",
            )
        self._changed.set()
        if event.type in self._boundary_types:
            # A boundary event cannot affect application state until it commits,
            # so don't leave it waiting behind the ordinary batching window.
            self._start_consensus.set()
        if gossip:
            self._send({"type": "peer_event", "event": event.to_dict()})
        for listener in self._pending_listeners:
            asyncio.create_task(self._notify_pending(listener, event, receipt))
        return receipt

    async def _notify_pending(
        self, listener: PendingListener, event: Event, receipt: IngressReceipt
    ) -> None:
        await listener(event, receipt)

    async def handle_message(self, message: dict[str, object]) -> None:
        message_type = message.get("type")
        try:
            if message_type == "peer_event":
                raw = message.get("event")
                if isinstance(raw, dict):
                    await self.submit_event(Event.from_dict(raw), gossip=False)
            elif message_type == "candidate":
                raw = message.get("candidate")
                if isinstance(raw, dict):
                    await self._handle_candidate(Candidate.from_dict(raw))
            elif message_type == "timestamp_observation":
                raw = message.get("observation")
                if isinstance(raw, dict):
                    await self._handle_observation(TimestampObservation.from_dict(raw))
            elif message_type == "proposal":
                raw = message.get("proposal")
                if isinstance(raw, dict):
                    await self._handle_proposal(Proposal.from_dict(raw))
            elif message_type == "vote":
                raw = message.get("vote")
                if isinstance(raw, dict):
                    await self._handle_vote(Vote.from_dict(raw))
            elif message_type == "commit":
                raw = message.get("commit")
                if isinstance(raw, dict):
                    await self._handle_commit(Commit.from_dict(raw), rebroadcast=False)
        except (ValueError, TypeError, ConsensusSafetyError) as exc:
            logger.warning(
                "peer message rejected group=%s type=%s reason=%s",
                _short(self.group),
                message_type,
                exc,
            )

    def _send(self, message: dict[str, object]) -> None:
        validators = self.head.state.validator_set
        asyncio.create_task(self._broadcast(message, validators))

    async def _broadcast(self, message: dict[str, object], validators: ValidatorSet) -> None:
        await self.broadcast(message, validators)

    def _new_core(self) -> ConsensusCore:
        head = self.head
        return ConsensusCore(
            keypair=self.keypair,
            validator_set=head.state.validator_set,
            group=self.group,
            chain_id=head.state.chain_id,
            height=head.height + 1,
            journal=self.store,
            proposal_validator=self._proposal_matches_head,
        )

    async def _run(self) -> None:
        while not self._stop.is_set():
            if self.keypair.pubkey_hex not in self.head.state.validator_set.pubkeys:
                self._changed.clear()
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                continue
            if (
                not self.store.pending_events(self.group, 1)
                and not self._candidates
                and not self._proposals
            ):
                self._changed.clear()
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                continue
            try:
                self._start_consensus.clear()
                urgent_governance = any(
                    event.type in self._boundary_types
                    for event in self.store.pending_events(self.group)
                )
                if not urgent_governance and not self._candidates and not self._proposals:
                    await asyncio.wait_for(
                        self._start_consensus.wait(), timeout=self.timing.block_interval
                    )
            except TimeoutError:
                pass
            self._core = self._new_core()
            self._committed.clear()
            self._sent_phases.clear()
            round = self._core.state.round
            self._target_round = round
            head = self.head
            logger.debug(
                "consensus starting group=%s epoch=%d height=%d pending=%d start_round=%d",
                _short(self.group),
                head.state.validator_set.epoch,
                head.height + 1,
                len(self.store.pending_events(self.group)),
                round,
            )
            while not self._stop.is_set() and not self._committed.is_set():
                await self._run_round(round)
                next_round = max(round + 1, self._target_round)
                if not self._candidates and not self._proposals:
                    # Every validator independently prunes entries that cannot
                    # form a valid candidate at the current finalized head.
                    # Otherwise only the designated proposer would remove a
                    # stale entry and its peers could keep advancing nil rounds.
                    self._select_pending_events()
                if (
                    not self.store.pending_events(self.group, 1)
                    and not self._candidates
                    and not self._proposals
                ):
                    # Candidate construction can discard an event that became
                    # invalid after ingress (most notably stale readiness).
                    # Persist the next safe round, then return to idle rather
                    # than producing nil votes forever with an empty mempool.
                    self._core.enter_round(next_round)
                    logger.debug(
                        "consensus idle after candidate rejection group=%s height=%d next_round=%d",
                        _short(self.group),
                        self.head.height + 1,
                        next_round,
                    )
                    self._core = None
                    break
                if not self._committed.is_set():
                    logger.debug(
                        "consensus advancing group=%s height=%d round=%d next_round=%d",
                        _short(self.group),
                        self.head.height + 1,
                        round,
                        next_round,
                    )
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self.timing.round_backoff)
                    except TimeoutError:
                        pass
                round = next_round

    async def _run_round(self, round: int) -> None:
        assert self._core is not None
        self._core.enter_round(round)
        validator_set = self.head.state.validator_set
        height = self.head.height + 1
        proposer = validator_set.proposer(height, round).pubkey
        logger.debug(
            "consensus stage group=%s epoch=%d height=%d round=%d stage=propose "
            "proposer=%s local_proposer=%s",
            _short(self.group),
            validator_set.epoch,
            height,
            round,
            _short(proposer),
            proposer == self.keypair.pubkey_hex,
        )
        if proposer == self.keypair.pubkey_hex:
            if self._core.state.valid_proposal is not None:
                await self._repropose_valid(round)
            else:
                candidate = self._build_candidate(round)
                if candidate is not None:
                    logger.debug(
                        "candidate built group=%s height=%d round=%d candidate=%s events=%d "
                        "governance=%s",
                        _short(self.group),
                        candidate.height,
                        round,
                        _short(candidate.id),
                        len(candidate.all_events),
                        candidate.governance.type if candidate.governance else "none",
                    )
                    self._candidates[round] = candidate
                    await self._handle_candidate(candidate)
                    self._send({"type": "candidate", "candidate": candidate.to_dict()})

        existing = self._proposals.get(round)
        if existing is not None and (round, PHASE_PREVOTE) not in self._sent_phases:
            await self._broadcast_prevote(existing, round)
        if self._committed.is_set():
            return

        logger.debug(
            "consensus stage group=%s height=%d round=%d stage=observe",
            _short(self.group),
            height,
            round,
        )
        await self._wait_until(
            lambda: round in self._proposals,
            self.timing.observe_timeout(round),
        )
        if self._committed.is_set() or self._target_round > round:
            return
        if proposer == self.keypair.pubkey_hex and round not in self._proposals:
            await self._try_build_proposal(round)
        logger.debug(
            "consensus stage group=%s height=%d round=%d stage=proposal",
            _short(self.group),
            height,
            round,
        )
        await self._wait_until(
            lambda: round in self._proposals,
            self.timing.propose_timeout(round),
        )
        if self._committed.is_set() or self._target_round > round:
            return
        if (round, PHASE_PREVOTE) not in self._sent_phases:
            await self._broadcast_prevote(None, round)
        logger.debug(
            "consensus stage group=%s height=%d round=%d stage=prevote",
            _short(self.group),
            height,
            round,
        )
        await self._wait_until(
            lambda: (round, PHASE_PRECOMMIT) in self._sent_phases,
            self.timing.prevote_timeout_for(round),
        )
        if self._committed.is_set() or self._target_round > round:
            return
        if (round, PHASE_PRECOMMIT) not in self._sent_phases:
            vote = self._core.precommit_nil(round)
            self._sent_phases.add((round, PHASE_PRECOMMIT))
            self.store.save_vote(vote)
            self._send({"type": "vote", "vote": vote.to_dict()})
            logger.debug(
                "local precommit group=%s height=%d round=%d value=nil reason=prevote-timeout",
                _short(self.group),
                height,
                round,
            )
            await self._handle_vote(vote)
        logger.debug(
            "consensus stage group=%s height=%d round=%d stage=precommit",
            _short(self.group),
            height,
            round,
        )
        await self._wait_until(
            lambda: self._has_precommit_quorum(round),
            self.timing.precommit_timeout_for(round),
        )

    async def _wait_until(self, condition: Callable[[], bool], timeout: float) -> None:
        """Wait for a phase condition without treating arbitrary traffic as progress."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not (
            condition()
            or self._committed.is_set()
            or self._stop.is_set()
            or (self._core is not None and self._target_round > self._core.state.round)
        ):
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            self._changed.clear()
            # Recheck after clearing so a condition that became true just
            # before the clear cannot be lost behind an unset Event.
            if condition():
                return
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=remaining)
            except TimeoutError:
                return

    def _has_precommit_quorum(self, round: int) -> bool:
        head = self.head
        votes = self.store.votes(
            self.group,
            head.state.validator_set.epoch,
            head.height + 1,
            round,
            PHASE_PRECOMMIT,
        )
        return self._vote_set(round, PHASE_PRECOMMIT, votes).any_quorum() is not None

    def _select_pending_events(self) -> tuple[tuple[Event, ...], Event | None]:
        head = self.head
        pending = self.store.pending_events(self.group)
        ordinary = [event for event in pending if event.type not in self._boundary_types]
        governance = [event for event in pending if event.type in self._boundary_types]
        selected: list[Event] = []
        selected_bytes = 0
        state = head.state
        for event in ordinary:
            event_bytes = len(canonical_json(event.to_dict()))
            if (
                len(selected) >= MAX_BLOCK_EVENTS - 1
                or selected_bytes + event_bytes > MAX_CANDIDATE_EVENT_BYTES
            ):
                break
            seen = self.store.first_seen_ms(event.id or "") or int(time.time() * 1000)
            try:
                state = apply_event(
                    state,
                    event,
                    seen,
                    checkpoint_height=head.height,
                    checkpoint_block_hash=head.block_hash,
                    history_root=head.history_root,
                    logical_bytes=head.logical_bytes,
                )
            except ApplicationError:
                self.store.remove_pending(event.id or "")
                logger.debug(
                    "pending event dropped during candidate selection group=%s event=%s type=%s",
                    _short(self.group),
                    _short(event.id or ""),
                    event.type,
                )
                continue
            selected.append(event)
            selected_bytes += event_bytes
        selected_governance: Event | None = None
        for event in governance:
            event_bytes = len(canonical_json(event.to_dict()))
            if selected_bytes + event_bytes > MAX_CANDIDATE_EVENT_BYTES:
                continue
            seen = self.store.first_seen_ms(event.id or "") or int(time.time() * 1000)
            try:
                apply_event(
                    state,
                    event,
                    seen,
                    checkpoint_height=head.height,
                    checkpoint_block_hash=head.block_hash,
                    history_root=head.history_root,
                    logical_bytes=head.logical_bytes,
                )
            except ApplicationError:
                self.store.remove_pending(event.id or "")
                logger.debug(
                    "pending governance dropped during candidate selection group=%s event=%s "
                    "type=%s",
                    _short(self.group),
                    _short(event.id or ""),
                    event.type,
                )
                continue
            selected_governance = event
            break
        if not selected and selected_governance is None:
            return (), None
        return tuple(selected), selected_governance

    def _build_candidate(self, round: int) -> Candidate | None:
        head = self.head
        selected, selected_governance = self._select_pending_events()
        if not selected and selected_governance is None:
            return None
        unsigned = Candidate(
            group=self.group,
            chain_id=head.state.chain_id,
            epoch=head.state.validator_set.epoch,
            height=head.height + 1,
            round=round,
            previous_block_hash=head.block_hash,
            previous_state_root=head.state.root,
            events=selected,
            governance=selected_governance,
            proposer=self.keypair.pubkey_hex,
        )
        return sign_candidate(unsigned, self.keypair)

    async def _handle_candidate(self, candidate: Candidate) -> None:
        async with self._lock:
            head = self.head
            validator_set = head.state.validator_set
            if not verify_candidate(candidate, validator_set):
                return
            if not (
                candidate.height == head.height + 1
                and candidate.previous_block_hash == head.block_hash
                and candidate.previous_state_root == head.state.root
            ):
                return
            if any(event.type in self._boundary_types for event in candidate.events):
                return
            if (
                candidate.governance is not None
                and candidate.governance.type not in self._boundary_types
            ):
                return
            app_module = get_app(head.state.core.app)
            observed: list[int] = []
            now = int(time.time() * 1000)
            for event in candidate.all_events:
                try:
                    verify_event(event)
                    if event.type in CORE_EVENT_TYPES:
                        validate_core_event_semantics(event)
                    else:
                        app_module.validate_semantics(event)
                    first_seen = self.store.first_seen_ms(event.id or "") or now
                except (ValueError, TypeError):
                    return
                observed.append(first_seen)
            try:
                execute_events(
                    head.state,
                    candidate.events,
                    tuple(observed),
                    governance=candidate.governance,
                    checkpoint_height=head.height,
                    checkpoint_block_hash=head.block_hash,
                    history_root=head.history_root,
                    logical_bytes=head.logical_bytes,
                )
            except ApplicationError:
                return
            for event, first_seen in zip(candidate.all_events, observed, strict=True):
                self.store.add_pending(event, first_seen)
            observation = sign_timestamp_observation(
                TimestampObservation(
                    group=self.group,
                    chain_id=head.state.chain_id,
                    epoch=validator_set.epoch,
                    height=candidate.height,
                    round=candidate.round,
                    candidate_id=candidate.id,
                    validator=self.keypair.pubkey_hex,
                    observed_ms=tuple(observed),
                ),
                self.keypair,
            )
            try:
                observation = self.store.record_own_observation(observation)
            except DoubleVoteError:
                return
            self.store.save_observation(observation)
            self._candidates[candidate.round] = candidate
            self._start_consensus.set()
            self._changed.set()
        logger.debug(
            "candidate accepted group=%s height=%d round=%d candidate=%s events=%d "
            "observation=sent",
            _short(self.group),
            candidate.height,
            candidate.round,
            _short(candidate.id),
            len(candidate.all_events),
        )
        self._send({"type": "timestamp_observation", "observation": observation.to_dict()})
        await self._try_build_proposal(candidate.round)

    async def _handle_observation(self, observation: TimestampObservation) -> None:
        candidate = self._candidates.get(observation.round)
        if candidate is None:
            return
        validator_set = self.head.state.validator_set
        if not (
            observation.candidate_id == candidate.id
            and observation.validator in validator_set.pubkeys
            and verify_timestamp_observation(observation, event_count=len(candidate.all_events))
        ):
            return
        self.store.save_observation(observation)
        self._changed.set()
        await self._try_build_proposal(observation.round)

    async def _try_build_proposal(self, round: int) -> None:
        if round in self._proposals:
            return
        head = self.head
        validator_set = head.state.validator_set
        if validator_set.proposer(head.height + 1, round).pubkey != self.keypair.pubkey_hex:
            return
        candidate = self._candidates.get(round)
        if candidate is None:
            return
        observations = self.store.observations(
            self.group, validator_set.epoch, candidate.height, round, candidate.id
        )
        valid = tuple(
            observation
            for observation in observations
            if observation.validator in validator_set.pubkeys
            and verify_timestamp_observation(observation, event_count=len(candidate.all_events))
        )
        by_validator = {item.validator: item for item in valid}
        if len(by_validator) < validator_set.quorum:
            return
        selected = tuple(by_validator[key] for key in sorted(by_validator)[: validator_set.quorum])
        times = observation_medians(selected, len(candidate.all_events))
        try:
            next_state = execute_events(
                head.state,
                candidate.events,
                times,
                governance=candidate.governance,
                checkpoint_height=head.height,
                checkpoint_block_hash=head.block_hash,
                history_root=head.history_root,
                logical_bytes=head.logical_bytes,
            )
        except ApplicationError:
            return
        block = build_block(
            candidate=candidate,
            observations=selected,
            validator_set=validator_set,
            previous_history_root=head.history_root,
            state_root=next_state.root,
        )
        proposal = sign_proposal(
            Proposal(
                block=block,
                round=round,
                valid_round=None,
                valid_round_votes=(),
                proposer=self.keypair.pubkey_hex,
            ),
            self.keypair,
        )
        self._proposals[round] = proposal
        logger.debug(
            "proposal built group=%s height=%d round=%d block=%s events=%d observations=%d/%d",
            _short(self.group),
            proposal.height,
            round,
            _short(proposal.block.id),
            len(proposal.block.candidate.all_events),
            len(selected),
            validator_set.quorum,
        )
        self._send({"type": "proposal", "proposal": proposal.to_dict()})
        await self._handle_proposal(proposal)

    async def _repropose_valid(self, round: int) -> None:
        assert self._core is not None
        valid = self._core.state.valid_proposal
        valid_round = self._core.state.valid_round
        if valid is None or valid_round is None:
            return
        votes = self.store.votes(
            self.group,
            self.head.state.validator_set.epoch,
            self.head.height + 1,
            valid_round,
            PHASE_PREVOTE,
        )
        vote_set = self._vote_set(valid_round, PHASE_PREVOTE, votes)
        quorum = vote_set.quorum_for(valid.block.id)
        if quorum is None:
            return
        proposal = sign_proposal(
            Proposal(
                block=valid.block,
                round=round,
                valid_round=valid_round,
                valid_round_votes=quorum,
                proposer=self.keypair.pubkey_hex,
            ),
            self.keypair,
        )
        self._proposals[round] = proposal
        logger.debug(
            "proposal reproposed group=%s height=%d round=%d block=%s valid_round=%d",
            _short(self.group),
            proposal.height,
            round,
            _short(proposal.block.id),
            valid_round,
        )
        self._send({"type": "proposal", "proposal": proposal.to_dict()})
        await self._handle_proposal(proposal)

    def _proposal_matches_head(self, proposal: Proposal) -> bool:
        head = self.head
        block = proposal.block
        if not (
            verify_proposal(proposal, head.state.validator_set)
            and block.height == head.height + 1
            and block.candidate.previous_block_hash == head.block_hash
            and block.candidate.previous_state_root == head.state.root
            and block.previous_history_root == head.history_root
        ):
            return False
        try:
            state = execute_events(
                head.state,
                block.candidate.events,
                block.certified_times_ms,
                governance=block.candidate.governance,
                checkpoint_height=head.height,
                checkpoint_block_hash=head.block_hash,
                history_root=head.history_root,
                logical_bytes=head.logical_bytes,
            )
        except ApplicationError:
            return False
        return state.root == block.state_root

    async def _handle_proposal(self, proposal: Proposal) -> None:
        if not self._proposal_matches_head(proposal):
            return
        was_known = proposal.round in self._proposals
        self._proposals[proposal.round] = proposal
        self._changed.set()
        if not was_known:
            logger.debug(
                "proposal accepted group=%s height=%d round=%d block=%s events=%d valid_round=%s",
                _short(self.group),
                proposal.height,
                proposal.round,
                _short(proposal.block.id),
                len(proposal.block.candidate.all_events),
                proposal.valid_round if proposal.valid_round is not None else "none",
            )
        if self._core is None or proposal.round != self._core.state.round:
            return
        if (proposal.round, PHASE_PREVOTE) not in self._sent_phases:
            await self._broadcast_prevote(proposal, proposal.round)

    async def _broadcast_prevote(self, proposal: Proposal | None, round: int) -> None:
        assert self._core is not None
        try:
            vote = self._core.prevote(proposal, round)
        except DoubleVoteError:
            return
        self._sent_phases.add((round, PHASE_PREVOTE))
        self.store.save_vote(vote)
        logger.debug(
            "local prevote group=%s height=%d round=%d value=%s",
            _short(self.group),
            vote.height,
            round,
            _vote_value(vote.block_id),
        )
        self._send({"type": "vote", "vote": vote.to_dict()})
        await self._handle_vote(vote)

    def _vote_set(self, round: int, phase: str, votes: tuple[Vote, ...]) -> VoteSet:
        head = self.head
        vote_set = VoteSet(
            validator_set=head.state.validator_set,
            group=self.group,
            chain_id=head.state.chain_id,
            height=head.height + 1,
            round=round,
            phase=phase,
        )
        for vote in votes:
            vote_set.add(vote)
        return vote_set

    async def _handle_vote(self, vote: Vote) -> None:
        if self._core is None:
            return
        head = self.head
        if not (
            verify_vote(vote)
            and vote.validator in head.state.validator_set.pubkeys
            and vote.epoch == head.state.validator_set.epoch
            and vote.height == head.height + 1
            and vote.round >= self._core.state.round
        ):
            return
        self.store.save_vote(vote)
        if vote.round > self._core.state.round:
            future_voters = {
                item.validator
                for phase in (PHASE_PREVOTE, PHASE_PRECOMMIT)
                for item in self.store.votes(self.group, vote.epoch, vote.height, vote.round, phase)
                if verify_vote(item) and item.validator in head.state.validator_set.pubkeys
            }
            catchup_threshold = head.state.validator_set.round_catchup_threshold
            if len(future_voters) >= catchup_threshold:
                previous_target = self._target_round
                self._target_round = max(self._target_round, vote.round)
                if self._target_round > previous_target:
                    logger.debug(
                        "future-round catch-up group=%s height=%d current_round=%d "
                        "target_round=%d voters=%d threshold=%d",
                        _short(self.group),
                        vote.height,
                        self._core.state.round,
                        self._target_round,
                        len(future_voters),
                        catchup_threshold,
                    )
                self._changed.set()
            return
        self._changed.set()
        votes = self.store.votes(self.group, vote.epoch, vote.height, vote.round, vote.phase)
        vote_set = self._vote_set(vote.round, vote.phase, votes)
        if vote.phase == PHASE_PREVOTE and (vote.round, PHASE_PRECOMMIT) not in self._sent_phases:
            quorum = vote_set.any_quorum()
            if quorum is None:
                return
            block_id, quorum_votes = quorum
            proposal = self._proposals.get(vote.round)
            if block_id is not None and (proposal is None or proposal.block.id != block_id):
                return
            try:
                precommit = self._core.precommit_from_prevote_quorum(
                    proposal=proposal if block_id is not None else None,
                    prevotes=quorum_votes,
                    round=vote.round,
                )
            except ConsensusSafetyError:
                return
            logger.debug(
                "prevote quorum group=%s height=%d round=%d value=%s votes=%d/%d",
                _short(self.group),
                vote.height,
                vote.round,
                _vote_value(block_id),
                len(quorum_votes),
                head.state.validator_set.quorum,
            )
            self._sent_phases.add((vote.round, PHASE_PRECOMMIT))
            self.store.save_vote(precommit)
            logger.debug(
                "local precommit group=%s height=%d round=%d value=%s",
                _short(self.group),
                precommit.height,
                vote.round,
                _vote_value(precommit.block_id),
            )
            self._send({"type": "vote", "vote": precommit.to_dict()})
            await self._handle_vote(precommit)
        elif vote.phase == PHASE_PRECOMMIT:
            proposal = self._proposals.get(vote.round)
            if proposal is None:
                return
            commit_quorum = vote_set.quorum_for(proposal.block.id)
            if commit_quorum is not None:
                logger.debug(
                    "precommit quorum group=%s height=%d round=%d block=%s votes=%d/%d",
                    _short(self.group),
                    vote.height,
                    vote.round,
                    _short(proposal.block.id),
                    len(commit_quorum),
                    head.state.validator_set.quorum,
                )
                await self._handle_commit(
                    Commit(
                        block=proposal.block,
                        round=vote.round,
                        precommits=commit_quorum,
                    ),
                    rebroadcast=True,
                )

    async def _handle_commit(self, commit: Commit, *, rebroadcast: bool) -> None:
        head = self.head
        previous_validators = head.state.validator_set
        if commit.block.height <= head.height:
            return
        if commit.block.height != head.height + 1:
            return
        if not verify_commit_evidence(commit, head.state.validator_set):
            return
        try:
            next_head = self.store.save_commit(self.group, commit)
        except ValueError:
            return
        self._committed.set()
        self._proposals.clear()
        self._candidates.clear()
        self._changed.set()
        logger.info(
            "block finalized group=%s epoch=%d height=%d round=%d block=%s events=%d proposer=%s",
            _short(self.group),
            commit.block.epoch,
            commit.block.height,
            commit.round,
            _short(commit.block.id),
            len(commit.block.candidate.all_events),
            _short(commit.block.candidate.proposer),
        )
        if next_head.state.validator_set != previous_validators:
            logger.info(
                "validator epoch activated group=%s old_epoch=%d new_epoch=%d validators=%d "
                "quorum=%d",
                _short(self.group),
                previous_validators.epoch,
                next_head.state.validator_set.epoch,
                len(next_head.state.validator_set.validators),
                next_head.state.validator_set.quorum,
            )
        if rebroadcast:
            message: dict[str, object] = {"type": "commit", "commit": commit.to_dict()}
            asyncio.create_task(self._broadcast(message, previous_validators))
            current_validators = self.head.state.validator_set
            if current_validators != previous_validators:
                asyncio.create_task(self._broadcast(message, current_validators))
        for listener in self._commit_listeners:
            asyncio.create_task(self._notify_commit(listener, commit))

    async def _notify_commit(self, listener: CommitListener, commit: Commit) -> None:
        await listener(commit)


class ValidatorNode:
    """Hosts multiple independent group engines under one validator key."""

    def __init__(
        self,
        *,
        keypair: Keypair,
        store: BFTStore,
        broadcast: Broadcast = _discard_broadcast,
        timing: ConsensusTiming | None = None,
        autostart: bool = True,
    ) -> None:
        self.keypair = keypair
        self.store = store
        self.broadcast = broadcast
        self.timing = timing
        self._autostart = autostart
        self._group_locks: dict[str, asyncio.Lock] = {}
        self.engines: dict[str, GroupEngine] = {}
        for group in store.hosted_groups():
            self._start_group(group)

    def _start_group(self, group: str) -> GroupEngine | None:
        if group in self.engines:
            return self.engines[group]
        head = self.store.get_chain_head(group)
        if self.keypair.pubkey_hex not in head.state.validator_set.pubkeys:
            return None
        engine = GroupEngine(
            keypair=self.keypair,
            store=self.store,
            group=group,
            broadcast=self.broadcast,
            timing=self.timing,
        )
        self.engines[group] = engine
        if head.state.validator_set.is_small_unanimous:
            logger.warning(
                "small unanimous validator set group=%s validators=%d quorum=%d; "
                "any unavailable validator halts consensus; use 4 validators with f=1 "
                "for standard BFT operation",
                _short(group),
                len(head.state.validator_set.validators),
                head.state.validator_set.quorum,
            )
        logger.info(
            "group engine active group=%s epoch=%d height=%d validators=%d quorum=%d",
            _short(group),
            head.state.validator_set.epoch,
            head.height,
            len(head.state.validator_set.validators),
            head.state.validator_set.quorum,
        )
        if self._autostart:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                engine.start()
        return engine

    def start(self) -> None:
        """Start every active group after any required startup synchronization."""

        self._autostart = True
        for engine in self.engines.values():
            engine.start()

    def _group_lock(self, group: str) -> asyncio.Lock:
        return self._group_locks.setdefault(group, asyncio.Lock())

    def bootstrap(self, genesis: Event) -> ChainHead:
        head = genesis_chain_head(genesis)
        if self.keypair.pubkey_hex not in head.state.validator_set.pubkeys:
            raise ValueError("this validator is not named by genesis")
        head = self.store.bootstrap_genesis(genesis)
        logger.info(
            "genesis accepted group=%s chain=%s validators=%d quorum=%d",
            _short(genesis.group),
            _short(head.state.chain_id),
            len(head.state.validator_set.validators),
            head.state.validator_set.quorum,
        )
        self._start_group(genesis.group)
        return head

    async def submit_event(self, event: Event) -> IngressReceipt:
        async with self._group_lock(event.group):
            engine = self.engines.get(event.group)
            if engine is None:
                raise ValueError("group is not hosted by this validator")
            return await engine.submit_event(event)

    async def handle_peer_message(self, group: str, message: dict[str, object]) -> None:
        async with self._group_lock(group):
            await self._handle_peer_message(group, message)

    async def _handle_peer_message(self, group: str, message: dict[str, object]) -> None:
        engine = self.engines.get(group)
        if engine is not None:
            await engine.handle_message(message)
            return
        # A fully synchronized prospective validator is deliberately inactive
        # until the old epoch commits its transition. It may accept that one
        # independently verified commit, then start only if the resulting set
        # activates its key.
        if group not in self.store.hosted_groups() or message.get("type") != "commit":
            return
        raw = message.get("commit")
        if not isinstance(raw, dict):
            return
        commit = Commit.from_dict(raw)
        head = self.store.get_chain_head(group)
        if commit.block.height != head.height + 1:
            return
        try:
            next_head = self.store.save_commit(group, commit)
        except ValueError:
            return
        logger.info(
            "transition commit accepted group=%s epoch=%d height=%d block=%s",
            _short(group),
            next_head.state.validator_set.epoch,
            next_head.height,
            _short(next_head.block_hash),
        )
        self._start_group(group)

    async def synchronize_group(self, group: str, synchronize: Synchronize) -> object:
        """Pause one group while a verified contiguous history sync updates its store."""

        async with self._group_lock(group):
            if group not in self.store.hosted_groups():
                raise ValueError("group is not hosted by this validator")
            engine = self.engines.pop(group, None)
            if engine is not None:
                logger.info(
                    "group engine pausing for catch-up group=%s height=%d",
                    _short(group),
                    self.store.get_chain_head(group).height,
                )
                await engine.stop()
            try:
                return await synchronize()
            finally:
                self._start_group(group)

    async def prepare_group_admission(
        self, group: str, prepare: Callable[[], Awaitable[_AdmissionResult]]
    ) -> _AdmissionResult:
        """Serialize prospective-validator history preparation for one group.

        Holding the group lock keeps the periodic catch-up path from racing
        the admission sync while the group first appears in the store.
        """

        async with self._group_lock(group):
            return await prepare()

    async def stop(self) -> None:
        await asyncio.gather(*(engine.stop() for engine in self.engines.values()))


__all__ = ["ConsensusTiming", "GroupEngine", "ValidatorNode"]
