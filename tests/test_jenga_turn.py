"""Jenga turn: the tower's bookkeeping, the camera check on real frames, and the plan of a turn. No hardware moves."""

import sys
from pathlib import Path

import cv2
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from so101_bridge.labs import jenga, jenga_tower, jenga_turn, jenga_vision  # noqa: E402
from test_jenga import Arm  # noqa: E402

FRAMES = Path(__file__).parent / "data" / "jenga"          # wrist frames from the 2026-11-12 session, tip under 1 cm from the face


def frame(name):
    return cv2.cvtColor(cv2.imread(str(FRAMES / f"{name}.jpg")), cv2.COLOR_BGR2RGB)


def test_only_untouched_end_rows_below_the_top_two_can_be_played_highest_first():
    c, t = jenga.DEFAULTS, jenga_tower.new(15)
    assert jenga_tower.candidates(t, c) == [13, 11]
    jenga_tower.mark(t, 13, 1, "out"); jenga_tower.mark(t, 11, 0, "pushed", 2.0)
    assert jenga_tower.candidates(t, c) == []
    assert "long side" in jenga_tower.playable(t, c, 12) and "top two" in jenga_tower.playable(t, c, 15)
    assert jenga_tower.mark(t, 13, 1, "in")["bricks"] == {"11-0": {"state": "pushed", "depth_cm": 2.0}}
    with pytest.raises(ValueError): jenga_tower.mark(t, 16, 1, "out")


def test_the_camera_check_reads_the_tip_against_the_rows_as_i_read_them_by_eye():
    centred, low, on_side_row = (jenga_vision.measure(frame(n)) for n in ("k4", "m2", "k1"))
    assert centred["end_row"] and abs(centred["offset_cm"]) <= 0.15            # this one was pushed and went in clean
    assert low["end_row"] and low["offset_cm"] == pytest.approx(0.45, abs=0.15)   # half a centimetre low: was corrected before pushing
    assert not on_side_row["end_row"]                                           # a full row off: facing the long side of a brick
    assert jenga_vision.measure(frame("k4")[:200]) is None                      # no stick in the picture: no answer rather than a guess


def test_a_turn_plans_the_highest_playable_row_and_refuses_the_others_without_moving(monkeypatch, tmp_path):
    monkeypatch.setattr(jenga_turn, "tower_file", lambda lab: tmp_path / "tower.json")
    ctrl = Arm()
    plan = jenga.LAB.command(ctrl, {"action": "turn", "dry": True})["plan"]
    assert (plan["row"], plan["col"], plan["depth_cm"], plan["stroke_cm"]) == (13, 1, 4.5, 5.0)
    assert plan["reading_cm"] == pytest.approx(15.4)                            # row 13 read 15.5 on the arm that day
    assert "long side" in jenga.LAB.command(ctrl, {"action": "turn", "row": 12, "dry": True})["error"]
    assert "needs_confirm" in jenga.LAB.command(ctrl, {"action": "turn"})
    jenga.LAB.command(ctrl, {"action": "tower_mark", "row": 13, "col": 1, "state": "out"})
    assert jenga.LAB.command(ctrl, {"action": "turn", "dry": True})["plan"]["row"] == 11
    assert jenga.LAB.state(ctrl)["tower"]["playable"] == [11] and ctrl.requests == []
