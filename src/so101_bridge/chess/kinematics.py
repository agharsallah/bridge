"""Gripper kinematics on the board plane — the chess-shaped view of :mod:`shared.plane`.

Painting fits the tool length because a brush glued into the gripper has no known length. Chess does
not: the tool *is* the bare gripper, and the distance from the wrist axis to the point between the
fingertips is a dimension of the arm (``config/chess.json["gripper"]["tool_len_cm"]``, seeded with the
floor model's L3 and corrected once it is measured on the real arm). Fixing it keeps the four taught
corners doing one job — locating and orienting the board — instead of trading length against position.

    m = GripperModel(ctrl.floor, workspace.load(), limits)
    m.pose_for("e4", z_cm=6.0)            # joint pose with the jaws centred over e4, 6 cm above the board
    m.reachability_report()               # all 64 squares at grasp / hover / carry height
"""

import math

from ..settings import CHESS_CARRY_CM
from ..shared.plane import PlaneModel, _wrap
from . import workspace as ws_mod

TERMS = {"tool": "gripper", "plane": "board", "depths": "hover and grasp height",
         "touching": "the closed fingertips on the square centres",
         "orient": "going round the board, looking at it from the robot's side"}


class GripperModel(PlaneModel):
    """A :class:`PlaneModel` over the chess board, with the gripper's reach given rather than fitted."""

    def __init__(self, floor, d, limits=None):
        self.d = d
        quad = ws_mod.corner_quad(d)
        super().__init__(floor, ws_mod.contact_points(d), ws_mod.plane_cm(d), limits=limits,
                         tool_len=float(d["gripper"]["tool_len_cm"]) / 100.0, quad=quad,
                         terms=TERMS, lean_max_dev_deg=float(d["gripper"].get("max_lean_deg", 45.0)))
        if quad is None and self.ok:
            # extra contacts can make up the count, but they cannot orient the board: without all four
            # corners the homography is anchored to whatever was taught and a1 may not be where it thinks.
            missing = [n for n in ws_mod.CORNERS if n not in d["board"].get("corners", {})]
            self.ok = False
            self.error = f"the board corners {', '.join(missing)} have not been taught"

    # ------------------------------------------------------------------ squares
    def pose_for(self, square, z_cm=0.0, lean_deg=None, **kw):
        """Joint pose with the jaw centre over ``square`` at ``z_cm`` above the board, or None.

        ``lean_deg`` is the lean the grasp was taught at (degrees from vertical-down); the IK starts
        there and walks outwards, so a square that needs more lean than the operator used still gets a
        solution instead of a refusal.

        A square the operator has driven to by hand (``chess.json["board"]["reach"]``) is solved against
        the range that pose proved, instead of the soft limit — on that square only."""
        u, v = ws_mod.square_uv(self.d, square)
        lean = None if lean_deg is None else math.pi - math.radians(float(lean_deg))
        kw.setdefault("allow", ws_mod.reach_allow(self.d, square))
        return self.uv_to_pose(u, v, z_cm, lean=lean, **kw)

    def square_uv(self, square):
        return ws_mod.square_uv(self.d, square)

    def lean_of(self, pose):
        """The lean of the gripper in a taught pose: degrees away from pointing straight down."""
        a1, a2, a3 = self._angles(pose)
        return round(math.degrees(abs(_wrap(a1 + a2 + a3 - math.pi))), 1)

    def pose_square(self, pose):
        """Which square the jaws are over in a given pose, and the height above the board in cm."""
        u, v, z = self.pose_to_uv(pose)
        return ws_mod.uv_square(self.d, u, v), round(z * 100, 2)

    # ------------------------------------------------------------------ the report the operator reads before a game
    def reachability_report(self, heights=None):
        """Every square at every working height: what can be reached, how much lean it costs.

        ``{"heights": {...}, "squares": {"e4": {"grasp": 12.0, "hover": 18.0, "carry": None}, ...},
           "unreachable": {"carry": ["a8", ...]}, "max_lean_deg": ..., "ok": bool}``

        A height whose value is a number is reachable and that number is the lean in degrees the IK
        would use; None means no pose inside the joint limits puts the jaws there. Printed by
        ``chess_compile`` before any game starts, and the source of ``chess.json["unreachable"]``."""
        h = self.d["heights"]
        heights = heights or {"grasp": float(h.get("grasp_default_cm", 1.4)),
                              "hover": float(h.get("hover_cm", 6.0)),
                              "carry": float(h.get("carry_cm", CHESS_CARRY_CM))}
        out = {"ok": self.ok, "error": self.error, "heights": heights, "squares": {}, "unreachable": {},
               "max_lean_deg": None, "reachable_pct": {}}
        if not self.ok: return out
        names = ws_mod.squares(self.d)
        worst = 0.0
        for name in names:
            row = {}
            for label, z in heights.items():
                pose = self.pose_for(name, z)
                row[label] = None if pose is None else self.lean_of(pose)
                if row[label] is not None: worst = max(worst, row[label])
            out["squares"][name] = row
        for label in heights:
            miss = [n for n in names if out["squares"][n][label] is None]
            out["unreachable"][label] = miss
            out["reachable_pct"][label] = round(100 * (len(names) - len(miss)) / len(names), 1)
        out["max_lean_deg"] = round(worst, 1)
        return out


def report_text(report, d=None):
    """The reachability report as an 8x8 table of leans, for the log and the terminal.

    One grid per height, files across, the robot's near rank at the bottom — the operator is looking at
    the board from the robot's seat, so the printout has to match what they see."""
    if not report.get("ok"):
        return f"chess: no board model — {report.get('error')}"
    d = d or ws_mod.load()
    names = ws_mod.squares(d)
    lines = []
    for label, z in report["heights"].items():
        miss = report["unreachable"][label]
        lines.append(f"  {label} ({z} cm above the board): {report['reachable_pct'][label]}% reachable"
                     + (f", out of reach: {' '.join(miss)}" if miss else ""))
        for r in range(8):
            row = names[r * 8:(r + 1) * 8]
            lines.append("    " + " ".join(f"{n}:" + ("--" if report['squares'][n][label] is None
                                                      else f"{report['squares'][n][label]:.0f}") for n in row))
    return ("chess: reachability (lean in degrees, -- = no pose inside the joint limits)\n"
            + "\n".join(lines) + f"\n  worst lean used: {report['max_lean_deg']} deg")
