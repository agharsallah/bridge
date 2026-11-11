"""A tool held in the gripper: where its ends are, what the table guard makes of them, the gripper lock."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from chess_synth import floor_model, true_pose  # noqa: E402
from so101_bridge import tool  # noqa: E402

FM = floor_model()
DOWN = true_pose(10, 10, z_cm=3.0, lean=10)          # fingertips 3 cm above the table, gripper pointing down
PEN = {"name": "pen", "length_cm": 14.0, "out_cm": 2.5}


def test_the_tool_lies_along_the_gripper_axis_with_its_measured_parts():
    (wr, wz), (tr, tz) = FM.arm_points(DOWN)[2:4]
    e = tool.ends(FM, DOWN, tool.normalize(PEN))
    assert math.dist((tr, tz), e["front"]) == pytest.approx(0.025, abs=2e-4)
    assert math.dist((tr, tz), e["back"]) == pytest.approx(0.115, abs=2e-4)
    cross = (tr - wr) * (e["front"][1] - wz) - (tz - wz) * (e["front"][0] - wr)
    assert abs(cross) < 1e-4                                                  # wrist, fingertips and pen tip are collinear


def test_the_guard_watches_the_pen_tip_with_its_own_margin():
    assert tool.table_ok(FM, DOWN, None)                                      # fingertips alone: 3 cm, clear
    pen = tool.normalize(PEN)                                                 # default margin = the fingertips' 0.8 cm
    assert not tool.table_ok(FM, DOWN, pen)                                   # pen tip ~0.5 cm: blocked
    assert tool.worst(FM, DOWN, pen)[0] == "pen tip"
    assert tool.table_ok(FM, DOWN, tool.normalize({**PEN, "margin_cm": 0.0})) # a pen allowed to touch the paper


def test_a_tip_well_past_its_margin_freezes_the_arm():
    deep = tool.normalize({**PEN, "out_cm": 4.0, "margin_cm": 0.0})          # tip ~1 cm into the table
    assert "pen tip" in tool.frozen(FM, DOWN, deep)
    assert tool.frozen(FM, DOWN, None) is None


def test_the_gripper_is_locked_except_for_the_mount_itself():
    pen = tool.normalize(PEN)
    assert tool.lock_gripper({"gripper": 60, "elbow_flex": 3}, pen, "web") == ({"elbow_flex": 3}, True)
    assert tool.lock_gripper({"gripper": 5}, pen, "tool") == ({"gripper": 5}, False)
    assert tool.lock_gripper({"gripper": 60}, None, "web") == ({"gripper": 60}, False)


def test_impossible_tools_are_refused():
    with pytest.raises(ValueError): tool.normalize({**PEN, "out_cm": 20})
    with pytest.raises(ValueError): tool.normalize({**PEN, "margin_cm": -3})
    with pytest.raises(ValueError): tool.normalize({**PEN, "length_cm": 0})


def test_a_mounted_tool_survives_a_restart():
    t = tool.save(tool.normalize(PEN))
    assert tool.load() == t
    tool.save(None)
    assert tool.load() is None
