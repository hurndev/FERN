from __future__ import annotations

import logging
from pathlib import Path

import click

from fern.bft.client import PublishResult, publish_to_validators, sync_from_validators
from fern.bft.store import BFTStore
from fern.bft.validators import ValidatorSet
from fern.events.build import build_event
from fern.events.event import Event
from fern.identity.user import UserIdentity


logger = logging.getLogger(__name__)


def warn_small_validator_set(validator_set: ValidatorSet) -> None:
    if not validator_set.is_small_unanimous:
        return
    validator_count = len(validator_set.validators)
    click.secho(
        "WARNING: This group uses unanimous small-set mode "
        f"(validators={validator_count}, quorum={validator_set.quorum}, f=0). "
        "Every validator must participate; any unavailable validator halts consensus. "
        "Use 4 validators with f=1 for standard BFT operation.",
        fg="yellow",
        bold=True,
        err=True,
    )


def validator_urls(group_info: dict[str, object]) -> list[str]:
    raw = group_info.get("validators", group_info.get("relays", []))
    return [str(value) for value in raw] if isinstance(raw, list) else []


def open_store(path: str | Path) -> BFTStore:
    logger.debug("opening BFT store path=%s", path)
    return BFTStore(path)


async def sync_group(group: str, group_info: dict[str, object], store: BFTStore) -> None:
    result = await sync_from_validators(group=group, urls=validator_urls(group_info), store=store)
    logger.debug(
        "group synchronized group=%s height=%d->%d commits=%d reachable=%d",
        group[:12],
        result.height_before,
        result.height_after,
        result.commits_added,
        result.reachable_validators,
    )
    warn_small_validator_set(store.get_chain_head(group).state.validator_set)


def build_user_event(
    *,
    store: BFTStore,
    group: str,
    user: UserIdentity,
    event_type: str,
    content: dict[str, object],
) -> Event:
    head = store.get_chain_head(group)
    sequence = store.next_pending_sequence(
        group, user.pubkey, head.state.sequences.get(user.pubkey, 0)
    )
    event = build_event(
        type=event_type,
        group=group,
        author_keypair=user.keypair,
        seq=sequence,
        content=content,
    )
    logger.debug(
        "event built group=%s event=%s type=%s author=%s seq=%d",
        group[:12],
        (event.id or "")[:12],
        event.type,
        event.author[:12],
        event.seq,
    )
    return event


async def publish_user_event(
    *,
    store: BFTStore,
    group: str,
    group_info: dict[str, object],
    event: Event,
) -> PublishResult:
    head = store.get_chain_head(group)
    result = await publish_to_validators(
        event=event,
        urls=validator_urls(group_info),
        validator_pubkeys=head.state.validator_set.pubkeys,
        propagation_threshold=head.state.validator_set.propagation_threshold,
    )
    if result.receipts:
        store.add_pending(event, min(receipt.first_seen_ms for receipt in result.receipts))
        logger.debug(
            "pending event cached group=%s event=%s receipts=%d",
            group[:12],
            (event.id or "")[:12],
            len(result.receipts),
        )
    return result


__all__ = [
    "build_user_event",
    "open_store",
    "publish_user_event",
    "sync_group",
    "validator_urls",
    "warn_small_validator_set",
]
