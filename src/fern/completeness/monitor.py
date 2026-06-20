from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from fern.completeness.attestations import (
    Attestation,
    compute_set_hash,
    hash_attestation,
)
from fern.completeness.receipts import Receipt
from fern.completeness.trust_ledger import Fault


@dataclass(frozen=True)
class MonitorResult:
    in_sync: bool
    faults: tuple[Fault, ...] = ()
    divergent_relays: tuple[str, ...] = ()
    candidates_to_check: tuple[str, ...] = ()


def monitor_pass(
    *,
    local_known_set: Iterable[str],
    local_receipts_for_relay: Mapping[str, Receipt],
    new_attestation: Attestation,
    prev_attestation: Attestation | None,
    relay_pubkey: str,
    sibling_attestations: Mapping[str, Attestation],
    now_ts: int,
) -> MonitorResult:
    faults: list[Fault] = []
    known_set = frozenset(local_known_set)
    local_hash = compute_set_hash(known_set)
    in_sync = local_hash == new_attestation.set_hash

    if prev_attestation is not None:
        expected_prev = hash_attestation(prev_attestation)
        if new_attestation.prev != expected_prev:
            faults.append(
                Fault(
                    ts=now_ts,
                    kind="attestation_chain_break",
                    evidence=(
                        f"Relay {relay_pubkey[:16]}... attestation prev "
                        f"does not match hash of previous attestation"
                    ),
                )
            )

    divergent_relays: list[str] = []
    for other_relay, other_att in sibling_attestations.items():
        if other_relay == relay_pubkey:
            continue
        if other_att.set_hash != new_attestation.set_hash:
            divergent_relays.append(other_relay)

    if in_sync:
        return MonitorResult(
            in_sync=True,
            faults=tuple(faults),
            divergent_relays=tuple(divergent_relays),
            candidates_to_check=(),
        )

    return MonitorResult(
        in_sync=False,
        faults=tuple(faults),
        divergent_relays=tuple(divergent_relays),
        candidates_to_check=tuple(sorted(known_set)),
    )
