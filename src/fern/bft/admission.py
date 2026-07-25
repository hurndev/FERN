from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from fern.bft.certificates import SyncReady, sign_sync_ready
from fern.bft.client import ClientVerificationError, sync_to_manifest
from fern.bft.manifest import (
    AdmissionRecord,
    HistoryManifest,
    HostingAttestation,
    hosting_threshold_met,
    verify_history_manifest,
    verify_hosting_attestation,
)
from fern.bft.store import BFTStore
from fern.bft.websocket import BFTWebSocketClient, ValidatorStatus
from fern.crypto.keys import Keypair


@dataclass(frozen=True)
class PreparedHistory:
    manifest: HistoryManifest
    attestations: tuple[HostingAttestation, ...]
    admission: AdmissionRecord
    readiness: SyncReady


async def _reachable_statuses(
    group: str, urls: list[str]
) -> list[tuple[BFTWebSocketClient, ValidatorStatus]]:
    clients = [BFTWebSocketClient(url) for url in urls]
    values = await asyncio.gather(
        *(client.status(group) for client in clients), return_exceptions=True
    )
    return [
        (client, value)
        for client, value in zip(clients, values, strict=True)
        if isinstance(value, ValidatorStatus)
    ]


async def prepare_validator_history(
    *,
    group: str,
    urls: list[str],
    store: BFTStore,
    keypair: Keypair,
    trusted_operators: dict[str, str],
    minimum_operators: int = 2,
    maximum_logical_bytes: int | None = None,
    manual: bool = False,
) -> PreparedHistory:
    """Admit, fully verify and persist a prospective validator's history.

    The normal path requires fresh matching attestations from independently
    trusted active validators. ``manual`` is an explicit operator override;
    it never masquerades as normal admission in the persisted provenance.
    """

    statuses = await _reachable_statuses(group, urls)
    if not statuses:
        raise ClientVerificationError("no current validator was reachable")
    by_height: dict[int, set[str]] = {}
    for _client, status in statuses:
        by_height.setdefault(status.height, set()).add(status.block_hash)
    if any(len(hashes) > 1 for hashes in by_height.values()):
        raise ClientVerificationError("current validators expose a conflicting checkpoint")

    source, source_status = max(statuses, key=lambda item: item[1].height)
    manifest = await source.history_manifest(group)
    if not verify_history_manifest(manifest):
        raise ClientVerificationError("source returned an invalid history manifest")
    if not (
        manifest.validator == source_status.validator
        and manifest.chain_id == source_status.chain_id
        and manifest.epoch == source_status.epoch
        and manifest.height == source_status.height
        and manifest.block_hash == source_status.block_hash
        and manifest.history_root == source_status.history_root
        and manifest.state_root == source_status.state_root
        and manifest.logical_bytes == source_status.logical_bytes
    ):
        raise ClientVerificationError("manifest does not match the source's signed status")
    if maximum_logical_bytes is not None and manifest.logical_bytes > maximum_logical_bytes:
        raise ClientVerificationError("history exceeds the local permanent-storage limit")

    attestations: list[HostingAttestation] = []
    for client, status in statuses:
        if status.validator not in trusted_operators:
            continue
        if status.height != manifest.height or status.block_hash != manifest.block_hash:
            continue
        try:
            attestation = await client.hosting_attestation(manifest)
        except Exception:
            continue
        if verify_hosting_attestation(attestation, manifest):
            attestations.append(attestation)

    ordered_attestations = tuple(
        sorted(
            {item.validator: item for item in attestations}.values(),
            key=lambda item: item.validator,
        )
    )
    if not manual and not hosting_threshold_met(
        manifest=manifest,
        attestations=ordered_attestations,
        current_validator_keys=source_status.validator_set.pubkeys,
        trusted_operators=trusted_operators,
        minimum_operators=minimum_operators,
    ):
        raise ClientVerificationError("trusted current-host admission threshold was not met")

    await sync_to_manifest(group=group, urls=urls, store=store, manifest=manifest)
    head = store.get_chain_head(group)
    admission = AdmissionRecord(
        group=group,
        chain_id=head.state.chain_id,
        epoch=head.state.validator_set.epoch,
        manifest_id=manifest.id,
        checkpoint_height=head.height,
        block_hash=head.block_hash,
        history_root=head.history_root,
        logical_bytes=head.logical_bytes,
        admission_class="manual" if manual else "normal",
        witnesses=tuple(item.validator for item in ordered_attestations),
        accepted_at=int(time.time()),
    )
    store.save_admission(admission)
    readiness = sign_sync_ready(
        SyncReady(
            group=group,
            chain_id=head.state.chain_id,
            from_epoch=head.state.validator_set.epoch,
            to_epoch=head.state.validator_set.epoch + 1,
            validator=keypair.pubkey_hex,
            checkpoint_height=head.height,
            checkpoint_block_hash=head.block_hash,
            history_root=head.history_root,
            byte_count=head.logical_bytes,
        ),
        keypair,
    )
    return PreparedHistory(manifest, ordered_attestations, admission, readiness)


__all__ = ["PreparedHistory", "prepare_validator_history"]
