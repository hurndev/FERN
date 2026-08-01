# Example app: chess

A worked example of building an application on the FERN protocol core **without
modifying it**. The core hosts a two-player chess game it knows nothing about,
purely through a registered `AppModule`. It's the second app on the core (after
chat) and exists to validate the protocol/app boundary.

**How it works and the design rationale** (app-module model, boundary events,
why the pre-finalization window isn't exploitable, validator management) are in
[DESIGN.md](DESIGN.md).

## Play it yourself

Start a 4-validator cluster in one terminal (it writes connection details to
`cluster.json`, which the CLI reads by default):

```sh
.venv/bin/python examples/chess/run_cluster.py
```

Then run the interactive CLI in one terminal per player — each with its own
identity file (created automatically on first use):

```sh
.venv/bin/python examples/chess/play.py --identity alice.json
```

Inside the prompt:

```
chess> whoami                    # copy this pubkey to give your opponent
chess> join
chess> board
chess> new-game me <bob-pubkey>  # "me" = your own pubkey
chess> move e2 e4
chess> watch                     # wait for the opponent's move
chess> resign
chess> help                      # all commands
chess> quit
```

Every command syncs the finalized chain before acting, waits for its event to
commit, and prints the resulting board. Illegal moves (wrong turn, invalid piece
movement) are rejected by the validators.

## Files

| file             | role                                                            |
|------------------|-----------------------------------------------------------------|
| `board.py`       | pure chess logic: squares, movement, captures, promotion        |
| `chess_app.py`   | the FERN app module: `ChessState` + `ChessApp` (the adapter)    |
| `client.py`      | client library: sync the chain, publish events, render the board |
| `cluster.py`     | in-process validator cluster used by `run_cluster.py`           |
| `run_cluster.py` | the chess validator entry point (long-running cluster)          |
| `play.py`        | the interactive CLI                                             |
| `DESIGN.md`      | how it works and why it's designed that way                     |

## At a glance

- **Events** — `chess.new_game {white, black}` (a *boundary* event), `chess.move
  {from, to}`, `chess.resign` — all namespaced `chess.*`, as the app name
  requires.
- **State** — the two players, the board, whose turn, status, winner, ply count;
  serialized into the group's single state root.
- **Authorization** — only joined, non-banned members interact; only a player
  may move/resign, and only on their turn; `new_game` must be started by one of
  its players.
- **Core events** — a player who `leave`s an active game forfeits it.
- **Ruleset** — standard piece movement and captures, but simplified: the game
  ends when a king is captured (no check/checkmate), no castling or en passant,
  pawns auto-promote to a queen.
