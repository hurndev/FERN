from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import click

from cli.validator_main import _configure_logging, run_validator
from fern.bft.notices import OperatorNotice, verify_operator_notice
from fern.bft.websocket import BFTWebSocketClient
from fern.validator.config import default_config_file, init_config, load_config, load_keypair


@click.group()
def command() -> None:
    """Manage a local FERN-BFT validator."""


@command.command()
@click.option("--config", "config_path", default=None)
@click.option("--port", default=None, type=int)
@click.option("--log-level", default="INFO")
@click.option("--no-color", is_flag=True)
@click.option("-v", "--verbose", is_flag=True, help="Show consensus rounds and stages.")
@click.pass_context
def start(
    ctx: click.Context,
    config_path: str | None,
    port: int | None,
    log_level: str,
    no_color: bool,
    verbose: bool,
) -> None:
    path = Path(config_path) if config_path else default_config_file()
    config = load_config(path)
    if port is not None:
        config = replace(config, port=port)
    try:
        keypair = load_keypair(config)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    root_verbose = bool(ctx.find_root().params.get("verbose", False))
    _configure_logging("DEBUG" if verbose or root_verbose else log_level, no_color)
    click.echo(f"Starting validator {keypair.pubkey_hex[:16]}... on {config.host}:{config.port}")
    asyncio.run(run_validator(config, notice_file=path))


@command.command()
@click.option("--name", default="FERN Validator")
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8765, type=int)
@click.option("--config", "config_path", default=None)
def init(name: str, host: str, port: int, config_path: str | None) -> None:
    path = Path(config_path) if config_path else default_config_file()
    config, keypair = init_config(
        name=name,
        host=host,
        port=port,
        store=str(path.parent / "validator.db"),
        config_path=path,
        key_path=path.parent / "validator.key",
    )
    click.echo("Validator initialised")
    click.echo(f"  Config: {path}")
    click.echo(f"  Store:  {config.store}")
    click.echo(f"  Pubkey: {keypair.pubkey_hex}")


@command.command()
@click.argument("url")
def info(url: str) -> None:
    asyncio.run(_info(url))


async def _info(url: str) -> None:
    try:
        metadata = await BFTWebSocketClient(url).metadata()
    except Exception as exc:
        raise click.ClickException(f"failed to fetch validator metadata: {exc}") from exc
    click.echo(f"Validator: {url}")
    click.echo(f"  Name:     {metadata.get('name', '')}")
    click.echo(f"  Pubkey:   {metadata.get('pubkey', '')}")
    click.echo(f"  Software: {metadata.get('software', '')} {metadata.get('version', '')}")
    groups = metadata.get("groups", [])
    click.echo(f"  Groups:   {len(groups) if isinstance(groups, list) else 0}")
    raw_notice = metadata.get("notice")
    if isinstance(raw_notice, dict):
        try:
            notice = OperatorNotice.from_dict({str(k): v for k, v in raw_notice.items()})
        except (ValueError, TypeError):
            notice = None
        if (
            notice is not None
            and notice.validator == metadata.get("pubkey")
            and verify_operator_notice(notice)
        ):
            click.echo(f"  Notice:   {notice.text}")
    raw_peer_notices = metadata.get("peer_notices")
    if isinstance(raw_peer_notices, list):
        for raw_peer in raw_peer_notices:
            if not isinstance(raw_peer, dict):
                continue
            try:
                peer_notice = OperatorNotice.from_dict(
                    {str(k): v for k, v in raw_peer.items()}
                )
            except (ValueError, TypeError):
                continue
            if verify_operator_notice(peer_notice):
                short = peer_notice.validator[:12]
                click.echo(f"  Notice ({short}…): {peer_notice.text}")
