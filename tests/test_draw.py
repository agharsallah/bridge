"""Drawing lab: where the pen is asked to go, what draw refuses, how the paper is felt. Nothing here moves hardware."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from so101_bridge.lab.state import tip  # noqa: E402
from so101_bridge.labs import draw, draw_run  # noqa: E402
from test_lab import FakeCtrl  # noqa: E402

HOLD = {"shoulder_pan": 0.0, "shoulder_lift": 40.0, "elbow_flex": 20.0, "wrist_flex": 50.0, "wrist_roll": 0.0}


class Arm(FakeCtrl):
    """A fake shaped like the real arm with the pen mounted. ``path`` goes where it is sent, but never below ``paper``."""
    def __init__(self, paper=None):
        super().__init__(); self.tool = draw.PEN; self.floor.params = (51.5, -100.0, 29.3, 0.096, 1, 1, 1)
        self.pose, self.paper = dict(HOLD), paper

    def snapshot(self):
        st, blob = super().snapshot()
        return {**st, "present": {**self.pose, "gripper": 5.0}}, blob

    def path(self, points, speed=6.0):
        for p in points:
            if self.paper is None or tip(self.floor, {**self.pose, **p}, self.tool)["z_cm"] >= self.paper: self.pose.update(p)
        return True


@pytest.fixture(autouse=True)
def fresh():
    draw.LIVE.update(name="robot", place={}, trace=[], phase="idle", stroke=None)


def test_the_pen_is_sent_to_the_table_point_and_the_paper_height_follows_the_reach():
    ctrl, c = Arm(), draw.dcfg(draw.LAB)
    at = tip(ctrl.floor, draw.pose_at(ctrl, c, draw.PEN, 5.0, 30.0, 1.0), draw.PEN)
    assert draw.xy_of(c, at) == pytest.approx((5.0, 30.0), abs=0.11) and at["z_cm"] == pytest.approx(1.0, abs=0.06)
    assert at["pan"] > 0                                                        # x is to the arm's right: positive pan
    assert at["r_cm"] == pytest.approx(30.4 - 6.0, abs=0.11)                    # the model's reach is 6 cm short of the real tip
    assert draw.pose_at(ctrl, c, draw.PEN, 0.0, 60.0, 1.0) is None
    curved = {**c, "touch": [[0, 26, 0.5], [0, 32, 1.0], [0, 38, 2.5]]}         # the paper is "higher" the further the arm reaches
    assert draw.paper_z(curved, 0, 32) == pytest.approx(1.0) and draw.paper_z(curved, 22.8, 30.4) == pytest.approx(2.5)
    assert draw.paper_z(curved, 0, 35) == pytest.approx(1.75) and draw.paper_z(curved, 0, 45) == pytest.approx(2.5)
    assert draw.paper_z({**c, "touch": [[1, 30, 0.7]]}, 5, 35) == pytest.approx(0.7) and draw.paper_z(c, 0, 32) == 0.0

def test_draw_asks_first_plans_inside_the_area_and_refuses_what_the_pen_cannot_reach():
    ctrl = Arm()
    assert "needs_confirm" in draw.LAB.command(ctrl, {"action": "draw"})
    plan = draw.LAB.command(ctrl, {"action": "draw", "name": "star", "dry": True})["plan"]
    xs, ys = [p[0] for s in plan["strokes"] for p in s], [p[1] for s in plan["strokes"] for p in s]
    assert -9 <= min(xs) and max(xs) <= 9 and 26 <= min(ys) and max(ys) <= 38 and max(ys) - min(ys) == pytest.approx(10.8, abs=0.3)
    assert "cannot reach" in draw.LAB.command(ctrl, {"action": "draw", "name": "star", "dy_cm": 15, "dry": True})["error"]
    assert ctrl.requests == [] and ctrl.routine_thread is None


def test_a_sketch_is_kept_and_a_saved_run_is_drawn_again_where_it_was_or_moved():
    ctrl = Arm()
    res = draw.LAB.command(ctrl, {"action": "show", "name": "zig", "strokes": [[[0, 0], [1, 1], [2, 0]]], "scale": 0.5})
    assert res["ok"] and "zig" in draw.LAB.state(ctrl)["draw"]["names"] and draw.LAB.state(ctrl)["draw"]["plan"]["name"] == "zig"
    assert "built-in" in draw.LAB.command(ctrl, {"action": "show", "name": "star", "strokes": [[[0, 0], [1, 1]]]})["error"]
    d = draw.files(draw.LAB, "runs"); d.mkdir(parents=True, exist_ok=True)
    (d / "r1.json").write_text(json.dumps({"file": "r1.json", "name": "zig", "place": {"scale": 0.5, "dx_cm": 1.0, "dy_cm": 0.0}}))
    draw.LAB.command(ctrl, {"action": "show", "name": "heart"})
    same = draw.LAB.command(ctrl, {"action": "draw", "run": "r1.json", "dry": True})["plan"]
    moved = draw.LAB.command(ctrl, {"action": "draw", "run": "r1.json", "dx_cm": 3.0, "dry": True})["plan"]
    assert same["name"] == "zig" and same["place"] == {"scale": 0.5, "dx_cm": 1.0, "dy_cm": 0.0}
    assert moved["strokes"][0][0][0] - same["strokes"][0][0][0] == pytest.approx(2.0)


def test_a_touch_stops_where_the_tip_stops_following_and_ends_pen_up(monkeypatch):
    monkeypatch.setattr(draw_run.time, "sleep", lambda s: None)
    ctrl = Arm(paper=1.0)
    pen = draw_run.Pen(draw.LAB, ctrl); pen.at = (0.0, 32.0, 4.0)
    t = pen.touch(0.0, 32.0)
    assert t["z"] == pytest.approx(1.0, abs=0.25) and pen.at[2] == pytest.approx(t["z"] + 2.5)
    free = draw_run.Pen(draw.LAB, Arm()); free.at = (0.0, 32.0, 4.0)
    assert free.touch(0.0, 32.0)["z"] is None                                   # nothing felt down to the guard: no height is invented


def test_square_kufic_is_traced_as_closed_outlines_with_holes_and_dots_apart():
    from so101_bridge.labs import draw_kufic
    loops = draw_kufic.outlines("###.#\n#.#..\n###..")
    assert len(loops) == 3 and all(len(s) == 5 and s[0] == s[-1] for s in loops)          # a box, its hole, a dot: corners only, closed
    words = draw_kufic.jumua()
    assert len(words) == 16 and all(a[0] == b[0] or a[1] == b[1] for s in words for a, b in zip(s, s[1:]))    # only horizontal and vertical lines
