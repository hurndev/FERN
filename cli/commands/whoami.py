from __future__ import annotations

import click

from fern.identity.user import UserIdentity
from cli.config import load_config
from cli.output import print_error


@click.command()
def command() -> None:
    config = load_config()
    privkey = config.get("user_privkey_hex")
    if not privkey:
        print_error("No identity found. Run `fern init` first.")
        return

    identity = UserIdentity.from_privkey_hex(privkey)
    click.echo(f"User pubkey: {identity.pubkey}")
