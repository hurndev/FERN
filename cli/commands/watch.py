from __future__ import annotations

import asyncio

import click

from fern.storage.sqlite_store import SqliteStore
from fern.state.machine import derive_group_state
from fern.events.event import Event
from fern.events.validation import verify_event
from cli.config import (
    load_config,
    get_cache_path,
    resolve_group,
    connect_transports,
    get_client_id,
)
from cli.commands.read import (
    ADMIN_TYPES,
    _compute_nicknames,
    _display_name,
    _format_admin_action,
    _channel_display_name,
    _resolve_channel_filter,
)
from cli.sync import sync_group_from_transports
from fern.client.sync import HealMode


@click.command()
@click.option("--channel", default=None, help="Filter by channel")
@click.option("--show-rejected", is_flag=True, help="Show messages from non-joined/banned users")
@click.argument("group_id")
@click.pass_context
def command(ctx: click.Context, channel: str | None, show_rejected: bool, group_id: str) -> None:
    asyncio.run(_watch(ctx, channel, show_rejected, group_id))


async def _watch(ctx: click.Context, channel: str | None, show_rejected: bool, group_id: str) -> None:
    config = load_config()
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
            client_id=get_client_id(config),
            heal_mode=heal_mode,
        )
    finally:
        await store.close()

    store = SqliteStore(cache_path)
    await store.open()
    events = []
    async for event in store.iter_group_events(group_pubkey):
        events.append(event)
    await store.close()

    click.echo(f"Watching group {group_id}... (Ctrl+C to stop)")

    def _refresh_nicknames() -> dict[str, str]:
        return _compute_nicknames(events)

    nicknames = _compute_nicknames(events)

    initial_state, _ = derive_group_state(events) if events else (None, [])
    channel_filter = _resolve_channel_filter(channel, initial_state) if channel and initial_state else None

    async def handle_event(event: Event) -> None:
        try:
            verify_event(event)
            live_store = SqliteStore(cache_path)
            await live_store.open()
            try:
                await live_store.put_event(event)
            finally:
                await live_store.close()
        except Exception:
            return

        if event.type in ADMIN_TYPES:
            formatted = _format_admin_action(event, nicknames)
            if formatted:
                click.echo(formatted)
            return

        if event.type == "chat.nickname_set":
            events.append(event)
            nick = event.content.get("nickname", "")
            author = _display_name(event.author, nicknames)
            click.echo(f"--- {author} is now known as {nick} ---")
            nicknames[event.author] = nick
            return

        if event.type != "chat.message":
            return
        msg_channel = event.content.get("channel", "")
        if channel_filter and msg_channel != channel_filter:
            return

        authorised = True
        live_state = None
        if events:
            live_events = events + [event]
            try:
                live_state, rejected = derive_group_state(live_events)
                authorised = event.id not in {e.id for e in rejected if e.id is not None}
            except Exception:
                pass

        if not authorised:
            events.append(event)
            if not show_rejected:
                return
            tag = " [not authorized]"
        else:
            events.append(event)
            tag = ""

        text = event.content.get("text", "")
        author = _display_name(event.author, nicknames)
        display_ch = _channel_display_name(msg_channel, live_state) if live_state else msg_channel
        channel_tag = f"#{display_ch}" if msg_channel else ""
        click.echo(f"[{channel_tag}] <{author}> {text}{tag}")

    for t in transports:
        t.on_event(handle_event)
        await t.subscribe(group_pubkey)

    click.echo("Connected. Waiting for messages...")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        click.echo("\nDisconnecting...")
    finally:
        for t in transports:
            try:
                await t.close()
            except Exception:
                pass
