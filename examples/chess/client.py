"""A very simple FERN chess client.

Shows how an application talks to a FERN group that runs the chess app: register
the app module (so this process understands ``chess.*`` events and state), sync
the chain from the validators, and publish signed chess events.

The client is intentionally thin — it reuses the core's sync/publish helpers and
adds only chess-specific actions (new game, move, resign) plus a board renderer.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from fern.apps import is_supported, register_app
from fern.bft.app import GroupState
from fern.bft.client import publish_to_validators, sync_from_validators
from fern.bft.store import BFTStore
from fern.bft.websocket import BFTWebSocketClient
from fern.crypto.keys import Keypair
from fern.events.build import build_event

from chess_app import CHESS_APP, ChessState, ChessStatus, ChessTurn, ChessTypes
from board import render_board

# Register the chess app so the core can decode chess state and validate chess
# events for us. A validator hosting chess does the same thing.
if not is_supported("chess"):
    register_app(CHESS_APP)


def describe(state: GroupState) -> str:
    """Human-readable snapshot of the chess state in a synced group."""
    chess = state.app
    assert isinstance(chess, ChessState)
    if chess.status == ChessStatus.WAITING:
        return "No game in progress (lobby)."
    names = f"white={chess.white[:8]}…  black={chess.black[:8]}…"
    if chess.status == ChessStatus.ACTIVE:
        turn = "white" if chess.turn == ChessTurn.WHITE else "black"
        return f"{render_board(chess.board)}\n{names}\n{turn} to move · ply {chess.moves}"
    return f"{render_board(chess.board)}\n{names}\ngame over · winner {chess.winner[:8]}…"


class ChessClient:
    def __init__(self, group: str, urls: list[str], keypair: Keypair, store: BFTStore) -> None:
        self.group = group
        self.urls = urls
        self.keypair = keypair
        self.store = store
        self._validator_pubkeys: frozenset[str] = frozenset()
        self._propagation_threshold = 0

    async def sync(self) -> GroupState:
        """Fetch and verify the chain from the validators; return current state."""
        await sync_from_validators(group=self.group, urls=self.urls, store=self.store)
        state = self.store.get_chain_head(self.group).state
        self._validator_pubkeys = state.validator_set.pubkeys
        self._propagation_threshold = state.validator_set.propagation_threshold
        return state

    def chess(self, state: GroupState) -> ChessState:
        chess = state.app
        assert isinstance(chess, ChessState)
        return chess

    async def wait_for(
        self,
        predicate: Callable[[GroupState], bool],
        *,
        timeout: float = 20.0,
        interval: float = 0.4,
    ) -> GroupState:
        """Poll until ``predicate`` holds on the finalized group state."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = await self.sync()
            if predicate(state):
                return state
            await asyncio.sleep(interval)
        raise TimeoutError("timed out waiting for group state condition")

    async def latest_height(self) -> int:
        """Cheap finalized-height poll across validators (no full sync)."""
        clients = [BFTWebSocketClient(url, timeout=2) for url in self.urls]
        results = await asyncio.gather(
            *(client.status(self.group) for client in clients), return_exceptions=True
        )
        heights = [r.height for r in results if not isinstance(r, BaseException)]
        return max(heights) if heights else 0

    async def _publish(self, event_type: str, content: dict[str, object]) -> GroupState:
        """Publish an event; return the state it was validated against."""
        state = await self.sync()
        seq = state.sequences.get(self.keypair.pubkey_hex, 0) + 1
        event = build_event(
            type=event_type,
            group=self.group,
            author_keypair=self.keypair,
            seq=seq,
            content=content,
            ts=int(time.time()),
        )
        result = await publish_to_validators(
            event=event,
            urls=self.urls,
            validator_pubkeys=self._validator_pubkeys,
            propagation_threshold=self._propagation_threshold,
        )
        if not result.receipts:
            reasons = sorted({str(e).partition(": ")[2] or str(e) for e in result.errors})
            message = "; ".join(reasons) if reasons else "no validator accepted the event"
            raise RuntimeError(message)
        return state

    async def join(self) -> GroupState:
        return await self._publish("join", {})

    async def new_game(self, white: str, black: str) -> GroupState:
        return await self._publish(ChessTypes.NEW_GAME, {"white": white, "black": black})

    async def move(self, src: str, dst: str) -> GroupState:
        return await self._publish(ChessTypes.MOVE, {"from": src, "to": dst})

    async def resign(self) -> GroupState:
        return await self._publish(ChessTypes.RESIGN, {})


__all__ = ["ChessClient", "describe"]
