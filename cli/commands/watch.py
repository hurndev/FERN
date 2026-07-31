from __future__ import annotations

import asyncio

import click

from cli.bft import open_store, sync_group, validator_urls
from cli.config import get_cache_path, load_config, resolve_group
from fern.apps.chat import ChatState
from fern.bft.blocks import Commit
from fern.bft.websocket import BFTWebSocketClient


@click.command()
@click.option("--channel", default=None)
@click.option("--show-rejected", is_flag=True, hidden=True)
@click.argument("group_id")
def command(channel: str | None, show_rejected: bool, group_id: str) -> None:
    del show_rejected
    asyncio.run(_watch(channel, group_id))


async def _watch(channel: str | None, group_id: str) -> None:
    config = load_config()
    group, info = resolve_group(group_id, config)
    urls = validator_urls(info)
    if not urls:
        raise click.ClickException("No validators configured.")
    store = open_store(str(info.get("cache_path") or get_cache_path(group)))
    try:
        await sync_group(group, info, store)
        click.echo(f"Watching finalized commits for {group[:16]}... (Ctrl-C to stop)")
        async for message in BFTWebSocketClient(urls[0]).subscribe(group):
            message_type = message.get("type")
            if message_type == "pending_event":
                raw = message.get("event")
                if isinstance(raw, dict) and raw.get("type") == "chat.message":
                    click.echo(
                        f"[pending] <{str(raw.get('author', ''))[:12]}...> {raw.get('content', {}).get('text', '')}"
                    )
            elif message_type == "commit":
                raw = message.get("commit")
                if not isinstance(raw, dict):
                    continue
                commit = Commit.from_dict(raw)
                try:
                    store.save_commit(group, commit)
                except ValueError:
                    continue
                state = store.get_chain_head(group).state
                chat = state.app
                assert isinstance(chat, ChatState)
                for event, certified_ms in zip(
                    commit.block.candidate.all_events,
                    commit.block.certified_times_ms,
                    strict=True,
                ):
                    if event.type != "chat.message":
                        continue
                    event_channel = str(event.content.get("channel", ""))
                    channel_name = chat.channels.get(event_channel)
                    label = channel_name.name if channel_name else event_channel
                    if channel and channel not in {label, event_channel}:
                        continue
                    click.echo(
                        f"[final #{commit.block.height} {certified_ms}] "
                        f"#{label} <{event.author[:12]}...> {event.content.get('text', '')}"
                    )
    finally:
        store.close()
