from __future__ import annotations

import asyncio

import click

from cli.bft import open_store, sync_group
from cli.config import get_cache_path, load_config, resolve_group
from cli.output import print_success
from fern.bft.chain import verify_chain


@click.command()
@click.argument("group_id")
def command(group_id: str) -> None:
    asyncio.run(_verify(group_id))


async def _verify(group_id: str) -> None:
    config = load_config()
    group, info = resolve_group(group_id, config)
    store = open_store(str(info.get("cache_path") or get_cache_path(group)))
    try:
        await sync_group(group, info, store)
        genesis = store.get_genesis(group)
        if genesis is None:
            raise click.ClickException("missing genesis")
        head = verify_chain(genesis, store.commits(group))
        print_success("Complete BFT chain verified from genesis.")
        click.echo(f"  Height: {head.height}")
        click.echo(f"  Epoch: {head.state.validator_set.epoch}")
        click.echo(f"  Block hash: {head.block_hash}")
        click.echo(f"  State root: {head.state.root}")
        click.echo(f"  History root: {head.history_root}")
    finally:
        store.close()
