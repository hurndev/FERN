from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import ClassVar

import click

from fern.relay.config import (
    RelayConfig,
    add_witness,
    default_config_file,
    init_config,
    load_config,
    load_keypair,
    remove_witness,
    save_config,
)


def _display_relay_url(host: str, port: int) -> str:
    display_host = "localhost" if host in {"0.0.0.0", "::"} else host
    return f"ws://{display_host}:{port}"


class _ColorFormatter(logging.Formatter):
    COLORS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    MAGENTA = "\033[35m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    RED = "\033[31m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        time_str = self.formatTime(record, "%H:%M:%S")
        level = f"{color}{record.levelname:<7}{self.RESET}"
        name = f"{self.DIM}{record.name}{self.RESET}"
        msg = self._colorize(record.getMessage())
        return f"{self.DIM}{time_str}{self.RESET} {level} {name}: {msg}"

    def _colorize(self, msg: str) -> str:
        for word in ["genesis", "join", "leave", "kick", "ban", "unban"]:
            msg = msg.replace(f"type={word}", f"type={self.MAGENTA}{word}{self.RESET}")
        msg = msg.replace("metadata", f"{self.YELLOW}metadata{self.RESET}")
        msg = msg.replace("auto-hosting", f"{self.MAGENTA}auto-hosting{self.RESET}")
        msg = msg.replace("broadcast", f"{self.CYAN}broadcast{self.RESET}")
        msg = msg.replace("rejecting", f"{self.RED}rejecting{self.RESET}")
        msg = msg.replace("fraud proof", f"{self.RED}fraud proof{self.RESET}")
        return msg


@click.group(invoke_without_command=True)
@click.option("--config", "config_path", default=None, help="Path to relay config file.")
@click.pass_context
def main_fn(ctx: click.Context, config_path: str | None) -> None:
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = Path(config_path) if config_path else None
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@main_fn.command()
@click.option("--name", default="FERN Relay", help="Relay name")
@click.option("--host", default="0.0.0.0", help="Bind address")
@click.option("--port", default=8765, help="Port to listen on")
@click.option("--store", default="relay.db", help="SQLite store path")
@click.option("--log-level", default="INFO", help="Log level (DEBUG/INFO/WARNING/ERROR)")
@click.option("--no-color", is_flag=True, help="Disable coloured log output")
@click.pass_context
def init(ctx: click.Context, name: str, host: str, port: int, store: str, log_level: str, no_color: bool) -> None:
    """Generate a relay keypair and create the default config file."""
    cfg_path = ctx.obj.get("config_path") or default_config_file()
    key_path = cfg_path.parent / "relay.key"

    BOLD = "\033[1m" if not no_color else ""
    RESET = "\033[0m" if not no_color else ""
    CYAN = "\033[36m" if not no_color else ""
    MAGENTA = "\033[35m" if not no_color else ""
    GREEN = "\033[32m" if not no_color else ""

    config, keypair = init_config(
        name=name, host=host, port=port, store=store,
        config_path=cfg_path, key_path=key_path,
    )

    click.echo(f"{BOLD}{MAGENTA}FERN relay initialised{RESET}")
    click.echo(f"  {CYAN}Config:{RESET}  {cfg_path}")
    click.echo(f"  {CYAN}Key:{RESET}     {key_path}")
    click.echo(f"  {CYAN}Pubkey:{RESET}  {GREEN}{keypair.pubkey_hex}{RESET}")
    click.echo(f"  {CYAN}Store:{RESET}   {store}")
    click.echo()
    click.echo("Edit the config file to add trusted witnesses, then run:")
    click.echo(f"  fern-relay [--config {cfg_path}]")


@main_fn.command()
@click.option("--log-level", default=None, help="Override log level")
@click.option("--no-color", is_flag=True, help="Disable coloured log output")
@click.pass_context
def run(ctx: click.Context, log_level: str | None, no_color: bool) -> None:
    """Start the relay server."""
    from fern.transport.websocket_server import RelayServer

    cfg_path = ctx.obj.get("config_path")
    resolved_path = cfg_path or default_config_file()

    if not resolved_path.exists():
        click.echo("No config found. Initialising...")
        store_path = str(resolved_path.parent / "relay.db")
        config, keypair = init_config(config_path=cfg_path, store=store_path)
        click.echo(f"  Config: {resolved_path}")
        click.echo(f"  Pubkey: {keypair.pubkey_hex}")
        click.echo()
    else:
        config = load_config(cfg_path)

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

    click.echo(f"{BOLD}{MAGENTA}Starting FERN relay{RESET} on {config.host}:{config.port}")
    click.echo(f"  {CYAN}Name:{RESET}     {config.name}")
    click.echo(f"  {CYAN}Address:{RESET}  {GREEN}{_display_relay_url(config.host, config.port)}{RESET}")
    click.echo(f"  {CYAN}Store:{RESET}    {config.store}")
    click.echo(f"  {CYAN}Pubkey:{RESET}   {GREEN}{keypair.pubkey_hex}{RESET}")
    if config.trusted_witness_relays:
        click.echo(f"  {CYAN}Witnesses:{RESET} {len(config.trusted_witness_relays)} trusted relay(s)")

    server = RelayServer(
        host=config.host,
        port=config.port,
        name=config.name,
        relay_keypair=keypair,
        store_path=config.store,
        trust_config_path=str(cfg_path) if cfg_path else None,
    )

    asyncio.run(server.start())


@main_fn.group()
def config() -> None:
    """Manage relay configuration."""
    pass


@config.command(name="show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    """Display the current relay config."""
    cfg_path = ctx.obj.get("config_path")
    config = load_config(cfg_path)
    resolved_path = cfg_path or default_config_file()
    _print_config(config, resolved_path)


@config.command(name="add-witness")
@click.argument("url")
@click.argument("pubkey")
@click.pass_context
def config_add_witness(ctx: click.Context, url: str, pubkey: str) -> None:
    """Add a trusted witness relay to the config."""
    cfg_path = ctx.obj.get("config_path") or default_config_file()
    config = load_config(cfg_path)
    try:
        config = add_witness(config, url, pubkey)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    save_config(config, cfg_path)
    click.echo(f"Added witness {pubkey[:16]}... ({url})")
    click.echo(f"Total witnesses: {len(config.trusted_witness_relays)}")


@config.command(name="remove-witness")
@click.argument("pubkey")
@click.pass_context
def config_remove_witness(ctx: click.Context, pubkey: str) -> None:
    """Remove a trusted witness relay from the config."""
    cfg_path = ctx.obj.get("config_path") or default_config_file()
    config = load_config(cfg_path)
    try:
        config = remove_witness(config, pubkey)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    save_config(config, cfg_path)
    click.echo(f"Removed witness {pubkey[:16]}...")
    click.echo(f"Total witnesses: {len(config.trusted_witness_relays)}")


def _print_config(config: RelayConfig, path: Path) -> None:
    click.echo(f"Config: {path}")
    click.echo(f"  Name:     {config.name}")
    click.echo(f"  Host:     {config.host}")
    click.echo(f"  Port:     {config.port}")
    click.echo(f"  Store:    {config.store}")
    click.echo(f"  Key file: {config.key_file}")
    click.echo()
    if config.trusted_witness_relays:
        click.echo(f"  Trusted witnesses ({len(config.trusted_witness_relays)}):")
        for w in config.trusted_witness_relays:
            click.echo(f"    {w.relay[:16]}...  {w.url}")
    else:
        click.echo("  Trusted witnesses: none (fast heal disabled)")
    click.echo()
    click.echo(f"  Threshold:       {config.threshold.num}/{config.threshold.den}, min {config.threshold.min}")
    click.echo(f"  Batch limits:    {config.batch_limits.max_events} events, {config.batch_limits.max_bytes} bytes")
    click.echo(f"  Group quota:     {config.per_group_storage_quota or 'unlimited'} events")
    click.echo(f"  Max message:     {config.max_message_bytes} bytes")
    click.echo(f"  Witnessing:      {'enabled' if config.witnessing_enabled else 'disabled'}")
    click.echo(f"  Challenge TTL:   {config.challenge_expiry_seconds}s")


def main() -> None:
    main_fn()