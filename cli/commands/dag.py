from __future__ import annotations

import os

import click


@click.command()
@click.option("--db", "db_path", required=True, help="Path to SQLite database (client cache or relay store)")
@click.option("--host", default="127.0.0.1", help="Bind address")
@click.option("--port", default=8760, help="Port to listen on")
def command(db_path: str, host: str, port: int) -> None:
    from cli.dag_viewer import launch_viewer

    expanded = os.path.expanduser(db_path)
    if not os.path.exists(expanded):
        click.echo(f"Database not found: {expanded}")
        return

    launch_viewer(expanded, host, port)
