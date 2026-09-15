"""The /chess page's data contract: every panel must have something real behind it.

The page does no chess and no geometry — it lays out what ``chess_state`` hands it. So these tests
check the *shape* the drawing code relies on, which is what breaks silently when the backend changes.
"""

import sys
import threading

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from chess_synth import WIDE_LIMITS, floor_model, taught  # noqa: E402
from so101_bridge import dashboard  # noqa: E402
from so101_bridge.chess import game as rules  # noqa: E402
from so101_bridge.chess import history as H  # noqa: E402
from so101_bridge.chess import workspace as ws  # noqa: E402
from so101_bridge.chess.log import GameLog  # noqa: E402


class FakeCtrl:
    def __init__(self, d):
        self.lock = threading.Lock()
        self.floor = floor_model()
        self.routine_status = "idle"
        self.teach = None
        self.chess_reader = None
        self.frame = {}
        self.present = {**d["board"]["corners"]["a1"], "gripper": 10.0}

    def snapshot(self):
        return {"present": dict(self.present), "mode": "hold", "torque_on": True, "estop": False,
                "limits": WIDE_LIMITS, "time": "12:00:00"}, {}


@pytest.fixture
def ctrl():
    d = taught(); ws.save(d)
    return FakeCtrl(d)


def test_the_position_panel_gets_64_squares_in_the_robots_order(ctrl):
    st = dashboard.chess_state(ctrl)
    sq = st["board"]["squares"]
    assert len(sq) == 64
    assert [s["name"] for s in sq] == ws.squares(ws.load())
    assert sum(1 for s in sq if s["glyph"]) == 32                  # a fresh board has 32 pieces on it
    a8 = next(s for s in sq if s["name"] == "a8")
    assert a8["piece"] == "r" and a8["colour"] == "black" and a8["glyph"]
    assert st["board"]["turn"] == "white" and st["board"]["check"] is False
    assert {s["light"] for s in sq} == {True, False}               # the board is actually chequered


@pytest.mark.parametrize("side", ["white", "black"])
def test_the_position_panel_puts_the_robots_own_rank_at_the_bottom(ctrl, side):
    d = ws.load(); d["board"]["robot_side"] = side; ws.save(d)
    sq = dashboard.chess_state(ctrl)["board"]["squares"]
    near = sq[-8:]                                                  # the row the page draws last
    assert all(s["name"][1] == ("1" if side == "white" else "8") for s in near)


def test_the_camera_panel_says_which_step_is_missing_rather_than_showing_nothing(ctrl):
    st = dashboard.chess_state(ctrl)
    assert st["camera"]["ok"] is False and "image corners" in st["camera"]["why"]
    d = ws.load()
    d["board"]["image_corners"] = {"a1": [100, 400], "h1": [500, 410], "h8": [495, 90], "a8": [105, 95]}
    ws.save(d)
    from so101_bridge.chess import vision as cv
    ctrl.chess_reader = cv.BoardReader(ws.load())
    st = dashboard.chess_state(ctrl)
    assert st["camera"]["ok"] is False and "empty-board reference" in st["camera"]["why"]


def test_the_camera_panel_compares_what_is_seen_with_what_should_be_there(ctrl):
    import numpy as np

    from so101_bridge.chess import vision as cv
    d = ws.load()
    d["board"]["image_corners"] = {"a1": [10, 250], "h1": [250, 250], "h8": [250, 10], "a8": [10, 10]}
    ws.save(d)
    reader = cv.BoardReader(ws.load())
    empty = np.full((300, 300, 3), 30, np.uint8)
    assert reader.set_reference(empty)
    ctrl.chess_reader = reader
    ctrl.frame[d["board"]["cam"]] = empty                            # an empty board, so nothing is "seen"
    GameLog.new(mode="play", robot="white")
    st = dashboard.chess_state(ctrl)
    cam = st["camera"]
    assert cam["ok"] and len(cam["cells"]) == 64
    assert all(c["seen"] is False for c in cam["cells"])
    # the game says 32 pieces are on the board, the camera sees none: every one of those is a mismatch
    assert len(cam["mismatch"]) == 32
    assert {c["name"] for c in cam["cells"] if not c["agree"]} == set(cam["mismatch"])


def test_the_side_view_gets_the_arm_and_where_the_board_sits_on_it(ctrl):
    st = dashboard.chess_state(ctrl)["side"]
    assert st["fitted"] and len(st["arm"]) == 4                      # shoulder, elbow, wrist, fingertips
    assert st["board_z_cm"] is not None
    assert set(st["heights"]) >= {"hover_cm", "carry_cm", "grasp_default_cm"}
    assert st["margin_cm"] > 0


def test_the_pose_panel_gets_a_copyable_live_pose(ctrl):
    present = dashboard.chess_state(ctrl)["arm"]["present"]
    assert set(present) >= {"shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex"}
    assert all(isinstance(v, (int, float)) for v in present.values())


def test_the_move_list_carries_what_the_arm_did_not_just_what_was_played(ctrl):
    g = GameLog.new(mode="play", robot="white", players={"white": "llm", "black": "human"})
    board = g.board()
    san, fen = rules.push(board, "e2e4")
    g.move(by="llm", uci="e2e4", san=san, fen_after=fen, executed_by="robot", verify="ok",
           exec={"seconds": 7.2, "steps": 11, "points": 60, "grips": 3})
    rows = dashboard.chess_state(ctrl)["moves"]
    assert rows[-1]["san"] == "e4" and rows[-1]["executed_by"] == "robot"
    assert rows[-1]["verify"] == "ok" and rows[-1]["exec"]["seconds"] == 7.2


def test_the_learning_panel_and_the_suggestions_come_from_the_history(ctrl):
    st = dashboard.chess_state(ctrl)
    assert st["history"]["totals"]["rows"] == 0 and st["suggestions"] == []
    for i in range(6):
        H.record_grasp("g1", i + 1, "p", "e2", 1.4, 20.0, 21.0, 12, True)
    st = dashboard.chess_state(ctrl)
    assert st["history"]["pieces"]["p"]["grasps"] == 6
    assert st["history"]["totals"]["grasp_success_pct"] == 100.0
    ids = [s["id"] for s in st["suggestions"]]
    assert "stall_p" in ids
    for s in st["suggestions"]:                                      # every suggestion carries its evidence
        assert s["what"] and s["why"]
        assert ("path" in s) and (s["path"] is None or s["value"] is not None)


def test_an_untaught_board_still_renders_the_page(ctrl):
    ws.clear_board_marks()
    st = dashboard.chess_state(ctrl)
    assert st["model"]["ok"] is False and st["reach"] == {}
    assert len(st["board"]["squares"]) == 64                         # the position is drawn regardless
    assert st["side"]["board_z_cm"] is None
    assert "jaws" not in st
