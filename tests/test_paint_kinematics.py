"""Brush-tip kinematics: fit from synthetic corner poses, then invert back (round trip)."""

import math

import pytest

from so101_bridge.floor import FloorModel
from so101_bridge.paint.kinematics import ToolModel

TRUTH = (-5.0, 20.0, -30.0, 0.075, 1, -1, 1)          # floor params: offsets, z0, signs
LINKS = {"L1": 0.1159, "L2": 0.1350, "L3": 0.110}
TOOL, PHI = 0.14, math.radians(170)                  # brush 14 cm, pitched 10 deg off vertical-down
PAN0 = 12.0                                          # paper rotated wrt the arm's pan zero


def floor_model():
    fm = FloorModel.__new__(FloorModel)
    fm.params, fm._c, fm.rms, fm.n = TRUTH, LINKS, 0.0005, 7
    return fm


def true_pose(u, v, z=0.0, paper_origin=(-0.10, 0.20), w=0.21, h=0.148):
    """Ground-truth joints for the brush tip at paper (u, v) cm (paper axes: u along +x, v along -y)."""
    x = paper_origin[0] + u / 100; y = paper_origin[1] - v / 100
    xr, yr = (x * math.cos(math.radians(PAN0)) - y * math.sin(math.radians(PAN0)),
              x * math.sin(math.radians(PAN0)) + y * math.cos(math.radians(PAN0)))
    r = math.hypot(xr, yr); pan = math.degrees(math.atan2(xr, yr))
    px = r - TOOL * math.sin(PHI); pz = z - TOOL * math.cos(PHI) - TRUTH[3]
    L1, L2 = LINKS["L1"], LINKS["L2"]; D = math.hypot(px, pz)
    a2 = -math.acos((D * D - L1 * L1 - L2 * L2) / (2 * L1 * L2))          # elbow bent "the other way" for this arm
    beta = math.acos((L1 * L1 + D * D - L2 * L2) / (2 * L1 * D)); a1 = math.atan2(px, pz) + beta
    a3 = PHI - a1 - a2
    o1, o2, o3, _, s1, s2, s3 = TRUTH
    return {"shoulder_pan": pan, "shoulder_lift": o1 + math.degrees(a1) / s1,
            "elbow_flex": o2 + math.degrees(a2) / s2, "wrist_flex": o3 + math.degrees(a3) / s3}


def workspace(extra=()):
    w, h = 21.0, 14.8
    return {"paper": {"width_cm": w, "height_cm": h,
                      "corners": {"A": true_pose(0, 0), "B": true_pose(w, 0), "C": true_pose(w, h), "D": true_pose(0, h)},
                      "extra": [{"u": u, "v": v, "pose": true_pose(u, v)} for u, v in extra]},
            "stations": {}, "brush": {}}


def test_fit_recovers_brush_and_maps_corners():
    tm = ToolModel(floor_model(), workspace())
    assert tm.ok, tm.error
    assert tm.report["brush_len_cm"] == pytest.approx(TOOL * 100, abs=0.2)
    assert tm.report["pitch_deg"] == pytest.approx(math.degrees(PHI), abs=0.5)
    assert tm.report["xy_rms_cm"] < 0.05 and tm.report["z_rms_mm"] < 1.0


def test_round_trip_inside_the_paper():
    tm = ToolModel(floor_model(), workspace())
    for u, v, z in [(3, 4, 0), (10.5, 7.4, 0), (18, 12, 1.5), (1, 13, 0.2)]:
        pose = tm.uv_to_pose(u, v, z)
        assert pose is not None
        uu, vv, zz = tm.pose_to_uv(pose)
        assert (uu, vv) == pytest.approx((u, v), abs=0.05)
        assert zz * 100 == pytest.approx(z, abs=0.05)
        truth = true_pose(u, v, z / 100)
        for j in pose:
            assert pose[j] == pytest.approx(truth[j], abs=0.3), j


def test_unreachable_and_limits():
    wide = {j: [-400, 400] for j in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex")}
    tm = ToolModel(floor_model(), workspace(), limits=wide)
    assert tm.uv_to_pose(10, 7, 0) is not None
    assert tm.uv_to_pose(300, 300, 0) is None                # far beyond the arm
    tight = ToolModel(floor_model(), workspace(), limits={**wide, "shoulder_pan": [-5, 5]})
    assert tight.uv_to_pose(10, 7, 0) is None                # pan -12 needed -> outside the limit
    cols, rows, grid = tm.reachability(2.0)
    assert cols == 10 or cols == 11
    assert sum(map(sum, grid)) > 0.8 * cols * rows            # most of an A5 sheet is reachable


def test_needs_four_corners_and_a_floor_model():
    ws = workspace(); del ws["paper"]["corners"]["D"]
    assert not ToolModel(floor_model(), ws).ok
    fm = floor_model(); fm.params = None
    assert "floor" in ToolModel(fm, workspace()).error
