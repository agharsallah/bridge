"""Brush-tip kinematics on the paper plane, fitted from the taught paper corners.

The arm's planar chain (shoulder → elbow → wrist) comes from the fitted floor model. The brush is treated
as a rigid extension of the gripper along the fingertip direction; from the corner poses (brush tip ON the
paper) we estimate the brush pitch the operator used, the brush length, and a homography from arm-frame
XY to paper coordinates (cm). That homography absorbs the unknown pan zero, the paper's rotation and
mild scale errors, so paper coordinates are accurate where it matters: on the paper.

    tool = ToolModel(ctrl.floor, workspace.load(), limits)
    tool.ok                      # fitted?
    tool.uv_to_pose(u, v, z_cm)  # joint dict for the brush tip at (u, v) cm, z above the paper — or None if unreachable
    tool.reachability(step_cm)   # grid mask of the paper
"""

import math

import numpy as np

from ..settings import ARM
from .workspace import contact_points

TOOL_DEFAULT_M = 0.16                 # brush tip distance from the wrist axis if it cannot be fitted


class ToolModel:
    def __init__(self, floor, ws, limits=None):
        self.floor, self.ws, self.limits = floor, ws, limits or {}
        self.ok, self.error = False, None
        self.report = {}
        self._fit()

    # ------------------------------------------------------------------ chain helpers (floor-model conventions)
    def _angles(self, pose):
        o1, o2, o3, z0, s1, s2, s3 = self.floor.params
        a1 = s1 * math.radians(pose["shoulder_lift"] - o1)
        a2 = s2 * math.radians(pose["elbow_flex"] - o2)
        a3 = s3 * math.radians(pose["wrist_flex"] - o3)
        return a1, a2, a3

    def _wrist_point(self, a1, a2):
        c = self.floor._c; z0 = self.floor.params[3]
        return (c["L1"] * math.sin(a1) + c["L2"] * math.sin(a1 + a2),
                z0 + c["L1"] * math.cos(a1) + c["L2"] * math.cos(a1 + a2))

    def tip_xyz(self, pose):
        """Brush tip in the arm frame (x right, y forward, z up; metres) for a joint pose."""
        a1, a2, a3 = self._angles(pose); phi = a1 + a2 + a3
        rw, zw = self._wrist_point(a1, a2)
        r = rw + self.tool_len * math.sin(phi); z = zw + self.tool_len * math.cos(phi)
        pan = math.radians(pose["shoulder_pan"])
        return r * math.sin(pan), r * math.cos(pan), z

    def pose_to_uv(self, pose):
        x, y, z = self.tip_xyz(pose)
        u, v = self._apply_h(self.H, x, y)
        return u, v, z

    # ------------------------------------------------------------------ fitting
    def _fit(self):
        if self.floor.params is None:
            self.error = "floor model not fitted"; return
        pts = contact_points(self.ws)
        if len(pts) < 4:
            self.error = f"{len(pts)} paper contacts taught, need the 4 corners"; return
        # 1) brush pitch = the orientation the operator used at the contacts (circular mean)
        phis, a2s, a3s = [], [], []
        for _, _, p in pts:
            a1, a2, a3 = self._angles(p); phis.append(a1 + a2 + a3); a2s.append(a2); a3s.append(a3)
        self.phi = math.atan2(sum(math.sin(f) for f in phis), sum(math.cos(f) for f in phis))
        self.elbow_sign = 1.0 if sum(a2s) >= 0 else -1.0
        self.a3_ref = float(np.mean(a3s))
        # 2) brush length from z(tip) = 0 at every contact
        ests = []
        for (_, _, p), f in zip(pts, phis):
            a1, a2, _ = self._angles(p); _, zw = self._wrist_point(a1, a2)
            if abs(math.cos(f)) > 0.3: ests.append(-zw / math.cos(f))
        self.tool_len = float(np.median(ests)) if ests else TOOL_DEFAULT_M
        tool_note = "" if ests else " (brush nearly horizontal: length assumed)"
        if not (0.03 < self.tool_len < 0.40):
            self.error = f"implausible brush length {self.tool_len * 100:.1f} cm — re-teach the corners with the brush touching the paper"; return
        # 3) arm-frame XY -> paper (u, v) homography (DLT, least squares if > 4 points)
        xy = np.array([self.tip_xyz(p)[:2] for _, _, p in pts]); uv = np.array([(u, v) for u, v, _ in pts])
        self.H = self._dlt(xy, uv)
        if self.H is None:
            self.error = "corner poses are degenerate (collinear?)"; return
        self.Hinv = np.linalg.inv(self.H)
        # 4) residuals
        res_uv = [math.hypot(*(np.array(self._apply_h(self.H, *xy[i])) - uv[i])) for i in range(len(pts))]
        res_z = [self.tip_xyz(p)[2] for _, _, p in pts]
        self.report = {"contacts": len(pts), "brush_len_cm": round(self.tool_len * 100, 1),
                       "pitch_deg": round(math.degrees(self.phi), 1), "xy_rms_cm": round(float(np.sqrt(np.mean(np.square(res_uv)))), 2),
                       "xy_max_cm": round(max(res_uv), 2), "z_rms_mm": round(float(np.sqrt(np.mean(np.square(res_z)))) * 1000, 1),
                       "note": tool_note.strip(" ()")}
        self.ok = True

    @staticmethod
    def _dlt(src, dst):
        rows = []
        for (x, y), (u, v) in zip(src, dst):
            rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u]); rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
        A = np.array(rows, dtype=float)
        try:
            _, s, vt = np.linalg.svd(A)
        except np.linalg.LinAlgError:
            return None
        H = vt[-1].reshape(3, 3)
        if abs(H[2, 2]) < 1e-12: return None
        H = H / H[2, 2]
        return H if abs(np.linalg.det(H)) > 1e-9 else None

    @staticmethod
    def _apply_h(H, x, y):
        w = H[2, 0] * x + H[2, 1] * y + H[2, 2]
        return (H[0, 0] * x + H[0, 1] * y + H[0, 2]) / w, (H[1, 0] * x + H[1, 1] * y + H[1, 2]) / w

    # ------------------------------------------------------------------ inverse kinematics
    def uv_to_pose(self, u, v, z_cm=0.0, check_limits=True):
        """Joint pose putting the brush tip at paper (u, v) cm, z_cm above the paper, with the taught pitch."""
        if not self.ok: return None
        x, y = self._apply_h(self.Hinv, u, v)
        r, z = math.hypot(x, y), z_cm / 100.0
        pan = math.degrees(math.atan2(x, y))
        c = self.floor._c; o1, o2, o3, z0, s1, s2, s3 = self.floor.params
        L1, L2 = c["L1"], c["L2"]
        px = r - self.tool_len * math.sin(self.phi); pz = z - self.tool_len * math.cos(self.phi) - z0
        D = math.hypot(px, pz)
        if D > L1 + L2 - 1e-6 or D < abs(L1 - L2) + 1e-6: return None
        cos_a2 = (D * D - L1 * L1 - L2 * L2) / (2 * L1 * L2)
        a2 = self.elbow_sign * math.acos(max(-1.0, min(1.0, cos_a2)))
        beta = math.acos(max(-1.0, min(1.0, (L1 * L1 + D * D - L2 * L2) / (2 * L1 * D))))
        theta = math.atan2(px, pz)
        a1 = theta - (beta if a2 > 0 else -beta)
        a3 = self.phi - a1 - a2
        a3 = a3 - 2 * math.pi * round((a3 - self.a3_ref) / (2 * math.pi))     # nearest wrap to the taught wrist angle
        pose = {"shoulder_pan": pan, "shoulder_lift": o1 + math.degrees(a1) / s1,
                "elbow_flex": o2 + math.degrees(a2) / s2, "wrist_flex": o3 + math.degrees(a3) / s3}
        if check_limits and self.limits:
            for j, val in pose.items():
                lo, hi = self.limits.get(j, (-1e9, 1e9))
                if not (lo <= val <= hi): return None
        return {j: round(val, 3) for j, val in pose.items()}

    def reachability(self, step_cm=1.0, z_cm=0.0):
        """Grid over the paper: 1 where the brush tip can be placed, 0 where not. Returns (cols, rows, list of rows)."""
        w, h = float(self.ws["paper"]["width_cm"]), float(self.ws["paper"]["height_cm"])
        cols, rows = max(1, int(round(w / step_cm))), max(1, int(round(h / step_cm)))
        grid = [[1 if self.uv_to_pose((i + 0.5) * w / cols, (k + 0.5) * h / rows, z_cm) else 0 for i in range(cols)] for k in range(rows)]
        return cols, rows, grid

    def summary(self):
        d = {"ok": self.ok, "error": self.error, **self.report}
        if self.ok:
            cols, rows, grid = self.reachability(1.0)
            d["reachable_pct"] = round(100 * sum(map(sum, grid)) / (cols * rows), 1)
        return d


def pose_with_gripper(pose, gripper=None):
    """IK poses have no gripper; keep whatever the arm holds (the brush) unless told otherwise."""
    p = {j: pose[j] for j in ARM if j in pose}
    if gripper is not None: p["gripper"] = gripper
    return p
