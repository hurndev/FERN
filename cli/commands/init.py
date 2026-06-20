from __future__ import annotations

import click

from fern.identity.user import UserIdentity
from cli.config import ensure_config_dir, load_config, save_config
from cli.output import print_success


@click.command()
def command() -> None:
    config = load_config()
    if "user_privkey_hex" in config:
        click.echo("Identity already exists:")
        click.echo(f"  pubkey: {UserIdentity.from_privkey_hex(config['user_privkey_hex']).pubkey}")
        return

    ensure_config_dir()
    identity = UserIdentity.generate()
    config["user_privkey_hex"] = identity.keypair.privkey_hex
    save_config(config)
    print_success(f"Identity created: {identity.pubkey}")
    click.echo("  Save this private key safely. It cannot be recovered.")
