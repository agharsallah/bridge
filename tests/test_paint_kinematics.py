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


def true_pose(u, v, z=0.0, paper_origin=(-0.10, 0.20), phi=None, z0=None):
    """Ground-truth joints for the brush tip at paper (u, v) cm (paper axes: u along +x, v along -y)."""
    PHI_ = PHI if phi is None else phi
    z0_ = TRUTH[3] if z0 is None else z0
    x = paper_origin[0] + u / 100; y = paper_origin[1] - v / 100
    xr, yr = (x * math.cos(math.radians(PAN0)) - y * math.sin(math.radians(PAN0)),
              x * math.sin(math.radians(PAN0)) + y * math.cos(math.radians(PAN0)))
    r = math.hypot(xr, yr); pan = math.degrees(math.atan2(xr, yr))
    px = r - TOOL * math.sin(PHI_); pz = z - TOOL * math.cos(PHI_) - z0_
    L1, L2 = LINKS["L1"], LINKS["L2"]; D = math.hypot(px, pz)
    a2 = -math.acos((D * D - L1 * L1 - L2 * L2) / (2 * L1 * L2))          # elbow bent "the other way" for this arm
    beta = math.acos((L1 * L1 + D * D - L2 * L2) / (2 * L1 * D)); a1 = math.atan2(px, pz) + beta
    a3 = PHI_ - a1 - a2
    o1, o2, o3, _, s1, s2, s3 = TRUTH
    return {"shoulder_pan": pan, "shoulder_lift": o1 + math.degrees(a1) / s1,
            "elbow_flex": o2 + math.degrees(a2) / s2, "wrist_flex": o3 + math.degrees(a3) / s3}


def workspace(extra=(), pitch=lambda u, v: PHI):
    w, h = 21.0, 14.8
    corner = {"A": (0.0, 0.0), "B": (w, 0.0), "C": (w, h), "D": (0.0, h)}
    return {"paper": {"width_cm": w, "height_cm": h,
                      "corners": {n: true_pose(u, v, phi=pitch(u, v)) for n, (u, v) in corner.items()},
                      "extra": [{"u": u, "v": v, "pose": true_pose(u, v, phi=pitch(u, v))} for u, v in extra]},
            "stations": {}, "brush": {}}


def elbow_distance(tm, pose):
    """Shoulder-axis to wrist-axis distance (m) for a pose — L1+L2 means a dead-straight arm."""
    a1, a2, _ = tm._angles(pose)
    rw, zw = tm._wrist_point(a1, a2)
    return math.hypot(rw, zw - tm.floor.params[3])


def test_fit_recovers_brush_and_maps_corners():
    tm = ToolModel(floor_model(), workspace())
    assert tm.ok, tm.error
    assert tm.report["brush_len_cm"] == pytest.approx(TOOL * 100, abs=0.2)
    assert tm.report["pitch_deg"] == pytest.approx(math.degrees(PHI), abs=0.5)
    assert tm.report["xy_rms_cm"] < 0.05 and tm.report["flatness_mm"] < 1.0 and tm.report["paper_rms_cm"] < 0.1


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


def test_crossed_corners_are_rejected_and_named():
    """Marking the bottom two corners the wrong way round used to fit silently and lose most of the paper."""
    ws = workspace(); cs = ws["paper"]["corners"]
    cs["C"], cs["D"] = cs["D"], cs["C"]
    tm = ToolModel(floor_model(), ws)
    assert not tm.ok
    assert "C and D" in tm.error and "cross" in tm.error


def test_brush_length_survives_a_wrong_floor_zero():
    """z0 is free in the floor fit unless it was measured; the brush length must not depend on it.

    The old estimator was -z_wrist / cos(pitch), which scales the whole error in z0 straight into the brush
    length — several cm of it — and then pushes every commanded pose off the paper.
    """
    w, h = 21.0, 14.8
    truth_z0 = TRUTH[3]
    ws = {"paper": {"width_cm": w, "height_cm": h, "extra": [],
                    "corners": {n: true_pose(u, v) for n, (u, v) in
                                {"A": (0, 0), "B": (w, 0), "C": (w, h), "D": (0, h)}.items()}},
          "stations": {}, "brush": {}}
    good = ToolModel(floor_model(), ws)
    fm = floor_model(); fm.params = TRUTH[:3] + (truth_z0 + 0.04,) + TRUTH[4:]      # floor model 4 cm out
    bad = ToolModel(fm, ws)
    assert bad.ok, bad.error
    assert bad.report["brush_len_cm"] == pytest.approx(TOOL * 100, abs=0.3)
    assert bad.report["paper_z_cm"] == pytest.approx(good.report["paper_z_cm"] + 4.0, abs=0.3)
    for u, v, z in [(3, 4, 0), (10.5, 7.4, 0), (18, 12, 1.5)]:                      # ...and the poses are unchanged
        assert bad.uv_to_pose(u, v, z) == pytest.approx(good.uv_to_pose(u, v, z), abs=0.05)


def test_heights_are_measured_from_the_taught_paper_plane():
    fm = floor_model(); fm.params = TRUTH[:3] + (TRUTH[3] + 0.04,) + TRUTH[4:]
    tm = ToolModel(fm, workspace())
    assert tm.uv_to_pose(10, 7, 0.0) == pytest.approx(true_pose(10, 7, 0.0), abs=0.1)
    _, _, z = tm.pose_to_uv(true_pose(10, 7, 0.015))
    assert z * 100 == pytest.approx(1.5, abs=0.05)


def test_pitch_follows_the_taught_trend_across_the_paper():
    """Operators stand the brush up to reach the far edge and lay it down close in; the corners record that."""
    def pitch(u, v): return PHI - math.radians(6.0) + math.radians(0.8) * v
    ws = workspace(pitch=pitch)
    tm = ToolModel(floor_model(), ws)
    assert tm.ok, tm.error
    assert tm.phi_coef is not None
    for u, v in [(0, 0), (21, 0), (10.5, 7.4), (0, 14.8), (21, 14.8)]:
        assert tm.pitch_at(u, v) == pytest.approx(pitch(u, v), abs=math.radians(0.2))
        assert tm.uv_to_pose(u, v, 0.0) == pytest.approx(true_pose(u, v, phi=pitch(u, v)), abs=0.5)


def test_pitch_bends_to_clear_a_joint_limit():
    """The taught pitch is preferred, but a pose that a limit rejects may still be reachable at another pitch."""
    wide = {j: [-400, 400] for j in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex")}
    free = ToolModel(floor_model(), workspace(), limits=wide)
    at_taught = free.uv_to_pose(10, 7, 0.0)
    band = [at_taught["shoulder_lift"] - 6.0, at_taught["shoulder_lift"] - 1.0]      # excludes the taught pitch
    bent = ToolModel(floor_model(), workspace(), limits={**wide, "shoulder_lift": band})
    pose = bent.uv_to_pose(10, 7, 0.0)
    assert pose is not None and band[0] <= pose["shoulder_lift"] <= band[1]
    uu, vv, zz = bent.pose_to_uv(pose)
    assert (uu, vv) == pytest.approx((10, 7), abs=0.05) and zz * 100 == pytest.approx(0.0, abs=0.05)


def test_no_solution_sits_on_a_straight_or_folded_elbow():
    """A pose at full extension has no manipulability left and sags under gravity — never hand one back."""
    wide = {j: [-400, 400] for j in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex")}
    tm = ToolModel(floor_model(), workspace(), limits=wide)
    L1, L2 = LINKS["L1"], LINKS["L2"]
    seen = 0
    for u in range(0, 22):
        for v in range(0, 15):
            pose = tm.uv_to_pose(u, v, 0.0)
            if pose is None: continue
            D = elbow_distance(tm, pose); seen += 1
            assert abs(L1 - L2) + 0.003 <= D <= L1 + L2 - 0.003, (u, v, D)
    assert seen > 200
