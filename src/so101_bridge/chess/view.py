"""How a game is shown: the position, the captured pieces and the move history the page draws.

Everything chess-specific about the ``/chess`` game panels lives here so the page itself does no
chess and no orientation arithmetic — it lays out the squares and the rows in the order given.

The move rows are built by replaying the game from ``start_fen``: that is the only way to know what
a move actually *did* (which piece moved, what it took, whether it castled) from a document that
stores no more than the UCI and the FEN after. Replaying fifty moves costs nothing and keeps the
game document small, which is what the LLM reads.
"""

import chess

from . import game as rules
from . import workspace as ws_mod

GLYPHS = {"K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
          "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟"}
NAMES = {"p": "pawn", "n": "knight", "b": "bishop", "r": "rook", "q": "queen", "k": "king"}
VALUE = {"p": 1, "n": 3, "b": 3, "r": 5, "q": 9, "k": 0}


def board_view(d, fen=None, last=None, start_fen=None):
    """The position as the page draws it: one entry per square, in the robot's own viewing order.

    Each square carries ``mark`` — ``from``, ``to``, ``captured`` or ``check`` — so the page can show
    what the last move did without knowing the rules. ``files``/``ranks`` are the edge labels in the
    same order, and ``captured`` is what each side has lost since the starting position."""
    board = chess.Board(fen) if fen else chess.Board()
    d = d or ws_mod.load()
    white_near = d["board"]["robot_side"] == "white"
    files = list(ws_mod.FILES) if white_near else list(ws_mod.FILES)[::-1]
    ranks = list(range(8, 0, -1)) if white_near else list(range(1, 9))
    marks = dict(_marks(board, last))
    out = []
    for name in ws_mod.squares(d):
        sq = chess.parse_square(name)
        p = board.piece_at(sq)
        f, r = ws_mod.parse_square(name)
        out.append({"name": name, "piece": p.symbol() if p else None,
                    "glyph": GLYPHS.get(p.symbol()) if p else None,
                    "colour": (None if p is None else ("white" if p.color else "black")),
                    "light": (f + r) % 2 == 1, "mark": marks.get(name)})
    lost = _captured(board, start_fen)
    return {"squares": out, "turn": "white" if board.turn else "black",
            "check": board.is_check(), "fen": board.fen(), "files": files, "ranks": ranks,
            "captured": lost, "material": _material(lost)}


def _marks(board, last):
    """``{square: mark}`` for the last move, so the page can paint it without replaying anything."""
    if last:
        if last.get("from"): yield last["from"], "from"
        if last.get("to"): yield last["to"], "to"
        cap = last.get("capture")
        if cap and cap["square"] != last.get("to"): yield cap["square"], "captured"
    if board.is_check():
        king = board.king(board.turn)
        if king is not None: yield chess.square_name(king), "check"


def _captured(board, start_fen=None):
    """What each side has lost, as glyphs, heaviest first — read off the board, not off the moves."""
    start = chess.Board(start_fen) if start_fen else chess.Board()
    out = {"white": [], "black": []}
    for colour, key in ((chess.WHITE, "white"), (chess.BLACK, "black")):
        for sym in "qrbnp":
            kind = chess.PIECE_SYMBOLS.index(sym)
            piece = sym.upper() if colour == chess.WHITE else sym
            gone = len(start.pieces(kind, colour)) - len(board.pieces(kind, colour))
            out[key] += [{"piece": piece, "glyph": GLYPHS[piece], "value": VALUE[sym]}] * max(0, gone)
    return out


def _material(lost):
    """Material balance in pawns: positive means white is up."""
    black_lost = sum(p["value"] for p in lost["black"])
    white_lost = sum(p["value"] for p in lost["white"])
    return {"white": black_lost, "black": white_lost, "diff": black_lost - white_lost}


def move_rows(doc):
    """One row per ply, oldest first: who moved, what moved, where from and to, what the arm did.

    A row is everything the history panel shows without a second lookup — the piece and its glyph,
    the capture with the square it was lifted from, the flags that made the move physically special,
    the arm's own pick/place chain and how long it took.
    """
    if not doc: return []
    start_fen = doc.get("start_fen") or chess.STARTING_FEN
    board = chess.Board(start_fen)
    rows, prev_t = [], None
    for m in doc.get("moves", []):
        row = {k: m.get(k) for k in ("ply", "by", "uci", "san", "executed_by", "verify", "time",
                                     "exec", "source")}
        row["no"] = (m.get("ply", 1) + 1) // 2
        row["side"] = "white" if board.turn else "black"
        row["flags"] = []
        try:
            info = rules.move_kind(board, m["uci"])
            row.update({"from": info["from"], "to": info["to"], "piece": info["piece"],
                        "piece_name": NAMES.get(info["piece"], info["piece"]),
                        "glyph": GLYPHS.get(info["piece"].upper() if info["colour"] == "white"
                                            else info["piece"])})
            if info["capture"]:
                taken = board.piece_at(chess.parse_square(info["capture"]))
                sym = taken.symbol() if taken else None
                row["capture"] = {"square": info["capture"], "piece": sym,
                                  "glyph": GLYPHS.get(sym), "name": NAMES.get((sym or "").lower())}
                row["flags"].append("en passant" if info["en_passant"] else "capture")
            if info["castle"]: row["flags"].append("castle")
            if info["promotion"]: row["flags"].append(f"promotes to {NAMES.get(info['promotion'])}")
            board.push(chess.Move.from_uci(m["uci"]))
        except Exception:                     # a document written by an older build, or a hand-edited FEN
            board = chess.Board(m.get("fen_after") or board.fen())
        if board.is_checkmate(): row["flags"].append("mate")
        elif board.is_check(): row["flags"].append("check")
        row["trace"] = [_action_text(a) for a in (m.get("actions") or [])]
        row["gap_s"], prev_t = _gap(prev_t, m.get("time")), m.get("time")
        rows.append(row)
    return rows


def _action_text(a):
    """One arm action as the operator reads it: ``pick pawn e2``, ``place handover (captured)``."""
    where = a.get("sq") or a.get("slot") or "?"
    piece = NAMES.get(a.get("piece"), a.get("piece") or "")
    why = f" ({a['why']})" if a.get("why") else ""
    return f"{a.get('op', '?')} {piece} {where}{why}".replace("  ", " ").strip()


def _gap(prev, now):
    """Seconds between two ``HH:MM:SS`` stamps, or ``None`` when they cannot be compared."""
    try:
        a = [int(x) for x in str(prev).split(":")]; b = [int(x) for x in str(now).split(":")]
        gap = (b[0] * 3600 + b[1] * 60 + b[2]) - (a[0] * 3600 + a[1] * 60 + a[2])
        return gap if gap >= 0 else None
    except Exception:
        return None
