"""Pure chess domain logic for the FERN chess example.

No FERN imports here: this module is the game itself (squares, movement,
captures, promotion) and could be used by any chess program. The FERN-specific
parts — ``ChessState`` and the ``ChessApp`` app module — live in
``chess_app.py`` and delegate to this.

Simplified rules: normal piece movement and captures; no check/checkmate (the
game ends when a king is captured); no castling or en passant; pawns
auto-promote to a queen.
"""
from __future__ import annotations

FILES = "abcdefgh"


def parse_square(square: object, field: str = "square") -> tuple[int, int]:
    """Return zero-based (file, rank) indices, validating the notation."""
    if not isinstance(square, str) or len(square) != 2:
        raise ValueError(f"{field} must be a square like 'e4'")
    file = FILES.find(square[0])
    rank = "12345678".find(square[1])
    if file < 0 or rank < 0:
        raise ValueError(f"{field} must be a square like 'e4'")
    return file, rank


def square_name(file: int, rank: int) -> str:
    return FILES[file] + str(rank + 1)


def initial_board() -> dict[str, str]:
    """The standard starting position: square -> piece code ("wP", "bK", ...)."""
    board: dict[str, str] = {}
    back_rank = "RNBQKBNR"
    for file, piece in enumerate(back_rank):
        board[square_name(file, 0)] = "w" + piece
        board[square_name(file, 1)] = "wP"
        board[square_name(file, 6)] = "bP"
        board[square_name(file, 7)] = "b" + piece
    return board


def _path_clear(board: dict[str, str], sf: int, sr: int, df: int, dr: int) -> bool:
    """True if every square strictly between (sf, sr) and (df, dr) is empty."""
    step_f = (df > sf) - (df < sf)
    step_r = (dr > sr) - (dr < sr)
    file, rank = sf + step_f, sr + step_r
    while (file, rank) != (df, dr):
        if square_name(file, rank) in board:
            return False
        file += step_f
        rank += step_r
    return True


def is_legal_move(board: dict[str, str], colour: str, src: str, dst: str) -> bool:
    """Basic move legality for ``colour`` (no awareness of check)."""
    if src == dst:
        return False
    piece = board.get(src)
    if piece is None or piece[0] != colour:
        return False
    target = board.get(dst)
    if target is not None and target[0] == colour:
        return False  # cannot capture your own piece
    sf, sr = parse_square(src)
    df, dr = parse_square(dst)
    d_file, d_rank = df - sf, dr - sr
    kind = piece[1]

    if kind == "P":
        direction = 1 if colour == "w" else -1
        start_rank = 1 if colour == "w" else 6
        if d_file == 0 and d_rank == direction and target is None:
            return True
        if d_file == 0 and d_rank == 2 * direction and sr == start_rank and target is None:
            return square_name(sf, sr + direction) not in board
        return abs(d_file) == 1 and d_rank == direction and target is not None
    if kind == "N":
        return (abs(d_file), abs(d_rank)) in {(1, 2), (2, 1)}
    if kind == "K":
        return abs(d_file) <= 1 and abs(d_rank) <= 1
    if kind == "B":
        return abs(d_file) == abs(d_rank) != 0 and _path_clear(board, sf, sr, df, dr)
    if kind == "R":
        return (d_file == 0) != (d_rank == 0) and _path_clear(board, sf, sr, df, dr)
    if kind == "Q":
        straight = (d_file == 0) != (d_rank == 0)
        diagonal = abs(d_file) == abs(d_rank) != 0
        return (straight or diagonal) and _path_clear(board, sf, sr, df, dr)
    return False


def apply_move(
    board: dict[str, str], colour: str, src: str, dst: str
) -> tuple[dict[str, str], str | None]:
    """Apply a legal move; return (new_board, captured_piece_or_None)."""
    piece = board[src]
    captured = board.get(dst)
    new_board = dict(board)
    del new_board[src]
    _, rank = parse_square(dst)
    if piece[1] == "P" and rank in (0, 7):
        new_board[dst] = colour + "Q"  # auto-promote
    else:
        new_board[dst] = piece
    return new_board, captured


def render_board(board: dict[str, str]) -> str:
    lines = [""]  # whitespace above the board
    for rank in range(7, -1, -1):
        cells = []
        for file in range(8):
            piece = board.get(square_name(file, rank))
            cells.append("." if piece is None else (piece[1] if piece[0] == "w" else piece[1].lower()))
        lines.append(f"{rank + 1}  " + " ".join(cells))
    lines.append("")  # whitespace between the grid and the file labels
    lines.append("   " + " ".join(FILES))
    return "\n".join(lines)


__all__ = [
    "FILES",
    "apply_move",
    "initial_board",
    "is_legal_move",
    "parse_square",
    "render_board",
    "square_name",
]
