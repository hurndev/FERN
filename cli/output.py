from __future__ import annotations

import json
import click


def print_json(data: object) -> None:
    click.echo(json.dumps(data, indent=2, default=str))


def print_table(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
    if not rows:
        click.echo("(empty)")
        return

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val)))

    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    click.echo(fmt.format(*headers))
    click.echo("  ".join("-" * w for w in col_widths))
    for row in rows:
        click.echo(fmt.format(*[str(v) for v in row]))


def print_success(msg: str) -> None:
    click.secho(f"✓ {msg}", fg="green")


def print_error(msg: str) -> None:
    click.secho(f"✗ {msg}", fg="red")
