from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fern.completeness.heal_attestations import Threshold, TrustedWitness
from fern.crypto.encoding import is_valid_pubkey_hex
from fern.crypto.keys import Keypair
from fern.relay.trust_config import BatchLimits, RateLimit, RelayTrustConfig


_DEFAULT_CONFIG_DIR = Path(os.environ.get("FERN_RELAY_HOME") or (Path.home() / ".fern-relay"))
_DEFAULT_CONFIG_FILE = _DEFAULT_CONFIG_DIR / "config.json"
_DEFAULT_KEY_FILE = _DEFAULT_CONFIG_DIR / "relay.key"


@dataclass(frozen=True)
class RelayConfig:
    name: str = "FERN Relay"
    host: str = "0.0.0.0"
    port: int = 8765
    store: str = "relay.db"
    key_file: str = str(_DEFAULT_KEY_FILE)

    trusted_witness_relays: tuple[TrustedWitness, ...] = ()
    threshold: Threshold = field(default_factory=Threshold)
    batch_limits: BatchLimits = field(default_factory=BatchLimits)
    rate_limits: dict[str, RateLimit] = field(
        default_factory=lambda: {
            "publish": RateLimit(max=60, window_seconds=60),
            "slow_heal": RateLimit(max=60, window_seconds=60),
            "heal_batch": RateLimit(max=10, window_seconds=60),
            "get_heal_challenge": RateLimit(max=10, window_seconds=60),
            "get_group_host_attestation": RateLimit(max=30, window_seconds=60),
            "get_inventory_attestation": RateLimit(max=15, window_seconds=60),
        }
    )
    per_group_storage_quota: int | None = 100_000
    max_message_bytes: int = 2 * 1024 * 1024
    witnessing_enabled: bool = True
    witness_for_receivers: tuple[str, ...] | None = None
    challenge_expiry_seconds: int = 300

    def to_trust_config(self) -> RelayTrustConfig:
        return RelayTrustConfig(
            trusted_witness_relays=self.trusted_witness_relays,
            threshold=self.threshold,
            batch_limits=self.batch_limits,
            rate_limits=self.rate_limits,
            per_group_storage_quota=self.per_group_storage_quota,
            max_message_bytes=self.max_message_bytes,
            witnessing_enabled=self.witnessing_enabled,
            witness_for_receivers=self.witness_for_receivers,
            challenge_expiry_seconds=self.challenge_expiry_seconds,
        )


def default_config_path() -> Path:
    return _DEFAULT_CONFIG_DIR


def default_config_file() -> Path:
    return _DEFAULT_CONFIG_FILE


def default_key_file() -> Path:
    return _DEFAULT_KEY_FILE


def load_config(path: Path | None = None) -> RelayConfig:
    config_path = path or _DEFAULT_CONFIG_FILE
    if not config_path.exists():
        return RelayConfig()
    with open(config_path) as f:
        data = json.load(f)
    return _parse_config(data)


def save_config(config: RelayConfig, path: Path | None = None) -> None:
    config_path = path or _DEFAULT_CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = _config_to_dict(config)
    with open(config_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def init_config(
    *,
    name: str = "FERN Relay",
    host: str = "0.0.0.0",
    port: int = 8765,
    store: str = "relay.db",
    config_path: Path | None = None,
    key_path: Path | None = None,
) -> tuple[RelayConfig, Keypair]:
    config_path = config_path or _DEFAULT_CONFIG_FILE
    key_path = key_path or _DEFAULT_KEY_FILE

    config_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        keypair = _load_keypair(key_path)
    else:
        keypair = Keypair.generate()
        key_path.write_text(keypair.privkey_hex)
        key_path.chmod(0o600)

    config = RelayConfig(
        name=name,
        host=host,
        port=port,
        store=store,
        key_file=str(key_path),
    )
    save_config(config, config_path)
    return config, keypair


def load_keypair(config: RelayConfig) -> Keypair:
    key_path = Path(config.key_file)
    if not key_path.exists():
        raise FileNotFoundError(
            f"Relay key file not found: {key_path}\n"
            f"Run 'fern-relay init' to generate a new keypair."
        )
    return _load_keypair(key_path)


def add_witness(config: RelayConfig, url: str, pubkey: str) -> RelayConfig:
    if not is_valid_pubkey_hex(pubkey):
        raise ValueError(f"Invalid witness pubkey: {pubkey[:20]}...")
    if not url:
        raise ValueError("Witness URL required")

    existing = {w.relay for w in config.trusted_witness_relays}
    if pubkey in existing:
        raise ValueError(f"Witness {pubkey[:16]}... already in trust set")

    new_witness = TrustedWitness(relay=pubkey, url=url)
    return RelayConfig(
        name=config.name,
        host=config.host,
        port=config.port,
        store=config.store,
        key_file=config.key_file,
        trusted_witness_relays=config.trusted_witness_relays + (new_witness,),
        threshold=config.threshold,
        batch_limits=config.batch_limits,
        rate_limits=config.rate_limits,
        per_group_storage_quota=config.per_group_storage_quota,
        max_message_bytes=config.max_message_bytes,
        witnessing_enabled=config.witnessing_enabled,
        witness_for_receivers=config.witness_for_receivers,
        challenge_expiry_seconds=config.challenge_expiry_seconds,
    )


def remove_witness(config: RelayConfig, pubkey: str) -> RelayConfig:
    new_witnesses = tuple(w for w in config.trusted_witness_relays if w.relay != pubkey)
    if len(new_witnesses) == len(config.trusted_witness_relays):
        raise ValueError(f"Witness {pubkey[:16]}... not found in trust set")
    return RelayConfig(
        name=config.name,
        host=config.host,
        port=config.port,
        store=config.store,
        key_file=config.key_file,
        trusted_witness_relays=new_witnesses,
        threshold=config.threshold,
        batch_limits=config.batch_limits,
        rate_limits=config.rate_limits,
        per_group_storage_quota=config.per_group_storage_quota,
        max_message_bytes=config.max_message_bytes,
        witnessing_enabled=config.witnessing_enabled,
        witness_for_receivers=config.witness_for_receivers,
        challenge_expiry_seconds=config.challenge_expiry_seconds,
    )


def _load_keypair(path: Path) -> Keypair:
    privkey_hex = path.read_text().strip()
    privkey_bytes = bytes.fromhex(privkey_hex)
    return Keypair.from_privkey(privkey_bytes)


def _config_to_dict(config: RelayConfig) -> dict[str, Any]:
    d: dict[str, Any] = {
        "name": config.name,
        "host": config.host,
        "port": config.port,
        "store": config.store,
        "key_file": config.key_file,
    }
    if config.trusted_witness_relays:
        d["trusted_witness_relays"] = [
            {"url": w.url, "pubkey": w.relay} for w in config.trusted_witness_relays
        ]
    d["threshold"] = {
        "kind": config.threshold.kind,
        "num": config.threshold.num,
        "den": config.threshold.den,
        "min": config.threshold.min,
    }
    d["batch_limits"] = {
        "max_events": config.batch_limits.max_events,
        "max_bytes": config.batch_limits.max_bytes,
    }
    d["rate_limits"] = {
        k: {"max": v.max, "window_seconds": v.window_seconds}
        for k, v in config.rate_limits.items()
    }
    d["per_group_storage_quota"] = config.per_group_storage_quota
    d["max_message_bytes"] = config.max_message_bytes
    d["witnessing_enabled"] = config.witnessing_enabled
    d["witness_for_receivers"] = (
        list(config.witness_for_receivers)
        if config.witness_for_receivers is not None
        else None
    )
    d["challenge_expiry_seconds"] = config.challenge_expiry_seconds
    return d


def _parse_config(data: dict[str, Any]) -> RelayConfig:
    witnesses: list[TrustedWitness] = []
    for w in data.get("trusted_witness_relays", []):
        pubkey = str(w.get("pubkey", ""))
        url = str(w.get("url", ""))
        if not is_valid_pubkey_hex(pubkey):
            raise ValueError(f"invalid witness pubkey: {pubkey[:20]}...")
        if not url:
            raise ValueError("witness relay missing url")
        witnesses.append(TrustedWitness(relay=pubkey, url=url))

    threshold_data = data.get("threshold", {})
    threshold = Threshold(
        kind=str(threshold_data.get("kind", "ratio")),
        num=int(threshold_data.get("num", 2)),
        den=int(threshold_data.get("den", 3)),
        min=int(threshold_data.get("min", 2)),
    )

    batch_data = data.get("batch_limits", {})
    batch_limits = BatchLimits(
        max_events=int(batch_data.get("max_events", 500)),
        max_bytes=int(batch_data.get("max_bytes", 2 * 1024 * 1024)),
    )

    rate_limits: dict[str, RateLimit] = {}
    default_rl = RelayConfig().rate_limits
    for key, rl in data.get("rate_limits", {}).items():
        rate_limits[key] = RateLimit(
            max=int(rl.get("max", 0)),
            window_seconds=int(rl.get("window_seconds", 60)),
        )
    for key, default_val in default_rl.items():
        if key not in rate_limits:
            rate_limits[key] = default_val

    quota = data.get("per_group_storage_quota", 100_000)
    per_group_quota = int(quota) if quota is not None else None

    wfr_raw = data.get("witness_for_receivers")
    wfr = tuple(str(x) for x in wfr_raw) if wfr_raw is not None else None

    return RelayConfig(
        name=str(data.get("name", "FERN Relay")),
        host=str(data.get("host", "0.0.0.0")),
        port=int(data.get("port", 8765)),
        store=str(data.get("store", "relay.db")),
        key_file=str(data.get("key_file", str(_DEFAULT_KEY_FILE))),
        trusted_witness_relays=tuple(witnesses),
        threshold=threshold,
        batch_limits=batch_limits,
        rate_limits=rate_limits,
        per_group_storage_quota=per_group_quota,
        max_message_bytes=int(data.get("max_message_bytes", 2 * 1024 * 1024)),
        witnessing_enabled=bool(data.get("witnessing_enabled", True)),
        witness_for_receivers=wfr,
        challenge_expiry_seconds=int(data.get("challenge_expiry_seconds", 300)),
    )