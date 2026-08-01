"""Start a long-running in-process chess validator cluster you can play against.

Runs 4 real validators (over localhost WebSockets), creates a fresh chess group,
and writes the connection details to ``cluster.json`` so the ``play.py`` CLI can
pick them up automatically. Leave it running in one terminal and play from
others. Stop with Ctrl-C.

    .venv/bin/python examples/chess/run_cluster.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path

from fern.apps import register_app
from fern.crypto.keys import Keypair

from chess_app import CHESS_APP
from cluster import MiniCluster, build_chess_genesis

register_app(CHESS_APP)
logging.getLogger("fern").setLevel(logging.ERROR)

CONFIG = Path(__file__).parent / "cluster.json"


async def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cluster = MiniCluster(Path(directory))
        founder = Keypair.generate()  # neutral founder; players join themselves
        genesis = build_chess_genesis(cluster.validator_set, founder)
        await cluster.start(genesis)
        CONFIG.write_text(json.dumps({"group": genesis.group, "urls": cluster.urls}, indent=2))
        try:
            print("Chess cluster running.\n")
            print(f"  group:      {genesis.group}")
            print(f"  validators: {' '.join(cluster.urls)}\n")
            print(f"Wrote {CONFIG.name} — the play CLI reads it by default.\n")
            print("Play from other terminals:")
            print("  .venv/bin/python examples/chess/play.py --identity alice.json")
            print("  (type 'help' inside the prompt)")
            print("\nCtrl-C to stop.\n")
            await asyncio.Event().wait()  # run until cancelled
        finally:
            await cluster.stop()
            CONFIG.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped.")
