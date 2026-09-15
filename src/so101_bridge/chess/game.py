"""Rules and board bookkeeping: a thin, deliberate wrapper around python-chess.

Nothing in the bridge implements a chess rule. ``python-chess`` owns legality, castling rights, en
passant, promotion, check, stalemate and the fifty-move rule; this module owns the translation between
a position and what the camera sees, which is the only chess-shaped problem that is ours.

The camera cannot read a piece off a square — at 72 px per square it can say *something is there* and,
usually, *which colour*. That is enough, because the position before the move is known: a move is
identified by finding the legal move whose resulting occupancy matches what the camera now sees. Two
legal moves can produce the same occupancy (two knights to the same square), so ``resolve`` returns
every candidate and the caller asks for help rather than guessing.
"""

import chess
import chess.pgn

SIDES = {"white": chess.WHITE, "black": chess.BLACK}


def new_board(fen=None):
    return chess.Board(fen) if fen else chess.Board()


def side(name):
    """'white'/'black' -> the python-chess colour constant."""
    return SIDES[str(name).lower()]


def side_name(colour):
    return "white" if colour == chess.WHITE else "black"


def occupancy(board):
    """{square name: True/False} for all 64 squares — what the camera should see in this position."""
    return {chess.square_name(s): board.piece_at(s) is not None for s in chess.SQUARES}


def colours(board):
    """{square name: 'white' | 'black' | None} — what a colour-capable camera should see."""
    out = {}
    for s in chess.SQUARES:
        p = board.piece_at(s)
        out[chess.square_name(s)] = None if p is None else side_name(p.color)
    return out


def changed(before, after):
    """Squares whose occupancy differs between two readings, in board order."""
    return [s for s in sorted(before) if bool(before[s]) != bool(after.get(s))]


def resolve(board, after_occ, after_colours=None):
    """Every legal move from ``board`` whose resulting occupancy matches ``after_occ``.

    ``after_occ`` is ``{square: bool}``; missing squares are treated as unknown and ignored, so a
    partial reading still narrows the field. ``after_colours`` (``{square: 'white'|'black'|None}``)
    is used only to discard candidates it contradicts — an unknown colour never rejects a move.
    Returns a list of UCI strings: one entry is the answer, several mean ask, none means the board
    does not show any legal position (a knocked-over piece, a hand in frame, an illegal human move).
    """
    out = []
    for mv in board.legal_moves:
        board.push(mv)
        try:
            occ = occupancy(board)
            if any(bool(v) != occ[s] for s, v in after_occ.items() if s in occ):
                continue
            if after_colours:
                cols = colours(board)
                if any(c is not None and cols[s] is not None and c != cols[s]
                       for s, c in after_colours.items() if s in cols):
                    continue
            out.append(mv.uci())
        finally:
            board.pop()
    return out


def is_legal(board, uci):
    try:
        mv = chess.Move.from_uci(str(uci).strip().lower())
    except ValueError:
        return False
    return mv in board.legal_moves


def push(board, uci):
    """Play a UCI move and return (san, fen_after). Raises ValueError if it is not legal here."""
    mv = chess.Move.from_uci(str(uci).strip().lower())
    if mv not in board.legal_moves:
        raise ValueError(f"{uci} is not legal in {board.fen()}")
    san = board.san(mv)
    board.push(mv)
    return san, board.fen()


def result(board):
    """None while the game is on, else the reason and the score: ('checkmate', '1-0')."""
    if board.is_checkmate(): return "checkmate", board.result()
    if board.is_stalemate(): return "stalemate", "1/2-1/2"
    if board.is_insufficient_material(): return "insufficient material", "1/2-1/2"
    if board.is_seventyfive_moves(): return "seventy-five moves", "1/2-1/2"
    if board.is_fivefold_repetition(): return "fivefold repetition", "1/2-1/2"
    if board.can_claim_draw(): return "draw claimable", "1/2-1/2"
    return None


def pgn(start_fen, ucis, headers=None):
    """A PGN string for a finished or running game — the human-readable mirror of the game log."""
    game = chess.pgn.Game()
    board = chess.Board(start_fen) if start_fen else chess.Board()
    if start_fen and board.fen() != chess.Board().fen():
        game.setup(board)
    for k, v in (headers or {}).items():
        game.headers[k] = str(v)
    node = game
    for u in ucis:
        mv = chess.Move.from_uci(u)
        node = node.add_variation(mv)
        board.push(mv)
    game.headers["Result"] = board.result(claim_draw=False)
    return str(game)


# ---------------------------------------------------------------- what the arm has to do about a move
def move_kind(board, uci):
    """Classify a move *before* it is played: what the physical execution has to handle.

    ``{"from", "to", "piece", "capture": square|None, "castle": (rook_from, rook_to)|None,
       "promotion": piece|None, "en_passant": bool}`` — everything ``physical.plan`` needs, and nothing
    it would otherwise have to re-derive from the FEN."""
    mv = chess.Move.from_uci(str(uci).strip().lower())
    piece = board.piece_at(mv.from_square)
    if piece is None: raise ValueError(f"no piece on {chess.square_name(mv.from_square)}")
    info = {"from": chess.square_name(mv.from_square), "to": chess.square_name(mv.to_square),
            "piece": piece.symbol().lower(), "colour": side_name(piece.color),
            "capture": None, "castle": None, "promotion": None, "en_passant": False}
    if board.is_en_passant(mv):
        info["en_passant"] = True
        taken = mv.to_square + (-8 if piece.color == chess.WHITE else 8)
        info["capture"] = chess.square_name(taken)
    elif board.piece_at(mv.to_square) is not None:
        info["capture"] = chess.square_name(mv.to_square)
    if board.is_castling(mv):
        rank = chess.square_rank(mv.from_square)
        kingside = chess.square_file(mv.to_square) > chess.square_file(mv.from_square)
        rook_from = chess.square(7 if kingside else 0, rank)
        rook_to = chess.square(5 if kingside else 3, rank)
        info["castle"] = (chess.square_name(rook_from), chess.square_name(rook_to))
    if mv.promotion:
        info["promotion"] = chess.piece_symbol(mv.promotion)
    return info
