"""A synthetic arm and a synthetic chess board, so the board model can be tested without hardware.

Ground truth is a planar 3-link chain with known joint offsets and a known gripper length; from it we
generate the poses an operator *would* have taught with the closed fingertips on a square centre. The
tests then fit the model to those poses and check it puts the jaws back where the truth says.

The taught lean grows towards the far edge, as it must on a real board — the arm stands the gripper up
to reach across — so the tests exercise the pitch field, not a single fixed wrist angle.
"""

import math

from so101_bridge.chess import workspace as ws_mod
from so101_bridge.floor import FloorModel

TRUTH = (-5.0, 20.0, -30.0, 0.075, 1, -1, 1)     # floor params: offsets, z0, signs
LINKS = {"L1": 0.1159, "L2": 0.1350, "L3": 0.110}
TOOL = 0.110                                     # wrist axis -> jaw centre, the value chess fixes
PAN0 = 8.0                                       # board rotated with respect to the arm's pan zero
ORIGIN = (-0.127, 0.285)                         # arm-frame xy of board (u=0, v=0), before the rotation
SIZE_CM = 29.0
PLANE_CM = 7 * SIZE_CM / 8


def floor_model():
    fm = FloorModel.__new__(FloorModel)
    fm.params, fm._c, fm.rms, fm.n = TRUTH, LINKS, 0.0005, 7
    return fm


def lean_deg(u, v):
    """The lean an operator would use at (u, v): upright close in, laid out towards the far edge.

    12 deg at the near rank to 40 at the far one, which is the shape of the real reach table: the arm
    has to stand the gripper up to get its wrist over the far squares at all."""
    return 12.0 + 28.0 * (PLANE_CM - v) / PLANE_CM


def true_pose(u, v, z_cm=0.0, lean=None, tool=TOOL):
    """Ground-truth joints with the jaw centre at board (u, v) cm, z_cm above the board."""
    phi = math.pi - math.radians(lean_deg(u, v) if lean is None else lean)
    x = ORIGIN[0] + u / 100.0
    y = ORIGIN[1] - v / 100.0
    xr = x * math.cos(math.radians(PAN0)) - y * math.sin(math.radians(PAN0))
    yr = x * math.sin(math.radians(PAN0)) + y * math.cos(math.radians(PAN0))
    r = math.hypot(xr, yr); pan = math.degrees(math.atan2(xr, yr))
    px = r - tool * math.sin(phi); pz = z_cm / 100.0 - tool * math.cos(phi) - TRUTH[3]
    L1, L2 = LINKS["L1"], LINKS["L2"]; D = math.hypot(px, pz)
    a2 = -math.acos(max(-1.0, min(1.0, (D * D - L1 * L1 - L2 * L2) / (2 * L1 * L2))))
    beta = math.acos(max(-1.0, min(1.0, (L1 * L1 + D * D - L2 * L2) / (2 * L1 * D))))
    a1 = math.atan2(px, pz) + beta
    a3 = phi - a1 - a2
    o1, o2, o3, _, s1, s2, s3 = TRUTH
    return {"shoulder_pan": pan, "shoulder_lift": o1 + math.degrees(a1) / s1,
            "elbow_flex": o2 + math.degrees(a2) / s2, "wrist_flex": o3 + math.degrees(a3) / s3}


WIDE_LIMITS = {j: [-400, 400] for j in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex")}


def taught(robot_side="white", extra=("e4", "d5"), grasps=("p", "k")):
    """A config/chess.json as it looks after a complete ``chess_board`` teaching session."""
    d = ws_mod.DEFAULTS
    import json
    d = json.loads(json.dumps(d))
    d["board"]["size_cm"] = SIZE_CM
    d["board"]["robot_side"] = robot_side
    d["gripper"]["tool_len_cm"] = TOOL * 100
    for name in ws_mod.CORNERS:
        u, v = ws_mod.square_uv(d, name)
        d["board"]["corners"][name] = true_pose(u, v)
    d["board"]["extra"] = [{"square": s, "pose": true_pose(*ws_mod.square_uv(d, s))} for s in extra]
    for p in grasps:
        z = 1.4 if p == "p" else 3.0
        u, v = ws_mod.square_uv(d, "e4")
        d["pieces"][p].update(grasp_z_cm=z, lean_deg=lean_deg(u, v), close=8.0, open=60.0, stall_min=12)
    for slot, (u, v) in {"handover": (-4.0, 12.7), "feed": (PLANE_CM + 4.0, 12.7),
                         "reserve_queen_white": (-4.0, 4.0), "reserve_queen_black": (-4.0, 20.0)}.items():
        d["slots"][slot] = {"pose": true_pose(u, v, 1.4), "uv": [u, v]}
    return d
