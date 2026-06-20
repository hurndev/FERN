from __future__ import annotations

import asyncio

import click

from fern.completeness.attestations import Attestation
from fern.completeness.trust_ledger import TrustLedger
from fern.client.monitor_runner import run_monitor_pass
from fern.storage.sqlite_store import SqliteStore
from cli.config import (
    load_config,
    get_cache_path,
    resolve_group,
    connect_transports,
    get_client_id,
)
from cli.output import print_success, print_error
from cli.sync import sync_group_from_transports


@click.command()
@click.argument("group_id")
def command(group_id: str) -> None:
    asyncio.run(_verify(group_id))


async def _verify(group_id: str) -> None:
    config = load_config()
    group_pubkey, group_info = resolve_group(group_id, config)
    relay_urls = list(group_info.get("relays", []))
    cache_path = group_info.get("cache_path") or str(get_cache_path(group_pubkey))

    if not relay_urls:
        print_error("No relays configured for this group.")
        return

    transports = await connect_transports(relay_urls)
    for t in transports:
        await t.subscribe(group_pubkey)

    store = SqliteStore(cache_path)
    await store.open()
    known_set: frozenset[str] = frozenset()
    sync_results = []
    try:
        sync_results = await sync_group_from_transports(
            group_pubkey=group_pubkey,
            transports=transports,
            store=store,
            client_id=get_client_id(config),
        )
        known_set = await store.get_known_set(group_pubkey)
    finally:
        pass

    trust_ledger = TrustLedger()
    sibling_attestations: dict[str, Attestation] = {}

    try:
        for t in transports:
            try:
                att = await t.request_attestation(group_pubkey)
                sibling_attestations[t.relay_pubkey] = att
            except Exception:
                pass

        for t in transports:
            relay_pk = t.relay_pubkey
            if not relay_pk or relay_pk not in sibling_attestations:
                continue
            att = sibling_attestations[relay_pk]
            await run_monitor_pass(
                relay=t,
                attestation=att,
                local_known_set=known_set,
                receipts_for_relay={},
                trust_ledger=trust_ledger,
                sibling_attestations={
                    k: v for k, v in sibling_attestations.items() if k != relay_pk
                },
            )

    finally:
        for t in transports:
            try:
                await t.close()
            except Exception:
                pass
        await store.close()

    click.echo(f"Verification for group {group_id}:")
    click.echo()

    if sync_results:
        fetched = sum(r.fetched for r in sync_results)
        backfilled = sum(r.backfilled for r in sync_results)
        skipped = sum(1 for r in sync_results if r.skipped_locked)
        click.echo(
            f"  Sync pass: fetched {fetched}, backfilled {backfilled}, skipped locked {skipped}"
        )
        click.echo()

    if not trust_ledger.entries:
        click.echo("  No relay attestations received.")
        return

    any_faults = False
    for relay_pk, entry in trust_ledger.entries.items():
        faults = entry.observed_faults
        if faults:
            any_faults = True
            click.echo(f"  Relay {relay_pk[:16]}... — {len(faults)} fault(s):")
            for f in faults:
                click.echo(f"    [{f.kind}] {f.evidence}")
        else:
            set_hash = entry.last_attestation.set_hash if entry.last_attestation else "(none)"
            click.echo(f"  Relay {relay_pk[:16]}... — in sync (set_hash: {set_hash[:16]}...)")

    if not any_faults:
        click.echo()
        print_success("No faults detected.")
