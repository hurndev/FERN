from __future__ import annotations

import asyncio

import click

from cli.bft import build_user_event, open_store, publish_user_event, sync_group
from cli.config import get_cache_path, load_config, resolve_group
from cli.output import print_error, print_success
from fern.events.types import ChatTypes
from fern.identity.user import UserIdentity


@click.command()
@click.option("--channel", default=None, help="Channel name or ID.")
@click.option("--reply-to", default=None, help="Finalized message ID to reply to.")
@click.argument("group_id")
@click.argument("text")
def command(channel: str | None, reply_to: str | None, group_id: str, text: str) -> None:
    asyncio.run(_post(channel, reply_to, group_id, text))


async def _post(channel: str | None, reply_to: str | None, group_id: str, text: str) -> None:
    config = load_config()
    private = config.get("user_privkey_hex")
    if not private:
        print_error("No identity found. Run `fern init` first.")
        return
    user = UserIdentity.from_privkey_hex(str(private))
    group, info = resolve_group(group_id, config)
    path = str(info.get("cache_path") or get_cache_path(group))
    store = open_store(path)
    try:
        await sync_group(group, info, store)
        state = store.get_chain_head(group).state
        if user.pubkey not in state.joined:
            print_error("You are not finalized as a joined member of this group.")
            return
        channel_id = channel or state.chat_settings.get("default_channel", "")
        for record in state.channels.values():
            if record.name == channel_id:
                channel_id = record.id
                break
        if channel_id not in state.channels:
            print_error("Unknown channel.")
            return
        event = build_user_event(
            store=store,
            group=group,
            user=user,
            event_type=ChatTypes.MESSAGE,
            content={"text": text, "channel": channel_id, "reply_to": reply_to},
        )
        result = await publish_user_event(store=store, group=group, group_info=info, event=event)
    finally:
        store.close()
    if not result.receipts:
        print_error(result.errors[0] if result.errors else "No validator accepted the message.")
        return
    print_success(f"Message submitted: {event.id}")
    click.echo("  Status: pending")
    click.echo(f"  Ingress receipts: {len(result.receipts)}")
    click.echo(
        "  Honest propagation: "
        + ("confirmed" if result.propagation_confirmed else "not yet confirmed")
    )
