from __future__ import annotations

import asyncio
import time

import click

from fern.identity.user import UserIdentity
from fern.chat.messages import build_chat_message
from fern.storage.sqlite_store import SqliteStore
from fern.state.machine import compute_accepted_heads, derive_group_state
from cli.sync import sync_group_from_transports
from cli.config import (
    load_config,
    save_config,
    ensure_config_dir,
    get_cache_path,
    resolve_group,
    connect_transports,
)
from cli.output import print_success, print_error


@click.command()
@click.option("--channel", default="general", help="Channel name")
@click.option("--reply-to", default=None, help="Event ID to reply to")
@click.argument("group_id")
@click.argument("text")
def command(channel: str, reply_to: str | None, group_id: str, text: str) -> None:
    asyncio.run(_post(channel, reply_to, group_id, text))


async def _post(channel: str, reply_to: str | None, group_id: str, text: str) -> None:
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
        await sync_group_from_transports(
            group_pubkey=group_pubkey,
            transports=transports,
            store=store,
            client_id=user.pubkey,
        )

        events = []
        async for e in store.iter_group_events(group_pubkey):
            events.append(e)

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

    event = build_chat_message(
        user=user,
        group=group_pubkey,
        parents=parents,
        text=text,
        channel=channel,
        reply_to=reply_to,
        ts=int(time.time()),
    )

    receipts = 0
    errors = 0
    first_error: str | None = None
    for t in transports:
        try:
            await t.publish(event)
            receipts += 1
        except Exception as e:
            errors += 1
            if first_error is None:
                first_error = str(e)

    for t in transports:
        try:
            await t.close()
        except Exception:
            pass

    if receipts == 0:
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
    click.echo(f"  Channel: #{channel}")
    click.echo(f"  Receipts: {receipts}/{len(relay_urls)}")
    if errors:
        click.echo(f"  Errors: {errors}")
    if event.id:
        click.echo(f"  Event ID: {event.id}")
