from __future__ import annotations

import asyncio
import json
import logging
import signal
from dataclasses import replace
from pathlib import Path

import click

from cli.logging_config import configure_logging
from fern.bft.node import ConsensusTiming, ValidatorNode
from fern.bft.admission import prepare_validator_history
from fern.bft.store import BFTStore
from fern.bft.websocket import PeerBroadcaster, ServerMetadata, ValidatorServer, parse_group_address
from fern.validator.config import (
    ValidatorConfig,
    add_witness,
    default_config_file,
    init_config,
    load_config,
    load_keypair,
    remove_witness,
    save_config,
)


logger = logging.getLogger(__name__)


def _display_validator_url(host: str, port: int) -> str:
    display_host = "localhost" if host in {"0.0.0.0", "::"} else host
    return f"ws://{display_host}:{port}"


def _configure_logging(level: str, no_color: bool) -> None:
    configure_logging(level=level, no_color=no_color)


async def run_validator(config: ValidatorConfig) -> None:
    keypair = load_keypair(config)
    store = BFTStore(config.store)
    timing = ConsensusTiming(
        block_interval=config.block_interval,
        observation_timeout=config.observation_timeout,
        observation_timeout_delta=config.observation_timeout_delta,
        proposal_timeout=config.proposal_timeout,
        proposal_timeout_delta=config.proposal_timeout_delta,
        prevote_timeout=config.prevote_timeout,
        prevote_timeout_delta=config.prevote_timeout_delta,
        precommit_timeout=config.precommit_timeout,
        precommit_timeout_delta=config.precommit_timeout_delta,
        round_backoff=config.round_backoff,
    )
    node = ValidatorNode(
        keypair=keypair,
        store=store,
        broadcast=PeerBroadcaster(keypair.pubkey_hex),
        timing=timing,
        autostart=False,
    )
    server = ValidatorServer(
        node=node,
        metadata=ServerMetadata(
            name=config.name,
            description=config.description,
            pubkey=keypair.pubkey_hex,
        ),
        host=config.host,
        port=config.port,
        allow_genesis=config.allow_genesis,
        ingress_limit=config.ingress_limit,
        ingress_window_seconds=config.ingress_window_seconds,
        maximum_message_bytes=config.maximum_message_bytes,
    )
    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []

    def request_shutdown(signal_name: str) -> None:
        if shutdown.is_set():
            return
        logger.info("validator shutdown requested signal=%s", signal_name)
        shutdown.set()

    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                shutdown_signal,
                request_shutdown,
                shutdown_signal.name,
            )
        except (NotImplementedError, RuntimeError):
            # asyncio signal handlers aren't available on every event loop.
            # asyncio.run() still turns Ctrl+C into cancellation on those platforms.
            continue
        installed_signals.append(shutdown_signal)

    try:
        await server.run(shutdown)
    finally:
        logger.info("validator shutting down")
        try:
            await node.stop()
        finally:
            store.close()
            for shutdown_signal in installed_signals:
                loop.remove_signal_handler(shutdown_signal)
        logger.info("validator stopped")


@click.group(invoke_without_command=True)
@click.option("--config", "config_path", default=None, help="Path to validator config file.")
@click.option("--port", default=None, type=int, help="Override the listen port.")
@click.option("-v", "--verbose", is_flag=True, help="Show consensus rounds and stages.")
@click.pass_context
def main_fn(ctx: click.Context, config_path: str | None, port: int | None, verbose: bool) -> None:
    """Run and configure a FERN-BFT validator."""

    ctx.ensure_object(dict)
    ctx.obj["config_path"] = Path(config_path) if config_path else None
    ctx.obj["port"] = port
    ctx.obj["verbose"] = verbose
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@main_fn.command()
@click.option("--name", default="FERN Validator")
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8765, type=int)
@click.option("--store", default=None, help="SQLite path (defaults beside the config).")
@click.option("--closed", is_flag=True, help="Require operator approval for new genesis.")
@click.pass_context
def init(
    ctx: click.Context, name: str, host: str, port: int, store: str | None, closed: bool
) -> None:
    """Create a validator key and configuration."""

    config_path = ctx.obj.get("config_path") or default_config_file()
    key_path = config_path.parent / "validator.key"
    store_path = store or str(config_path.parent / "validator.db")
    config, keypair = init_config(
        name=name,
        host=host,
        port=port,
        store=store_path,
        config_path=config_path,
        key_path=key_path,
    )
    if closed:
        config = replace(config, allow_genesis=False)
        save_config(config, config_path)
    click.echo("FERN-BFT validator initialised")
    click.echo(f"  Config: {config_path}")
    click.echo(f"  Key:    {key_path}")
    click.echo(f"  Store:  {store_path}")
    click.echo(f"  Pubkey: {keypair.pubkey_hex}")


@main_fn.command()
@click.option("--port", default=None, type=int)
@click.option("--log-level", default="INFO")
@click.option("--no-color", is_flag=True)
@click.option("-v", "--verbose", is_flag=True, help="Show consensus rounds and stages.")
@click.pass_context
def run(
    ctx: click.Context,
    port: int | None,
    log_level: str,
    no_color: bool,
    verbose: bool,
) -> None:
    """Start the validator server."""

    config_path = ctx.obj.get("config_path") or default_config_file()
    if not config_path.exists():
        config, _keypair = init_config(config_path=config_path)
    else:
        config = load_config(config_path)
    effective_port = port if port is not None else ctx.obj.get("port")
    if effective_port is not None:
        config = replace(config, port=effective_port)
    try:
        keypair = load_keypair(config)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    effective_verbose = verbose or bool(ctx.obj.get("verbose"))
    _configure_logging("DEBUG" if effective_verbose else log_level, no_color)
    click.echo(f"Starting FERN-BFT validator on {config.host}:{config.port}")
    click.echo(f"  Address: {_display_validator_url(config.host, config.port)}")
    click.echo(f"  Store:   {config.store}")
    click.echo(f"  Pubkey:  {keypair.pubkey_hex}")
    inspection_store = BFTStore(config.store)
    try:
        click.echo(f"  Groups:  {len(inspection_store.hosted_groups())}")
    finally:
        inspection_store.close()
    try:
        asyncio.run(run_validator(config))
    except KeyboardInterrupt:
        # Fallback for event loops without add_signal_handler(). Cleanup has
        # already run in run_validator's finally block.
        pass


@main_fn.group()
def config() -> None:
    """Manage validator resource-admission trust."""


@config.command(name="show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    path = ctx.obj.get("config_path") or default_config_file()
    value = load_config(path)
    click.echo(f"Config: {path}")
    click.echo(f"  Name:            {value.name}")
    click.echo(f"  Bind:            {value.host}:{value.port}")
    click.echo(f"  Store:           {value.store}")
    click.echo(f"  Open genesis:    {value.allow_genesis}")
    click.echo(f"  Block interval:  {value.block_interval}s")
    click.echo(f"  Trusted hosts:   {len(value.trusted_hosts)}")


@config.command(name="add-witness")
@click.argument("url")
@click.argument("pubkey")
@click.option("--operator", required=True, help="Local independent-operator trust label.")
@click.pass_context
def config_add_witness(ctx: click.Context, url: str, pubkey: str, operator: str) -> None:
    """Trust an operator key for history resource admission."""

    path = ctx.obj.get("config_path") or default_config_file()
    try:
        value = add_witness(load_config(path), url, pubkey, operator)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    save_config(value, path)
    click.echo(f"Trusted host added: {pubkey}")


@config.command(name="remove-witness")
@click.argument("pubkey")
@click.pass_context
def config_remove_witness(ctx: click.Context, pubkey: str) -> None:
    path = ctx.obj.get("config_path") or default_config_file()
    try:
        value = remove_witness(load_config(path), pubkey)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    save_config(value, path)
    click.echo(f"Trusted host removed: {pubkey}")


@main_fn.command(name="prepare")
@click.argument("address")
@click.option("--manual", is_flag=True, help="Explicitly override the trusted-host threshold.")
@click.option("--output", type=click.Path(path_type=Path), default=None)
@click.pass_context
def prepare_history(ctx: click.Context, address: str, manual: bool, output: Path | None) -> None:
    """Verify and stage full history, then produce a SyncReady proof."""

    path = ctx.obj.get("config_path") or default_config_file()
    config_value = load_config(path)
    group, urls = parse_group_address(address)
    if not group or not urls:
        raise click.UsageError("address must be fern:<group>@<validator>,...")
    try:
        readiness = asyncio.run(_prepare_history(config_value, group, urls, manual))
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    encoded = json.dumps(readiness, indent=2, ensure_ascii=False) + "\n"
    if output is not None:
        output.write_text(encoded, encoding="utf-8")
        click.echo(f"SyncReady written to {output}")
    else:
        click.echo(encoded, nl=False)
    click.echo(
        "The proof is bound to this exact checkpoint; regenerate it if another block commits "
        "before the validator update."
    )


async def _prepare_history(
    config: ValidatorConfig, group: str, urls: list[str], manual: bool
) -> dict[str, object]:
    keypair = load_keypair(config)
    store = BFTStore(config.store)
    try:
        prepared = await prepare_validator_history(
            group=group,
            urls=urls,
            store=store,
            keypair=keypair,
            trusted_operators={host.pubkey: host.operator for host in config.trusted_hosts},
            minimum_operators=config.minimum_trusted_operators,
            maximum_logical_bytes=config.maximum_group_logical_bytes,
            manual=manual,
        )
        return prepared.readiness.to_dict()
    finally:
        store.close()


def main() -> None:
    main_fn()


def legacy_main() -> None:
    click.echo(
        "Warning: 'fern-relay' was renamed to 'fern-validator'; update your scripts.",
        err=True,
    )
    main_fn(prog_name="fern-relay")
