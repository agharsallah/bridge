"""Joint-space and plane-space helpers for building guarded ``path`` steps.

Every routine ends up doing the same four things: densify a joint-space transition so no single point is
step-capped by the control loop, lift the tool straight up before travelling, walk from wherever the arm
happens to be to the first point of a compiled step, and move in a straight line *on the taught surface*
at a given height. None of that is about brushes or chess pieces, so it lives here.
"""

import math

from ..settings import ARM

JOINT_STEP_DEG = 8.0           # densify joint-space transitions so every path point is within the step cap
SAFE_LIFT_DEG = 10.0           # go "up" this much (shoulder back) before travelling between work areas
BRIDGE_STEP_DEG = 8.0          # densify the approach from wherever the arm is to a step's first point
PLANE_STEP_CM = 2.0            # spacing of the interpolated points along an in-plane travel


def densify_joint(a, b, step=JOINT_STEP_DEG):
    """Points from just after ``a`` to ``b`` inclusive, no joint moving more than ``step`` degrees per point."""
    n = max(1, int(math.ceil(max(abs(b[j] - a[j]) for j in ARM if j in a and j in b) / step)))
    return [{j: round(a[j] + (b[j] - a[j]) * k / n, 3) for j in ARM if j in a and j in b} for k in range(1, n + 1)]


def up(pose, d=SAFE_LIFT_DEG):
    """The same pose lifted: shoulder back by ``d``, wrist compensating so the tool keeps pointing down."""
    p = dict(pose); p["shoulder_lift"] = pose["shoulder_lift"] - d; p["wrist_flex"] = pose.get("wrist_flex", 0) + 0.7 * d
    return p


def bridge_from_present(ctrl, points, step=BRIDGE_STEP_DEG):
    """Prepend a densified approach from the arm's present pose to the first point of a step.

    Programs are compiled without knowing where the arm will be when they start (rest, or wherever a
    previous run was interrupted). A far first point would be step-capped by the control loop and the arm
    would run the rest of the step from the wrong place — so walk there first, in small steps."""
    st, _ = ctrl.snapshot(); present = st.get("present") or {}
    first = points[0]
    joints = [j for j in first if j in present]
    if not joints: return points
    far = max(abs(first[j] - present[j]) for j in joints)
    if far <= step: return points
    n = int(far // step) + 1
    lead = [{j: round(present[j] + (first[j] - present[j]) * k / n, 3) for j in joints} for k in range(1, n)]
    return lead + list(points)


def z_correction(probes, default=0.0):
    """Height correction (cm) to add to any surface z the plane model is asked for, as a function of (u, v).

    The plane model is geometric and its corners were taught in free-drive; under torque the arm sags and the
    tool ends up lower than planned (1.5-2 cm on this arm). A probe records where the tool *actually* touched
    the surface at a *commanded* height: ``{"u", "v", "z_touch_cm"}``. Between probes the correction is
    inverse-distance interpolated; with no probes it is the constant ``default``."""
    probes = [p for p in (probes or []) if "z_touch_cm" in p]
    if not probes:
        return lambda u, v: float(default)

    def corr(u, v):
        w = [1.0 / max(0.25, math.hypot(u - float(p["u"]), v - float(p["v"]))) ** 2 for p in probes]
        return sum(wi * float(p["z_touch_cm"]) for wi, p in zip(w, probes)) / sum(w)
    return corr


def plane_travel(model, from_uv, to_uv, z_cm, step_cm=PLANE_STEP_CM, z_to=None, zcorr=None, **ik):
    """Poses along a straight line on the surface, from ``from_uv`` to ``to_uv`` at ``z_cm`` above it.

    Straight in *surface* coordinates, not in joint space: a joint-space interpolation between two board
    squares bows across the board and knocks over whatever stands between them. ``z_to`` ramps the height
    linearly along the way, which is how a piece lifted from the near rank (where the arm can only reach at
    grasp height) climbs to carry height without being dragged over its neighbours. ``zcorr(u, v)`` adds the
    measured sag correction. Returns None if any point on the line is unreachable — the caller decides
    whether to route around it or hand the move to a human."""
    (u0, v0), (u1, v1) = from_uv, to_uv
    dist = math.hypot(u1 - u0, v1 - v0)
    n = max(1, int(math.ceil(dist / max(step_cm, 0.1))))
    z1 = z_cm if z_to is None else z_to
    out = []
    for k in range(1, n + 1):
        t = k / n
        u, v, z = u0 + (u1 - u0) * t, v0 + (v1 - v0) * t, z_cm + (z1 - z_cm) * t
        pose = model.uv_to_pose(u, v, z + (zcorr(u, v) if zcorr else 0.0), **ik)
        if pose is None: return None
        out.append(pose)
    return out


def pose_with_gripper(pose, gripper=None):
    """IK poses have no gripper; keep whatever the arm holds unless told otherwise."""
    p = {j: pose[j] for j in ARM if j in pose}
    if gripper is not None: p["gripper"] = gripper
    return p
