from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from fern.completeness.group_statuses import (
    GroupStatus,
    compute_set_hash,
    hash_group_status,
)
from fern.completeness.event_receipts import EventReceipt
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
    local_event_receipts_for_relay: Mapping[str, EventReceipt],
    new_group_status: GroupStatus,
    prev_group_status: GroupStatus | None,
    relay_pubkey: str,
    sibling_group_statuses: Mapping[str, GroupStatus],
    now_ts: int,
) -> MonitorResult:
    faults: list[Fault] = []
    known_set = frozenset(local_known_set)
    local_hash = compute_set_hash(known_set)
    in_sync = local_hash == new_group_status.set_hash

    if prev_group_status is not None:
        expected_prev = hash_group_status(prev_group_status)
        if new_group_status.prev != expected_prev:
            faults.append(
                Fault(
                    ts=now_ts,
                    kind="group_status_chain_break",
                    evidence=(
                        f"Relay {relay_pubkey[:16]}... group_status prev "
                        f"does not match hash of previous group_status"
                    ),
                )
            )

    divergent_relays: list[str] = []
    for other_relay, other_att in sibling_group_statuses.items():
        if other_relay == relay_pubkey:
            continue
        if other_att.set_hash != new_group_status.set_hash:
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
