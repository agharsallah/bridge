"""Rules and the occupancy-diff move resolver, on real games replayed square by square."""

import chess
import pytest

from so101_bridge.chess import game as g

# Scholar's mate: covers a capture; the Italian opening below covers castling; then en passant and promotion.
SCHOLARS = ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"]
CASTLING = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "e1g1"]
EN_PASSANT = ["e2e4", "a7a6", "e4e5", "d7d5", "e5d6"]


def replay(ucis, start=None):
    """Play a line, yielding (board before the move, uci, occupancy after) — what the camera would see."""
    board = g.new_board(start)
    for u in ucis:
        before = g.new_board(board.fen())
        board.push(chess.Move.from_uci(u))
        yield before, u, g.occupancy(board), g.colours(board)


@pytest.mark.parametrize("line", [SCHOLARS, CASTLING, EN_PASSANT], ids=["scholars", "castling", "en_passant"])
def test_every_move_of_a_real_game_is_resolved_from_occupancy_alone(line):
    for before, uci, occ, cols in replay(line):
        cands = g.resolve(before, occ, cols)
        assert uci in cands, f"{uci} not among {cands}"
        assert len(cands) == 1, f"{uci} ambiguous with {cands}"


def test_promotion_resolves_but_not_which_piece():
    """Occupancy cannot tell a new queen from a new knight — the routine must ask, not guess."""
    board = g.new_board("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    after = g.new_board(board.fen()); after.push(chess.Move.from_uci("a7a8q"))
    cands = g.resolve(board, g.occupancy(after))
    assert set(cands) == {"a7a8q", "a7a8r", "a7a8b", "a7a8n"}


def test_the_origin_square_disambiguates_two_knights():
    """Both knights can go to d5, but only one of them leaves its own square empty — so occupancy is enough.

    This is why the resolver works at all from a binary reading: what the camera cannot see is *which*
    piece arrived, and the position before the move already says that. Genuine ambiguity is therefore
    rare, and where it does exist (a promotion, see above) it is a question about the piece, not the move."""
    board = g.new_board("7k/8/8/8/8/2N1N3/8/4K3 w - - 0 1")
    assert {"c3d5", "e3d5"} <= {m.uci() for m in board.legal_moves}
    for uci in ("c3d5", "e3d5"):
        after = g.new_board(board.fen()); after.push(chess.Move.from_uci(uci))
        assert g.resolve(board, g.occupancy(after)) == [uci]


def test_colour_breaks_a_tie_that_occupancy_cannot():
    """Both sides' pieces can land on the same square; the camera's colour reading discards the wrong one."""
    board = g.new_board("4k3/8/8/3p4/8/8/8/3QK3 w - - 0 1")
    after = g.new_board(board.fen()); after.push(chess.Move.from_uci("d1d5"))
    occ = g.occupancy(after)
    assert "d1d5" in g.resolve(board, occ)
    wrong = {**g.colours(after), "d5": "black"}                     # a camera insisting d5 is still black
    assert "d1d5" not in g.resolve(board, occ, wrong)


def test_a_knocked_over_board_resolves_to_nothing():
    board = g.new_board()
    nonsense = {s: False for s in g.occupancy(board)}
    assert g.resolve(board, nonsense) == []


def test_partial_readings_still_narrow_the_field():
    """Squares the camera could not read are simply left out, rather than voting for 'empty'."""
    board = g.new_board()
    after = g.new_board(); after.push(chess.Move.from_uci("e2e4"))
    partial = {s: v for s, v in g.occupancy(after).items() if s[0] in "de"}
    assert "e2e4" in g.resolve(board, partial)


def test_move_kind_describes_what_the_arm_has_to_do():
    board = g.new_board("rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    assert g.move_kind(board, "e1g1")["castle"] == ("h1", "f1")
    assert g.move_kind(board, "c4f7")["capture"] == "f7"
    board = g.new_board("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    assert g.move_kind(board, "a7a8q")["promotion"] == "q"
    board = g.new_board("rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 3")
    ep = g.move_kind(board, "d4e3")
    assert ep["en_passant"] and ep["capture"] == "e4"               # the pawn taken is not on the target square


def test_legality_and_results():
    board = g.new_board()
    assert g.is_legal(board, "e2e4") and not g.is_legal(board, "e2e5")
    assert not g.is_legal(board, "nonsense")
    with pytest.raises(ValueError):
        g.push(board, "e2e5")
    mate = g.new_board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    assert g.result(mate) == ("checkmate", "0-1")
    assert g.result(g.new_board()) is None


def test_pgn_mirrors_the_move_list():
    text = g.pgn(None, SCHOLARS, {"White": "human", "Black": "llm"})
    assert "1. e4 e5" in text and "Qxf7#" in text and '[Black "llm"]' in text
