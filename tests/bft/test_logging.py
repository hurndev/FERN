from __future__ import annotations

import asyncio
import logging
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli.bft import warn_small_validator_set
from cli.main import fern_cli
from cli.validator_main import main_fn as validator_cli
from fern.bft.node import ConsensusTiming, ValidatorNode
from fern.bft.store import BFTStore
from fern.validator.config import init_config
from tests.bft.helpers import genesis_fixture, message_fixture, validator_fixture


@pytest.mark.asyncio
async def test_validator_logs_lifecycle_and_consensus_stages(tmp_path, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="fern.bft.node")
    keys, validator_set = validator_fixture(faults=0)
    _group_key, founder, genesis, head, channel_id = genesis_fixture(validator_set)
    store = BFTStore(tmp_path / "validator.sqlite")
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
    node.bootstrap(genesis)
    await node.submit_event(message_fixture(founder, head, channel_id))

    for _ in range(200):
        if store.get_chain_head(genesis.group).height == 1:
            break
        await asyncio.sleep(0.01)

    assert store.get_chain_head(genesis.group).height == 1
    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("genesis accepted") for message in messages)
    assert any(message.startswith("group engine active") for message in messages)
    assert any(message.startswith("small unanimous validator set") for message in messages)
    assert any(message.startswith("event accepted") for message in messages)
    assert any("stage=propose" in message for message in messages)
    assert any(message.startswith("candidate built") for message in messages)
    assert any(message.startswith("local prevote") for message in messages)
    assert any(message.startswith("local precommit") for message in messages)
    assert any(message.startswith("block finalized") for message in messages)

    await node.stop()
    store.close()


def test_verbose_options_are_exposed_by_both_clis() -> None:
    runner = CliRunner()

    fern_help = runner.invoke(fern_cli, ["--help"])
    assert fern_help.exit_code == 0
    assert "--verbose" in fern_help.output
    assert "validator" in fern_help.output
    assert "relay" not in fern_help.output

    validator_help = runner.invoke(validator_cli, ["--help"])
    assert validator_help.exit_code == 0
    assert "--verbose" in validator_help.output

    validator_run_help = runner.invoke(validator_cli, ["run", "--help"])
    assert validator_run_help.exit_code == 0
    assert "--verbose" in validator_run_help.output


def test_cli_warns_for_small_validator_sets(capsys) -> None:
    _keys, validator_set = validator_fixture(faults=0, validator_count=3)

    warn_small_validator_set(validator_set)

    warning = capsys.readouterr().err
    assert "unanimous small-set mode" in warning
    assert "validators=3, quorum=3" in warning
    assert "any unavailable validator halts consensus" in warning


def test_validator_ctrl_c_shuts_down_cleanly(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    init_config(
        host="127.0.0.1",
        port=0,
        store=str(tmp_path / "validator.db"),
        config_path=config_path,
        key_path=tmp_path / "validator.key",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from cli.validator_main import main; main()",
            "--config",
            str(config_path),
            "run",
            "--no-color",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = ""
    try:
        assert process.stdout is not None
        for _ in range(12):
            line = process.stdout.readline()
            output += line
            if "validator websocket listening" in line:
                break
            if not line and process.poll() is not None:
                break
        assert "validator websocket listening" in output

        process.send_signal(signal.SIGINT)
        remainder, _ = process.communicate(timeout=5)
        output += remainder
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0
    assert "validator shutdown requested signal=SIGINT" in output
    assert "validator websocket stopped" in output
    assert "validator stopped" in output
    assert "Traceback" not in output
