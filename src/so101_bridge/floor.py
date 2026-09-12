"""Floor model: planar 3-link kinematics fitted to poses where the gripper tips touch the table.

Height is independent of pan. Link lengths come from the SO-101 design; the fitted unknowns are the
joint zero-offsets, the axis directions and the shoulder height. Fingertip height is what stops the
arm from pushing into the table — see docs/learnings.md.
"""

import json

import numpy as np

from .paths import FLOOR_CFG, FLOOR_FILE
from .util import log

L1, L2, L3 = 0.1159, 0.1350, 0.110         # m: shoulder->elbow, elbow->wrist, wrist axis -> fingertip (defaults)
FLOOR_MARGIN = 0.008                       # m: never command the tips below this height
FLOOR_FREEZE = 0.0                         # m: freeze if the predicted height ever drops below this while moving
GRASP_Z = 0.010                            # m: tip height for grasping a Duplo brick (its top is ~19 mm)
SEED_CONTACTS = [                          # contacts observed during commissioning (lift, elbow, wrist)
    {"shoulder_lift": 41.3, "elbow_flex": 76.3, "wrist_flex": 0.0},
    {"shoulder_lift": 66.7, "elbow_flex": 53.6, "wrist_flex": -5.3},
]


class FloorModel:
    def __init__(self):
        self.params = None; self.rms = None; self.n = 0
        self.load()

    def cfg(self):
        try: return json.loads(FLOOR_CFG.read_text()) if FLOOR_CFG.is_file() else {}
        except Exception as e: log(f"config/floor_config.json unreadable: {e}"); return {}

    def height(self, q, p):
        o1, o2, o3, z0, s1, s2, s3 = p
        c = self._c
        a1 = s1 * np.radians(q[..., 0] - o1); a2 = s2 * np.radians(q[..., 1] - o2); a3 = s3 * np.radians(q[..., 2] - o3)
        return z0 + c["L1"] * np.cos(a1) + c["L2"] * np.cos(a1 + a2) + c["L3"] * np.cos(a1 + a2 + a3)

    def points(self):
        pts = list(SEED_CONTACTS)
        if FLOOR_FILE.is_file():
            try: pts += json.loads(FLOOR_FILE.read_text())
            except Exception as e: log(f"config/floor_points.json unreadable: {e}")
        return pts

    def add_point(self, pose, height_m=0.0):
        pts = json.loads(FLOOR_FILE.read_text()) if FLOOR_FILE.is_file() else []
        pt = {k: round(float(pose[k]), 2) for k in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")}
        pt["height"] = round(float(height_m), 4); pts.append(pt)
        FLOOR_FILE.write_text(json.dumps(pts, indent=1)); log(f"FLOOR contact #{len(pts)} saved (tips at {height_m * 100:.1f} cm): {pt}")
        self.load()

    def load(self):
        cfg = self.cfg()
        self._c = {"L1": float(cfg.get("L1", L1)), "L2": float(cfg.get("L2", L2)), "L3": float(cfg.get("L3", L3))}
        z0_fixed = cfg.get("z0")
        pts = self.points(); self.n = len(pts)
        q = np.array([[pt["shoulder_lift"], pt["elbow_flex"], pt["wrist_flex"]] for pt in pts], dtype=float)
        self._h = np.array([float(pt.get("height", 0.0)) for pt in pts])
        need = 3 if z0_fixed is not None else 4
        if len(q) < need:
            self.params = None; log(f"floor model: {len(q)} contact points, need >= {need} (add more with 'Mark FLOOR')"); return
        best = None
        for s1 in (1, -1):
            for s2 in (1, -1):
                for s3 in (1, -1):
                    for o1 in (-60, 0, 60):
                        for o2 in (-60, 0, 60):
                            for o3 in (-60, 0, 60):
                                p = self._fit(q, [o1, o2, o3, float(z0_fixed) if z0_fixed is not None else 0.08, s1, s2, s3],
                                              nfit=3 if z0_fixed is not None else 4)
                                if p is None: continue
                                r = self.height(q, p) - self._h; rms = float(np.sqrt(np.mean(r ** 2)))
                                # sanity: READY pose must be clearly above the table, z0 plausible
                                ready = float(self.height(np.array([0.0, 70.5, 37.5]), p))
                                if not (0.0 < p[3] < 0.25) or not (0.03 < ready < 0.45): continue
                                if best is None or rms < best[1]: best = (p, rms)
        if best is None:
            self.params = None; log("floor model: fit failed (contact points inconsistent?)"); return
        self.params, self.rms = best
        log(f"floor model fitted on {len(q)} points ({'z0 measured' if z0_fixed is not None else 'z0 free - measure it for accuracy'}): "
            f"rms {self.rms * 1000:.1f} mm, offsets {[round(float(x), 1) for x in self.params[:3]]}, "
            f"z0 {self.params[3] * 100:.1f} cm, signs {[int(x) for x in self.params[4:]]}")

    def _fit(self, q, p0, nfit=4, iters=80):
        p = np.array(p0, dtype=float); h = self._h
        lam = 1e-2
        for _ in range(iters):
            r = self.height(q, p) - h
            J = np.zeros((len(q), nfit))
            for k in range(nfit):
                dp = np.zeros_like(p); dp[k] = 1e-4 if k == 3 else 1e-2
                J[:, k] = (self.height(q, p + dp) - h - r) / dp[k]
            H = J.T @ J + lam * np.eye(nfit); g = J.T @ r
            try: step = np.linalg.solve(H, g)
            except np.linalg.LinAlgError: return None
            p_new = p.copy(); p_new[:nfit] -= step
            if np.sum((self.height(q, p_new) - h) ** 2) < np.sum(r ** 2): p, lam = p_new, max(lam / 3, 1e-6)
            else: lam = min(lam * 5, 1e3)
            if np.abs(step).max() < 1e-6: break
        return p

    def z(self, pose):
        """Predicted fingertip height (m) for a joint pose dict, or None if the model isn't fitted."""
        if self.params is None: return None
        return float(self.height(np.array([pose["shoulder_lift"], pose["elbow_flex"], pose["wrist_flex"]]), self.params))
