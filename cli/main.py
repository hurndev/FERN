from __future__ import annotations

import click

from cli.lazy_group import LazyCommandSpec, LazyGroup


LAZY_COMMANDS = {
    "chain": LazyCommandSpec(
        "cli.commands.chain",
        "Inspect finalized blocks, consensus evidence, and pending events.",
    ),
    "dag": LazyCommandSpec("cli.commands.dag", "", hidden=True),
    "group": LazyCommandSpec("cli.commands.group", "Create, join and administer FERN-BFT groups."),
    "init": LazyCommandSpec("cli.commands.init", "Create a local user identity."),
    "post": LazyCommandSpec("cli.commands.post", "Post a message to a group."),
    "read": LazyCommandSpec("cli.commands.read", "Read finalized and pending messages."),
    "validator": LazyCommandSpec("cli.commands.validator", "Manage a local FERN-BFT validator."),
    "verify": LazyCommandSpec("cli.commands.verify", "Verify a group's complete cached chain."),
    "watch": LazyCommandSpec(
        "cli.commands.watch", "Watch a group for pending events and finalized blocks."
    ),
    "whoami": LazyCommandSpec("cli.commands.whoami", "Show the local user identity."),
}


@click.group(cls=LazyGroup, lazy_commands=LAZY_COMMANDS)
@click.option("--no-heal", is_flag=True, hidden=True)
@click.option("-v", "--verbose", is_flag=True, help="Show validator sync and publish details.")
@click.pass_context
def fern_cli(ctx: click.Context, no_heal: bool, verbose: bool) -> None:
    from cli.logging_config import configure_logging

    ctx.ensure_object(dict)
    ctx.obj["no_heal"] = no_heal  # accepted for legacy scripts; BFT sync never heals loose events
    ctx.obj["verbose"] = verbose
    configure_logging(level="DEBUG" if verbose else "WARNING")


def main() -> None:
    fern_cli()
