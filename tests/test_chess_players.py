"""Who chooses the robot's move, and the game document they choose through."""

import threading

import pytest

from so101_bridge.chess import game as rules
from so101_bridge.chess import players
from so101_bridge.chess.log import GameLog


class FakeCtrl:
    def __init__(self):
        self.lock = threading.Lock()
        self.chess_request = {}
        self.routine_abort = threading.Event()


@pytest.fixture
def glog():
    return GameLog.new(mode="play", robot="black", players={"white": "human", "black": "llm"})


def test_llm_player_takes_a_legal_move_and_clears_the_request(glog):
    ctrl = FakeCtrl(); board = rules.new_board()
    ctrl.chess_request = {"move": "E2E4 "}
    assert players.LlmPlayer(timeout_s=2).choose(board, glog, ctrl) == "e2e4"
    assert "move" not in ctrl.chess_request
    assert glog.doc["waiting_for"] == "llm"           # it parked the game there while it waited


def test_llm_player_rejects_an_illegal_move_keeps_waiting_and_says_why(glog):
    ctrl = FakeCtrl(); board = rules.new_board()
    ctrl.chess_request = {"move": "e2e5"}
    p = players.LlmPlayer(timeout_s=0.6)
    assert p.choose(board, glog, ctrl) is None
    need = GameLog.load(glog.id).doc["needs"]
    assert need["kind"] == "illegal_move" and need["move"] == "e2e5" and "e2e4" in need["hint"]


def test_llm_player_gives_up_quietly_on_abort(glog):
    ctrl = FakeCtrl(); ctrl.routine_abort.set()
    assert players.LlmPlayer(timeout_s=5).choose(rules.new_board(), glog, ctrl) is None


def test_human_player_never_chooses(glog):
    assert players.HumanPlayer().choose(rules.new_board(), glog, FakeCtrl()) is None


def test_script_player_follows_the_line_then_declares_itself_off_book(glog):
    ctrl = FakeCtrl(); board = rules.new_board()
    p = players.ScriptPlayer(["e2e4", "d2d4"])
    assert p.choose(board, glog, ctrl) == "e2e4"
    rules.push(board, "e2e4"); rules.push(board, "e7e5")
    assert p.choose(board, glog, ctrl) == "d2d4"
    assert p.choose(board, glog, ctrl) is None
    assert GameLog.load(glog.id).doc["needs"]["kind"] == "off_book"


def test_script_player_stops_when_the_position_has_left_the_line(glog):
    ctrl = FakeCtrl()
    p = players.ScriptPlayer(["a1a8"])
    assert p.choose(rules.new_board(), glog, ctrl) is None
    assert GameLog.load(glog.id).doc["needs"]["kind"] == "off_book"


def test_asking_for_an_engine_without_one_installed_falls_back_to_waiting_for_a_move(monkeypatch):
    monkeypatch.setattr(players, "CHESS_ENGINE", "")
    assert isinstance(players.make("engine"), players.LlmPlayer)
    with pytest.raises(ValueError):
        players.make("telepathy")


def test_the_game_document_is_small_and_says_what_it_is_waiting_for(glog):
    board = glog.board()
    san, fen = rules.push(board, "e2e4")
    glog.move(by="human", uci="e2e4", san=san, fen_after=fen, executed_by="human", verify="ok")
    doc = GameLog.load(glog.id).doc
    assert doc["turn"] == "black" and doc["moves"][0]["san"] == "e4"
    assert doc["pgn"].strip().startswith("[Event")
    s = glog.summary()
    assert set(s) >= {"id", "mode", "fen", "turn", "waiting_for", "phase", "needs", "last_move"}
    assert len(str(s)) < 900                                     # the LLM reads this every turn


def test_a_need_is_recorded_and_cleared(glog):
    glog.need("unreachable", move="e2e4", actions=[])
    assert GameLog.load(glog.id).doc["needs"]["kind"] == "unreachable"
    glog.clear_need()
    assert GameLog.load(glog.id).doc["needs"] is None
    with pytest.raises(ValueError):
        glog.need("something_else")


def test_a_finished_game_records_how_it_ended(glog):
    board = glog.board()
    for u in ["f2f3", "e7e5", "g2g4", "d8h4"]:
        san, fen = rules.push(board, u)
        glog.move(by="human", uci=u, san=san, fen_after=fen)
    doc = GameLog.load(glog.id).doc
    assert doc["result"] == {"reason": "checkmate", "score": "0-1"}
    assert "#" in doc["moves"][-1]["san"]
