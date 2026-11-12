"""Jenga lab: the straight tip moves and what push refuses. Nothing here moves hardware."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from so101_bridge.lab.state import tip  # noqa: E402
from so101_bridge.labs import jenga  # noqa: E402
from test_lab import LIMITS, FakeCtrl  # noqa: E402

STICK = {"name": "stick", "length_cm": 13.0, "out_cm": 10.0, "margin_cm": 0.8, "grip": 8.6}
HOLD = {"shoulder_pan": -7.0, "shoulder_lift": 20.0, "elbow_flex": 55.0, "wrist_flex": -10.0, "wrist_roll": 0.0}


class Arm(FakeCtrl):
    """A fake shaped like the real arm, holding HOLD with the stick mounted."""
    def __init__(self):
        super().__init__(); self.tool = STICK; self.floor.params = (51.5, -100.0, 29.3, 0.096, 1, 1, 1)

    def snapshot(self):
        st, blob = super().snapshot()
        return {**st, "present": {**HOLD, "gripper": 8.6}, "goal": {**HOLD, "gripper": 8.6}}, blob


def test_a_push_path_moves_the_tip_straight_forward_and_keeps_the_gripper_angle():
    ctrl = Arm(); st, pose = jenga.held(ctrl)
    pts = jenga.shifted(ctrl, st, pose, 3.0, 0.0)
    a, b = tip(ctrl.floor, pose, STICK), tip(ctrl.floor, {**pose, **pts[-1]}, STICK)
    assert (b["r_cm"] - a["r_cm"], b["z_cm"] - a["z_cm"]) == pytest.approx((3.0, 0.0), abs=0.11)
    assert jenga.pitch_of(ctrl.floor, {**pose, **pts[-1]}) == pytest.approx(jenga.pitch_of(ctrl.floor, pose), abs=0.05)
    assert max(abs(q[j] - p[j]) for p, q in zip(pts, pts[1:]) for j in p) < 3          # dense: no joint jumps on the way


def test_nudge_sends_one_guarded_path_and_refuses_big_steps_and_the_table():
    ctrl = Arm()
    assert jenga.LAB.command(ctrl, {"action": "nudge", "dz_cm": 0.5})["ok"]
    (req,) = ctrl.requests
    assert req["action"] == "path" and req["src"] != "floor_ok" and set(req["points"][0]) == set(jenga.PLANE)
    assert "at most" in jenga.LAB.command(ctrl, {"action": "nudge", "dr_cm": 5})["error"]
    with pytest.raises(ValueError, match="table"):
        jenga.shifted(ctrl, {"limits": LIMITS}, jenga.held(ctrl)[1], 0.0, -30.0)
    assert len(ctrl.requests) == 1


def test_push_asks_first_and_a_dry_run_plans_without_moving():
    ctrl = Arm()
    assert "needs_confirm" in jenga.LAB.command(ctrl, {"action": "push"})
    res = jenga.LAB.command(ctrl, {"action": "push", "depth_cm": 2, "dry": True})
    assert res["ok"] and res["plan"]["depth_cm"] == 2 and res["points"] == 8
    assert "depth" in jenga.LAB.command(ctrl, {"action": "push", "depth_cm": 20, "dry": True})["error"]
    assert ctrl.requests == [] and ctrl.routine_thread is None


def test_place_plans_a_straight_line_to_the_middle_of_a_row_with_a_level_stick():
    ctrl = Arm()
    res = jenga.LAB.command(ctrl, {"action": "place", "r_cm": 26, "row": 9, "dry": True})
    assert res["ok"] and res["target"] == {"r_cm": 26.0, "z_cm": 12.75, "pitch_deg": 90.0}
    end = tip(ctrl.floor, {**HOLD, **res["end"]}, STICK)
    assert (end["r_cm"], end["z_cm"]) == pytest.approx((26.0, 12.75), abs=0.11) and ctrl.requests == []
    assert "out of reach" in jenga.LAB.command(ctrl, {"action": "place", "r_cm": 70, "row": 9, "dry": True})["error"]
