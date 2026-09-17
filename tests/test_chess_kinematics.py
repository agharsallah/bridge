"""The board model: fit from taught corners, then put the jaws back on all 64 square centres."""

import math
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from chess_synth import (  # noqa: E402
    PLANE_CM,
    TOOL,
    WIDE_LIMITS,
    floor_model,
    lean_deg,
    taught,
    true_pose,
)
from so101_bridge.chess import workspace as ws  # noqa: E402
from so101_bridge.chess.kinematics import GripperModel, report_text  # noqa: E402


def model(side="white", limits=None, **kw):
    return GripperModel(floor_model(), taught(side, **kw), limits if limits is not None else WIDE_LIMITS)


def test_fit_uses_the_given_gripper_length_and_locates_the_board():
    m = model()
    assert m.ok, m.error
    assert m.report["gripper_len_cm"] == pytest.approx(TOOL * 100, abs=1e-6)   # given, not fitted
    assert m.report["xy_rms_cm"] < 0.05
    assert m.report["board_rms_cm"] < 0.1 and m.report["flatness_mm"] < 1.0


def test_a_wrong_tool_length_shows_up_as_a_residual_rather_than_being_absorbed():
    """Fixing the length is what makes a mis-measured gripper visible; fitting it would hide the error."""
    d = taught(); d["gripper"]["tool_len_cm"] = TOOL * 100 + 2.0
    bad = GripperModel(floor_model(), d, WIDE_LIMITS)
    assert bad.ok, bad.error
    assert bad.report["board_rms_cm"] > model().report["board_rms_cm"]


@pytest.mark.parametrize("side", ["white", "black"])
def test_every_square_centre_within_a_millimetre(side):
    m = model(side)
    d = m.d
    for name in ws.squares(d):
        pose = m.pose_for(name)
        assert pose is not None, name
        u, v = ws.square_uv(d, name)
        uu, vv, zz = m.pose_to_uv(pose)
        assert (uu, vv) == pytest.approx((u, v), abs=0.1), name        # 1 mm
        assert zz * 100 == pytest.approx(0.0, abs=0.1), name
        assert m.pose_square(pose)[0] == name


def test_heights_are_measured_from_the_taught_board_plane():
    m = model()
    for z in (1.4, 6.0, 9.0):
        pose = m.pose_for("e4", z)
        assert pose is not None
        assert m.pose_to_uv(pose)[2] * 100 == pytest.approx(z, abs=0.05)


def test_the_taught_lean_is_reproduced_and_can_be_overridden():
    m = model()
    u, v = ws.square_uv(m.d, "d4")
    assert m.lean_of(m.pose_for("d4")) == pytest.approx(lean_deg(u, v), abs=0.5)
    forced = m.pose_for("d4", 0.0, lean_deg=lean_deg(u, v) + 6.0)
    assert m.lean_of(forced) == pytest.approx(lean_deg(u, v) + 6.0, abs=0.5)
    assert m.pose_to_uv(forced)[:2] == pytest.approx((u, v), abs=0.1)   # still on the square


def test_grasp_poses_round_trip_through_the_model():
    """What the operator taught as 'the jaws here, holding a king' must come back as a height and a lean."""
    m = model()
    u, v = ws.square_uv(m.d, "e4")
    pose = true_pose(u, v, 3.0)
    assert m.pose_square(pose) == ("e4", pytest.approx(3.0, abs=0.05))
    assert m.lean_of(pose) == pytest.approx(lean_deg(u, v), abs=0.5)


def test_reachability_report_covers_every_square_at_every_height():
    m = model()
    rep = m.reachability_report()
    assert rep["ok"] and len(rep["squares"]) == 64
    assert set(rep["heights"]) == {"grasp", "hover", "carry"}
    for label in rep["heights"]:
        assert rep["reachable_pct"][label] == 100.0 and rep["unreachable"][label] == []
    assert 0 < rep["max_lean_deg"] < 90
    text = report_text(rep, m.d)
    assert "reachability" in text and "a1:" in text and "h8:" in text


def test_a_joint_limit_removes_squares_and_the_report_names_them():
    free = model()
    lo, hi = free.pose_for("a1")["shoulder_pan"], free.pose_for("h1")["shoulder_pan"]
    band = [lo + 0.25 * (hi - lo), hi]                             # the robot's left-hand files fall outside
    tight = model(limits={**WIDE_LIMITS, "shoulder_pan": band})
    rep = tight.reachability_report()
    assert rep["unreachable"]["grasp"], "a tight pan limit must cost some squares"
    for n in rep["unreachable"]["grasp"]:
        assert tight.pose_for(n, rep["heights"]["grasp"]) is None


def test_missing_corners_and_crossed_corners_are_refused_by_name():
    d = taught(); del d["board"]["corners"]["a8"]
    assert not GripperModel(floor_model(), d, WIDE_LIMITS).ok
    d = taught()
    d["board"]["corners"]["h8"], d["board"]["corners"]["a8"] = d["board"]["corners"]["a8"], d["board"]["corners"]["h8"]
    m = GripperModel(floor_model(), d, WIDE_LIMITS)
    assert not m.ok and "cross" in m.error and "h8" in m.error and "a8" in m.error


def test_no_solution_sits_on_a_straight_or_folded_elbow():
    """A pose at full extension has no manipulability left and sags under gravity — never hand one back."""
    m = model()
    L1, L2 = 0.1159, 0.1350
    for name in ws.squares(m.d):
        for z in (0.0, 6.0, 9.0):
            pose = m.pose_for(name, z)
            if pose is None: continue
            a1, a2, _ = m._angles(pose)
            rw, zw = m._wrist_point(a1, a2)
            D = math.hypot(rw, zw - m.floor.params[3])
            assert abs(L1 - L2) + 0.003 <= D <= L1 + L2 - 0.003, (name, z, D)


def test_the_plane_spans_corner_centres_not_the_board_edges():
    m = model()
    assert (m.w_cm, m.h_cm) == pytest.approx((PLANE_CM, PLANE_CM))


def test_a_square_reached_by_hand_is_solved_with_its_own_allowance_and_no_other_square_is():
    """The soft limits stay as calibrated; the square the operator drove to gets the range it proved."""
    free = model()
    lo, hi = free.pose_for("a1")["shoulder_pan"], free.pose_for("h1")["shoulder_pan"]
    band = [lo + 0.25 * (hi - lo), hi]
    d = taught()
    tight = GripperModel(floor_model(), d, {**WIDE_LIMITS, "shoulder_pan": band})
    lost = tight.reachability_report()["unreachable"]["grasp"]
    assert len(lost) >= 2, "need a square to teach and one to leave alone"
    taught_sq, other = lost[0], lost[1]

    d["board"]["reach"] = {taught_sq: {"pose": free.pose_for(taught_sq),
                                       "allow": {"shoulder_pan": [lo, hi]}}}
    m = GripperModel(floor_model(), d, {**WIDE_LIMITS, "shoulder_pan": band})
    assert m.pose_for(taught_sq, m.d["heights"]["grasp_default_cm"]) is not None
    assert m.pose_for(other, m.d["heights"]["grasp_default_cm"]) is None
