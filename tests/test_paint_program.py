"""Planner + compiler + paint routine on synthetic geometry (reuses the kinematics test rig)."""

import sys

import numpy as np
import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from so101_bridge.controller import Controller  # noqa: E402
from so101_bridge.paint import planner, program  # noqa: E402
from so101_bridge.paint import routine as paint_routine  # noqa: E402
from so101_bridge.paint.kinematics import ToolModel  # noqa: E402
from test_paint_kinematics import floor_model, workspace  # noqa: E402


def ws_with_stations():
    ws = workspace()
    ws["brush"] = {"hover_cm": 1.5, "press_cm": 0.15, "width_mm": 6.0, "dip_every_cm": 12.0,
                   "stroke_speed": 6.0, "travel_speed": 8.0, "dip_dwell_s": 0.2, "rinse_dips": 1}
    off = {"shoulder_pan": 40, "shoulder_lift": 30, "elbow_flex": 40, "wrist_flex": 10}
    ws["stations"] = {"red": {"kind": "color", "rgb": [200, 30, 30], "dip": dict(off), "hover": None},
                      "blue": {"kind": "color", "rgb": [30, 60, 200], "dip": {**off, "shoulder_pan": 50}, "hover": None},
                      "water": {"kind": "water", "dip": {**off, "shoulder_pan": 60}, "hover": None}}
    return ws


def picture():
    img = np.full((60, 90, 3), 255, np.uint8)          # white paper
    img[10:30, 10:40] = (30, 30, 200)                   # red block (BGR)
    img[35:55, 50:85] = (200, 60, 30)                   # blue block
    return img


def test_planner_quantises_and_hatches():
    ws = ws_with_stations()
    plan = planner.plan_image(picture(), ws)
    assert {c["name"] for c in plan["palette"]} == {"red", "blue"}
    assert plan["stats"]["strokes"] > 10 and set(plan["stats"]["per_color"]) == {"red", "blue"}
    assert plan["stats"]["paper_cells"] > 0                       # white stays unpainted
    assert planner.preview_png(plan)[:4] == b"\x89PNG"
    # blue (darker) is painted after red (lighter)
    colors = [s["color"] for s in plan["strokes"]]
    assert colors.index("blue") > colors.index("red") and colors[-1] == "blue"


def test_compile_and_store(tmp_path, monkeypatch):
    monkeypatch.setattr(program, "PAINTINGS_DIR", tmp_path)
    ws = ws_with_stations(); tool = ToolModel(floor_model(), ws)
    plan = planner.plan_image(picture(), ws)
    prog = program.compile_program(plan, ws, tool, "test pic", "pic.png")
    ops = [s["op"] for s in prog["steps"]]
    assert prog["stats"]["strokes"] == plan["stats"]["strokes"] and prog["stats"]["skipped"] == 0
    assert ops[0] == "travel" and "dip" in ops and "dwell" in ops and prog["stats"]["rinses"] == 1
    # every path point stays within the step cap of its predecessor
    for st in prog["steps"]:
        for a, b in zip(st.get("points", []), st.get("points", [])[1:]):
            assert max(abs(b[j] - a[j]) for j in a) <= 12.0 + 1e-6
    # stroke points sit at press depth, hover points above
    stroke = next(s for s in prog["steps"] if s["op"] == "stroke")
    assert tool.pose_to_uv(stroke["points"][0])[2] * 100 == pytest.approx(-0.15, abs=0.05)
    assert tool.pose_to_uv(stroke["points"][-1])[2] * 100 == pytest.approx(1.5, abs=0.05)
    name = program.save(prog)
    assert program.load(name)["stats"] == prog["stats"] and program.catalog()[0]["name"] == name
    dry = program.compile_program(plan, ws, tool, "dry", dry_run=True)
    assert all(s["op"] != "dip" for s in dry["steps"])


def test_paint_routine_replays_a_program(tmp_path, monkeypatch):
    monkeypatch.setattr(program, "PAINTINGS_DIR", tmp_path)
    monkeypatch.setattr(paint_routine.time, "sleep", lambda *_: None)
    ws = ws_with_stations(); tool = ToolModel(floor_model(), ws)
    prog = program.compile_program(planner.plan_image(picture(), ws), ws, tool, "run me")
    program.save(prog)
    ctrl = Controller(); paths = []
    ctrl.path = lambda pts, speed=6.0, timeout=None: paths.append((len(pts), speed)) or True
    ctrl.paint_request = {"name": "run me"}
    paint_routine.run(ctrl)
    assert ctrl.routine_status == "done"
    assert len(paths) == sum(1 for s in prog["steps"] if s["op"] != "dwell")
    assert ctrl.progress["paint"]["stroke_done"] == prog["stats"]["strokes"]
    # an aborted move fails cleanly
    ctrl2 = Controller(); ctrl2.path = lambda *a, **k: False; ctrl2.paint_request = {"name": "run me"}
    paint_routine.run(ctrl2)
    assert ctrl2.routine_status.startswith("failed")
