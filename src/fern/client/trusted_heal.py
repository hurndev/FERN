from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from fern.completeness.heal_attestations import (
    GroupHostAttestation,
    InventoryAttestation,
)
from fern.events.event import Event
from fern.transport.interfaces import RelayTransport


logger = logging.getLogger("fern.client.trusted_heal")


@dataclass(frozen=True)
class TrustedHealResult:
    stored: tuple[str, ...] = ()
    already_have: tuple[str, ...] = ()
    rejected_ids: tuple[str, ...] = ()
    fell_back: bool = False
    error: str = ""


WitnessConnector = Callable[[str, str], Awaitable[RelayTransport | None]]


async def trusted_heal_missing(
    *,
    target_relay: RelayTransport,
    group: str,
    to_heal: Sequence[Event],
    existing_witness_transports: Mapping[str, RelayTransport],
    connect_witness: WitnessConnector,
    fast_heal_min_events: int = 3,
) -> TrustedHealResult:
    """Attempt fast trusted-heal on a single relay. Falls back to slow heal on any failure.

    Returns TrustedHealResult with fell_back=True if the caller should slow-heal
    the rejected_ids (or all ids if the challenge itself failed).
    """
    if len(to_heal) < fast_heal_min_events:
        return TrustedHealResult(fell_back=True)

    event_ids = sorted({e.id for e in to_heal if e.id is not None})
    if not event_ids:
        return TrustedHealResult()

    try:
        challenge = await target_relay.get_heal_challenge(group, event_ids)
    except Exception as e:
        logger.debug("get_heal_challenge failed: %s", e)
        return TrustedHealResult(fell_back=True, error=str(e))

    if not challenge.trusted_witnesses:
        return TrustedHealResult(fell_back=True)

    temp_transports: list[RelayTransport] = []
    witness_transports: dict[str, RelayTransport] = dict(existing_witness_transports)

    try:
        for w in challenge.trusted_witnesses:
            if w.relay not in witness_transports:
                t = await connect_witness(w.url, w.relay)
                if t is not None:
                    witness_transports[w.relay] = t
                    temp_transports.append(t)

        host_atts: list[GroupHostAttestation] = []
        inv_atts: list[tuple[InventoryAttestation, Sequence[str]]] = []

        for w in challenge.trusted_witnesses:
            transport = witness_transports.get(w.relay)
            if transport is None:
                continue
            try:
                ha = await asyncio.wait_for(
                    transport.get_group_host_attestation(challenge), timeout=10
                )
                if ha is not None:
                    host_atts.append(ha)
            except Exception as e:
                logger.debug("host attestation from %s... failed: %s", w.relay[:16], e)

            if ha is not None and not ha.hosts:
                continue

            try:
                inv = await asyncio.wait_for(
                    transport.get_inventory_attestation(challenge, event_ids), timeout=15
                )
                if inv.attestation is not None:
                    inv_atts.append((inv.attestation, inv.covered))
            except Exception as e:
                logger.debug("inventory attestation from %s... failed: %s", w.relay[:16], e)

        try:
            result = await target_relay.heal_batch(
                challenge=challenge,
                events=to_heal,
                group_host_attestations=host_atts,
                inventory_attestations=inv_atts,
            )
        except Exception as e:
            logger.debug("heal_batch failed: %s", e)
            return TrustedHealResult(fell_back=True, error=str(e))

        rejected_ids = tuple(eid for eid, reason in result.rejected)
        return TrustedHealResult(
            stored=result.stored,
            already_have=result.already_have,
            rejected_ids=rejected_ids,
        )
    finally:
        for t in temp_transports:
            try:
                await t.close()
            except Exception:
                pass