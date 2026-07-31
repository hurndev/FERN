from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from fern.bft.app import ChainHead
from fern.bft.certificates import IngressReceipt, verify_ingress_receipt
from fern.bft.manifest import HistoryManifest, verify_history_manifest
from fern.bft.store import BFTStore
from fern.bft.websocket import BFTWebSocketClient, ValidatorStatus
from fern.events.event import Event


logger = logging.getLogger(__name__)


def _short(value: str, length: int = 12) -> str:
    return value if len(value) <= length else f"{value[:length]}…"


class ClientVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class SyncResult:
    height_before: int
    height_after: int
    commits_added: int
    reachable_validators: int


@dataclass(frozen=True)
class PublishResult:
    event: Event
    receipts: tuple[IngressReceipt, ...]
    errors: tuple[str, ...]
    propagation_confirmed: bool


async def _statuses(
    group: str, clients: list[BFTWebSocketClient]
) -> tuple[list[tuple[BFTWebSocketClient, ValidatorStatus]], list[str]]:
    results = await asyncio.gather(
        *(client.status(group) for client in clients), return_exceptions=True
    )
    statuses: list[tuple[BFTWebSocketClient, ValidatorStatus]] = []
    errors: list[str] = []
    for client, result in zip(clients, results, strict=True):
        if isinstance(result, BaseException):
            errors.append(f"{client.url}: {result}")
            logger.debug("validator status failed url=%s reason=%s", client.url, result)
        else:
            statuses.append((client, result))
            logger.debug(
                "validator status group=%s url=%s validator=%s epoch=%d height=%d block=%s",
                _short(group),
                client.url,
                _short(result.validator),
                result.epoch,
                result.height,
                _short(result.block_hash),
            )
    return statuses, errors


async def sync_from_validators(*, group: str, urls: list[str], store: BFTStore) -> SyncResult:
    if not urls:
        raise ClientVerificationError("no validator URLs configured")
    logger.debug(
        "sync starting group=%s validators=%d local_store=%s",
        _short(group),
        len(urls),
        store.path,
    )
    clients = [BFTWebSocketClient(url) for url in urls]
    statuses, status_errors = await _statuses(group, clients)
    if not statuses:
        raise ClientVerificationError(
            "no validator was reachable" + (f": {status_errors[0]}" if status_errors else "")
        )

    # Two validators claiming different finalized hashes at the same height are
    # cryptographic split evidence. Do not silently select one.
    by_height: dict[int, set[str]] = {}
    for _client, status in statuses:
        by_height.setdefault(status.height, set()).add(status.block_hash)
    conflicting = [height for height, hashes in by_height.items() if len(hashes) > 1]
    if conflicting:
        raise ClientVerificationError(
            f"validators report conflicting finalized blocks at height {min(conflicting)}"
        )

    before = store.get_chain_head(group).height if store.get_genesis(group) is not None else 0
    source, _remote = max(statuses, key=lambda item: item[1].height)
    manifest = await source.history_manifest(group)
    logger.debug(
        "sync source selected group=%s url=%s manifest_height=%d manifest=%s",
        _short(group),
        source.url,
        manifest.height,
        _short(manifest.id),
    )
    local, added = await _sync_to_manifest(
        group=group,
        statuses=statuses,
        store=store,
        manifest=manifest,
    )
    result = SyncResult(
        height_before=before,
        height_after=local.height,
        commits_added=added,
        reachable_validators=len(statuses),
    )
    logger.debug(
        "sync complete group=%s height=%d->%d commits=%d reachable=%d/%d",
        _short(group),
        result.height_before,
        result.height_after,
        result.commits_added,
        result.reachable_validators,
        len(urls),
    )
    return result


async def sync_to_manifest(
    *, group: str, urls: list[str], store: BFTStore, manifest: HistoryManifest
) -> SyncResult:
    clients = [BFTWebSocketClient(url) for url in urls]
    statuses, _errors = await _statuses(group, clients)
    if not statuses:
        raise ClientVerificationError("no validator was reachable")
    before = store.get_chain_head(group).height if store.get_genesis(group) is not None else 0
    local, added = await _sync_to_manifest(
        group=group,
        statuses=statuses,
        store=store,
        manifest=manifest,
    )
    return SyncResult(before, local.height, added, len(statuses))


async def _sync_to_manifest(
    *,
    group: str,
    statuses: list[tuple[BFTWebSocketClient, ValidatorStatus]],
    store: BFTStore,
    manifest: HistoryManifest,
) -> tuple[ChainHead, int]:
    if not verify_history_manifest(manifest) or manifest.group != group:
        raise ClientVerificationError("history manifest is invalid")
    matching = [
        (client, status)
        for client, status in statuses
        if status.chain_id == manifest.chain_id
        and status.height == manifest.height
        and status.block_hash == manifest.block_hash
        and status.history_root == manifest.history_root
        and status.state_root == manifest.state_root
        and status.logical_bytes == manifest.logical_bytes
        and status.validator == manifest.validator
    ]
    if not matching:
        raise ClientVerificationError("no validator serves the manifest checkpoint")
    source, remote = matching[0]
    if manifest.validator not in remote.validator_set.pubkeys:
        raise ClientVerificationError("manifest signer is not active at its checkpoint")

    genesis = store.get_genesis(group)
    if genesis is None:
        candidate = await source.get_genesis(group)
        if candidate is None or candidate.group != group:
            raise ClientVerificationError("validator omitted group genesis")
        store.bootstrap_genesis(candidate)
        logger.debug(
            "genesis verified group=%s source=%s event=%s",
            _short(group),
            source.url,
            _short(candidate.id or ""),
        )

    local = store.get_chain_head(group)
    if local.height > manifest.height:
        raise ClientVerificationError("local history is ahead of the requested checkpoint")
    next_height = local.height + 1
    added = 0
    while next_height <= manifest.height:
        commits = await source.get_commits(group, next_height)
        if not commits:
            raise ClientVerificationError(f"validator omitted commit at height {next_height}")
        logger.debug(
            "commit page received group=%s source=%s from_height=%d count=%d target=%d",
            _short(group),
            source.url,
            next_height,
            len(commits),
            manifest.height,
        )
        for commit in commits:
            if commit.block.height != next_height:
                raise ClientVerificationError("validator returned non-contiguous commits")
            store.save_commit(group, commit)
            logger.debug(
                "commit verified group=%s height=%d round=%d block=%s events=%d",
                _short(group),
                commit.block.height,
                commit.round,
                _short(commit.block.id),
                len(commit.block.candidate.all_events),
            )
            next_height += 1
            added += 1
            if next_height > manifest.height:
                break

    local = store.get_chain_head(group)
    if not (
        local.height == manifest.height
        and local.block_hash == manifest.block_hash
        and local.history_root == manifest.history_root
        and local.state.root == manifest.state_root
        and local.logical_bytes == manifest.logical_bytes
        and store.event_count(group) + 1 == manifest.event_count
    ):
        raise ClientVerificationError("verified history does not match the manifest")
    genesis = store.get_genesis(group)
    assert genesis is not None and genesis.id is not None
    verified_hashes = {0: genesis.id}
    verified_hashes.update(
        (commit.block.height, commit.block.id) for commit in store.commits(group)
    )
    for _client, status in statuses:
        if status.chain_id != local.state.chain_id or (
            status.height <= local.height
            and verified_hashes.get(status.height) != status.block_hash
        ):
            raise ClientVerificationError("validator exposes a conflicting finalized checkpoint")
    return local, added


async def publish_to_validators(
    *, event: Event, urls: list[str], validator_pubkeys: frozenset[str], propagation_threshold: int
) -> PublishResult:
    logger.debug(
        "publish starting group=%s event=%s type=%s seq=%d validators=%d threshold=%d",
        _short(event.group),
        _short(event.id or ""),
        event.type,
        event.seq,
        len(urls),
        propagation_threshold,
    )
    clients = [BFTWebSocketClient(url) for url in urls]
    results = await asyncio.gather(
        *(client.submit_event(event) for client in clients), return_exceptions=True
    )
    receipts: dict[str, IngressReceipt] = {}
    errors: list[str] = []
    for client, result in zip(clients, results, strict=True):
        if isinstance(result, BaseException):
            errors.append(f"{client.url}: {result}")
            logger.debug(
                "publish rejected group=%s event=%s url=%s reason=%s",
                _short(event.group),
                _short(event.id or ""),
                client.url,
                result,
            )
            continue
        if not (
            verify_ingress_receipt(result)
            and result.event_id == event.id
            and result.group == event.group
            and result.validator in validator_pubkeys
        ):
            errors.append(f"{client.url}: invalid ingress receipt")
            logger.warning(
                "invalid ingress receipt group=%s event=%s url=%s",
                _short(event.group),
                _short(event.id or ""),
                client.url,
            )
            continue
        receipts[result.validator] = result
        logger.debug(
            "ingress receipt verified group=%s event=%s url=%s validator=%s first_seen_ms=%d",
            _short(event.group),
            _short(event.id or ""),
            client.url,
            _short(result.validator),
            result.first_seen_ms,
        )
    ordered = tuple(receipts[key] for key in sorted(receipts))
    publish_result = PublishResult(
        event=event,
        receipts=ordered,
        errors=tuple(errors),
        propagation_confirmed=len(ordered) >= propagation_threshold,
    )
    logger.debug(
        "publish complete group=%s event=%s receipts=%d/%d propagation_confirmed=%s errors=%d",
        _short(event.group),
        _short(event.id or ""),
        len(ordered),
        len(urls),
        publish_result.propagation_confirmed,
        len(errors),
    )
    return publish_result


__all__ = [
    "ClientVerificationError",
    "PublishResult",
    "SyncResult",
    "publish_to_validators",
    "sync_from_validators",
    "sync_to_manifest",
]
