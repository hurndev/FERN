from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fern.crypto.encoding import is_valid_pubkey_hex
from fern.crypto.keys import Keypair


def _default_config_dir() -> Path:
    configured = os.environ.get("FERN_VALIDATOR_HOME") or os.environ.get("FERN_RELAY_HOME")
    if configured:
        return Path(configured)
    current = Path.home() / ".fern-validator"
    legacy = Path.home() / ".fern-relay"
    legacy_identity_exists = any(
        (legacy / name).exists() for name in ("config.json", "relay.key", "validator.db")
    )
    return legacy if not current.exists() and legacy_identity_exists else current


_DEFAULT_CONFIG_DIR = _default_config_dir()
_DEFAULT_CONFIG_FILE = _DEFAULT_CONFIG_DIR / "config.json"
_LEGACY_KEY_FILE = _DEFAULT_CONFIG_DIR / "relay.key"
_DEFAULT_KEY_FILE = (
    _LEGACY_KEY_FILE
    if not (_DEFAULT_CONFIG_DIR / "validator.key").exists() and _LEGACY_KEY_FILE.exists()
    else _DEFAULT_CONFIG_DIR / "validator.key"
)
_DEFAULT_STORE = str(_DEFAULT_CONFIG_DIR / "validator.db")


@dataclass(frozen=True)
class TrustedHost:
    pubkey: str
    url: str
    operator: str

    def __post_init__(self) -> None:
        if not is_valid_pubkey_hex(self.pubkey):
            raise ValueError("trusted host key must be 64-char lowercase hex")
        parsed = urlparse(self.url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
            raise ValueError("trusted host URL must use ws:// or wss://")
        if not self.operator.strip():
            raise ValueError("trusted host operator label is required")


@dataclass(frozen=True)
class ValidatorConfig:
    name: str = "FERN Validator"
    description: str = "A FERN-BFT validator"
    host: str = "0.0.0.0"
    port: int = 8765
    store: str = _DEFAULT_STORE
    key_file: str = str(_DEFAULT_KEY_FILE)
    allow_genesis: bool = True
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
    ingress_limit: int = 60
    ingress_window_seconds: int = 60
    trusted_hosts: tuple[TrustedHost, ...] = ()
    minimum_trusted_operators: int = 2
    maximum_group_logical_bytes: int | None = 10 * 1024 * 1024 * 1024
    maximum_message_bytes: int = 4 * 1024 * 1024


def default_config_path() -> Path:
    return _DEFAULT_CONFIG_DIR


def default_config_file() -> Path:
    return _DEFAULT_CONFIG_FILE


def default_key_file() -> Path:
    return _DEFAULT_KEY_FILE


def load_config(path: Path | None = None) -> ValidatorConfig:
    config_path = path or _DEFAULT_CONFIG_FILE
    if not config_path.exists():
        return ValidatorConfig()
    with open(config_path, encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("validator config must be a JSON object")
    return _parse_config(value)


def save_config(config: ValidatorConfig, path: Path | None = None) -> None:
    config_path = path or _DEFAULT_CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(_config_to_dict(config), handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def init_config(
    *,
    name: str = "FERN Validator",
    host: str = "0.0.0.0",
    port: int = 8765,
    store: str = _DEFAULT_STORE,
    config_path: Path | None = None,
    key_path: Path | None = None,
) -> tuple[ValidatorConfig, Keypair]:
    config_path = config_path or _DEFAULT_CONFIG_FILE
    key_path = key_path or _DEFAULT_KEY_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        keypair = _load_keypair(key_path)
    else:
        keypair = Keypair.generate()
        key_path.write_text(keypair.privkey_hex, encoding="utf-8")
        key_path.chmod(0o600)
    config = ValidatorConfig(
        name=name,
        host=host,
        port=port,
        store=store,
        key_file=str(key_path),
    )
    save_config(config, config_path)
    return config, keypair


def load_keypair(config: ValidatorConfig) -> Keypair:
    path = Path(config.key_file)
    if not path.exists():
        raise FileNotFoundError(
            f"Validator key file not found: {path}\nRun 'fern-validator init' first."
        )
    return _load_keypair(path)


def add_witness(
    config: ValidatorConfig, url: str, pubkey: str, operator: str | None = None
) -> ValidatorConfig:
    """Compatibility spelling for adding a trusted history host."""

    if any(host.pubkey == pubkey for host in config.trusted_hosts):
        raise ValueError(f"trusted host {pubkey[:16]}... already exists")
    host = TrustedHost(pubkey=pubkey, url=url, operator=operator or pubkey)
    return replace(config, trusted_hosts=config.trusted_hosts + (host,))


def remove_witness(config: ValidatorConfig, pubkey: str) -> ValidatorConfig:
    hosts = tuple(host for host in config.trusted_hosts if host.pubkey != pubkey)
    if len(hosts) == len(config.trusted_hosts):
        raise ValueError(f"trusted host {pubkey[:16]}... was not found")
    return replace(config, trusted_hosts=hosts)


def _load_keypair(path: Path) -> Keypair:
    value = path.read_text(encoding="utf-8").strip()
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"invalid validator key file: {path}") from exc
    return Keypair.from_privkey(raw)


def _config_to_dict(config: ValidatorConfig) -> dict[str, object]:
    return {
        "name": config.name,
        "description": config.description,
        "host": config.host,
        "port": config.port,
        "store": config.store,
        "key_file": config.key_file,
        "allow_genesis": config.allow_genesis,
        "consensus": {
            "block_interval": config.block_interval,
            "observation_timeout": config.observation_timeout,
            "observation_timeout_delta": config.observation_timeout_delta,
            "proposal_timeout": config.proposal_timeout,
            "proposal_timeout_delta": config.proposal_timeout_delta,
            "prevote_timeout": config.prevote_timeout,
            "prevote_timeout_delta": config.prevote_timeout_delta,
            "precommit_timeout": config.precommit_timeout,
            "precommit_timeout_delta": config.precommit_timeout_delta,
            "round_backoff": config.round_backoff,
        },
        "ingress": {
            "max_requests": config.ingress_limit,
            "window_seconds": config.ingress_window_seconds,
        },
        "history_admission": {
            "minimum_trusted_operators": config.minimum_trusted_operators,
            "maximum_group_logical_bytes": config.maximum_group_logical_bytes,
            "trusted_hosts": [
                {"pubkey": host.pubkey, "url": host.url, "operator": host.operator}
                for host in config.trusted_hosts
            ],
        },
        "maximum_message_bytes": config.maximum_message_bytes,
    }


def _parse_config(data: dict[str, Any]) -> ValidatorConfig:
    consensus = data.get("consensus", {})
    ingress = data.get("ingress", {})
    admission = data.get("history_admission", {})
    if not all(isinstance(value, dict) for value in (consensus, ingress, admission)):
        raise ValueError("validator config sections must be objects")
    raw_hosts = admission.get("trusted_hosts", data.get("trusted_witness_relays", []))
    if not isinstance(raw_hosts, list):
        raise ValueError("trusted_hosts must be an array")
    hosts: list[TrustedHost] = []
    for raw in raw_hosts:
        if not isinstance(raw, dict):
            raise ValueError("trusted host must be an object")
        pubkey = str(raw.get("pubkey", raw.get("relay", "")))
        hosts.append(
            TrustedHost(
                pubkey=pubkey,
                url=str(raw.get("url", "")),
                operator=str(raw.get("operator", pubkey)),
            )
        )
    raw_maximum = admission.get("maximum_group_logical_bytes", 10 * 1024 * 1024 * 1024)
    return ValidatorConfig(
        name=str(data.get("name", "FERN Validator")),
        description=str(data.get("description", "A FERN-BFT validator")),
        host=str(data.get("host", "0.0.0.0")),
        port=int(data.get("port", 8765)),
        store=str(data.get("store", _DEFAULT_STORE)),
        key_file=str(data.get("key_file", str(_DEFAULT_KEY_FILE))),
        allow_genesis=bool(data.get("allow_genesis", True)),
        block_interval=float(consensus.get("block_interval", 20.0)),
        observation_timeout=float(consensus.get("observation_timeout", 3.0)),
        observation_timeout_delta=float(consensus.get("observation_timeout_delta", 0.5)),
        proposal_timeout=float(consensus.get("proposal_timeout", 3.0)),
        proposal_timeout_delta=float(consensus.get("proposal_timeout_delta", 0.5)),
        prevote_timeout=float(consensus.get("prevote_timeout", 3.0)),
        prevote_timeout_delta=float(consensus.get("prevote_timeout_delta", 0.5)),
        precommit_timeout=float(consensus.get("precommit_timeout", 3.0)),
        precommit_timeout_delta=float(consensus.get("precommit_timeout_delta", 0.5)),
        round_backoff=float(consensus.get("round_backoff", 1.0)),
        ingress_limit=int(ingress.get("max_requests", 60)),
        ingress_window_seconds=int(ingress.get("window_seconds", 60)),
        trusted_hosts=tuple(hosts),
        minimum_trusted_operators=int(admission.get("minimum_trusted_operators", 2)),
        maximum_group_logical_bytes=int(raw_maximum) if raw_maximum is not None else None,
        maximum_message_bytes=int(data.get("maximum_message_bytes", 4 * 1024 * 1024)),
    )


__all__ = [
    "ValidatorConfig",
    "TrustedHost",
    "add_witness",
    "default_config_file",
    "default_config_path",
    "default_key_file",
    "init_config",
    "load_config",
    "load_keypair",
    "remove_witness",
    "save_config",
]
