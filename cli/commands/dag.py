from __future__ import annotations

from pathlib import Path

import click

from cli.commands.chain import inspect_database


@click.command(hidden=True)
@click.option(
    "--db",
    "db_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="FERN-BFT SQLite cache or validator DB.",
)
@click.option("--host", default="127.0.0.1", hidden=True)
@click.option("--port", default=8760, hidden=True)
def command(db_path: Path, host: str, port: int) -> None:
    """Compatibility alias for ``fern chain --db``."""

    del host, port
    click.secho(
        "WARNING: `fern dag` has been renamed to `fern chain`; use `fern chain --db <path>`.",
        fg="yellow",
        err=True,
    )
    inspect_database(
        db_path,
        None,
        limit=10,
        show_events=True,
        show_pending=True,
        full_ids=False,
    )
