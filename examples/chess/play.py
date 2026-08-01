"""Interactive chess CLI for the FERN chess example.

Start a cluster (``run_cluster.py``), then run this in a terminal per player.
It reads the cluster's connection details from ``cluster.json`` by default and
drops you into a prompt — type ``help`` for the command list.

    .venv/bin/python examples/chess/play.py --identity alice.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
import tempfile
import time
from pathlib import Path

from fern.bft.app import GroupState
from fern.bft.store import BFTStore
from fern.crypto.keys import Keypair

from chess_app import ChessState, ChessStatus
from client import ChessClient, describe

CONFIG = Path(__file__).parent / "cluster.json"

HELP = """commands:
  whoami                 print your public key
  join                   join the group
  new-game WHITE BLACK   start a game ("me" = your own pubkey)
  move FROM TO           make a move, e.g. "move e2 e4" or "move e2e4"
  board                  show the current board
  resign                 resign the active game
  watch [seconds]        wait for the opponent's next move
  sync                   re-sync from the validators
  help                   show this help
  quit                   exit
"""


def load_identity(path: Path) -> Keypair:
    if path.exists():
        data = json.loads(path.read_text())
        return Keypair.from_privkey(bytes.fromhex(str(data["privkey"])))
    keypair = Keypair.generate()
    path.write_text(json.dumps({"privkey": keypair.privkey_hex}))
    print(f"(created new identity in {path})")
    return keypair


def load_config() -> dict[str, object]:
    if not CONFIG.exists():
        sys.exit(
            f"No {CONFIG.name}. Start the cluster first:\n"
            "  .venv/bin/python examples/chess/run_cluster.py"
        )
    config = json.loads(CONFIG.read_text())
    assert isinstance(config, dict)
    return config


def chess_state(state: GroupState) -> ChessState:
    app = state.app
    assert isinstance(app, ChessState)
    return app


async def do_join(client: ChessClient, identity: Keypair) -> None:
    await client.join()
    await client.wait_for(lambda state: identity.pubkey_hex in state.joined)
    print("joined.")


async def do_new_game(client: ChessClient, identity: Keypair, args: list[str]) -> None:
    if len(args) != 2:
        print("! usage: new-game WHITE BLACK  (use 'me' for your own pubkey)")
        return
    me = identity.pubkey_hex
    white = me if args[0] == "me" else args[0]
    black = me if args[1] == "me" else args[1]
    await client.new_game(white, black)
    state = await client.wait_for(lambda s: chess_state(s).status == ChessStatus.ACTIVE)
    print("game started.")
    print(describe(state))


async def do_move(client: ChessClient, args: list[str]) -> None:
    if len(args) == 2:
        src, dst = args
    elif len(args) == 1 and len(args[0]) == 4:
        src, dst = args[0][:2], args[0][2:]
    else:
        print("! usage: move FROM TO, e.g. 'move e2 e4'")
        return
    before = chess_state(await client.move(src, dst)).moves
    state = await client.wait_for(lambda s: chess_state(s).moves > before)
    print(f"{src}-{dst} played.")
    print(describe(state))


async def do_resign(client: ChessClient) -> None:
    await client.resign()
    state = await client.wait_for(lambda s: chess_state(s).status == ChessStatus.OVER)
    print("you resigned.")
    print(describe(state))


async def do_watch(client: ChessClient, args: list[str]) -> None:
    try:
        timeout = float(args[0]) if args else 90.0
    except ValueError:
        print("! usage: watch [seconds]")
        return
    marker = chess_state(await client.sync())
    baseline = (marker.moves, marker.status)
    target = await client.latest_height()
    deadline = time.time() + timeout
    print(f"watching for the opponent's move (Ctrl-C quits, up to {timeout:.0f}s)…")
    # Poll by cheap status (not full syncs): the validators rate-limit the
    # history-manifest reads that a full sync performs.
    while time.time() < deadline:
        await asyncio.sleep(2.0)
        try:
            height = await client.latest_height()
        except (RuntimeError, TimeoutError) as exc:
            print(f"! {exc}")
            return
        if height <= target:
            continue
        try:
            state = await client.sync()
        except (RuntimeError, TimeoutError) as exc:
            print(f"! {exc}")
            return
        current = chess_state(state)
        if (current.moves, current.status) != baseline:
            print(describe(state))
            return
        target = height
    print("no change.")


async def interactive(client: ChessClient, identity: Keypair) -> None:
    state = await client.sync()
    if state.core.app != "chess":
        print(f"! that group runs app {state.core.app!r}, not chess")
        return
    print(f"you are {identity.pubkey_hex}")
    print(describe(state))
    while True:
        try:
            line = await asyncio.to_thread(input, "chess> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        line = line.strip()
        if not line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            print(f"! {exc}")
            continue
        command, args = parts[0].lower(), parts[1:]
        try:
            if command in ("quit", "exit", "q"):
                break
            elif command in ("help", "?"):
                print(HELP)
            elif command == "whoami":
                print(identity.pubkey_hex)
            elif command == "join":
                await do_join(client, identity)
            elif command == "new-game":
                await do_new_game(client, identity, args)
            elif command == "move":
                await do_move(client, args)
            elif command == "board":
                print(describe(await client.sync()))
            elif command == "resign":
                await do_resign(client)
            elif command == "watch":
                await do_watch(client, args)
            elif command == "sync":
                await client.sync()
                print("synced.")
            else:
                print(f"! unknown command: {command} (try 'help')")
        except (RuntimeError, TimeoutError, ValueError) as exc:
            print(f"! {exc}")


async def run(args: argparse.Namespace) -> None:
    config = load_config()
    group = args.group if args.group else str(config["group"])
    config_urls = config["urls"]
    assert isinstance(config_urls, list)
    if args.urls:
        urls: list[str] = []
        for item in args.urls:
            urls.extend(part.strip() for part in item.split(",") if part.strip())
    else:
        urls = [str(item) for item in config_urls]
    identity = load_identity(Path(str(args.identity)))

    with tempfile.TemporaryDirectory() as directory:
        store = BFTStore(Path(directory) / "client.db")
        client = ChessClient(group, urls, identity, store)
        try:
            await interactive(client, identity)
        except (RuntimeError, TimeoutError, ValueError) as exc:
            print(f"! {exc}")
        finally:
            store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Play chess on a FERN chess group.")
    parser.add_argument("--group", help="group pubkey (default: from cluster.json)")
    parser.add_argument("--urls", nargs="+", help="validator ws:// urls (default: cluster.json)")
    parser.add_argument(
        "--identity", default="identity.json", help="identity key file (created if missing)"
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nbye.")


if __name__ == "__main__":
    main()
