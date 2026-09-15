"""The chess routines against a simulated arm: a whole game, headless, in a fraction of a second.

The simulator answers only what the routine actually asks the arm — where are you, go here, close the
jaws — so the test exercises the real turn loop, the real path compilation and the real failure paths.
"""

import sys
import threading
import time

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from chess_synth import WIDE_LIMITS, floor_model, taught  # noqa: E402
from so101_bridge.chess import game as rules  # noqa: E402
from so101_bridge.chess import physical  # noqa: E402
from so101_bridge.chess import routine as R  # noqa: E402
from so101_bridge.chess import workspace as ws  # noqa: E402
from so101_bridge.chess.kinematics import GripperModel  # noqa: E402
from so101_bridge.chess.log import GameLog  # noqa: E402
from so101_bridge.controller import Controller  # noqa: E402
from so101_bridge.settings import JOINTS  # noqa: E402

# Scholar's mate: the robot (white) plays four moves, the last of them a capture, and the game ends by
# itself — which is what lets the routine run to completion instead of waiting forever for move 8.
GAME = ["e2e4", "e7e5", "f1c4", "b8c6", "d1h5", "g8f6", "h5f7"]


class SimArm:
    """Answers the four questions a chess routine asks: where are you, go there, grip, park."""

    def __init__(self, ctrl, d, grasp_ok=True):
        self.ctrl, self.d, self.grasp_ok = ctrl, d, grasp_ok
        self.pose = dict.fromkeys(JOINTS, 0.0)
        self.pose.update(d["board"]["corners"]["e1" if "e1" in d["board"]["corners"] else "a1"])
        self.pose["gripper"] = 10.0
        self.paths, self.grips, self.rests = [], [], 0
        ctrl.floor = floor_model()
        ctrl.snapshot = lambda: ({"present": dict(self.pose), "mode": "reached", "estop": False,
                                  "limits": WIDE_LIMITS}, {})
        ctrl.path = self.path
        ctrl.goto = lambda pose, speed=4.0, timeout=15.0: self.path([pose], speed)
        ctrl.goto_far = lambda pose, speed=4.0, step=10.0: self.path([pose], speed)
        ctrl.gripper_to = self.gripper_to
        ctrl.rest_path = self.rest_path

    def path(self, points, speed=6.0, timeout=None):
        self.paths.append(list(points))
        for j, v in points[-1].items():
            if j in self.pose: self.pose[j] = float(v)
        return True

    def gripper_to(self, value, speed=15.0):
        self.grips.append(float(value))
        closing = float(value) < 30
        self.pose["gripper"] = (14.0 if (closing and self.grasp_ok) else float(value))
        return self.pose["gripper"]

    def rest_path(self, gripper="rest"):
        self.rests += 1
        return True

    def squares_visited(self):
        """Every square the jaws were taken over, in order."""
        model = GripperModel(floor_model(), self.d, WIDE_LIMITS)
        out = []
        for p in self.paths:
            sq = model.pose_square(p[-1])[0]
            if sq and (not out or out[-1] != sq): out.append(sq)
        return out


@pytest.fixture
def sim(monkeypatch):
    d = taught(); ws.save(d)
    monkeypatch.setattr(R, "CHESS_POLL_S", 0.005)
    ctrl = Controller()
    return SimArm(ctrl, d), ctrl


def feed(ctrl, moves, stop):
    """Play the part of both people at the table: supply each move as soon as the last one is recorded."""
    i = 0
    while i < len(moves) and not stop.is_set():
        try:
            g = GameLog.latest()
        except Exception:                                     # the routine is mid-write; look again
            time.sleep(0.005); continue
        plies = len(g.doc["moves"]) if g else 0
        req = getattr(ctrl, "chess_request", None) or {}
        if plies == i and not req.get("move"):
            ctrl.chess_request = {**req, "move": moves[i]}
            i += 1
        time.sleep(0.005)


def run_with_feed(ctrl, moves, fn, timeout=30):
    stop = threading.Event()
    t = threading.Thread(target=feed, args=(ctrl, moves, stop), daemon=True); t.start()
    try:
        done = threading.Event()
        out = {}
        def go():
            try: out["ok"] = fn(ctrl)
            finally: done.set()
        threading.Thread(target=go, daemon=True).start()
        assert done.wait(timeout), "routine did not finish"
        return out.get("ok")
    finally:
        stop.set()


def test_a_whole_game_is_played_to_checkmate_headless(sim):
    arm, ctrl = sim
    GameLog.new(mode="play", robot="white", players={"white": "llm", "black": "human"})
    run_with_feed(ctrl, GAME, R.play)
    doc = GameLog.latest().doc
    assert [m["uci"] for m in doc["moves"]] == GAME
    assert doc["result"] == {"reason": "checkmate", "score": "1-0"}
    by_robot = [m for m in doc["moves"] if m.get("executed_by") == "robot"]
    assert len(by_robot) == 4 and all(m["by"] == "llm" for m in by_robot)
    assert arm.rests >= 4                                            # parked after every move it made
    assert doc["moves"][-1]["san"] == "Qxf7#"
    assert "handover" in str(doc["moves"][-1]["actions"])             # the captured pawn came off the board
    assert ctrl.routine_status == "done"


def test_the_robot_takes_the_captured_piece_off_before_it_moves_its_own(sim):
    arm, ctrl = sim
    board = rules.new_board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    glog = GameLog.new(mode="play", robot="white", players={"white": "llm", "black": "human"},
                       start_fen=board.fen())
    model = GripperModel(floor_model(), arm.d, WIDE_LIMITS)
    assert R._execute(ctrl, arm.d, model, board, glog, "e4d5", "llm", None)
    assert arm.squares_visited()[:3] == ["d5", "e4", "d5"]           # clear d5, fetch e4, put it on d5
    assert glog.doc["moves"][-1]["san"] == "exd5"


def test_a_missed_grasp_stops_the_move_and_says_so(sim):
    arm, ctrl = sim
    arm.grasp_ok = False
    board = rules.new_board()
    glog = GameLog.new(mode="play", robot="white", players={"white": "llm", "black": "human"})
    model = GripperModel(floor_model(), arm.d, WIDE_LIMITS)
    assert R._execute(ctrl, arm.d, model, board, glog, "e2e4", "llm", None) is False
    assert "grasp missed" in ctrl.routine_status
    assert GameLog.load(glog.id).doc["needs"]["kind"] == "grasp_failed"
    assert glog.doc["moves"] == []                                   # nothing is recorded that did not happen


def test_an_unreachable_move_is_handed_to_a_human_and_the_game_goes_on(sim):
    arm, ctrl = sim
    d = taught(); d["slots"].pop("handover")                          # nowhere to put a captured piece
    board = rules.new_board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    glog = GameLog.new(mode="play", robot="white", players={"white": "llm", "black": "human"},
                       start_fen=board.fen())
    model = GripperModel(floor_model(), d, WIDE_LIMITS)
    done = threading.Event()
    out = {}
    def go():
        out["ok"] = R._execute(ctrl, d, model, board, glog, "e4d5", "llm", None); done.set()
    threading.Thread(target=go, daemon=True).start()
    for _ in range(400):                                             # the operator sees the need and plays it
        if (GameLog.load(glog.id).doc.get("needs") or {}).get("kind") == "unreachable": break
        time.sleep(0.01)
    need = GameLog.load(glog.id).doc["needs"]
    assert need["kind"] == "unreachable" and need["san"] == "exd5" and "handover" in need["why"][0]
    ctrl.chess_request = {"confirm": True}
    assert done.wait(10) and out["ok"]
    assert glog.doc["moves"][-1]["executed_by"] == "human"
    assert arm.grips == []                                           # the arm never moved


def test_an_untaught_board_refuses_to_start(sim):
    arm, ctrl = sim
    ws.clear_board_marks()
    assert R.play(ctrl) is False
    assert "board model" in ctrl.routine_status


def test_run_steps_reports_which_step_was_interrupted(sim):
    arm, ctrl = sim
    ctrl.path = lambda points, speed=6.0, timeout=None: False
    model = GripperModel(floor_model(), arm.d, WIDE_LIMITS)
    steps = physical.steps(model, physical.plan(rules.new_board(), "e2e4", arm.d)["actions"], arm.d)
    ok, why = R.run_steps(ctrl, steps)
    assert not ok and "interrupted" in why and "e2" in why


def test_setup_places_the_pieces_it_is_handed(sim):
    arm, ctrl = sim
    ctrl.chess_request = {"fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1"}
    stop = threading.Event()
    def confirmer():
        while not stop.is_set():
            ctrl.chess_request = {**(ctrl.chess_request or {}), "confirm": True}
            time.sleep(0.005)
    threading.Thread(target=confirmer, daemon=True).start()
    try:
        done = threading.Event()
        threading.Thread(target=lambda: (R.setup(ctrl), done.set()), daemon=True).start()
        assert done.wait(20)
    finally:
        stop.set()
    assert ctrl.routine_status == "done"
    assert set(arm.squares_visited()) >= {"e1", "e8"}
