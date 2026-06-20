from __future__ import annotations

import asyncio

import click

from fern.storage.sqlite_store import SqliteStore
from fern.state.machine import derive_group_state
from fern.client.bootstrap import initial_sync
from cli.config import (
    load_config,
    get_cache_path,
    resolve_group,
    connect_transports,
)
from cli.output import print_error


MOD_TYPES = {"kick", "ban", "unban", "invite", "mod_add", "mod_remove", "join", "leave"}


def _compute_nicknames(events: list) -> dict[str, str]:
    nicknames: dict[str, str] = {}
    claimed_by: dict[str, str] = {}
    for e in sorted(
        [e for e in events if e.type == "chat.nickname_set"],
        key=lambda e: (e.ts, e.id),
    ):
        nick = e.content.get("nickname", "")
        if not nick:
            continue
        existing_owner = claimed_by.get(nick)
        if existing_owner is not None and existing_owner != e.author:
            continue
        old_nick = nicknames.get(e.author)
        if old_nick and old_nick != nick:
            claimed_by.pop(old_nick, None)
        nicknames[e.author] = nick
        claimed_by[nick] = e.author
    return nicknames


def _display_name(pubkey: str, nicknames: dict[str, str]) -> str:
    return nicknames.get(pubkey) or f"{pubkey[:12]}..."


def _format_mod_action(event, nicknames: dict[str, str]) -> str | None:
    t = event.type
    target = event.content.get("target", "")
    target_name = _display_name(target, nicknames) if target else ""
    author = _display_name(event.author, nicknames) if event.author else "anon"
    if t == "kick":
        return f"--- {author} kicked {target_name} ---"
    if t == "ban":
        reason = event.content.get("reason", "")
        until = event.content.get("until")
        extra = f" (reason: {reason})" if reason else ""
        if until:
            extra += f" (until: {until})"
        return f"--- {author} banned {target_name}{extra} ---"
    if t == "unban":
        return f"--- {author} unbanned {target_name} ---"
    if t == "invite":
        invitee = event.content.get("invitee", "")
        invitee_name = _display_name(invitee, nicknames) if invitee else ""
        return f"--- {author} invited {invitee_name} ---"
    if t == "mod_add":
        return f"--- {author} promoted {target_name} to mod ---"
    if t == "mod_remove":
        return f"--- {author} demoted {target_name} ---"
    if t == "join":
        return f"--- {author} joined the group ---"
    if t == "leave":
        return f"--- {author} left the group ---"
    return None


@click.command()
@click.option("--channel", default=None, help="Filter by channel")
@click.option("-n", "--count", default=50, help="Number of entries to show")
@click.option("--show-rejected", is_flag=True, help="Show messages from non-joined/banned users")
@click.argument("group_id")
def command(channel: str | None, count: int, show_rejected: bool, group_id: str) -> None:
    asyncio.run(_read(channel, count, show_rejected, group_id))


async def _read(channel: str | None, count: int, show_rejected: bool, group_id: str) -> None:
    config = load_config()
    privkey = config.get("user_privkey_hex")
    if not privkey:
        print_error("No identity found. Run `fern init` first.")
        return

    group_pubkey, group_info = resolve_group(group_id, config)
    cache_path = group_info.get("cache_path") or str(get_cache_path(group_pubkey))

    relay_urls = list(group_info.get("relays", []))
    if relay_urls:
        transports = await connect_transports(relay_urls)

        store = SqliteStore(cache_path)
        await store.open()
        try:
            await initial_sync(group_pubkey, transports, store)
        finally:
            await store.close()

        for t in transports:
            try:
                await t.close()
            except Exception:
                pass

    store = SqliteStore(cache_path)
    await store.open()
    try:
        events = []
        async for event in store.iter_group_events(group_pubkey):
            events.append(event)

        if not events:
            click.echo("No events cached.")
            return

        state, rejected = derive_group_state(events)
        rejected_ids = {e.id for e in rejected if e.id is not None}
        nicknames = _compute_nicknames(events)

        entries: list[tuple[int, str]] = []
        for event in events:
            ts = event.ts
            author = _display_name(event.author, nicknames)

            if event.type == "chat.message":
                msg_channel = event.content.get("channel", "")
                if channel and msg_channel != channel:
                    continue
                text = event.content.get("text", "")
                channel_tag = f"#{msg_channel}" if msg_channel else ""
                line = f"[{channel_tag}] <{author}> {text}"
                if event.id in rejected_ids:
                    line += "  [not authorized]"
                    if not show_rejected:
                        continue
                entries.append((ts, line))

            elif event.type in MOD_TYPES:
                formatted = _format_mod_action(event, nicknames)
                if formatted is not None:
                    entries.append((ts, formatted))

        entries.sort(key=lambda e: e[0])
        entries = entries[-count:]

        if not entries:
            click.echo("No messages or events found.")
            return

        for _, line in entries:
            click.echo(line)

    finally:
        await store.close()
