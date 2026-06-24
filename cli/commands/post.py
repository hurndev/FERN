from __future__ import annotations

import asyncio
import time

import click

from fern.identity.user import UserIdentity
from fern.chat.messages import build_chat_message
from fern.storage.sqlite_store import SqliteStore
from fern.state.machine import compute_accepted_heads, derive_group_state
from fern.state.types import GroupState
from cli.sync import sync_group_from_transports
from fern.client.sync import HealMode
from cli.config import (
    load_config,
    save_config,
    ensure_config_dir,
    get_cache_path,
    resolve_group,
    connect_transports,
)
from cli.output import print_success, print_error


def _resolve_channel_id(name_or_id: str, state: GroupState) -> str:
    for ch in state.channels.values():
        if ch.name == name_or_id:
            return ch.id
    if name_or_id in state.channels:
        return name_or_id
    return name_or_id


@click.command()
@click.option("--channel", default=None, help="Channel name or ID (defaults to default channel)")
@click.option("--reply-to", default=None, help="Event ID to reply to")
@click.argument("group_id")
@click.argument("text")
@click.pass_context
def command(ctx: click.Context, channel: str, reply_to: str | None, group_id: str, text: str) -> None:
    asyncio.run(_post(ctx, channel, reply_to, group_id, text))


async def _post(ctx: click.Context, channel: str | None, reply_to: str | None, group_id: str, text: str) -> None:
    config = load_config()
    privkey = config.get("user_privkey_hex")
    if not privkey:
        print_error("No identity found. Run `fern init` first.")
        return

    user = UserIdentity.from_privkey_hex(privkey)
    group_pubkey, group_info = resolve_group(group_id, config)

    relay_urls = list(group_info.get("relays", []))
    if not relay_urls:
        relay_urls = ["ws://localhost:8765"]

    cache_path = group_info.get("cache_path") or str(get_cache_path(group_pubkey))

    transports = await connect_transports(relay_urls)

    store = SqliteStore(cache_path)
    await store.open()
    try:
        heal_mode = HealMode.NONE if ctx.obj and ctx.obj.get("no_heal") else HealMode.AUTO
        await sync_group_from_transports(
            group_pubkey=group_pubkey,
            transports=transports,
            store=store,
            client_id=user.pubkey,
            heal_mode=heal_mode,
        )

        events = []
        async for e in store.iter_group_events(group_pubkey):
            events.append(e)

        state = None
        if events:
            state, _ = derive_group_state(events)
            if user.pubkey not in state.joined:
                print_error("You have not joined this group. Run `fern group join` first.")
                return
            if state.is_banned_at(user.pubkey, int(time.time())):
                print_error("You are banned from this group.")
                return

        tips = list(compute_accepted_heads(events)) if events else []
    finally:
        await store.close()

    parents = tuple(tips) if tips else ()

    channel_id = channel
    if state and channel:
        channel_id = _resolve_channel_id(channel, state)
    elif state:
        channel_id = state.chat_settings.get("default_channel", "")
    if not channel_id:
        print_error("No channel specified and no default channel found.")
        return

    event = build_chat_message(
        user=user,
        group=group_pubkey,
        parents=parents,
        text=text,
        channel=channel_id,
        reply_to=reply_to,
        ts=int(time.time()),
    )

    event_receipts = 0
    errors = 0
    first_error: str | None = None
    for t in transports:
        try:
            await t.publish(event)
            event_receipts += 1
        except Exception as e:
            errors += 1
            if first_error is None:
                first_error = str(e)

    for t in transports:
        try:
            await t.close()
        except Exception:
            pass

    if event_receipts == 0:
        msg = first_error or "unknown error"
        print_error(f"Failed to publish to any relay: {msg}")
        return

    ensure_config_dir()
    config.setdefault("groups", {})
    if group_pubkey not in config["groups"]:
        config["groups"][group_pubkey] = {}
    config["groups"][group_pubkey]["relays"] = relay_urls
    config["groups"][group_pubkey].setdefault("cache_path", cache_path)
    save_config(config)

    print_success(f"Posted to group {group_id}: {event.id[:16] if event.id else ''}...")
    if state:
        display_channel = state.channels[channel_id].name if channel_id in state.channels else channel_id
    else:
        display_channel = channel_id
    click.echo(f"  Channel: #{display_channel}")
    click.echo(f"  Event receipts: {event_receipts}/{len(relay_urls)}")
    if errors:
        click.echo(f"  Errors: {errors}")
    if event.id:
        click.echo(f"  Event ID: {event.id}")
