from __future__ import annotations

import asyncio
import time

import click

from fern.identity.user import UserIdentity
from fern.identity.group import GroupKeypair
from fern.events.build import build_event
from fern.events.types import ProtocolTypes
from fern.state.machine import derive_group_state
from fern.storage.sqlite_store import SqliteStore
from fern.transport.websocket_client import WebSocketRelayClient
from fern.client.bootstrap import initial_sync
from cli.config import (
    load_config,
    save_config,
    ensure_config_dir,
    get_cache_path,
    parse_group_address,
    resolve_group,
    add_group_to_order,
    connect_transports,
)
from cli.output import print_success, print_error


from typing import Any


DEFAULT_RELAY = "ws://localhost:8765"


def _get_user(config: dict[str, Any]) -> UserIdentity:
    privkey = config.get("user_privkey_hex")
    if not privkey:
        raise click.UsageError("No identity found. Run `fern init` first.")
    return UserIdentity.from_privkey_hex(str(privkey))


def _collect_known_relays(config: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    relays: list[str] = []
    for group_info in config.get("groups", {}).values():
        for url in group_info.get("relays", []):
            if url not in seen:
                seen.add(url)
                relays.append(url)
    return relays


def _prompt_relays(config: dict[str, Any]) -> list[str]:
    known = _collect_known_relays(config)
    if known:
        click.echo("Known relays:")
        for i, url in enumerate(known, 1):
            click.echo(f"  {i}. {url}")
        click.echo()
        raw = click.prompt(
            "Choose relays by number (comma-separated), or type URL(s) space-separated",
            default=known[0],
        )
    else:
        raw = click.prompt("No known relays. Enter relay URL(s) space-separated")

    chosen: list[str] = []
    for token in raw.replace(",", " ").split():
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(known):
                chosen.append(known[idx])
            else:
                click.echo(f"  ignoring invalid relay number: {token}")
        else:
            if not token.startswith(("ws://", "wss://")):
                token = f"ws://{token}"
            chosen.append(token)

    if not chosen:
        raise click.UsageError("No relays selected.")
    return chosen


async def _close_transports(transports: list[WebSocketRelayClient]) -> None:
    for t in transports:
        try:
            await t.close()
        except Exception:
            pass


@click.group(name="group")
def command() -> None:
    pass


@command.command()
@click.option("--name", required=True, help="Group name")
@click.option("--description", default="", help="Group description")
@click.option("--public/--private", default=True, help="Public or private group")
@click.option("--relay", "relays", multiple=True, help="Relay URLs (may be repeated)")
def create(name: str, description: str, public: bool, relays: list[str]) -> None:
    asyncio.run(_create(name, description, public, relays))


async def _create(name: str, description: str, public: bool, relay_urls: list[str]) -> None:
    config = load_config()
    user = _get_user(config)

    if not relay_urls:
        relay_urls = _prompt_relays(config)

    ensure_config_dir()
    group_kp = GroupKeypair.generate()

    transports = await connect_transports(list(relay_urls))
    genesis: Any = None
    try:
        genesis = build_event(
            type=ProtocolTypes.GENESIS,
            group=group_kp.pubkey,
            author_keypair=user.keypair,
            parents=(),
            content={
                "name": name,
                "description": description,
                "public": public,
                "founder": user.pubkey,
                "mods": [user.pubkey],
                "relays": list(relay_urls),
            },
            group_keypair=group_kp.keypair,
        )

        receipts = 0
        first_error: str | None = None
        for t in transports:
            try:
                await t.publish(genesis)
                receipts += 1
            except Exception as e:
                if first_error is None:
                    first_error = str(e)

        if receipts == 0:
            msg = first_error or "unknown error"
            print_error(f"Failed to publish genesis to any relay: {msg}")
            return
    finally:
        await _close_transports(transports)

    cache_path = str(get_cache_path(group_kp.pubkey))
    store = SqliteStore(cache_path)
    await store.open()
    try:
        if genesis is not None:
            await store.put_event(genesis)
    finally:
        await store.close()

    config.setdefault("groups", {})
    config["groups"][group_kp.pubkey] = {
        "relays": list(relay_urls),
        "cache_path": cache_path,
        "joined": True,
    }
    num = add_group_to_order(group_kp.pubkey, config)
    save_config(config)

    print_success(f"Group {num} created.")
    click.echo(f"  Name: {name}")
    click.echo(f"  Public: {public}")
    click.echo(f"  Address: fern:{group_kp.pubkey}@" + ",".join(relay_urls))
    click.echo(f"  Pubkey: {group_kp.pubkey}")


@command.command()
@click.argument("address")
def join(address: str) -> None:
    asyncio.run(_join(address))


async def _join(address: str) -> None:
    config = load_config()
    user = _get_user(config)
    group_pubkey, relay_urls = parse_group_address(address)

    if not relay_urls:
        raise click.UsageError("No relay URLs in address.")

    transports = await connect_transports(relay_urls)
    genesis: Any = None
    sync_error: str | None = None
    try:
        for t in transports:
            try:
                async for event in t.sync(group_pubkey):
                    if event.type == "genesis":
                        genesis = event
                        break
            except Exception as e:
                if sync_error is None:
                    sync_error = str(e)
                continue
            if genesis is not None:
                break

        if genesis is None:
            hint = f": {sync_error}" if sync_error else ""
            print_error(f"Could not fetch genesis for group {group_pubkey[:16]}...{hint}")
            return

        from fern.events.validation import verify_event

        verify_event(genesis)

        ensure_config_dir()
        cache_path = str(get_cache_path(group_pubkey))
        store = SqliteStore(cache_path)
        await store.open()
        try:
            await store.put_event(genesis)
            await initial_sync(group_pubkey, transports, store)
            tips = await store.get_tips(group_pubkey)
        finally:
            await store.close()

        parents = tuple(tips) if tips else (genesis.id,) if genesis.id else ()
        join_event = build_event(
            type=ProtocolTypes.JOIN,
            group=group_pubkey,
            author_keypair=user.keypair,
            parents=parents,
            content={},
        )

        for t in transports:
            try:
                await t.publish(join_event)
            except Exception as e:
                click.echo(f"Warning: could not publish join event to {t.url}: {e}")

        try:
            store = SqliteStore(cache_path)
            await store.open()
            try:
                await store.put_event(join_event)
            finally:
                await store.close()
        except Exception as e:
            click.echo(f"Warning: could not cache join event: {e}")

        config.setdefault("groups", {})
        config["groups"][group_pubkey] = {
            "relays": list(relay_urls),
            "cache_path": cache_path,
            "joined": True,
        }
        num = add_group_to_order(group_pubkey, config)
        save_config(config)

        group_name = genesis.content.get("name", "Unnamed")
        print_success(f"Joined group {num}: {group_name}")
        click.echo(f"  Pubkey: {group_pubkey}")
        click.echo(f"  Relays: {', '.join(relay_urls)}")
    finally:
        await _close_transports(transports)


@command.command(name="list")
def list_groups() -> None:
    config = load_config()
    group_order: list[str] = config.get("group_order", [])
    groups = config.get("groups", {})

    if not group_order:
        click.echo("No groups configured. Create one with 'fern group create' or join with 'fern group join'.")
        return

    for i, pubkey in enumerate(group_order, 1):
        info = groups.get(pubkey, {})
        relays = info.get("relays", [])
        relay_str = relays[0] if relays else "(no relays)"
        click.echo(f"  {i}: {pubkey[:16]}...  {relay_str}")


@command.command()
@click.argument("group_id")
def info(group_id: str) -> None:
    asyncio.run(_info(group_id))


async def _info(group_id: str) -> None:
    config = load_config()
    group_pubkey, group_info = resolve_group(group_id, config)

    cache_path = group_info.get("cache_path") or str(get_cache_path(group_pubkey))
    store = SqliteStore(cache_path)
    await store.open()
    try:
        relay_urls = group_info.get("relays", [])
        if relay_urls:
            transports = await connect_transports(relay_urls)
            try:
                await initial_sync(group_pubkey, transports, store)
            finally:
                await _close_transports(transports)

        events = []
        async for e in store.iter_group_events(group_pubkey):
            events.append(e)

        if not events:
            click.echo("No events cached. Try joining the group first.")
            return

        state, _ = derive_group_state(events)
        genesis = next((e for e in events if e.type == ProtocolTypes.GENESIS), None)

        if genesis:
            click.echo(f"Group: {genesis.content.get('name', 'Unnamed')}")
            click.echo(f"  Description: {genesis.content.get('description', '')}")
        else:
            click.echo("Group: (no genesis found)")
        click.echo(f"  Public: {state.public}")
        click.echo(f"  Pubkey: {group_pubkey}")
        click.echo(f"  Mods: {len(state.mods)}")
        click.echo(f"  Members: {len(state.joined)}")
        click.echo(f"  Banned: {len(state.banned)}")
        click.echo(f"  Relays: {', '.join(state.relays)}")
        invite_relays = ", ".join(state.relays if state.relays else group_info.get("relays", []))
        if invite_relays:
            click.echo(f"  Invite: fern:{group_pubkey}@{invite_relays}")
    finally:
        await store.close()


@command.command()
@click.argument("group_id")
def members(group_id: str) -> None:
    asyncio.run(_members(group_id))


async def _members(group_id: str) -> None:
    config = load_config()
    group_pubkey, group_info = resolve_group(group_id, config)

    cache_path = group_info.get("cache_path") or str(get_cache_path(group_pubkey))
    store = SqliteStore(cache_path)
    await store.open()
    try:
        relay_urls = group_info.get("relays", [])
        if relay_urls:
            transports = await connect_transports(relay_urls)
            try:
                await initial_sync(group_pubkey, transports, store)
            finally:
                await _close_transports(transports)

        events = []
        async for e in store.iter_group_events(group_pubkey):
            events.append(e)

        if not events:
            click.echo("No events cached.")
            return

        state, _ = derive_group_state(events)
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

        click.echo(f"Members ({len(state.joined)}):")
        for pubkey in sorted(state.joined):
            role = "mod" if pubkey in state.mods else "member"
            banned_info = state.banned.get(pubkey)
            status = " [banned]" if banned_info and (banned_info.until is None or banned_info.until > int(time.time())) else ""
            nick = nicknames.get(pubkey) or ""
            name_str = f"  ({nick})" if nick else ""
            click.echo(f"  {pubkey}{name_str}  {role}{status}")
    finally:
        await store.close()


@command.command()
@click.argument("group_id")
def leave(group_id: str) -> None:
    asyncio.run(_leave(group_id))


async def _leave(group_id: str) -> None:
    config = load_config()
    user = _get_user(config)
    group_pubkey, group_info = resolve_group(group_id, config)

    relay_urls = group_info.get("relays", [])
    if not relay_urls:
        print_error("No relays configured for this group.")
        return

    transports = await connect_transports(relay_urls)
    try:
        leave_event = build_event(
            type=ProtocolTypes.LEAVE,
            group=group_pubkey,
            author_keypair=user.keypair,
            parents=(),
            content={},
        )
        sent = 0
        first_error: str | None = None
        for t in transports:
            try:
                await t.publish(leave_event)
                sent += 1
            except Exception as e:
                if first_error is None:
                    first_error = str(e)

        if sent == 0:
            msg = first_error or "unknown error"
            print_error(f"Failed to publish leave event to any relay: {msg}")
            return
    finally:
        await _close_transports(transports)

    if group_pubkey in config["groups"]:
        config["groups"][group_pubkey]["joined"] = False
        save_config(config)

    print_success(f"Left group {group_id}.")


@command.command(name="relay-update")
@click.argument("group_id")
@click.argument("urls", nargs=-1, required=True)
def relay_update(group_id: str, urls: tuple[str, ...]) -> None:
    asyncio.run(_relay_update(group_id, list(urls)))


async def _relay_update(group_id: str, urls: list[str]) -> None:
    config = load_config()
    user = _get_user(config)
    group_pubkey, group_info = resolve_group(group_id, config)

    relay_urls: list[str] = []
    for u in urls:
        if not u.startswith(("ws://", "wss://")):
            u = f"ws://{u}"
        relay_urls.append(u)

    if not relay_urls:
        raise click.UsageError("At least one relay URL is required.")

    old_relays = list(group_info.get("relays", []))
    cache_path = group_info.get("cache_path") or str(get_cache_path(group_pubkey))

    try:
        store = SqliteStore(cache_path)
        await store.open()
        try:
            tips = await store.get_tips(group_pubkey)
        finally:
            await store.close()
    except Exception as e:
        click.echo(f"Warning: could not read tips from cache: {e}")
        tips = []

    parents = tuple(tips) if tips else ()
    if not parents:
        print_error("No tips in local cache. Run `fern read <group>` to sync first.")
        return

    transports = await connect_transports(old_relays)
    try:
        event = build_event(
            type=ProtocolTypes.RELAY_UPDATE,
            group=group_pubkey,
            author_keypair=user.keypair,
            parents=parents,
            content={"relays": relay_urls},
        )

        receipts = 0
        first_error: str | None = None
        for t in transports:
            try:
                await t.publish(event)
                receipts += 1
            except Exception as e:
                if first_error is None:
                    first_error = str(e)

        if receipts == 0:
            msg = first_error or "unknown error"
            print_error(f"Failed to publish relay_update to any relay: {msg}")
            return

        try:
            store = SqliteStore(cache_path)
            await store.open()
            try:
                await store.put_event(event)
            finally:
                await store.close()
        except Exception as e:
            click.echo(f"Warning: could not cache event: {e}")
    finally:
        await _close_transports(transports)

    config["groups"][group_pubkey]["relays"] = relay_urls
    save_config(config)

    print_success(f"Relay list updated for group {group_id}.")
    click.echo(f"  Old relays: {', '.join(old_relays) if old_relays else '(none)'}")
    click.echo(f"  New relays: {', '.join(relay_urls)}")
    click.echo(f"  Event ID: {event.id[:16] if event.id else '?'}...")
    click.echo("  New relays will be seeded with history on next sync.")


async def _publish_mod_event(
    group_id: str,
    event_type: str,
    content: dict,
    success_msg: str,
) -> None:
    config = load_config()
    user = _get_user(config)
    group_pubkey, group_info = resolve_group(group_id, config)

    cache_path = group_info.get("cache_path") or str(get_cache_path(group_pubkey))
    relay_urls = list(group_info.get("relays", []))
    if not relay_urls:
        relay_urls = [DEFAULT_RELAY]

    try:
        store = SqliteStore(cache_path)
        await store.open()
        try:
            tips = await store.get_tips(group_pubkey)
        finally:
            await store.close()
    except Exception as e:
        click.echo(f"Warning: could not read tips from cache: {e}")
        tips = []

    parents = tuple(tips) if tips else ()
    if not parents:
        print_error("No tips in local cache. Run `fern read <group>` to sync first.")
        return

    transports = await connect_transports(relay_urls)
    try:
        event = build_event(
            type=event_type,
            group=group_pubkey,
            author_keypair=user.keypair,
            parents=parents,
            content=content,
        )

        receipts = 0
        first_error: str | None = None
        for t in transports:
            try:
                await t.publish(event)
                receipts += 1
            except Exception as e:
                if first_error is None:
                    first_error = str(e)

        if receipts == 0:
            msg = first_error or "unknown error"
            print_error(f"Failed to publish {event_type}: {msg}")
            return

        try:
            store = SqliteStore(cache_path)
            await store.open()
            try:
                await store.put_event(event)
            finally:
                await store.close()
        except Exception as e:
            click.echo(f"Warning: could not cache event: {e}")
    finally:
        await _close_transports(transports)

    print_success(success_msg)
    if event.id:
        click.echo(f"  Event ID: {event.id[:16]}...")


@command.command(name="kick")
@click.argument("group_id")
@click.argument("target_pubkey")
def kick(group_id: str, target_pubkey: str) -> None:
    asyncio.run(_publish_mod_event(
        group_id,
        ProtocolTypes.KICK,
        {"target": target_pubkey},
        f"Kicked {target_pubkey[:16]}... from group {group_id}.",
    ))


@command.command(name="ban")
@click.argument("group_id")
@click.argument("target_pubkey")
@click.option("--until", type=int, default=None, help="Unix timestamp when ban expires")
@click.option("--reason", default="", help="Reason for ban")
def ban(group_id: str, target_pubkey: str, until: int | None, reason: str) -> None:
    content: dict = {"target": target_pubkey, "until": until, "reason": reason}
    asyncio.run(_publish_mod_event(
        group_id,
        ProtocolTypes.BAN,
        content,
        f"Banned {target_pubkey[:16]}... from group {group_id}.",
    ))


@command.command(name="unban")
@click.argument("group_id")
@click.argument("target_pubkey")
def unban(group_id: str, target_pubkey: str) -> None:
    asyncio.run(_publish_mod_event(
        group_id,
        ProtocolTypes.UNBAN,
        {"target": target_pubkey},
        f"Unbanned {target_pubkey[:16]}... from group {group_id}.",
    ))


@command.command(name="invite")
@click.argument("group_id")
@click.argument("invitee_pubkey")
def invite(group_id: str, invitee_pubkey: str) -> None:
    asyncio.run(_publish_mod_event(
        group_id,
        ProtocolTypes.INVITE,
        {"invitee": invitee_pubkey, "role": "member"},
        f"Invited {invitee_pubkey[:16]}... to group {group_id}.",
    ))


@command.command(name="mod-add")
@click.argument("group_id")
@click.argument("target_pubkey")
def mod_add_cmd(group_id: str, target_pubkey: str) -> None:
    asyncio.run(_publish_mod_event(
        group_id,
        ProtocolTypes.MOD_ADD,
        {"target": target_pubkey},
        f"Promoted {target_pubkey[:16]}... to mod in group {group_id}.",
    ))


@command.command(name="mod-remove")
@click.argument("group_id")
@click.argument("target_pubkey")
def mod_remove_cmd(group_id: str, target_pubkey: str) -> None:
    asyncio.run(_publish_mod_event(
        group_id,
        ProtocolTypes.MOD_REMOVE,
        {"target": target_pubkey},
        f"Demoted {target_pubkey[:16]}... from mod in group {group_id}.",
    ))


@command.command(name="nickname")
@click.argument("group_id")
@click.argument("name")
def nickname(group_id: str, name: str) -> None:
    asyncio.run(_nickname(group_id, name))


async def _nickname(group_id: str, name: str) -> None:
    config = load_config()
    user = _get_user(config)
    group_pubkey, group_info = resolve_group(group_id, config)

    cache_path = group_info.get("cache_path") or str(get_cache_path(group_pubkey))
    relay_urls = list(group_info.get("relays", []))
    if not relay_urls:
        relay_urls = [DEFAULT_RELAY]

    try:
        store = SqliteStore(cache_path)
        await store.open()
        try:
            tips = await store.get_tips(group_pubkey)
        finally:
            await store.close()
    except Exception as e:
        click.echo(f"Warning: could not read tips from cache: {e}")
        tips = []

    parents = tuple(tips) if tips else ()
    if not parents:
        print_error("No tips in local cache. Run `fern read <group>` to sync first.")
        return

    event = build_event(
        type="chat.nickname_set",
        group=group_pubkey,
        author_keypair=user.keypair,
        parents=parents,
        content={"nickname": name},
    )

    transports = await connect_transports(relay_urls)
    try:
        receipts = 0
        first_error: str | None = None
        for t in transports:
            try:
                await t.publish(event)
                receipts += 1
            except Exception as e:
                if first_error is None:
                    first_error = str(e)

        if receipts == 0:
            msg = first_error or "unknown error"
            print_error(f"Failed to publish nickname: {msg}")
            return

        try:
            store = SqliteStore(cache_path)
            await store.open()
            try:
                await store.put_event(event)
            finally:
                await store.close()
        except Exception as e:
            click.echo(f"Warning: could not cache event: {e}")
    finally:
        await _close_transports(transports)

    print_success(f"Nickname set to '{name}' in group {group_id}.")
    if event.id:
        click.echo(f"  Event ID: {event.id[:16]}...")
