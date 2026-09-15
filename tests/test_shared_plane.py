"""What painting and chess share: the plane model's generic contract and the motion helpers.

The task-shaped tests live next door (``test_paint_kinematics``, ``test_chess_kinematics``); this file
pins the parts that would otherwise only be tested by accident through one of them.
"""

import math
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from chess_synth import PLANE_CM, TOOL, WIDE_LIMITS, floor_model, true_pose  # noqa: E402
from so101_bridge.chess import workspace as ws  # noqa: E402
from so101_bridge.shared import motion  # noqa: E402
from so101_bridge.shared.plane import PlaneModel  # noqa: E402


def contacts(corners=((0, 0), (PLANE_CM, 0), (PLANE_CM, PLANE_CM), (0, PLANE_CM))):
    return [(u, v, true_pose(u, v)) for u, v in corners]


def build(tool_len=TOOL, terms=None, **kw):
    return PlaneModel(floor_model(), contacts(), (PLANE_CM, PLANE_CM), limits=WIDE_LIMITS,
                      tool_len=tool_len, terms=terms, **kw)


def test_the_report_is_named_after_the_task_that_built_it():
    """The same numbers read as 'brush on paper' or 'gripper on board' — one model, two vocabularies."""
    m = build(terms={"tool": "gripper", "plane": "board"})
    assert "gripper_len_cm" in m.report and "board_rms_cm" in m.report and "board_z_cm" in m.report
    m2 = build(terms={"tool": "brush", "plane": "paper"})
    assert "brush_len_cm" in m2.report and "paper_rms_cm" in m2.report


def test_a_given_tool_length_is_used_exactly_and_a_missing_one_is_fitted():
    given = build(tool_len=TOOL)
    assert given.tool_len == TOOL
    fitted = build(tool_len=None)
    assert fitted.ok and fitted.tool_len == pytest.approx(TOOL, abs=0.004)


def test_it_refuses_to_guess_from_fewer_than_four_contacts():
    m = PlaneModel(floor_model(), contacts()[:3], (PLANE_CM, PLANE_CM), tool_len=TOOL)
    assert not m.ok and "need the 4 corners" in m.error


def test_an_unfitted_floor_model_is_named_as_the_cause():
    fm = floor_model(); fm.params = None
    assert "floor" in PlaneModel(fm, contacts(), (PLANE_CM, PLANE_CM), tool_len=TOOL).error


def test_lean_can_be_forced_and_is_reported_back():
    m = build()
    at_taught = m.lean_at(10.0, 10.0)
    forced = m.uv_to_pose(10.0, 10.0, 0.0, lean=math.pi - math.radians(at_taught + 8))
    a1, a2, a3 = m._angles(forced)
    assert math.degrees(abs(math.pi - (a1 + a2 + a3))) == pytest.approx(at_taught + 8, abs=0.5)


def test_the_lean_cap_is_what_lets_a_high_square_be_served_at_all():
    """The taught pitch is a preference, not a constraint — but how far it may be bent is a setting.

    Chess raises it to 45 degrees because the far squares need the gripper stood right up; painting
    keeps it at 25 because a glued-in brush that is tilted too far drags its ferrule on the paper."""
    loose, tight = build(lean_max_dev_deg=45.0), build(lean_max_dev_deg=1.0)
    gained = [(u, v, z) for u in range(0, 26, 5) for v in range(0, 26, 5) for z in (0.0, 6.0, 9.0)
              if loose.uv_to_pose(u, v, z) is not None and tight.uv_to_pose(u, v, z) is None]
    assert gained, "a 45 degree cap must buy poses that a 1 degree cap cannot reach"
    for u, v, z in [(10.0, 10.0, 0.0)]:                                  # ...without changing the easy ones
        assert tight.uv_to_pose(u, v, z) == loose.uv_to_pose(u, v, z)


# ---------------------------------------------------------------- motion helpers
def test_densify_keeps_every_step_inside_the_control_loop_cap():
    a = {"shoulder_pan": 0.0, "shoulder_lift": 0.0, "elbow_flex": 0.0, "wrist_flex": 0.0, "wrist_roll": 0.0}
    b = {**a, "shoulder_lift": 47.0}
    pts = motion.densify_joint(a, b, step=8.0)
    assert pts[-1]["shoulder_lift"] == 47.0
    prev = a
    for p in pts:
        assert max(abs(p[j] - prev[j]) for j in p) <= 8.0 + 1e-9
        prev = p


def test_up_lifts_the_shoulder_and_keeps_the_tool_pointing_down():
    p = motion.up({"shoulder_lift": 20.0, "wrist_flex": 5.0}, 10.0)
    assert p["shoulder_lift"] == 10.0 and p["wrist_flex"] == 12.0


def test_bridge_walks_from_wherever_the_arm_actually_is():
    class C:
        def snapshot(self): return {"present": {"shoulder_lift": 0.0}}, {}
    pts = motion.bridge_from_present(C(), [{"shoulder_lift": 40.0}], step=8.0)
    assert pts[-1]["shoulder_lift"] == 40.0
    prev = 0.0
    for p in pts:                                             # every hop small enough to survive the step cap
        assert abs(p["shoulder_lift"] - prev) <= 8.0 + 1e-9
        prev = p["shoulder_lift"]
    near = motion.bridge_from_present(C(), [{"shoulder_lift": 3.0}], step=8.0)
    assert len(near) == 1                                     # already close: nothing prepended


def test_z_correction_interpolates_between_probes_and_falls_back_to_a_constant():
    assert motion.z_correction([], default=1.2)(5, 5) == 1.2
    corr = motion.z_correction([{"u": 0, "v": 0, "z_touch_cm": 1.0}, {"u": 20, "v": 0, "z_touch_cm": 2.0}])
    assert corr(0, 0) == pytest.approx(1.0, abs=0.05)
    assert corr(20, 0) == pytest.approx(2.0, abs=0.05)
    assert 1.0 < corr(10, 0) < 2.0


def test_plane_travel_is_straight_on_the_surface_and_can_ramp_the_height():
    m = build()
    pts = motion.plane_travel(m, (2.0, 2.0), (22.0, 22.0), 2.0, step_cm=2.0, z_to=8.0)
    assert pts is not None
    us, vs, zs = zip(*(m.pose_to_uv(p) for p in pts))
    assert (us[-1], vs[-1]) == pytest.approx((22.0, 22.0), abs=0.1)
    assert zs[-1] * 100 == pytest.approx(8.0, abs=0.1) and zs[0] * 100 < 3.0
    for u, v in zip(us, vs):
        assert abs(u - v) < 0.2                               # the line u = v, which is what was asked for
    assert all(zs[i] <= zs[i + 1] + 1e-6 for i in range(len(zs) - 1))


def test_plane_travel_refuses_rather_than_cutting_a_corner():
    m = build()
    assert motion.plane_travel(m, (2.0, 2.0), (400.0, 400.0), 2.0) is None


def test_pose_with_gripper_keeps_the_jaws_alone_unless_told():
    p = {"shoulder_pan": 1.0, "shoulder_lift": 2.0, "elbow_flex": 3.0, "wrist_flex": 4.0, "wrist_roll": 5.0}
    assert "gripper" not in motion.pose_with_gripper(p)
    assert motion.pose_with_gripper(p, 60)["gripper"] == 60


def test_square_names_survive_the_trip_through_the_generic_model():
    """A PlaneModel knows nothing about chess; the square names come back through the workspace alone."""
    m = build()
    d = ws.load(); d["board"]["size_cm"] = 29.0
    u, v = ws.square_uv(d, "c6")
    uu, vv, _ = m.pose_to_uv(m.uv_to_pose(u, v, 0.0))
    assert ws.uv_square(d, uu, vv) == "c6"
