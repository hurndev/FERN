from __future__ import annotations

from click.testing import CliRunner

from cli.commands import chain
from cli.main import fern_cli
from fern.bft.blocks import Commit
from fern.bft.store import BFTStore
from tests.bft.helpers import (
    genesis_fixture,
    message_fixture,
    proposal_fixture,
    validator_fixture,
    vote_quorum,
)


def _chain_database(tmp_path):
    keys, validator_set = validator_fixture(faults=0, validator_count=1)
    _group_key, founder, genesis, head, channel_id = genesis_fixture(validator_set)
    path = tmp_path / "chain.sqlite"
    store = BFTStore(path)
    store.bootstrap_genesis(genesis)
    event = message_fixture(founder, head, channel_id, "finalized message")
    proposal, block = proposal_fixture(head=head, event=event, validator_keys=keys)
    commit = Commit(
        block=block,
        round=proposal.round,
        precommits=vote_quorum(block=block, keys=keys),
    )
    next_head = store.save_commit(genesis.group, commit)
    pending = message_fixture(founder, next_head, channel_id, "pending message")
    store.add_pending(pending)
    store.close()
    return path, genesis.group


def test_chain_command_shows_consensus_blocks_events_and_pending(tmp_path) -> None:
    path, group = _chain_database(tmp_path)

    result = CliRunner().invoke(fern_cli, ["chain", group[:12], "--db", str(path)])

    assert result.exit_code == 0, result.output
    assert "Chain: BFT test group" in result.output
    assert f"Group: {group}" in result.output
    assert "Height: 1" in result.output
    assert "Consensus: UNANIMOUS SMALL SET; epoch=0; validators=1; quorum=1; f=0" in result.output
    assert "Events: 1 finalized; 1 pending" in result.output
    assert (
        "Local consensus journal: none for height 2; pending events are waiting for the next block"
        in result.output
    )
    assert "Block 1  round=0  epoch=0  events=1  observations=1  precommits=1" in result.output
    assert "1:0" in result.output
    assert "text='finalized message'" in result.output
    assert "pending" in result.output
    assert "text='pending message'" in result.output


def test_chain_command_resolves_configured_group_without_database_option(
    tmp_path, monkeypatch
) -> None:
    path, group = _chain_database(tmp_path)
    monkeypatch.setattr(
        chain,
        "load_config",
        lambda: {
            "group_order": [group],
            "groups": {group: {"cache_path": str(path), "validators": []}},
        },
    )

    result = CliRunner().invoke(
        fern_cli,
        ["chain", "1", "--no-sync", "--no-events", "--no-pending"],
    )

    assert result.exit_code == 0, result.output
    assert f"Group: {group}" in result.output
    assert "Block 1" in result.output
    assert "text='finalized message'" not in result.output
    assert "Pending events:" not in result.output


def test_chain_replaces_visible_dag_command_and_legacy_alias_still_works(tmp_path) -> None:
    path, _group = _chain_database(tmp_path)
    runner = CliRunner()

    help_result = runner.invoke(fern_cli, ["--help"])
    legacy_result = runner.invoke(fern_cli, ["dag", "--db", str(path)])

    assert help_result.exit_code == 0
    assert "chain" in help_result.output
    assert "dag" not in help_result.output
    assert legacy_result.exit_code == 0, legacy_result.output
    assert "fern dag` has been renamed to `fern chain" in legacy_result.output
    assert "Chain: BFT test group" in legacy_result.output
