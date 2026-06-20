from __future__ import annotations

import click

from cli.commands import init, whoami, group, post, read, relay, verify, watch, dag


@click.group()
def fern_cli() -> None:
    pass


fern_cli.add_command(init.command, name="init")
fern_cli.add_command(whoami.command, name="whoami")
fern_cli.add_command(group.command, name="group")
fern_cli.add_command(post.command, name="post")
fern_cli.add_command(read.command, name="read")
fern_cli.add_command(watch.command, name="watch")
fern_cli.add_command(relay.command, name="relay")
fern_cli.add_command(verify.command, name="verify")
fern_cli.add_command(dag.command, name="dag")


def main() -> None:
    fern_cli()
