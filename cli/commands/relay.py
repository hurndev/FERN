from __future__ import annotations

import asyncio

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
@click.option("--port", default=8765, help="Port to listen on")
@click.option("--name", default="FERN Relay", help="Relay name")
@click.option("--store", default="relay.db", help="SQLite store path")
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option(
    "--trust-config",
    default=None,
    help="Path to a JSON file configuring trusted witness relays, thresholds, and rate limits.",
)
@click.option("--log-level", default="INFO", help="Log level (DEBUG/INFO/WARNING/ERROR)")
@click.option("--no-color", is_flag=True, help="Disable coloured log output")
def start(
    port: int,
    name: str,
    store: str,
    host: str,
    trust_config: str | None,
    log_level: str,
    no_color: bool,
) -> None:
    import logging

    from cli.relay_main import _ColorFormatter
    from fern.crypto.keys import Keypair
    from fern.transport.websocket_server import RelayServer

    handler = logging.StreamHandler()
    if no_color:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
        )
    else:
        handler.setFormatter(_ColorFormatter())
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        handlers=[handler],
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)

    BOLD = "\033[1m" if not no_color else ""
    RESET = "\033[0m" if not no_color else ""
    CYAN = "\033[36m" if not no_color else ""
    MAGENTA = "\033[35m" if not no_color else ""
    GREEN = "\033[32m" if not no_color else ""

    keypair = Keypair.generate()
    click.echo(f"{BOLD}{MAGENTA}Starting FERN relay{RESET} on {host}:{port}")
    click.echo(f"  {CYAN}Name:{RESET}     {name}")
    click.echo(f"  {CYAN}Address:{RESET}  {GREEN}{_display_relay_url(host, port)}{RESET}")
    click.echo(f"  {CYAN}Store:{RESET}    {store}")
    click.echo(f"  {CYAN}Log level:{RESET} {log_level.upper()}")
    click.echo(f"  {CYAN}Pubkey:{RESET}    {GREEN}{keypair.pubkey_hex}{RESET}")

    server = RelayServer(
        host=host,
        port=port,
        name=name,
        relay_keypair=keypair,
        store_path=store,
        trust_config_path=trust_config,
    )
    asyncio.run(server.start())


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
