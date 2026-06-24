from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import click

from fern.transport.metadata import fetch_relay_metadata
from cli.output import print_error


def _display_relay_url(host: str, port: int) -> str:
    display_host = "localhost" if host in {"0.0.0.0", "::"} else host
    return f"ws://{display_host}:{port}"


@click.group()
def command() -> None:
    pass


@command.command()
@click.option("--config", "config_path", default=None, help="Path to relay config file.")
@click.option("--port", default=None, type=int, help="Override port to listen on")
@click.option("--log-level", default=None, help="Log level (DEBUG/INFO/WARNING/ERROR)")
@click.option("--no-color", is_flag=True, help="Disable coloured log output")
def start(config_path: str | None, port: int | None, log_level: str | None, no_color: bool) -> None:
    """Start the relay server using the config file."""
    import logging

    from cli.relay_main import _ColorFormatter
    from fern.relay.config import default_config_file, load_config, load_keypair
    from fern.transport.websocket_server import RelayServer

    cfg_path = Path(config_path) if config_path else None
    config = load_config(cfg_path)
    if port is not None:
        config = replace(config, port=port)

    handler = logging.StreamHandler()
    if no_color:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
        )
    else:
        handler.setFormatter(_ColorFormatter())
    logging.basicConfig(
        level=getattr(logging, (log_level or "INFO").upper(), logging.INFO),
        handlers=[handler],
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)

    BOLD = "\033[1m" if not no_color else ""
    RESET = "\033[0m" if not no_color else ""
    CYAN = "\033[36m" if not no_color else ""
    MAGENTA = "\033[35m" if not no_color else ""
    GREEN = "\033[32m" if not no_color else ""

    try:
        keypair = load_keypair(config)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e

    resolved_path = cfg_path or default_config_file()
    click.echo(f"{BOLD}{MAGENTA}Starting FERN relay{RESET} on {config.host}:{config.port}")
    click.echo(f"  {CYAN}Name:{RESET}     {config.name}")
    click.echo(f"  {CYAN}Address:{RESET}  {GREEN}{_display_relay_url(config.host, config.port)}{RESET}")
    click.echo(f"  {CYAN}Store:{RESET}    {config.store}")
    click.echo(f"  {CYAN}Config:{RESET}   {resolved_path}")
    click.echo(f"  {CYAN}Pubkey:{RESET}   {GREEN}{keypair.pubkey_hex}{RESET}")
    if config.trusted_witness_relays:
        click.echo(f"  {CYAN}Witnesses:{RESET} {len(config.trusted_witness_relays)} trusted relay(s)")

    server = RelayServer(
        host=config.host,
        port=config.port,
        name=config.name,
        relay_keypair=keypair,
        store_path=config.store,
        config=config,
    )
    asyncio.run(server.start())


@command.command()
@click.option("--name", default="FERN Relay", help="Relay name")
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", default=8765, help="Port to listen on")
@click.option("--config", "config_path", default=None, help="Path to relay config file.")
def init(name: str, host: str, port: int, config_path: str | None) -> None:
    """Generate a relay keypair and create the default config file."""
    from fern.relay.config import default_config_file, init_config

    cfg_path = Path(config_path) if config_path else None
    config, keypair = init_config(
        name=name, host=host, port=port,
        config_path=cfg_path,
    )
    resolved = cfg_path or default_config_file()
    click.echo("Relay initialised.")
    click.echo(f"  Config:  {resolved}")
    click.echo(f"  Pubkey:  {keypair.pubkey_hex}")
    click.echo()
    click.echo("Edit the config file to add trusted witnesses, then run:")
    click.echo("  fern relay start")


@command.command()
@click.argument("url")
def info(url: str) -> None:
    asyncio.run(_info(url))


@command.command(name="revoke-witness")
@click.argument("witness_pubkey")
@click.option("--store", default="relay.db", help="SQLite store path")
def revoke_witness(witness_pubkey: str, store: str) -> None:
    """Delete events admitted only by the given witness pubkey, and clean provenance."""
    asyncio.run(_revoke_witness(witness_pubkey, store))


async def _revoke_witness(witness_pubkey: str, store: str) -> None:
    from fern.storage.sqlite_store import SqliteStore

    if len(witness_pubkey) != 64:
        print_error("Witness pubkey must be 64-char hex.")
        return
    s = SqliteStore(store)
    await s.open()
    try:
        orphans = await s.delete_events_admitted_only_by(witness_pubkey)
    finally:
        await s.close()
    if orphans:
        click.echo(f"Deleted {len(orphans)} event(s) admitted only by {witness_pubkey[:16]}...")
        for eid in orphans:
            click.echo(f"  {eid}")
    else:
        click.echo(f"No events were admitted only by {witness_pubkey[:16]}...")


async def _info(url: str) -> None:
    try:
        metadata = await fetch_relay_metadata(url)
        click.echo(f"Relay: {url}")
        if metadata.name:
            click.echo(f"  Name: {metadata.name}")
        if metadata.description:
            click.echo(f"  Description: {metadata.description}")
        click.echo(f"  Pubkey: {metadata.pubkey}")
        click.echo(f"  Software: {metadata.software} {metadata.version}")
        click.echo(f"  Retention: {metadata.retention}")
        if metadata.groups:
            click.echo(f"  Groups: {len(metadata.groups)}")
    except Exception as e:
        click.echo(f"Failed to fetch metadata: {e}")