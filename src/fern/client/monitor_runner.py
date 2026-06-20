from __future__ import annotations

import time
from collections.abc import Mapping

from fern.completeness.attestations import Attestation
from fern.completeness.monitor import monitor_pass, MonitorResult
from fern.completeness.receipts import Receipt
from fern.completeness.trust_ledger import TrustLedger, Fault
from fern.transport.interfaces import RelayTransport


async def run_monitor_pass(
    *,
    relay: RelayTransport,
    attestation: Attestation,
    local_known_set: frozenset[str],
    receipts_for_relay: Mapping[str, Receipt],
    trust_ledger: TrustLedger,
    sibling_attestations: Mapping[str, Attestation],
) -> MonitorResult:
    prev_entry = trust_ledger.entries.get(relay.relay_pubkey)
    result = monitor_pass(
        local_known_set=local_known_set,
        local_receipts_for_relay=receipts_for_relay,
        new_attestation=attestation,
        prev_attestation=prev_entry.last_attestation if prev_entry else None,
        relay_pubkey=relay.relay_pubkey,
        sibling_attestations=sibling_attestations,
        now_ts=int(time.time()),
    )

    for fault in result.faults:
        trust_ledger.add_fault(relay.relay_pubkey, fault)

    trust_ledger.update_attestation(relay.relay_pubkey, attestation)

    if not result.in_sync:
        faults_from_investigation = await _investigate_missing_events(
            relay=relay,
            candidates=result.candidates_to_check,
            receipts_for_relay=receipts_for_relay,
            trust_ledger=trust_ledger,
        )
        for fault in faults_from_investigation:
            trust_ledger.add_fault(relay.relay_pubkey, fault)
        all_faults = list(result.faults) + faults_from_investigation
        return MonitorResult(
            in_sync=False,
            faults=tuple(all_faults),
            divergent_relays=result.divergent_relays,
            candidates_to_check=result.candidates_to_check,
        )

    return result


async def _investigate_missing_events(
    *,
    relay: RelayTransport,
    candidates: tuple[str, ...],
    receipts_for_relay: Mapping[str, Receipt],
    trust_ledger: TrustLedger,
) -> list[Fault]:
    faults: list[Fault] = []
    now_ts = int(time.time())

    for event_id in candidates:
        event = await relay.get(event_id)
        if event is not None:
            continue

        receipt = receipts_for_relay.get(event_id)
        relay_pk = relay.relay_pubkey
        if receipt is not None:
            faults.append(
                Fault(
                    ts=now_ts,
                    kind="missing_event_with_receipt",
                    event_id=event_id,
                    evidence=(
                        f"Relay {relay_pk[:16]}... attestation omits "
                        f"event {event_id[:16]}... despite signed receipt"
                    ),
                )
            )
        else:
            faults.append(
                Fault(
                    ts=now_ts,
                    kind="missing_event_no_receipt",
                    event_id=event_id,
                    evidence=(
                        f"Relay {relay_pk[:16]}... attestation omits event {event_id[:16]}..."
                    ),
                )
            )

    return faults
