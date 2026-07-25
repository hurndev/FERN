from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import click

from cli.bft import open_store, sync_group, warn_small_validator_set
from cli.config import get_cache_path, load_config, resolve_group
from fern.bft.blocks import Commit
from fern.bft.store import BFTStore
from fern.events.event import Event


def _short(value: str, *, full: bool) -> str:
    return value if full or len(value) <= 20 else f"{value[:16]}..."


def _time(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "unknown"
    return (
        datetime.fromtimestamp(milliseconds / 1000, UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z"
    )


def _bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if amount < 1024 or unit == "GiB":
            return f"{int(amount)} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    raise AssertionError("unreachable")


def _preview(value: object, limit: int = 90) -> str:
    text = str(value).replace("\n", "\\n")
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def _event_detail(event: Event) -> str:
    if event.type == "chat.message":
        return f"text={_preview(event.content.get('text', ''))!r}"
    for key in ("target", "invitee", "nickname", "name"):
        if key in event.content:
            return f"{key}={_preview(event.content[key])!r}"
    if event.type == "validator_update":
        validators = event.content.get("validators", [])
        count = len(validators) if isinstance(validators, list) else "?"
        return f"validators={count} f={event.content.get('fault_tolerance', '?')}"
    return ""


def _event_line(
    event: Event,
    *,
    position: str,
    certified_ms: int | None,
    full_ids: bool,
    governance: bool = False,
) -> str:
    detail = _event_detail(event)
    suffix = f"  {detail}" if detail else ""
    role = " governance" if governance else ""
    return (
        f"    {position:<9} {event.type}{role}  author={_short(event.author, full=full_ids)} "
        f"seq={event.seq}  time={_time(certified_ms)}  "
        f"id={_short(event.id or '', full=full_ids)}{suffix}"
    )


def _render_commit(commit: Commit, *, show_events: bool, full_ids: bool) -> None:
    block = commit.block
    candidate = block.candidate
    times = block.certified_times_ms
    time_summary = (
        _time(times[0]) if len(times) == 1 else f"{_time(min(times))} to {_time(max(times))}"
    )
    click.echo(
        f"  Block {block.height}  round={commit.round}  epoch={block.epoch}  "
        f"events={len(candidate.all_events)}  observations={len(block.observations)}  "
        f"precommits={len(commit.precommits)}  "
        f"certified={time_summary}"
    )
    click.echo(f"    Hash: {_short(block.id, full=full_ids)}")
    click.echo(f"    Previous: {_short(candidate.previous_block_hash, full=full_ids)}")
    click.echo(f"    Proposer: {_short(candidate.proposer, full=full_ids)}")
    if not show_events:
        return
    ordinary_count = len(candidate.events)
    for position, (event, certified_ms) in enumerate(zip(candidate.all_events, times, strict=True)):
        click.echo(
            _event_line(
                event,
                position=f"{block.height}:{position}",
                certified_ms=certified_ms,
                full_ids=full_ids,
                governance=position >= ordinary_count,
            )
        )


def render_group(
    store: BFTStore,
    group: str,
    *,
    limit: int,
    show_events: bool,
    show_pending: bool,
    full_ids: bool,
    show_warning: bool = True,
) -> None:
    head = store.get_chain_head(group)
    state = head.state
    validator_set = state.validator_set
    if show_warning:
        warn_small_validator_set(validator_set)

    finalized_count = store.event_count(group)
    total_count = store.event_count(group, finalized_only=False)
    pending_count = total_count - finalized_count
    mode = "UNANIMOUS SMALL SET" if validator_set.is_small_unanimous else "standard BFT"

    click.echo(f"Chain: {state.metadata.get('name', 'Unnamed group')}")
    click.echo(f"  Group: {group}")
    click.echo(f"  Chain ID: {state.chain_id}")
    click.echo(f"  Height: {head.height}")
    click.echo(f"  Head block: {head.block_hash}")
    click.echo(f"  State root: {state.root}")
    click.echo(f"  History root: {head.history_root}")
    click.echo(f"  Logical history: {_bytes(head.logical_bytes)} ({head.logical_bytes} bytes)")
    click.echo(
        f"  Consensus: {mode}; epoch={validator_set.epoch}; "
        f"validators={len(validator_set.validators)}; quorum={validator_set.quorum}; "
        f"f={validator_set.fault_tolerance}"
    )
    click.echo(f"  Events: {finalized_count} finalized; {pending_count} pending")
    next_height = head.height + 1
    safety = store.load_safety_state(
        group,
        state.chain_id,
        validator_set.epoch,
        next_height,
    )
    if safety is None:
        waiting = "; pending events are waiting for the next block" if pending_count else ""
        click.echo(f"  Local consensus journal: none for height {next_height}{waiting}")
    else:
        locked = _short(safety.locked_block_id or "none", full=full_ids)
        valid_round = safety.valid_round if safety.valid_round is not None else "none"
        click.echo(
            f"  Local consensus journal: height={next_height}; round={safety.round}; "
            f"locked={locked}; valid_round={valid_round}"
        )
    click.echo("  Validators:")
    for index, validator in enumerate(validator_set.validators, 1):
        operator = f" ({validator.operator})" if validator.operator else ""
        click.echo(
            f"    {index}. {_short(validator.pubkey, full=full_ids)}  {validator.url}{operator}"
        )

    commits = store.commits(group)
    selected = commits[-limit:] if limit else commits
    if not commits:
        click.echo("  Blocks: none after genesis")
    else:
        click.echo(f"  Blocks: showing {len(selected)} of {len(commits)}")
        for commit in selected:
            _render_commit(commit, show_events=show_events, full_ids=full_ids)

    if not show_pending:
        return
    pending = store.pending_events(group, limit=max(pending_count, 1))
    if not pending:
        click.echo("  Pending events: none")
        return
    selected_pending = pending[-limit:] if limit else pending
    click.echo(f"  Pending events: showing {len(selected_pending)} of {pending_count}")
    for event in selected_pending:
        click.echo(
            _event_line(
                event,
                position="pending",
                certified_ms=store.first_seen_ms(event.id or ""),
                full_ids=full_ids,
            )
        )


def _select_database_groups(store: BFTStore, selector: str | None) -> tuple[str, ...]:
    groups = store.hosted_groups()
    if selector is None:
        return groups
    if selector.isdigit():
        index = int(selector) - 1
        if 0 <= index < len(groups):
            return (groups[index],)
    exact = tuple(group for group in groups if group == selector)
    if exact:
        return exact
    prefixes = tuple(group for group in groups if group.startswith(selector))
    if len(prefixes) == 1:
        return prefixes
    names = tuple(
        group
        for group in groups
        if store.get_chain_head(group).state.metadata.get("name") == selector
    )
    if len(names) == 1:
        return names
    if len(prefixes) > 1 or len(names) > 1:
        raise click.ClickException(f"group selector is ambiguous: {selector}")
    raise click.ClickException(f"group is not present in this database: {selector}")


def inspect_database(
    db_path: Path,
    group_selector: str | None,
    *,
    limit: int,
    show_events: bool,
    show_pending: bool,
    full_ids: bool,
) -> None:
    store = BFTStore(db_path)
    try:
        groups = _select_database_groups(store, group_selector)
        if not groups:
            click.echo("No FERN-BFT groups in this database.")
            return
        for index, group in enumerate(groups):
            if index:
                click.echo()
            render_group(
                store,
                group,
                limit=limit,
                show_events=show_events,
                show_pending=show_pending,
                full_ids=full_ids,
            )
    finally:
        store.close()


async def _inspect_configured_group(
    group_id: str,
    *,
    no_sync: bool,
    limit: int,
    show_events: bool,
    show_pending: bool,
    full_ids: bool,
) -> None:
    config = load_config()
    try:
        group, info = resolve_group(group_id, config)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    path = Path(str(info.get("cache_path") or get_cache_path(group)))
    if no_sync and not path.exists():
        raise click.ClickException(f"local cache does not exist: {path}")
    store = open_store(path)
    try:
        if not no_sync:
            await sync_group(group, info, store)
        render_group(
            store,
            group,
            limit=limit,
            show_events=show_events,
            show_pending=show_pending,
            full_ids=full_ids,
            show_warning=no_sync,
        )
    finally:
        store.close()


@click.command()
@click.argument("group_id", required=False)
@click.option(
    "--db",
    "db_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Inspect a local client cache or validator database instead of syncing a configured group.",
)
@click.option(
    "-n",
    "--limit",
    type=click.IntRange(min=0),
    default=10,
    show_default=True,
    help="Maximum recent blocks and pending events to show; 0 shows all.",
)
@click.option("--events/--no-events", "show_events", default=True, show_default=True)
@click.option("--pending/--no-pending", "show_pending", default=True, show_default=True)
@click.option("--full-ids", is_flag=True, help="Print full event, validator, and block IDs.")
@click.option("--no-sync", is_flag=True, help="Inspect the configured local cache without syncing.")
def command(
    group_id: str | None,
    db_path: Path | None,
    limit: int,
    show_events: bool,
    show_pending: bool,
    full_ids: bool,
    no_sync: bool,
) -> None:
    """Inspect finalized blocks, consensus evidence, and pending events.

    GROUP_ID is a configured group number or public key. With --db it may also
    be a database group number, unique public-key prefix, or exact group name.
    Omitting GROUP_ID with --db displays every group in that database.
    """

    if db_path is not None:
        inspect_database(
            db_path,
            group_id,
            limit=limit,
            show_events=show_events,
            show_pending=show_pending,
            full_ids=full_ids,
        )
        return
    if group_id is None:
        raise click.UsageError("GROUP_ID is required unless --db is supplied.")
    asyncio.run(
        _inspect_configured_group(
            group_id,
            no_sync=no_sync,
            limit=limit,
            show_events=show_events,
            show_pending=show_pending,
            full_ids=full_ids,
        )
    )


__all__ = ["command", "inspect_database", "render_group"]
