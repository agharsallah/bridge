"""Balloon lab: the thrust geometry, the pop detector and the replay plan. Nothing here moves hardware."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from so101_bridge.lab.state import tip  # noqa: E402
from so101_bridge.labs import balloon  # noqa: E402
from test_lab import LIMITS, FakeCtrl  # noqa: E402

NEEDLE = {"name": "needle", "length_cm": 3.0, "out_cm": 1.0, "margin_cm": 0.8, "grip": 0.0}
ARM_FIT = (51.5, -100.0, 29.3, 0.096, 1, 1, 1)          # a floor fit shaped like the real arm's (elbow folds one way)


def fake():
    ctrl = FakeCtrl(); ctrl.tool = NEEDLE; ctrl.floor.params = ARM_FIT
    return ctrl


def test_ik_puts_the_needle_tip_on_the_asked_point_with_a_level_gripper():
    ctrl = fake()
    pose = balloon.ik(ctrl.floor, NEEDLE, 24.0, 11.0, 90.0)
    at = tip(ctrl.floor, pose, NEEDLE)
    assert (at["r_cm"], at["z_cm"]) == pytest.approx((24.0, 11.0), abs=0.1)
    wrist, fingers = ctrl.floor.arm_points(pose)[2:]
    assert fingers[1] == pytest.approx(wrist[1], abs=1e-3)                  # level: wrist and fingertips at one height
    assert balloon.ik(ctrl.floor, NEEDLE, 60.0, 11.0, 90.0) is None         # out of reach is refused, not clamped


def test_thrust_line_stays_inside_the_limits_and_above_the_table():
    ctrl = fake()
    plan = {"r0_cm": 18.0, "r1_cm": 60.0, "z_cm": 11.0, "pitch_deg": 90.0}
    pts = balloon.line(ctrl, LIMITS, plan, 5.0)
    assert len(pts) > 10 and all(balloon.allowed(ctrl, LIMITS, p) for p in pts)
    assert tip(ctrl.floor, pts[-1], NEEDLE)["r_cm"] < 40                    # it stops where the arm ends, not at r1


def test_popped_needs_the_orange_to_collapse_on_two_different_frames():
    run = balloon.Run(balloon.LAB, FakeCtrl(), "t", {})
    seen = lambda frac, t: run.popped({"frac": frac, "t": t})  # noqa: E731
    assert not seen(0.12, 1) and not seen(0.15, 2)          # growing as the needle closes in
    assert not seen(0.01, 3) and not seen(0.01, 3)          # one bad frame, seen twice
    assert not seen(0.14, 4) and not seen(0.01, 5)          # recovered: the count starts again
    assert seen(0.0, 6)
    empty = balloon.Run(balloon.LAB, FakeCtrl(), "t", {})
    assert not empty.popped({"frac": 0.0, "t": 1}) and not empty.popped({"frac": 0.0, "t": 2})     # never saw a balloon


def test_replay_repeats_the_saved_pan_and_depth_shifted_without_moving_on_a_dry_run(monkeypatch):
    ctrl = fake()
    base = {"file": "x.json", "name": "first", "pan": 12.0, "pop": {"r_cm": 24.0},
            "plan": {"aim": True, "pan": None, "z_cm": 11.0, "pitch_deg": 90.0, "r0_cm": 18.0, "r1_cm": 35.0, "move_speed": 10.0, "speed": 15.0}}
    monkeypatch.setattr(balloon, "load_run", lambda lab, name: base)
    res = balloon.LAB.command(ctrl, {"action": "replay", "run": "x.json", "dpan": -3, "dr_cm": 2, "dz_cm": 1, "dry": True})
    plan = res["plan"]
    assert res["ok"] and (plan["aim"], plan["pan"], plan["z_cm"], plan["r0_cm"], plan["r1_cm"]) == (False, 9.0, 12.0, 22.0, 27.0)    # 24 + 2, from 4 before to 1 past
    assert ctrl.requests == [] and ctrl.routine_thread is None
    assert "needs_confirm" in balloon.LAB.command(ctrl, {"action": "replay", "run": "x.json"})
