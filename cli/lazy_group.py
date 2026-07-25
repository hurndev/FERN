from __future__ import annotations

from importlib import import_module
from typing import Any, NamedTuple

import click


class LazyCommandSpec(NamedTuple):
    module: str
    short_help: str
    hidden: bool = False
    attribute: str = "command"


class LazyGroup(click.Group):
    """A Click group that imports only the command selected by the user."""

    def __init__(
        self,
        *args: Any,
        lazy_commands: dict[str, LazyCommandSpec],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.lazy_commands = lazy_commands

    def list_commands(self, ctx: click.Context) -> list[str]:
        del ctx
        return sorted(name for name, spec in self.lazy_commands.items() if not spec.hidden)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        spec = self.lazy_commands.get(cmd_name)
        if spec is None:
            return super().get_command(ctx, cmd_name)
        cached = self.commands.get(cmd_name)
        if cached is not None:
            return cached
        module = import_module(spec.module)
        command = getattr(module, spec.attribute, None)
        if not isinstance(command, click.Command):
            raise TypeError(f"{spec.module}.{spec.attribute} is not a Click command")
        self.commands[cmd_name] = command
        return command

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        del ctx
        rows = [
            (name, spec.short_help)
            for name, spec in sorted(self.lazy_commands.items())
            if not spec.hidden
        ]
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)


__all__ = ["LazyCommandSpec", "LazyGroup"]
