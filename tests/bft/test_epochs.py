from __future__ import annotations

from dataclasses import replace

import pytest

from fern.bft.blocks import Commit
from fern.bft.app import ApplicationError, execute_events
from fern.bft.certificates import SyncReady, sign_sync_ready
from fern.bft.chain import verify_and_apply_commit
from fern.bft.node import ValidatorNode
from fern.bft.store import BFTStore
from fern.bft.validators import Validator, make_validator_set
from fern.crypto.keys import Keypair
from fern.events.build import build_event
from tests.bft.helpers import genesis_fixture, proposal_fixture, validator_fixture, vote_quorum


def _transition_fixture():
    old_keys, old_set = validator_fixture()
    _group, founder, genesis, head, _channel = genesis_fixture(old_set)
    added_key = Keypair.generate()
    retained = list(old_set.validators[1:])
    retained.append(
        Validator(pubkey=added_key.pubkey_hex, url="ws://127.0.0.1:9999", operator="new")
    )
    next_set = make_validator_set(retained, epoch=1, fault_tolerance=1)
    ready = sign_sync_ready(
        SyncReady(
            group=head.state.group,
            chain_id=head.state.chain_id,
            from_epoch=0,
            to_epoch=1,
            validator=added_key.pubkey_hex,
            checkpoint_height=head.height,
            checkpoint_block_hash=head.block_hash,
            history_root=head.history_root,
            byte_count=head.logical_bytes,
        ),
        added_key,
    )
    event = build_event(
        type="validator_update",
        group=head.state.group,
        author_keypair=founder,
        seq=1,
        content={
            "validators": [validator.to_dict() for validator in next_set.validators],
            "fault_tolerance": 1,
            "readiness": [ready.to_dict()],
        },
    )
    proposal, block = proposal_fixture(
        head=head, event=None, governance=event, validator_keys=old_keys
    )
    commit = Commit(
        block=block, round=proposal.round, precommits=vote_quorum(block=block, keys=old_keys)
    )
    return old_keys, added_key, founder, genesis, head, commit


def test_old_epoch_commits_transition_and_new_epoch_starts_next_height() -> None:
    _old_keys, added_key, _founder, _genesis, head, commit = _transition_fixture()
    next_head = verify_and_apply_commit(head, commit)
    assert commit.block.epoch == 0
    assert next_head.state.validator_set.epoch == 1
    assert added_key.pubkey_hex in next_head.state.validator_set.pubkeys
    assert next_head.height == 1


def test_small_set_can_add_validator_with_unanimous_transition() -> None:
    old_keys, old_set = validator_fixture(faults=0, validator_count=1)
    _group, founder, _genesis, head, _channel = genesis_fixture(old_set)
    added_key = Keypair.generate()
    next_set = make_validator_set(
        [
            *old_set.validators,
            Validator(
                pubkey=added_key.pubkey_hex,
                url="ws://127.0.0.1:9999",
                operator="new",
            ),
        ],
        epoch=1,
        fault_tolerance=0,
    )
    ready = sign_sync_ready(
        SyncReady(
            group=head.state.group,
            chain_id=head.state.chain_id,
            from_epoch=0,
            to_epoch=1,
            validator=added_key.pubkey_hex,
            checkpoint_height=head.height,
            checkpoint_block_hash=head.block_hash,
            history_root=head.history_root,
            byte_count=head.logical_bytes,
        ),
        added_key,
    )
    event = build_event(
        type="validator_update",
        group=head.state.group,
        author_keypair=founder,
        seq=1,
        content={
            "validators": [validator.to_dict() for validator in next_set.validators],
            "fault_tolerance": 0,
            "readiness": [ready.to_dict()],
        },
    )
    proposal, block = proposal_fixture(
        head=head,
        event=None,
        governance=event,
        validator_keys=old_keys,
    )
    commit = Commit(
        block=block,
        round=proposal.round,
        precommits=vote_quorum(block=block, keys=old_keys),
    )

    next_head = verify_and_apply_commit(head, commit)

    assert next_head.state.validator_set.is_small_unanimous
    assert len(next_head.state.validator_set.validators) == 2
    assert next_head.state.validator_set.quorum == 2
    assert added_key.pubkey_hex in next_head.state.validator_set.pubkeys


def test_readiness_must_match_exact_logical_byte_count() -> None:
    _old_keys, added_key, founder, _genesis, head, commit = _transition_fixture()
    governance = commit.block.candidate.governance
    assert governance is not None
    ready = SyncReady.from_dict(dict(governance.content["readiness"][0]))
    bad_ready = sign_sync_ready(replace(ready, byte_count=ready.byte_count + 1, sig=""), added_key)
    tampered = build_event(
        type="validator_update",
        group=head.state.group,
        author_keypair=founder,
        seq=1,
        content={**governance.content, "readiness": [bad_ready.to_dict()]},
    )
    with pytest.raises(ApplicationError):
        execute_events(
            head.state,
            (),
            (1_711_234_567_000,),
            governance=tampered,
            checkpoint_height=head.height,
            checkpoint_block_hash=head.block_hash,
            history_root=head.history_root,
            logical_bytes=head.logical_bytes,
        )


@pytest.mark.asyncio
async def test_staged_validator_activates_only_after_transition_commit(tmp_path) -> None:
    _old_keys, added_key, _founder, genesis, _head, commit = _transition_fixture()
    store = BFTStore(tmp_path / "new-validator.sqlite")
    store.bootstrap_genesis(genesis)
    node = ValidatorNode(keypair=added_key, store=store)
    assert genesis.group not in node.engines
    await node.handle_peer_message(genesis.group, {"type": "commit", "commit": commit.to_dict()})
    assert store.get_chain_head(genesis.group).height == 1
    assert genesis.group in node.engines
    await node.stop()
    store.close()
