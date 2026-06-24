from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fern.completeness.heal_attestations import Threshold, TrustedWitness
from fern.crypto.encoding import is_valid_pubkey_hex


@dataclass(frozen=True)
class BatchLimits:
    max_events: int = 500
    max_bytes: int = 2 * 1024 * 1024


@dataclass(frozen=True)
class RateLimit:
    max: int
    window_seconds: int


@dataclass(frozen=True)
class RelayTrustConfig:
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

    @property
    def has_trusted_witnesses(self) -> bool:
        return len(self.trusted_witness_relays) > 0

    def is_willing_to_witness_for(self, receiver_pubkey: str) -> bool:
        if not self.witnessing_enabled:
            return False
        if self.witness_for_receivers is None:
            return True
        return receiver_pubkey in self.witness_for_receivers


def load_trust_config(path: str | None) -> RelayTrustConfig:
    if path is None:
        return RelayTrustConfig()
    config_path = Path(path)
    if not config_path.exists():
        return RelayTrustConfig()
    with open(config_path) as f:
        data = json.load(f)
    return _parse_trust_config(data)


def _parse_trust_config(data: dict[str, Any]) -> RelayTrustConfig:
    witnesses: list[TrustedWitness] = []
    for w in data.get("trusted_witness_relays", []):
        pubkey = str(w.get("pubkey", ""))
        url = str(w.get("url", ""))
        if not is_valid_pubkey_hex(pubkey):
            raise ValueError(f"invalid witness pubkey in trust config: {pubkey[:20]}...")
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
    for key, rl in data.get("rate_limits", {}).items():
        rate_limits[key] = RateLimit(
            max=int(rl.get("max", 0)),
            window_seconds=int(rl.get("window_seconds", 60)),
        )

    quota = data.get("per_group_storage_quota", 100_000)
    per_group_quota = int(quota) if quota is not None else None

    max_msg = int(data.get("max_message_bytes", 2 * 1024 * 1024))

    witnessing = bool(data.get("witnessing_enabled", True))
    wfr_raw = data.get("witness_for_receivers")
    wfr = tuple(str(x) for x in wfr_raw) if wfr_raw is not None else None

    expiry = int(data.get("challenge_expiry_seconds", 300))

    return RelayTrustConfig(
        trusted_witness_relays=tuple(witnesses),
        threshold=threshold,
        batch_limits=batch_limits,
        rate_limits=rate_limits,
        per_group_storage_quota=per_group_quota,
        max_message_bytes=max_msg,
        witnessing_enabled=witnessing,
        witness_for_receivers=wfr,
        challenge_expiry_seconds=expiry,
    )