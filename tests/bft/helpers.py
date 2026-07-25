from __future__ import annotations

import secrets

from fern.bft.application import ChainHead, execute_events, genesis_chain_head
from fern.bft.blocks import (
    Block,
    Candidate,
    Proposal,
    build_block,
    observation_medians,
    sign_candidate,
    sign_proposal,
)
from fern.bft.certificates import (
    TimestampObservation,
    Vote,
    sign_timestamp_observation,
    sign_vote,
)
from fern.bft.constants import PHASE_PRECOMMIT, PHASE_PREVOTE
from fern.bft.validators import Validator, ValidatorSet, make_validator_set
from fern.crypto.hashes import random_channel_id
from fern.crypto.keys import Keypair
from fern.events.build import build_event
from fern.events.event import Event


def validator_fixture(
    faults: int = 1, *, validator_count: int | None = None
) -> tuple[tuple[Keypair, ...], ValidatorSet]:
    count = validator_count if validator_count is not None else 3 * faults + 1
    keys = tuple(Keypair.generate() for _ in range(count))
    validators = [
        Validator(pubkey=key.pubkey_hex, url=f"ws://127.0.0.1:{9100 + index}")
        for index, key in enumerate(keys)
    ]
    return keys, make_validator_set(validators, epoch=0, fault_tolerance=faults)


def genesis_fixture(
    validator_set: ValidatorSet,
) -> tuple[Keypair, Keypair, Event, ChainHead, str]:
    group_key = Keypair.generate()
    founder = Keypair.generate()
    channel_id = random_channel_id()
    genesis = build_event(
        type="genesis",
        group=group_key.pubkey_hex,
        author_keypair=founder,
        seq=0,
        group_keypair=group_key,
        content={
            "chain_id": secrets.token_hex(32),
            "name": "BFT test group",
            "description": "",
            "public": True,
            "founder": founder.pubkey_hex,
            "admins": [founder.pubkey_hex],
            "validators": [validator.to_dict() for validator in validator_set.validators],
            "fault_tolerance": validator_set.fault_tolerance,
            "app": "chat",
            "chat.channels": [
                {"id": channel_id, "name": "general", "description": "", "position": 0}
            ],
            "chat.default_channel": channel_id,
            "chat.system_channel": channel_id,
        },
    )
    return group_key, founder, genesis, genesis_chain_head(genesis), channel_id


def message_fixture(
    founder: Keypair, head: ChainHead, channel_id: str, text: str = "hello"
) -> Event:
    return build_event(
        type="chat.message",
        group=head.state.group,
        author_keypair=founder,
        seq=head.state.sequences.get(founder.pubkey_hex, 0) + 1,
        content={"text": text, "channel": channel_id, "reply_to": None},
        ts=1_711_234_567,
    )


def proposal_fixture(
    *,
    head: ChainHead,
    event: Event | None,
    governance: Event | None = None,
    validator_keys: tuple[Keypair, ...],
    round: int = 0,
) -> tuple[Proposal, Block]:
    validator_set = head.state.validator_set
    proposer_key = next(
        key
        for key in validator_keys
        if key.pubkey_hex == validator_set.proposer(head.height + 1, round).pubkey
    )
    candidate = sign_candidate(
        Candidate(
            group=head.state.group,
            chain_id=head.state.chain_id,
            epoch=validator_set.epoch,
            height=head.height + 1,
            round=round,
            previous_block_hash=head.block_hash,
            previous_state_root=head.state.root,
            events=(event,) if event is not None else (),
            governance=governance,
            proposer=proposer_key.pubkey_hex,
        ),
        proposer_key,
    )
    observations = tuple(
        sign_timestamp_observation(
            TimestampObservation(
                group=head.state.group,
                chain_id=head.state.chain_id,
                epoch=validator_set.epoch,
                height=head.height + 1,
                round=round,
                candidate_id=candidate.id,
                validator=key.pubkey_hex,
                observed_ms=tuple(1_711_234_567_000 + index for _event in candidate.all_events),
            ),
            key,
        )
        for index, key in enumerate(validator_keys[: validator_set.quorum])
    )
    ordered = tuple(sorted(observations, key=lambda item: item.validator))
    times = observation_medians(ordered, len(candidate.all_events))
    next_state = execute_events(
        head.state,
        candidate.events,
        times,
        governance=governance,
        checkpoint_height=head.height,
        checkpoint_block_hash=head.block_hash,
        history_root=head.history_root,
        logical_bytes=head.logical_bytes,
    )
    block = build_block(
        candidate=candidate,
        observations=observations,
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
            proposer=proposer_key.pubkey_hex,
        ),
        proposer_key,
    )
    return proposal, block


def vote_quorum(
    *,
    block: Block,
    keys: tuple[Keypair, ...],
    phase: str = PHASE_PRECOMMIT,
    round: int = 0,
) -> tuple[Vote, ...]:
    validator_set_size = len(keys)
    if validator_set_size < 4:
        quorum = validator_set_size
    else:
        faults = (validator_set_size - 1) // 3
        quorum = 2 * faults + 1
    return tuple(
        sign_vote(
            Vote(
                group=block.group,
                chain_id=block.chain_id,
                epoch=block.epoch,
                height=block.height,
                round=round,
                phase=phase,
                block_id=block.id,
                validator=key.pubkey_hex,
            ),
            key,
        )
        for key in keys[:quorum]
    )


__all__ = [
    "PHASE_PRECOMMIT",
    "PHASE_PREVOTE",
    "genesis_fixture",
    "message_fixture",
    "proposal_fixture",
    "validator_fixture",
    "vote_quorum",
]
