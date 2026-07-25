from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from fern.bft.admission import prepare_validator_history
from fern.bft.client import sync_from_validators
from fern.bft.node import ConsensusTiming
from fern.bft.store import BFTStore
from fern.bft.validators import Validator, make_validator_set
from fern.bft.websocket import BFTWebSocketClient
from fern.crypto.hashes import random_channel_id
from fern.crypto.keys import Keypair
from fern.events.build import build_event
from fern.events.event import Event
from tests.bft.live_cluster import LiveCluster, LiveValidator


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _head(cluster: LiveCluster, index: int = 0):
    store = cluster.validators[index].store
    assert store is not None
    return store.get_chain_head(cluster.group)


def _event(
    cluster: LiveCluster,
    *,
    author: Keypair,
    event_type: str,
    content: dict[str, object],
    index: int = 0,
) -> Event:
    state = _head(cluster, index).state
    return build_event(
        type=event_type,
        group=cluster.group,
        author_keypair=author,
        seq=state.sequences.get(author.pubkey_hex, 0) + 1,
        content=content,
    )


def _assert_accepted(results: tuple[object, ...]) -> None:
    failures = [result for result in results if isinstance(result, BaseException)]
    assert not failures, failures


def _assert_same_finalized_head(cluster: LiveCluster, indices: tuple[int, ...]) -> None:
    heads = [_head(cluster, index) for index in indices]
    assert len({head.height for head in heads}) == 1
    assert len({head.block_hash for head in heads}) == 1
    assert len({head.state.root for head in heads}) == 1


async def _fern(home: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["FERN_HOME"] = str(home)

    def invoke() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "from cli.main import main; main()",
                *arguments,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=15,
        )

    return await asyncio.to_thread(invoke)


async def _wait_remote_height(urls: list[str], group: str, height: int) -> None:
    deadline = asyncio.get_running_loop().time() + 8
    while asyncio.get_running_loop().time() < deadline:
        statuses = await asyncio.gather(
            *(BFTWebSocketClient(url, timeout=0.5).status(group) for url in urls),
            return_exceptions=True,
        )
        if all(
            not isinstance(status, BaseException) and status.height >= height for status in statuses
        ):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"remote group {group} did not reach height {height}")


@pytest.mark.asyncio
async def test_live_cluster_membership_channels_and_bans(tmp_path: Path) -> None:
    cluster = LiveCluster(tmp_path / "workflow")
    member = Keypair.generate()
    secondary_channel = random_channel_id()
    try:
        await cluster.start()

        invite = _event(
            cluster,
            author=cluster.founder,
            event_type="invite",
            content={"invitee": member.pubkey_hex, "role": "member"},
        )
        _assert_accepted(await cluster.submit(invite))
        await cluster.wait_for_height(1)

        join = _event(cluster, author=member, event_type="join", content={})
        _assert_accepted(await cluster.submit(join))
        await cluster.wait_for_height(2)

        channel = _event(
            cluster,
            author=cluster.founder,
            event_type="chat.channel_create",
            content={
                "id": secondary_channel,
                "name": "operations",
                "description": "system testing",
                "position": 1,
            },
        )
        _assert_accepted(await cluster.submit(channel))
        await cluster.wait_for_height(3)

        nickname = _event(
            cluster,
            author=member,
            event_type="chat.nickname_set",
            content={"nickname": "member-one"},
        )
        _assert_accepted(await cluster.submit(nickname))
        await cluster.wait_for_height(4)
        message = _event(
            cluster,
            author=member,
            event_type="chat.message",
            content={"text": "before ban", "channel": secondary_channel, "reply_to": None},
        )
        _assert_accepted(await cluster.submit(message))
        await cluster.wait_for_height(5)

        ban = _event(
            cluster,
            author=cluster.founder,
            event_type="ban",
            content={"target": member.pubkey_hex, "until": None, "reason": "system test"},
        )
        _assert_accepted(await cluster.submit(ban))
        await cluster.wait_for_height(6)
        state = _head(cluster).state
        assert member.pubkey_hex in state.banned
        assert member.pubkey_hex not in state.joined

        rejected = _event(
            cluster,
            author=member,
            event_type="chat.message",
            content={"text": "must fail", "channel": secondary_channel, "reply_to": None},
        )
        results = await cluster.submit(rejected)
        assert all(isinstance(result, ValueError) for result in results)
        assert _head(cluster).height == 6

        unban = _event(
            cluster,
            author=cluster.founder,
            event_type="unban",
            content={"target": member.pubkey_hex},
        )
        _assert_accepted(await cluster.submit(unban))
        await cluster.wait_for_height(7)
        rejoin = _event(cluster, author=member, event_type="join", content={})
        _assert_accepted(await cluster.submit(rejoin))
        await cluster.wait_for_height(8)
        _assert_same_finalized_head(cluster, (0, 1, 2, 3))
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_real_fern_cli_creates_posts_reads_bans_and_verifies(tmp_path: Path) -> None:
    cluster = LiveCluster(tmp_path / "cli-validators")
    client_home = tmp_path / "cli-user"
    try:
        await cluster.start()
        initialized = await _fern(client_home, "init")
        assert initialized.returncode == 0, initialized.stderr

        create_arguments = ["group", "create", "--name", "CLI system group", "--faults", "1"]
        for url in cluster.urls:
            create_arguments.extend(("--validator", url))
        created = await _fern(client_home, *create_arguments)
        assert created.returncode == 0, created.stderr
        assert "created with 4 validator(s)" in created.stdout

        config = json.loads((client_home / "config.json").read_text(encoding="utf-8"))
        group = str(config["group_order"][0])
        await _wait_remote_height(cluster.urls, group, 0)

        posted = await _fern(client_home, "post", "1", "hello from the real CLI")
        assert posted.returncode == 0, posted.stderr
        assert "Message submitted" in posted.stdout
        await _wait_remote_height(cluster.urls, group, 1)

        read = await _fern(client_home, "read", "1")
        assert read.returncode == 0, read.stderr
        assert "hello from the real CLI" in read.stdout

        banned_key = Keypair.generate().pubkey_hex
        banned = await _fern(client_home, "group", "ban", "1", banned_key, "--reason", "test")
        assert banned.returncode == 0, banned.stderr
        assert "Ban submitted" in banned.stdout
        await _wait_remote_height(cluster.urls, group, 2)

        info = await _fern(client_home, "group", "info", "1")
        assert info.returncode == 0, info.stderr
        assert "Height: 2" in info.stdout
        assert "Validators: 4" in info.stdout

        verified = await _fern(client_home, "verify", "1")
        assert verified.returncode == 0, verified.stderr
        assert "verified" in verified.stdout.lower()
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_restarted_validator_catches_up_after_missed_commit(tmp_path: Path) -> None:
    cluster = LiveCluster(tmp_path / "downtime-catchup")
    try:
        await cluster.start()

        # One validator can be unavailable in a standard n=4, f=1 group.
        await cluster.validators[3].stop()
        for height in range(1, 4):
            event = _event(
                cluster,
                author=cluster.founder,
                event_type="chat.message",
                content={
                    "text": f"offline block {height}",
                    "channel": cluster.channel_id,
                    "reply_to": None,
                },
            )
            _assert_accepted(await cluster.submit(event, indices=(0, 1, 2)))
            await cluster.wait_for_height(height, indices=(0, 1, 2))
        _assert_same_finalized_head(cluster, (0, 1, 2))

        await cluster.validators[3].start()
        await cluster.wait_for_height(3, indices=(3,))
        _assert_same_finalized_head(cluster, (0, 1, 2, 3))

        resumed = _event(
            cluster,
            author=cluster.founder,
            event_type="chat.message",
            content={"text": "after catch-up", "channel": cluster.channel_id, "reply_to": None},
        )
        _assert_accepted(await cluster.submit(resumed))
        await cluster.wait_for_height(4)
        _assert_same_finalized_head(cluster, (0, 1, 2, 3))
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_running_validator_catches_up_when_enough_peers_return(tmp_path: Path) -> None:
    cluster = LiveCluster(tmp_path / "delayed-catchup-peers")
    try:
        await cluster.start()
        await cluster.validators[3].stop()
        event = _event(
            cluster,
            author=cluster.founder,
            event_type="chat.message",
            content={"text": "missed block", "channel": cluster.channel_id, "reply_to": None},
        )
        _assert_accepted(await cluster.submit(event, indices=(0, 1, 2)))
        await cluster.wait_for_height(1, indices=(0, 1, 2))

        # One signed ahead status is insufficient in f=1 mode, preventing a
        # single Byzantine peer from repeatedly pausing a healthy engine.
        await asyncio.gather(cluster.validators[0].stop(), cluster.validators[1].stop())
        await cluster.validators[3].start()
        await asyncio.sleep(0.5)
        assert _head(cluster, 3).height == 0

        # Once a second independently signed ahead status is reachable, the
        # already-running validator's background supervisor catches it up.
        await cluster.validators[0].start()
        await cluster.wait_for_height(1, indices=(3,), timeout=6)
        assert _head(cluster, 3).block_hash == _head(cluster, 2).block_hash
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_governance_starts_consensus_without_batching_delay(tmp_path: Path) -> None:
    timing = ConsensusTiming(
        block_interval=2.0,
        observation_timeout=0.25,
        observation_timeout_delta=0.0,
        proposal_timeout=0.25,
        proposal_timeout_delta=0.0,
        prevote_timeout=0.25,
        prevote_timeout_delta=0.0,
        precommit_timeout=0.25,
        precommit_timeout_delta=0.0,
        round_backoff=0.02,
    )
    cluster = LiveCluster(tmp_path / "urgent-governance", timing=timing)
    try:
        await cluster.start()
        invitee = Keypair.generate()
        invite = _event(
            cluster,
            author=cluster.founder,
            event_type="invite",
            content={"invitee": invitee.pubkey_hex, "role": "member"},
        )
        started = asyncio.get_running_loop().time()
        _assert_accepted(await cluster.submit(invite))
        await cluster.wait_for_height(1, timeout=1.5)
        assert asyncio.get_running_loop().time() - started < 1.5
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_live_ingress_rate_limit_and_malformed_requests(tmp_path: Path) -> None:
    cluster = LiveCluster(
        tmp_path / "ingress",
        validator_count=1,
        faults=0,
        ingress_limit=5,
        ingress_window_seconds=1,
        timing=ConsensusTiming(
            block_interval=2.0,
            observation_timeout_delta=0.0,
            proposal_timeout_delta=0.0,
            prevote_timeout_delta=0.0,
            precommit_timeout_delta=0.0,
        ),
    )
    try:
        await cluster.start()
        client = BFTWebSocketClient(cluster.urls[0])
        events = tuple(
            build_event(
                type="chat.message",
                group=cluster.group,
                author_keypair=cluster.founder,
                seq=sequence,
                content={
                    "text": f"spam-{sequence}",
                    "channel": cluster.channel_id,
                    "reply_to": None,
                },
            )
            for sequence in range(1, 9)
        )
        results: list[object] = []
        for event in events:
            try:
                results.append(await client.submit_event(event))
            except Exception as exc:
                results.append(exc)
        assert sum(not isinstance(result, BaseException) for result in results) == 5
        assert sum("rate limit exceeded" in str(result) for result in results) == 3

        await asyncio.sleep(1.05)
        recovered = await client.submit_event(events[5])
        assert recovered.event_id == events[5].id

        async with connect(cluster.urls[0]) as websocket:
            await websocket.send("not-json")
            malformed = json.loads(str(await websocket.recv()))
            assert malformed["type"] == "error"
            await websocket.send(b"binary")
            binary = json.loads(str(await websocket.recv()))
            assert binary["type"] == "error"
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_live_cluster_finalizes_sustained_valid_event_burst(tmp_path: Path) -> None:
    cluster = LiveCluster(
        tmp_path / "valid-burst",
        timing=ConsensusTiming(
            block_interval=0.5,
            observation_timeout=0.3,
            observation_timeout_delta=0.0,
            proposal_timeout=0.3,
            proposal_timeout_delta=0.0,
            prevote_timeout=0.3,
            prevote_timeout_delta=0.0,
            precommit_timeout=0.3,
            precommit_timeout_delta=0.0,
            round_backoff=0.02,
        ),
    )
    try:
        await cluster.start()
        for sequence in range(1, 41):
            event = build_event(
                type="chat.message",
                group=cluster.group,
                author_keypair=cluster.founder,
                seq=sequence,
                content={
                    "text": f"burst-{sequence}",
                    "channel": cluster.channel_id,
                    "reply_to": None,
                },
            )
            _assert_accepted(await cluster.submit(event))

        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            if all(
                _head(cluster, index).state.sequences.get(cluster.founder.pubkey_hex) == 40
                for index in range(4)
            ):
                break
            await asyncio.sleep(0.05)
        assert all(
            _head(cluster, index).state.sequences.get(cluster.founder.pubkey_hex) == 40
            for index in range(4)
        )
        _assert_same_finalized_head(cluster, (0, 1, 2, 3))
        for validator in cluster.validators:
            assert validator.store is not None
            finalized = validator.store.finalized_events(cluster.group)
            assert [event.seq for event, *_rest in finalized] == list(range(1, 41))
            assert not validator.store.pending_events(cluster.group)
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_subscription_pushes_pending_and_commit_then_accepts_reconnect(
    tmp_path: Path,
) -> None:
    cluster = LiveCluster(
        tmp_path / "subscription",
        validator_count=1,
        faults=0,
    )
    try:
        await cluster.start()
        stream = BFTWebSocketClient(cluster.urls[0]).subscribe(cluster.group)
        subscribed = await anext(stream)
        assert subscribed == {"type": "subscribed", "group": cluster.group}

        event = _event(
            cluster,
            author=cluster.founder,
            event_type="chat.message",
            content={"text": "subscription", "channel": cluster.channel_id, "reply_to": None},
        )
        pending_task = asyncio.create_task(anext(stream))
        _assert_accepted(await cluster.submit(event))
        pending = await asyncio.wait_for(pending_task, timeout=2)
        assert pending["type"] == "pending_event"
        assert pending["event"]["id"] == event.id
        commit = await asyncio.wait_for(anext(stream), timeout=2)
        assert commit["type"] == "commit"
        assert commit["commit"]["block"]["height"] == 1

        await cluster.validators[0].stop()
        with pytest.raises(ConnectionClosed):
            await anext(stream)

        await cluster.validators[0].start()
        replacement_stream = BFTWebSocketClient(cluster.urls[0]).subscribe(cluster.group)
        resubscribed = await anext(replacement_stream)
        assert resubscribed == {"type": "subscribed", "group": cluster.group}
        await replacement_stream.aclose()
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_live_cluster_sacrifices_liveness_without_quorum_then_recovers(
    tmp_path: Path,
) -> None:
    cluster = LiveCluster(tmp_path / "quorum-recovery")
    try:
        await cluster.start()
        await asyncio.gather(cluster.validators[2].stop(), cluster.validators[3].stop())
        event = _event(
            cluster,
            author=cluster.founder,
            event_type="chat.message",
            content={"text": "waiting for quorum", "channel": cluster.channel_id, "reply_to": None},
        )
        _assert_accepted(await cluster.submit(event, indices=(0, 1)))
        await asyncio.sleep(0.8)
        assert _head(cluster, 0).height == 0
        assert _head(cluster, 1).height == 0

        await asyncio.gather(cluster.validators[2].start(), cluster.validators[3].start())
        await cluster.wait_for_height(1, timeout=8)
        _assert_same_finalized_head(cluster, (0, 1, 2, 3))
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_all_validators_restart_from_disk_and_continue_consensus(tmp_path: Path) -> None:
    cluster = LiveCluster(tmp_path / "full-restart")
    try:
        await cluster.start()
        for height in range(1, 4):
            event = _event(
                cluster,
                author=cluster.founder,
                event_type="chat.message",
                content={
                    "text": f"before restart {height}",
                    "channel": cluster.channel_id,
                    "reply_to": None,
                },
            )
            _assert_accepted(await cluster.submit(event))
            await cluster.wait_for_height(height)

        await cluster.close()
        await asyncio.gather(*(validator.start() for validator in cluster.validators))
        await cluster.wait_for_height(3)
        _assert_same_finalized_head(cluster, (0, 1, 2, 3))

        after_restart = _event(
            cluster,
            author=cluster.founder,
            event_type="chat.message",
            content={
                "text": "after restart",
                "channel": cluster.channel_id,
                "reply_to": None,
            },
        )
        _assert_accepted(await cluster.submit(after_restart))
        await cluster.wait_for_height(4)
        _assert_same_finalized_head(cluster, (0, 1, 2, 3))
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_live_server_returns_error_for_tampered_event(tmp_path: Path) -> None:
    cluster = LiveCluster(tmp_path / "adversarial")
    try:
        await cluster.start()
        valid = _event(
            cluster,
            author=cluster.founder,
            event_type="chat.message",
            content={"text": "valid payload", "channel": cluster.channel_id, "reply_to": None},
        )
        tampered = replace(valid, id="0" * 64)
        with pytest.raises(ValueError, match="Event ID mismatch"):
            await BFTWebSocketClient(cluster.urls[0]).submit_event(tampered)

        assert all(_head(cluster, index).height == 0 for index in range(4))
        assert all(
            not validator.store.pending_events(cluster.group)
            for validator in cluster.validators
            if validator.store is not None
        )
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_live_server_rejects_mismatched_peer_group(tmp_path: Path) -> None:
    cluster = LiveCluster(tmp_path / "peer-group")
    try:
        await cluster.start()

        async with connect(cluster.urls[0]) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "action": "peer",
                        "group": cluster.group,
                        "message": {
                            "type": "commit",
                            "commit": {"group": "0" * 64},
                        },
                    }
                )
            )
            response = json.loads(str(await websocket.recv()))
            assert response["type"] == "error"
            assert "group mismatch" in response["message"]

        assert all(_head(cluster, index).height == 0 for index in range(4))
        assert all(
            not validator.store.pending_events(cluster.group)
            for validator in cluster.validators
            if validator.store is not None
        )
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_invalid_pending_governance_returns_consensus_to_idle(tmp_path: Path) -> None:
    cluster = LiveCluster(tmp_path / "invalid-governance")
    try:
        await cluster.start()
        replacement_key = Keypair.generate()
        next_set = make_validator_set(
            [
                *cluster.validator_set.validators[:3],
                Validator(
                    pubkey=replacement_key.pubkey_hex,
                    url=f"ws://127.0.0.1:{_unused_port()}",
                    operator="unprepared",
                ),
            ],
            epoch=1,
            fault_tolerance=1,
        )
        stale_update = _event(
            cluster,
            author=cluster.founder,
            event_type="validator_update",
            content={
                "validators": [validator.to_dict() for validator in next_set.validators],
                "fault_tolerance": 1,
                "readiness": [],
            },
        )
        _assert_accepted(await cluster.submit(stale_update))

        await asyncio.sleep(1.5)
        assert all(_head(cluster, index).height == 0 for index in range(4))
        assert all(
            not validator.store.pending_events(cluster.group)
            for validator in cluster.validators
            if validator.store is not None
        )
        before = tuple(
            validator.node.engines[cluster.group]._core.state.round
            for validator in cluster.validators
            if validator.node is not None
            and validator.node.engines[cluster.group]._core is not None
        )
        await asyncio.sleep(0.5)
        after = tuple(
            validator.node.engines[cluster.group]._core.state.round
            for validator in cluster.validators
            if validator.node is not None
            and validator.node.engines[cluster.group]._core is not None
        )
        assert after == before
    finally:
        await cluster.close()


@pytest.mark.asyncio
async def test_live_validator_epoch_migration_and_new_committee(tmp_path: Path) -> None:
    cluster = LiveCluster(tmp_path / "migration")
    replacement: LiveValidator | None = None
    prospective_store: BFTStore | None = None
    try:
        await cluster.start()
        initial_message = _event(
            cluster,
            author=cluster.founder,
            event_type="chat.message",
            content={"text": "before migration", "channel": cluster.channel_id, "reply_to": None},
        )
        _assert_accepted(await cluster.submit(initial_message))
        await cluster.wait_for_height(1)

        replacement_key = Keypair.generate()
        replacement_port = _unused_port()
        replacement_url = f"ws://127.0.0.1:{replacement_port}"
        replacement_store_path = tmp_path / "migration" / "replacement.sqlite"
        prospective_store = BFTStore(replacement_store_path)
        prepared = await prepare_validator_history(
            group=cluster.group,
            urls=cluster.urls,
            store=prospective_store,
            keypair=replacement_key,
            trusted_operators={
                validator.keypair.pubkey_hex: f"operator-{validator.index}"
                for validator in cluster.validators
            },
            minimum_operators=2,
        )
        assert prepared.readiness.checkpoint_height == 1
        prospective_store.close()
        prospective_store = None

        replacement = LiveValidator(
            index=4,
            keypair=replacement_key,
            url=replacement_url,
            store_path=replacement_store_path,
            timing=cluster.timing,
        )
        await replacement.start()
        retained = [
            Validator(
                pubkey=validator.keypair.pubkey_hex,
                url=validator.url,
                operator=f"operator-{validator.index}",
            )
            for validator in cluster.validators[:3]
        ]
        next_set = make_validator_set(
            [
                *retained,
                Validator(
                    pubkey=replacement_key.pubkey_hex,
                    url=replacement_url,
                    operator="operator-4",
                ),
            ],
            epoch=1,
            fault_tolerance=1,
        )
        transition = _event(
            cluster,
            author=cluster.founder,
            event_type="validator_update",
            content={
                "validators": [validator.to_dict() for validator in next_set.validators],
                "fault_tolerance": 1,
                "readiness": [prepared.readiness.to_dict()],
            },
        )
        _assert_accepted(await cluster.submit(transition))
        await cluster.wait_for_height(2, indices=(0, 1, 2))

        for _ in range(200):
            assert replacement.store is not None
            if replacement.store.get_chain_head(cluster.group).height >= 2:
                break
            await asyncio.sleep(0.02)
        assert replacement.store is not None
        replacement_head = replacement.store.get_chain_head(cluster.group)
        assert replacement_head.height == 2
        assert replacement_head.state.validator_set.epoch == 1
        assert cluster.group in (replacement.node.engines if replacement.node else {})

        post_transition = _event(
            cluster,
            author=cluster.founder,
            event_type="chat.message",
            content={
                "text": "after migration",
                "channel": cluster.channel_id,
                "reply_to": None,
            },
        )
        # The removed validator retains history but can no longer admit events.
        with pytest.raises(ValueError, match="local validator is not active"):
            await BFTWebSocketClient(cluster.urls[3]).submit_event(post_transition)

        current_urls = [*cluster.urls[:3], replacement_url]
        results = await asyncio.gather(
            *(BFTWebSocketClient(url).submit_event(post_transition) for url in current_urls),
            return_exceptions=True,
        )
        assert not [result for result in results if isinstance(result, BaseException)]
        await cluster.wait_for_height(3, indices=(0, 1, 2))
        for _ in range(200):
            if replacement.store.get_chain_head(cluster.group).height >= 3:
                break
            await asyncio.sleep(0.02)
        assert replacement.store.get_chain_head(cluster.group).height == 3
        assert cluster.validators[3].store is not None
        assert cluster.validators[3].store.get_chain_head(cluster.group).height == 2
    finally:
        if prospective_store is not None:
            prospective_store.close()
        if replacement is not None and replacement.online:
            await replacement.stop()
        await cluster.close()


@pytest.mark.asyncio
async def test_client_syncs_full_live_history_after_multiple_blocks(tmp_path: Path) -> None:
    cluster = LiveCluster(tmp_path / "client-sync")
    client_store = BFTStore(tmp_path / "client.sqlite")
    try:
        await cluster.start()
        for sequence in range(1, 6):
            event = _event(
                cluster,
                author=cluster.founder,
                event_type="chat.message",
                content={
                    "text": f"history-{sequence}",
                    "channel": cluster.channel_id,
                    "reply_to": None,
                },
            )
            _assert_accepted(await cluster.submit(event))
            await cluster.wait_for_height(sequence)

        result = await sync_from_validators(
            group=cluster.group,
            urls=cluster.urls,
            store=client_store,
        )
        assert result.height_before == 0
        assert result.height_after == 5
        assert result.commits_added == 5
        assert result.reachable_validators == 4
        assert client_store.get_chain_head(cluster.group).block_hash == _head(cluster).block_hash
    finally:
        client_store.close()
        await cluster.close()
