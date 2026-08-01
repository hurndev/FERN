"""FERN app module: chess (the protocol adapter for the chess board engine).

Implements :class:`fern.bft.app.AppModule` for a two-player chess game, using
the pure game logic from :mod:`board`. It is registered under the app name
``chess`` — the value a group commits in its genesis ``app`` field.

The dependency direction is app -> core: this module imports the core, the core
never imports it. A process hosts chess by calling ``register_app(CHESS_APP)``;
the core then looks the module up by name and delegates all chess policy and
state to it.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from fern.bft.app import ApplicationError, AppState, CoreState
from fern.bft.canonical import strict_dict, strict_int
from fern.events.event import Event
from fern.events.semantic import SemanticValidationError, only_fields, pubkey_field
from fern.events.types import ProtocolTypes

from board import apply_move, initial_board, is_legal_move, parse_square


class ChessTypes:
    """Chess event types. The namespace must equal the app name."""

    NEW_GAME = "chess.new_game"
    MOVE = "chess.move"
    RESIGN = "chess.resign"


class ChessStatus:
    WAITING = "waiting"
    ACTIVE = "active"
    OVER = "over"


class ChessTurn:
    WHITE = "w"
    BLACK = "b"


@dataclass(frozen=True)
class ChessState:
    white: str  # player pubkey, "" before a game starts
    black: str
    board: dict[str, str]
    turn: str  # ChessTurn
    status: str  # ChessStatus
    winner: str  # player pubkey, or ""
    moves: int  # plies played in the current/last game

    def to_dict(self) -> dict[str, object]:
        return {
            "chess_white": self.white,
            "chess_black": self.black,
            "chess_board": {key: self.board[key] for key in sorted(self.board)},
            "chess_turn": self.turn,
            "chess_status": self.status,
            "chess_winner": self.winner,
            "chess_moves": self.moves,
        }


class ChessApp:
    name = "chess"
    # Starting a game changes who may act, so it is a boundary event (applied
    # last, at most one per block). Moves and resignations are ordinary.
    boundary_types = frozenset({ChessTypes.NEW_GAME})

    # --- state construction / serialization --------------------------------

    def initial_state(self, genesis_content: dict[str, object]) -> ChessState:
        return ChessState(
            white="",
            black="",
            board={},
            turn=ChessTurn.WHITE,
            status=ChessStatus.WAITING,
            winner="",
            moves=0,
        )

    def state_from_dict(self, value: dict[str, object]) -> ChessState:
        raw_board = strict_dict(value.get("chess_board"), "state chess_board")
        return ChessState(
            white=str(value.get("chess_white", "")),
            black=str(value.get("chess_black", "")),
            board={str(key): str(item) for key, item in raw_board.items()},
            turn=str(value.get("chess_turn", ChessTurn.WHITE)),
            status=str(value.get("chess_status", ChessStatus.WAITING)),
            winner=str(value.get("chess_winner", "")),
            moves=strict_int(value.get("chess_moves"), "state chess_moves"),
        )

    # --- validation --------------------------------------------------------

    def validate_genesis(self, genesis_content: dict[str, object]) -> None:
        # Chess needs no app-specific genesis fields; reject any we don't know.
        for key in genesis_content:
            if key.startswith("chess."):
                raise SemanticValidationError(f"unexpected chess genesis field: {key}")

    def validate_semantics(self, event: Event) -> None:
        content = event.content
        event_type = event.type
        if event_type == ChessTypes.NEW_GAME:
            only_fields(content, {"white", "black"})
            pubkey_field(content.get("white"), "white")
            pubkey_field(content.get("black"), "black")
        elif event_type == ChessTypes.MOVE:
            only_fields(content, {"from", "to"})
            parse_square(content.get("from"), "from")
            parse_square(content.get("to"), "to")
        elif event_type == ChessTypes.RESIGN:
            only_fields(content, set())
        else:
            raise SemanticValidationError(f"unknown chess event type: {event_type}")

    def validate_for_state(
        self, core: CoreState, app: AppState, event: Event, certified_time_ms: int
    ) -> None:
        assert isinstance(app, ChessState)
        content = event.content
        event_type = event.type
        certified_seconds = certified_time_ms // 1000

        if event_type == ChessTypes.NEW_GAME:
            if app.status == ChessStatus.ACTIVE:
                raise ApplicationError("a game is already in progress")
            white = str(content["white"])
            black = str(content["black"])
            if white == black:
                raise ApplicationError("white and black must differ")
            for player in (white, black):
                if player not in core.joined or core.is_banned_at(player, certified_seconds):
                    raise ApplicationError("both players must be eligible members")
        elif event_type == ChessTypes.MOVE:
            if app.status != ChessStatus.ACTIVE:
                raise ApplicationError("no active game")
            player = app.white if app.turn == ChessTurn.WHITE else app.black
            if event.author != player:
                raise ApplicationError("it is not your turn")
            if not is_legal_move(app.board, app.turn, str(content["from"]), str(content["to"])):
                raise ApplicationError("illegal move")
        elif event_type == ChessTypes.RESIGN:
            if app.status != ChessStatus.ACTIVE:
                raise ApplicationError("no active game")

    # --- authorization -----------------------------------------------------

    def authorize(
        self, core: CoreState, app: AppState, event: Event, certified_time_ms: int
    ) -> bool:
        assert isinstance(app, ChessState)
        signer = event.author
        certified_seconds = certified_time_ms // 1000
        if signer not in core.joined or core.is_banned_at(signer, certified_seconds):
            return False
        content = event.content
        event_type = event.type
        if event_type == ChessTypes.NEW_GAME:
            # You may start a game only as one of its two players. (Defensive:
            # content was already checked by validate_semantics.)
            return signer in {str(content.get("white", "")), str(content.get("black", ""))}
        if event_type in (ChessTypes.MOVE, ChessTypes.RESIGN):
            return signer in {app.white, app.black}
        return False

    # --- state transitions -------------------------------------------------

    def apply_event(
        self, core: CoreState, app: AppState, event: Event, certified_time_ms: int
    ) -> ChessState:
        assert isinstance(app, ChessState)
        content = event.content
        event_type = event.type

        if event_type == ChessTypes.NEW_GAME:
            return replace(
                app,
                white=str(content["white"]),
                black=str(content["black"]),
                board=initial_board(),
                turn=ChessTurn.WHITE,
                status=ChessStatus.ACTIVE,
                winner="",
                moves=0,
            )
        if event_type == ChessTypes.MOVE:
            colour = app.turn
            new_board, captured = apply_move(
                app.board, colour, str(content["from"]), str(content["to"])
            )
            status = app.status
            winner = app.winner
            if captured is not None and captured[1] == "K":
                status = ChessStatus.OVER
                winner = event.author  # capturing a king wins
            return replace(
                app,
                board=new_board,
                turn=ChessTurn.BLACK if colour == ChessTurn.WHITE else ChessTurn.WHITE,
                status=status,
                winner=winner,
                moves=app.moves + 1,
            )
        if event_type == ChessTypes.RESIGN:
            winner = app.white if event.author == app.black else app.black
            return replace(app, status=ChessStatus.OVER, winner=winner)
        if event_type == ProtocolTypes.LEAVE and app.status == ChessStatus.ACTIVE:
            # A player who leaves an active game forfeits it. ``leave`` is a
            # self-service core event, so this shows the app applying a side
            # effect to a core membership transition (cf. chat dropping roles).
            if event.author in {app.white, app.black}:
                winner = app.white if event.author == app.black else app.black
                return replace(app, status=ChessStatus.OVER, winner=winner)
        # Core membership events and anything else leave chess state unchanged.
        return app


CHESS_APP = ChessApp()


__all__ = [
    "CHESS_APP",
    "ChessApp",
    "ChessState",
    "ChessStatus",
    "ChessTurn",
    "ChessTypes",
]
