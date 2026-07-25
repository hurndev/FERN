from __future__ import annotations

import asyncio
import json
import logging
import secrets
from pathlib import Path
from typing import Any

import click

from cli.bft import (
    build_user_event,
    open_store,
    publish_user_event,
    sync_group,
    validator_urls,
    warn_small_validator_set,
)
from cli.config import (
    add_group_to_order,
    ensure_config_dir,
    get_cache_path,
    load_config,
    parse_group_address,
    resolve_group,
    save_config,
)
from cli.output import print_success
from fern.bft.validators import Validator, make_validator_set
from fern.bft.certificates import SyncReady, verify_sync_ready
from fern.bft.constants import PROTOCOL_VERSION
from fern.bft.websocket import BFTWebSocketClient
from fern.crypto.hashes import random_channel_id
from fern.events.build import build_event
from fern.events.types import ChatTypes, ProtocolTypes
from fern.identity.group import GroupKeypair
from fern.identity.user import UserIdentity


DEFAULT_VALIDATOR = "ws://localhost:8765"
logger = logging.getLogger(__name__)


def _user(config: dict[str, Any]) -> UserIdentity:
    private = config.get("user_privkey_hex")
    if not private:
        raise click.UsageError("No identity found. Run `fern init` first.")
    return UserIdentity.from_privkey_hex(str(private))


def _normalize_url(url: str) -> str:
    return url if url.startswith(("ws://", "wss://")) else f"ws://{url}"


async def _discover_validators(
    urls: list[str], faults: int | None
) -> tuple[list[str], int, list[Validator]]:
    normalized = list(dict.fromkeys(_normalize_url(url) for url in urls))
    if not normalized:
        normalized = [DEFAULT_VALIDATOR]
    discovered: list[Validator] = []
    for url in normalized:
        logger.debug("discovering validator url=%s", url)
        try:
            metadata = await BFTWebSocketClient(url).metadata()
        except Exception as exc:
            raise click.ClickException(f"cannot reach validator {url}: {exc}") from exc
        pubkey = str(metadata.get("pubkey", ""))
        operator = str(metadata.get("name", url))
        if metadata.get("protocol") != PROTOCOL_VERSION or metadata.get("role") != "validator":
            raise click.ClickException(f"endpoint is not a {PROTOCOL_VERSION} validator: {url}")
        try:
            discovered.append(Validator(pubkey=pubkey, url=url, operator=operator))
        except ValueError as exc:
            raise click.ClickException(f"invalid validator metadata from {url}: {exc}") from exc
        logger.debug(
            "validator discovered url=%s pubkey=%s operator=%s",
            url,
            pubkey[:12],
            operator,
        )
    inferred = 0 if len(discovered) == 1 else (len(discovered) - 1) // 3
    selected_faults = inferred if faults is None else faults
    try:
        validator_set = make_validator_set(discovered, epoch=0, fault_tolerance=selected_faults)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    return normalized, selected_faults, list(validator_set.validators)


@click.group(name="group")
def command() -> None:
    """Create, join and administer FERN-BFT groups."""


@command.command()
@click.option("--name", required=True)
@click.option("--description", default="")
@click.option("--public/--private", default=True)
@click.option(
    "--validator",
    "validators",
    multiple=True,
    help="Validator URL; use 1-3 unanimous test validators or exactly 3f+1 standard validators.",
)
@click.option("--faults", type=int, default=None, help="Byzantine validators to tolerate.")
def create(
    name: str, description: str, public: bool, validators: tuple[str, ...], faults: int | None
) -> None:
    asyncio.run(_create(name, description, public, list(validators), faults))


async def _create(
    name: str, description: str, public: bool, urls: list[str], faults: int | None
) -> None:
    config = load_config()
    user = _user(config)
    urls, faults, validators = await _discover_validators(urls, faults)
    group_key = GroupKeypair.generate()
    channel_id = random_channel_id()
    genesis = build_event(
        type=ProtocolTypes.GENESIS,
        group=group_key.pubkey,
        author_keypair=user.keypair,
        seq=0,
        group_keypair=group_key.keypair,
        content={
            "chain_id": secrets.token_hex(32),
            "name": name,
            "description": description,
            "public": public,
            "founder": user.pubkey,
            "admins": [user.pubkey],
            "validators": [validator.to_dict() for validator in validators],
            "fault_tolerance": faults,
            "app": "chat",
            "chat.channels": [
                {"id": channel_id, "name": "general", "description": "", "position": 0}
            ],
            "chat.default_channel": channel_id,
            "chat.system_channel": channel_id,
        },
    )
    logger.debug(
        "genesis built group=%s chain=%s validators=%d faults=%d",
        group_key.pubkey[:12],
        str(genesis.content["chain_id"])[:12],
        len(validators),
        faults,
    )
    results = await asyncio.gather(
        *(BFTWebSocketClient(url).bootstrap(genesis) for url in urls), return_exceptions=True
    )
    failures = [
        f"{url}: {result}"
        for url, result in zip(urls, results, strict=True)
        if isinstance(result, BaseException)
    ]
    if failures:
        raise click.ClickException("genesis was not accepted by every validator: " + failures[0])
    logger.debug(
        "genesis accepted group=%s validators=%d/%d",
        group_key.pubkey[:12],
        len(results),
        len(urls),
    )

    ensure_config_dir()
    cache_path = str(get_cache_path(group_key.pubkey))
    store = open_store(cache_path)
    try:
        local_head = store.bootstrap_genesis(genesis)
        warn_small_validator_set(local_head.state.validator_set)
    finally:
        store.close()
    config.setdefault("groups", {})[group_key.pubkey] = {
        "validators": urls,
        "cache_path": cache_path,
        "joined": True,
    }
    number = add_group_to_order(group_key.pubkey, config)
    save_config(config)
    print_success(f"Group {number} created with {len(validators)} validator(s).")
    click.echo(f"  Name: {name}")
    click.echo(f"  Address: fern:{group_key.pubkey}@{','.join(urls)}")
    click.echo(f"  Pubkey: {group_key.pubkey}")


@command.command()
@click.argument("address")
def join(address: str) -> None:
    asyncio.run(_join(address))


async def _join(address: str) -> None:
    config = load_config()
    user = _user(config)
    group, urls = parse_group_address(address)
    if not urls:
        raise click.UsageError("The group address must contain validator URLs.")
    cache_path = str(get_cache_path(group))
    group_info: dict[str, object] = {"validators": urls, "cache_path": cache_path}
    ensure_config_dir()
    store = open_store(cache_path)
    try:
        await sync_group(group, group_info, store)
        head = store.get_chain_head(group)
        event = build_user_event(
            store=store, group=group, user=user, event_type=ProtocolTypes.JOIN, content={}
        )
        result = await publish_user_event(
            store=store, group=group, group_info=group_info, event=event
        )
        if not result.receipts:
            raise click.ClickException("no validator accepted the join event")
        authoritative_urls = [validator.url for validator in head.state.validator_set.validators]
    finally:
        store.close()
    config.setdefault("groups", {})[group] = {
        "validators": authoritative_urls,
        "cache_path": cache_path,
        "joined": True,
    }
    number = add_group_to_order(group, config)
    save_config(config)
    print_success(
        f"Join submitted for group {number}: {head.state.metadata.get('name', 'Unnamed')}"
    )
    click.echo(f"  Status: pending ({len(result.receipts)} ingress receipt(s))")
    click.echo(f"  Pubkey: {group}")


@command.command(name="list")
def list_groups() -> None:
    config = load_config()
    order = config.get("group_order", [])
    groups = config.get("groups", {})
    if not order:
        click.echo("No groups configured.")
        return
    has_small_set = False
    for index, group in enumerate(order, 1):
        info = groups.get(group, {})
        urls = validator_urls(info)
        small_label = "  [UNANIMOUS SMALL SET]" if 0 < len(urls) < 4 else ""
        has_small_set = has_small_set or bool(small_label)
        click.echo(
            f"  {index}: {group[:16]}...  {urls[0] if urls else '(no validators)'}{small_label}"
        )
    if has_small_set:
        click.secho(
            "WARNING: Small-set groups require every validator to participate; "
            "any unavailable validator halts consensus.",
            fg="yellow",
            bold=True,
            err=True,
        )


async def _synced(group_id: str) -> tuple[dict[str, Any], str, dict[str, Any], Any]:
    config = load_config()
    group, info = resolve_group(group_id, config)
    path = str(info.get("cache_path") or get_cache_path(group))
    store = open_store(path)
    await sync_group(group, info, store)
    current_urls = [
        validator.url for validator in store.get_chain_head(group).state.validator_set.validators
    ]
    if info.get("validators") != current_urls:
        info["validators"] = current_urls
        save_config(config)
    return config, group, info, store


@command.command()
@click.argument("group_id")
def info(group_id: str) -> None:
    asyncio.run(_info(group_id))


async def _info(group_id: str) -> None:
    _config, group, info, store = await _synced(group_id)
    try:
        head = store.get_chain_head(group)
        state = head.state
        click.echo(f"Group: {state.metadata.get('name', 'Unnamed')}")
        click.echo(f"  Description: {state.metadata.get('description', '')}")
        click.echo(f"  Public: {state.public}")
        click.echo(f"  Height: {head.height}")
        click.echo(f"  Epoch: {state.validator_set.epoch}")
        click.echo(f"  Fault tolerance: {state.validator_set.fault_tolerance}")
        click.echo(f"  Validators: {len(state.validator_set.validators)}")
        click.echo(f"  Joined members: {len(state.joined)}")
        click.echo(f"  Invite: fern:{group}@{','.join(validator_urls(info))}")
    finally:
        store.close()


@command.command()
@click.argument("group_id")
def members(group_id: str) -> None:
    asyncio.run(_members(group_id))


async def _members(group_id: str) -> None:
    _config, group, _info, store = await _synced(group_id)
    try:
        head = store.get_chain_head(group)
        nicknames: dict[str, str] = {}
        for event, _height, _position, _time in store.finalized_events(group):
            if event.type == ChatTypes.NICKNAME_SET:
                nicknames[event.author] = str(event.content["nickname"])
        click.echo(f"Members ({len(head.state.joined)}):")
        for pubkey in sorted(head.state.joined):
            role = "admin" if pubkey in head.state.admins else "member"
            nickname = f" ({nicknames[pubkey]})" if pubkey in nicknames else ""
            click.echo(f"  {pubkey}{nickname}  {role}")
    finally:
        store.close()


async def _publish(
    group_id: str, event_type: str, content: dict[str, object], success: str
) -> None:
    config, group, info, store = await _synced(group_id)
    user = _user(config)
    try:
        event = build_user_event(
            store=store,
            group=group,
            user=user,
            event_type=event_type,
            content=content,
        )
        result = await publish_user_event(store=store, group=group, group_info=info, event=event)
    finally:
        store.close()
    if not result.receipts:
        raise click.ClickException(
            result.errors[0] if result.errors else "no validator accepted event"
        )
    print_success(success)
    click.echo(f"  Status: pending ({len(result.receipts)} ingress receipt(s))")
    click.echo(f"  Event ID: {event.id}")


@command.command()
@click.argument("group_id")
def leave(group_id: str) -> None:
    asyncio.run(_publish(group_id, ProtocolTypes.LEAVE, {}, f"Leave submitted for {group_id}."))


@command.command()
@click.argument("group_id")
@click.argument("target_pubkey")
def kick(group_id: str, target_pubkey: str) -> None:
    asyncio.run(
        _publish(group_id, ProtocolTypes.KICK, {"target": target_pubkey}, "Kick submitted.")
    )


@command.command()
@click.argument("group_id")
@click.argument("target_pubkey")
@click.option("--until", type=int, default=None)
@click.option("--reason", default="")
def ban(group_id: str, target_pubkey: str, until: int | None, reason: str) -> None:
    asyncio.run(
        _publish(
            group_id,
            ProtocolTypes.BAN,
            {"target": target_pubkey, "until": until, "reason": reason},
            "Ban submitted.",
        )
    )


@command.command()
@click.argument("group_id")
@click.argument("target_pubkey")
def unban(group_id: str, target_pubkey: str) -> None:
    asyncio.run(
        _publish(group_id, ProtocolTypes.UNBAN, {"target": target_pubkey}, "Unban submitted.")
    )


@command.command()
@click.argument("group_id")
@click.argument("invitee_pubkey")
def invite(group_id: str, invitee_pubkey: str) -> None:
    asyncio.run(
        _publish(
            group_id,
            ProtocolTypes.INVITE,
            {"invitee": invitee_pubkey, "role": "member"},
            "Invite submitted.",
        )
    )


@command.command(name="admin-add")
@click.argument("group_id")
@click.argument("target_pubkey")
def admin_add_cmd(group_id: str, target_pubkey: str) -> None:
    asyncio.run(
        _publish(
            group_id, ProtocolTypes.ADMIN_ADD, {"target": target_pubkey}, "Promotion submitted."
        )
    )


@command.command(name="admin-remove")
@click.argument("group_id")
@click.argument("target_pubkey")
def admin_remove_cmd(group_id: str, target_pubkey: str) -> None:
    asyncio.run(
        _publish(
            group_id, ProtocolTypes.ADMIN_REMOVE, {"target": target_pubkey}, "Demotion submitted."
        )
    )


@command.command()
@click.argument("group_id")
@click.argument("name")
def nickname(group_id: str, name: str) -> None:
    asyncio.run(
        _publish(group_id, ChatTypes.NICKNAME_SET, {"nickname": name}, "Nickname submitted.")
    )


@command.command(name="validator-update")
@click.argument("group_id")
@click.argument("urls", nargs=-1, required=True)
@click.option("--faults", type=int, default=None)
@click.option(
    "--readiness",
    "readiness_paths",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="SyncReady JSON from each newly added validator.",
)
def validator_update(
    group_id: str,
    urls: tuple[str, ...],
    faults: int | None,
    readiness_paths: tuple[Path, ...],
) -> None:
    """Replace the validator set after any new validators prepare history."""

    asyncio.run(_validator_update(group_id, list(urls), faults, list(readiness_paths)))


async def _validator_update(
    group_id: str, urls: list[str], faults: int | None, readiness_paths: list[Path]
) -> None:
    config, group, info, store = await _synced(group_id)
    user = _user(config)
    try:
        head = store.get_chain_head(group)
        normalized, selected_faults, validators = await _discover_validators(urls, faults)
        next_set = make_validator_set(
            validators,
            epoch=head.state.validator_set.epoch + 1,
            fault_tolerance=selected_faults,
        )
        warn_small_validator_set(next_set)
        added = next_set.pubkeys - head.state.validator_set.pubkeys
        readiness: list[SyncReady] = []
        for path in readiness_paths:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise click.ClickException(f"readiness file is not an object: {path}")
            readiness.append(SyncReady.from_dict(raw))
        if {ready.validator for ready in readiness} != added:
            raise click.ClickException(
                "readiness files must cover exactly the newly added validators"
            )
        for ready in readiness:
            if not (
                verify_sync_ready(ready)
                and ready.group == group
                and ready.chain_id == head.state.chain_id
                and ready.from_epoch == head.state.validator_set.epoch
                and ready.to_epoch == next_set.epoch
                and ready.checkpoint_height == head.height
                and ready.checkpoint_block_hash == head.block_hash
                and ready.history_root == head.history_root
                and ready.byte_count == head.logical_bytes
            ):
                raise click.ClickException(
                    f"SyncReady from {ready.validator[:16]}... does not match the current checkpoint"
                )
        event = build_user_event(
            store=store,
            group=group,
            user=user,
            event_type=ProtocolTypes.VALIDATOR_UPDATE,
            content={
                "validators": [validator.to_dict() for validator in next_set.validators],
                "fault_tolerance": selected_faults,
                "readiness": [ready.to_dict() for ready in readiness],
            },
        )
        result = await publish_user_event(store=store, group=group, group_info=info, event=event)
    finally:
        store.close()
    if not result.receipts:
        raise click.ClickException(
            result.errors[0] if result.errors else "no validator accepted the update"
        )
    print_success("Validator-set update submitted.")
    click.echo(f"  Status: pending ({len(result.receipts)} ingress receipt(s))")
    click.echo(f"  Event ID: {event.id}")
    click.echo(f"  Proposed endpoints: {', '.join(normalized)}")
