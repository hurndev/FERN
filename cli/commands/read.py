from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import click

from cli.bft import open_store, sync_group
from cli.config import get_cache_path, load_config, resolve_group
from fern.apps.chat import ChatState
from fern.events.event import Event


def _names(events: list[Event]) -> dict[str, str]:
    result: dict[str, str] = {}
    for event in events:
        if event.type == "chat.nickname_set":
            result[event.author] = str(event.content.get("nickname", ""))
    return result


def _name(pubkey: str, names: dict[str, str]) -> str:
    return names.get(pubkey) or f"{pubkey[:12]}..."


def _time(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, UTC).strftime("%Y-%m-%d %H:%M:%S")


@click.command()
@click.option("--channel", default=None)
@click.option("-n", "--count", default=50, type=int)
@click.option("--show-rejected", is_flag=True, hidden=True)
@click.argument("group_id")
def command(channel: str | None, count: int, show_rejected: bool, group_id: str) -> None:
    del show_rejected  # BFT history contains only consensus-valid events.
    asyncio.run(_read(channel, count, group_id))


async def _read(channel: str | None, count: int, group_id: str) -> None:
    config = load_config()
    group, info = resolve_group(group_id, config)
    path = str(info.get("cache_path") or get_cache_path(group))
    store = open_store(path)
    try:
        await sync_group(group, info, store)
        state = store.get_chain_head(group).state
        chat = state.app
        assert isinstance(chat, ChatState)
        finalized = store.finalized_events(group)
        plain_events = [item[0] for item in finalized]
        names = _names(plain_events)
        channel_id = channel
        if channel:
            for record in chat.channels.values():
                if record.name == channel:
                    channel_id = record.id
                    break
        rows: list[str] = []
        for event, height, position, certified_ms in finalized:
            if event.type == "chat.message":
                event_channel = str(event.content.get("channel", ""))
                if channel_id and event_channel != channel_id:
                    continue
                channel_name = chat.channels.get(event_channel)
                label = channel_name.name if channel_name else event_channel
                rows.append(
                    f"[{_time(certified_ms)} #{label}] <{_name(event.author, names)}> "
                    f"{event.content.get('text', '')}  [{height}:{position}]"
                )
            elif event.type in {"join", "leave", "kick", "ban", "unban"}:
                rows.append(
                    f"[{_time(certified_ms)}] --- {event.type} by "
                    f"{_name(event.author, names)} [{height}:{position}] ---"
                )
        for row in rows[-count:]:
            click.echo(row)
        pending = store.pending_events(group)
        for event in pending:
            if event.type == "chat.message":
                click.echo(
                    f"[pending] <{_name(event.author, names)}> {event.content.get('text', '')}"
                )
        if not rows and not pending:
            click.echo("No messages.")
    finally:
        store.close()
