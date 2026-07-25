from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _config_defaults(home: Path, *, legacy_home: str | None = None) -> tuple[Path, Path]:
    environment = dict(os.environ)
    environment["HOME"] = str(home)
    environment.pop("FERN_VALIDATOR_HOME", None)
    environment.pop("FERN_RELAY_HOME", None)
    if legacy_home is not None:
        environment["FERN_RELAY_HOME"] = legacy_home
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from fern.validator.config import default_config_file, default_key_file; "
                "print(default_config_file()); print(default_key_file())"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    config, key = result.stdout.strip().splitlines()
    return Path(config), Path(key)


def test_validator_uses_validator_named_defaults(tmp_path: Path) -> None:
    config, key = _config_defaults(tmp_path)

    assert config == tmp_path / ".fern-validator" / "config.json"
    assert key == tmp_path / ".fern-validator" / "validator.key"


def test_validator_preserves_an_existing_legacy_identity(tmp_path: Path) -> None:
    legacy = tmp_path / ".fern-relay"
    legacy.mkdir()
    (legacy / "relay.key").write_text("existing-key", encoding="utf-8")

    config, key = _config_defaults(tmp_path)

    assert config == legacy / "config.json"
    assert key == legacy / "relay.key"


def test_legacy_home_environment_variable_remains_a_migration_alias(tmp_path: Path) -> None:
    legacy = tmp_path / "custom-legacy-home"
    config, key = _config_defaults(tmp_path, legacy_home=str(legacy))

    assert config == legacy / "config.json"
    assert key == legacy / "validator.key"


def test_legacy_executable_warns_and_forwards_to_validator_cli() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from cli.relay_main import main; "
                "sys.argv=['fern-relay', '--help']; main()"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "renamed to 'fern-validator'" in result.stderr
    assert "Run and configure a FERN-BFT validator" in result.stdout
