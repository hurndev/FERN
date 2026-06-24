from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fern.completeness.heal_attestations import (
    GroupHostAttestation,
    HealChallenge,
    InventoryAttestation,
    compute_challenge_id,
    threshold_required,
    verify_group_host_attestation,
    verify_inventory_attestation,
)


REASON_INSUFFICIENT = "insufficient_trusted_witnesses"
REASON_DENOMINATOR_ZERO = "denominator_zero"
REASON_QUOTA_EXCEEDED = "quota_exceeded"


@dataclass(frozen=True)
class InventoryEvidence:
    attestation: InventoryAttestation
    covered_ids: frozenset[str]


@dataclass(frozen=True)
class AdmissionDecision:
    denominator: tuple[str, ...]
    accepted: tuple[str, ...]
    already_have: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    admitted_by: Mapping[str, tuple[str, ...]]


def compute_admission(
    *,
    challenge: HealChallenge,
    event_ids: Sequence[str],
    already_have_ids: frozenset[str],
    group_host_attestations: Sequence[GroupHostAttestation],
    inventory_evidence: Sequence[InventoryEvidence],
    now_ts: int,
    remaining_quota: int | None,
) -> AdmissionDecision:
    """Decide which events a heal_batch may store, per trusted-witness quorum rules.

    This is the sole security decision point. All relay signatures, expiry, and
    challenge_id binding are verified here. The caller is responsible for
    verifying the challenge itself (own signature) and the events (hash/sig).
    """
    challenge_id = compute_challenge_id(challenge)
    witness_set = frozenset(w.relay for w in challenge.trusted_witnesses)

    host_values: dict[str, list[bool]] = {}
    for ha in group_host_attestations:
        if ha.relay not in witness_set:
            continue
        if not verify_group_host_attestation(
            ha, challenge_id=challenge_id, witness_pubkey=ha.relay, now_ts=now_ts
        ):
            continue
        host_values.setdefault(ha.relay, []).append(ha.hosts)

    relay_conflict: set[str] = set()
    relay_hosts_false: set[str] = set()
    for relay, values in host_values.items():
        unique = set(values)
        if len(unique) > 1:
            relay_conflict.add(relay)
        elif unique == {False}:
            relay_hosts_false.add(relay)

    relay_inventory: dict[str, set[str]] = {}
    relay_has_inventory: set[str] = set()
    for ev in inventory_evidence:
        relay = ev.attestation.relay
        if relay not in witness_set:
            continue
        if not verify_inventory_attestation(
            ev.attestation,
            challenge_id=challenge_id,
            witness_pubkey=relay,
            now_ts=now_ts,
            covered_ids=list(ev.covered_ids),
        ):
            continue
        relay_has_inventory.add(relay)
        relay_inventory.setdefault(relay, set()).update(ev.covered_ids)

    tainted: set[str] = set()
    for relay in witness_set:
        if relay in relay_conflict:
            tainted.add(relay)
        elif relay in relay_hosts_false and relay in relay_has_inventory:
            tainted.add(relay)

    denominator: set[str] = set(witness_set)
    for relay in tainted:
        relay_inventory.pop(relay, None)
        relay_hosts_false.discard(relay)
    for relay in list(relay_hosts_false):
        denominator.discard(relay)
        relay_inventory.pop(relay, None)

    denominator_tuple = tuple(sorted(denominator))

    candidate_ids = sorted(set(event_ids) - already_have_ids)
    already_have_tuple = tuple(sorted(set(event_ids) & already_have_ids))

    rejected: list[tuple[str, str]] = []
    accepted: list[str] = []
    admitted_by: dict[str, tuple[str, ...]] = {}

    if not denominator:
        for eid in candidate_ids:
            rejected.append((eid, REASON_DENOMINATOR_ZERO))
        return AdmissionDecision(
            denominator=denominator_tuple,
            accepted=(),
            already_have=already_have_tuple,
            rejected=tuple(rejected),
            admitted_by=admitted_by,
        )

    required = threshold_required(len(denominator), challenge.threshold)

    for eid in candidate_ids:
        witnesses_for_eid: list[str] = []
        for relay in denominator:
            if eid in relay_inventory.get(relay, set()):
                witnesses_for_eid.append(relay)

        if len(witnesses_for_eid) >= required:
            accepted.append(eid)
            admitted_by[eid] = tuple(sorted(witnesses_for_eid))
        else:
            rejected.append((eid, REASON_INSUFFICIENT))

    if remaining_quota is not None and remaining_quota < len(accepted):
        overflow = accepted[remaining_quota:]
        accepted = accepted[:remaining_quota]
        for eid in overflow:
            rejected.append((eid, REASON_QUOTA_EXCEEDED))
            admitted_by.pop(eid, None)

    return AdmissionDecision(
        denominator=denominator_tuple,
        accepted=tuple(accepted),
        already_have=already_have_tuple,
        rejected=tuple(rejected),
        admitted_by=admitted_by,
    )