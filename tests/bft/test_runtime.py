from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from fern.bft.blocks import Candidate, sign_candidate
from fern.bft.node import ConsensusTiming, ValidatorNode
from fern.bft.store import BFTStore
from fern.bft.validators import ValidatorSet
from fern.events.build import build_event
from tests.bft.helpers import (
    genesis_fixture,
    message_fixture,
    proposal_fixture,
    validator_fixture,
)


def _message_group(message: dict[str, object]) -> str:
    for key in ("event", "candidate", "observation", "proposal", "vote", "commit"):
        value = message.get(key)
        if isinstance(value, dict):
            group = value.get("group")
            if isinstance(group, str):
                return group
            block = value.get("block")
            if isinstance(block, dict) and isinstance(block.get("group"), str):
                return str(block["group"])
    raise ValueError("message has no group")


@pytest.mark.parametrize(
    ("validator_count", "faults"),
    [(2, 0), (3, 0), (4, 1)],
)
@pytest.mark.asyncio
async def test_validator_runtime_finalizes_same_block(
    tmp_path, validator_count: int, faults: int
) -> None:
    keys, validator_set = validator_fixture(faults=faults, validator_count=validator_count)
    _group_key, founder, genesis, initial_head, channel_id = genesis_fixture(validator_set)
    stores = [BFTStore(tmp_path / f"validator-{index}.sqlite") for index in range(validator_count)]
    for store in stores:
        store.bootstrap_genesis(genesis)

    nodes: dict[str, ValidatorNode] = {}

    def broadcaster(sender: str) -> Callable[[dict[str, object], ValidatorSet], Awaitable[None]]:
        async def send(message: dict[str, object], _validators: ValidatorSet) -> None:
            group = _message_group(message)
            await asyncio.gather(
                *(
                    node.handle_peer_message(group, message)
                    for pubkey, node in nodes.items()
                    if pubkey != sender
                )
            )

        return send

    timing = ConsensusTiming(
        block_interval=0.03,
        observation_timeout=0.03,
        observation_timeout_delta=0.0,
        proposal_timeout=0.03,
        proposal_timeout_delta=0.0,
        prevote_timeout=0.03,
        prevote_timeout_delta=0.0,
        precommit_timeout=0.03,
        precommit_timeout_delta=0.0,
        round_backoff=0.01,
    )
    for key, store in zip(keys, stores, strict=True):
        nodes[key.pubkey_hex] = ValidatorNode(
            keypair=key,
            store=store,
            broadcast=broadcaster(key.pubkey_hex),
            timing=timing,
        )

    event = message_fixture(founder, initial_head, channel_id)
    await nodes[keys[0].pubkey_hex].submit_event(event)

    async def all_finalized() -> bool:
        return all(store.get_chain_head(genesis.group).height == 1 for store in stores)

    for _ in range(300):
        if await all_finalized():
            break
        await asyncio.sleep(0.01)
    assert await all_finalized()
    hashes = {store.get_chain_head(genesis.group).block_hash for store in stores}
    assert len(hashes) == 1
    assert all(store.get_event(event.id or "")[1] == "finalized" for store in stores)

    await asyncio.gather(*(node.stop() for node in nodes.values()))
    for store in stores:
        store.close()


@pytest.mark.asyncio
async def test_delayed_phase_traffic_finalizes_first_round_then_returns_idle(tmp_path) -> None:
    keys, validator_set = validator_fixture(faults=0, validator_count=3)
    _group_key, founder, genesis, initial_head, channel_id = genesis_fixture(validator_set)
    stores = [BFTStore(tmp_path / f"delayed-{index}.sqlite") for index in range(3)]
    for store in stores:
        store.bootstrap_genesis(genesis)

    nodes: dict[str, ValidatorNode] = {}
    deliveries: list[asyncio.Task[None]] = []
    message_delays = {
        "peer_event": 0.001,
        "candidate": 0.004,
        "timestamp_observation": 0.008,
        # Observations deliberately arrive before the proposal. Arbitrary
        # traffic must not make validators leave the proposal phase early.
        "proposal": 0.012,
        "vote": 0.003,
        "commit": 0.002,
    }

    def broadcaster(sender: str) -> Callable[[dict[str, object], ValidatorSet], Awaitable[None]]:
        async def send(message: dict[str, object], _validators: ValidatorSet) -> None:
            group = _message_group(message)
            delay = message_delays.get(str(message.get("type")), 0.001)
            for recipient_index, (pubkey, node) in enumerate(nodes.items()):
                if pubkey == sender:
                    continue

                async def deliver(
                    target: ValidatorNode = node,
                    stagger: float = recipient_index * 0.001,
                ) -> None:
                    await asyncio.sleep(delay + stagger)
                    await target.handle_peer_message(group, message)

                deliveries.append(asyncio.create_task(deliver()))

        return send

    timing = ConsensusTiming(
        block_interval=0.01,
        observation_timeout=0.15,
        observation_timeout_delta=0.0,
        proposal_timeout=0.15,
        proposal_timeout_delta=0.0,
        prevote_timeout=0.15,
        prevote_timeout_delta=0.0,
        precommit_timeout=0.15,
        precommit_timeout_delta=0.0,
        round_backoff=0.005,
    )
    for key, store in zip(keys, stores, strict=True):
        nodes[key.pubkey_hex] = ValidatorNode(
            keypair=key,
            store=store,
            broadcast=broadcaster(key.pubkey_hex),
            timing=timing,
        )

    event = message_fixture(founder, initial_head, channel_id, "delayed network")
    await nodes[keys[0].pubkey_hex].submit_event(event)

    for _ in range(300):
        if all(store.get_chain_head(genesis.group).height == 1 for store in stores):
            break
        await asyncio.sleep(0.01)

    assert all(store.get_chain_head(genesis.group).height == 1 for store in stores)
    assert {store.commits(genesis.group)[0].round for store in stores} == {0}

    # Once the only pending event commits, no empty height-2 consensus should
    # start even while delayed copies of height-1 messages finish arriving.
    await asyncio.sleep(0.1)
    assert all(
        store.load_safety_state(
            genesis.group,
            initial_head.state.chain_id,
            validator_set.epoch,
            2,
        )
        is None
        for store in stores
    )

    await asyncio.gather(*(node.stop() for node in nodes.values()))
    if deliveries:
        await asyncio.gather(*deliveries)
    for store in stores:
        store.close()


@pytest.mark.asyncio
async def test_small_group_recovers_promptly_after_two_validators_restart(tmp_path) -> None:
    """A unanimous group must resynchronize after losing and restoring quorum.

    The survivor advances alone while its peers are offline.  On restart the
    peers begin at their durable round, see the survivor's authenticated
    future-round vote, and catch up instead of replaying every missed round.
    """

    keys, validator_set = validator_fixture(faults=0, validator_count=3)
    _group_key, founder, genesis, initial_head, channel_id = genesis_fixture(validator_set)
    stores = [BFTStore(tmp_path / f"restart-{index}.sqlite") for index in range(3)]
    for store in stores:
        store.bootstrap_genesis(genesis)

    online: dict[str, ValidatorNode] = {}

    def broadcaster(sender: str) -> Callable[[dict[str, object], ValidatorSet], Awaitable[None]]:
        async def send(message: dict[str, object], _validators: ValidatorSet) -> None:
            group = _message_group(message)
            await asyncio.gather(
                *(
                    node.handle_peer_message(group, message)
                    for pubkey, node in tuple(online.items())
                    if pubkey != sender
                )
            )

        return send

    timing = ConsensusTiming(
        block_interval=0.01,
        observation_timeout=0.02,
        observation_timeout_delta=0.0,
        proposal_timeout=0.02,
        proposal_timeout_delta=0.0,
        prevote_timeout=0.02,
        prevote_timeout_delta=0.0,
        precommit_timeout=0.02,
        precommit_timeout_delta=0.0,
        round_backoff=0.005,
    )

    # Only the first validator remains online when the event arrives.
    survivor = ValidatorNode(
        keypair=keys[0],
        store=stores[0],
        broadcast=broadcaster(keys[0].pubkey_hex),
        timing=timing,
    )
    online[keys[0].pubkey_hex] = survivor
    event = message_fixture(founder, initial_head, channel_id, "submitted without quorum")
    await survivor.submit_event(event)

    survivor_engine = survivor.engines[genesis.group]
    for _ in range(300):
        core = survivor_engine._core
        if core is not None and core.state.round >= 4:
            break
        await asyncio.sleep(0.005)
    assert survivor_engine._core is not None
    assert survivor_engine._core.state.round >= 4
    recovery_start_round = survivor_engine._core.state.round
    assert stores[0].get_chain_head(genesis.group).height == 0

    # Restart the other validators with their unchanged round-0 stores.
    for key, store in zip(keys[1:], stores[1:], strict=True):
        online[key.pubkey_hex] = ValidatorNode(
            keypair=key,
            store=store,
            broadcast=broadcaster(key.pubkey_hex),
            timing=timing,
        )

    for _ in range(200):
        if all(store.get_chain_head(genesis.group).height == 1 for store in stores):
            break
        await asyncio.sleep(0.005)

    assert all(store.get_chain_head(genesis.group).height == 1 for store in stores)
    rounds = {store.commits(genesis.group)[0].round for store in stores}
    assert len(rounds) == 1
    assert next(iter(rounds)) <= recovery_start_round + 6

    await asyncio.gather(*(node.stop() for node in online.values()))
    for store in stores:
        store.close()


@pytest.mark.asyncio
async def test_validator_accepts_consecutive_pending_author_sequences(tmp_path) -> None:
    keys, validator_set = validator_fixture(faults=0)
    _group, founder, genesis, head, channel_id = genesis_fixture(validator_set)
    store = BFTStore(tmp_path / "validator.sqlite")
    store.bootstrap_genesis(genesis)
    node = ValidatorNode(
        keypair=keys[0],
        store=store,
        timing=ConsensusTiming(
            block_interval=0.05,
            observation_timeout=0.02,
            observation_timeout_delta=0.0,
            proposal_timeout=0.02,
            proposal_timeout_delta=0.0,
            prevote_timeout=0.02,
            prevote_timeout_delta=0.0,
            precommit_timeout=0.02,
            precommit_timeout_delta=0.0,
            round_backoff=0.01,
        ),
    )
    first = message_fixture(founder, head, channel_id, "first")
    second = build_event(
        type="chat.message",
        group=genesis.group,
        author_keypair=founder,
        seq=2,
        content={"text": "second", "channel": channel_id, "reply_to": None},
    )
    await node.submit_event(first)
    await node.submit_event(second)
    for _ in range(200):
        if store.get_chain_head(genesis.group).height == 1:
            break
        await asyncio.sleep(0.01)
    finalized = store.get_chain_head(genesis.group)
    assert finalized.state.sequences[founder.pubkey_hex] == 2
    assert [event.id for event, *_rest in store.finalized_events(genesis.group)] == [
        first.id,
        second.id,
    ]
    await node.stop()
    store.close()


@pytest.mark.asyncio
async def test_validator_does_not_observe_invalid_candidate_state(tmp_path) -> None:
    keys, validator_set = validator_fixture()
    _group, founder, genesis, head, channel_id = genesis_fixture(validator_set)
    store = BFTStore(tmp_path / "validator.sqlite")
    store.bootstrap_genesis(genesis)
    local_key = keys[0]
    node = ValidatorNode(keypair=local_key, store=store)
    proposer_key = next(
        key for key in keys if key.pubkey_hex == validator_set.proposer(head.height + 1, 0).pubkey
    )
    skipped_sequence = build_event(
        type="chat.message",
        group=genesis.group,
        author_keypair=founder,
        seq=2,
        content={"text": "invalid sequence", "channel": channel_id, "reply_to": None},
    )
    candidate = sign_candidate(
        Candidate(
            group=genesis.group,
            chain_id=head.state.chain_id,
            epoch=validator_set.epoch,
            height=1,
            round=0,
            previous_block_hash=head.block_hash,
            previous_state_root=head.state.root,
            events=(skipped_sequence,),
            governance=None,
            proposer=proposer_key.pubkey_hex,
        ),
        proposer_key,
    )

    await node.handle_peer_message(
        genesis.group, {"type": "candidate", "candidate": candidate.to_dict()}
    )

    assert store.first_seen_ms(skipped_sequence.id or "") is None
    assert not store.observations(genesis.group, validator_set.epoch, 1, 0, candidate.id)
    await node.stop()
    store.close()


@pytest.mark.asyncio
async def test_concurrent_conflicting_ingress_gets_only_one_receipt(tmp_path) -> None:
    keys, validator_set = validator_fixture(faults=0)
    _group, founder, genesis, head, channel_id = genesis_fixture(validator_set)
    store = BFTStore(tmp_path / "validator.sqlite")
    store.bootstrap_genesis(genesis)
    node = ValidatorNode(
        keypair=keys[0],
        store=store,
        timing=ConsensusTiming(
            block_interval=0.05,
            observation_timeout_delta=0.0,
            proposal_timeout_delta=0.0,
            prevote_timeout_delta=0.0,
            precommit_timeout_delta=0.0,
        ),
    )
    first = message_fixture(founder, head, channel_id, "first value")
    conflict = message_fixture(founder, head, channel_id, "conflicting value")

    results = await asyncio.gather(
        node.submit_event(first), node.submit_event(conflict), return_exceptions=True
    )

    assert sum(not isinstance(result, BaseException) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    await node.stop()
    store.close()


@pytest.mark.asyncio
async def test_complete_proposal_is_actionable_without_candidate_gossip(tmp_path) -> None:
    keys, validator_set = validator_fixture(faults=0)
    _group, founder, genesis, head, channel_id = genesis_fixture(validator_set)
    event = message_fixture(founder, head, channel_id)
    proposal, _block = proposal_fixture(head=head, event=event, validator_keys=keys)
    store = BFTStore(tmp_path / "validator.sqlite")
    store.bootstrap_genesis(genesis)
    node = ValidatorNode(
        keypair=keys[0],
        store=store,
        timing=ConsensusTiming(
            block_interval=0.02,
            observation_timeout=0.02,
            observation_timeout_delta=0.0,
            proposal_timeout=0.02,
            proposal_timeout_delta=0.0,
            prevote_timeout=0.02,
            prevote_timeout_delta=0.0,
            precommit_timeout=0.02,
            precommit_timeout_delta=0.0,
            round_backoff=0.01,
        ),
    )

    await node.handle_peer_message(
        genesis.group, {"type": "proposal", "proposal": proposal.to_dict()}
    )
    for _ in range(100):
        if store.get_chain_head(genesis.group).height == 1:
            break
        await asyncio.sleep(0.01)

    assert store.get_chain_head(genesis.group).height == 1
    await node.stop()
    store.close()
