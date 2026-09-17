"""Tool-tip kinematics on a flat taught surface, fitted from poses where the tip touched it.

The arm's planar chain (shoulder -> elbow -> wrist) comes from the fitted floor model. The tool is treated
as a rigid extension of the gripper along the fingertip direction; from the taught contacts (tip ON the
surface) we estimate the tool length — or take it as given — the tool pitch the operator used, and a
homography from arm-frame XY to surface coordinates (cm). That homography absorbs the unknown pan zero,
the surface's rotation and mild scale errors, so surface coordinates are accurate where it matters: on
the surface.

    m = PlaneModel(ctrl.floor, contacts, (w_cm, h_cm), limits, tool_len=None)
    m.ok                      # fitted?
    m.uv_to_pose(u, v, z_cm)  # joint dict for the tip at (u, v) cm, z above the surface — None if unreachable
    m.reachability(step_cm)   # grid mask of the surface

``tool_len=None`` fits the length from the contacts (a glued-in brush, whose length nobody measured).
A number fixes it (the bare gripper: wrist axis -> jaw centre is a known dimension of the arm).
"""

import math

import numpy as np

TOOL_DEFAULT_M = 0.16                 # tip distance from the wrist axis if it cannot be fitted
TOOL_MIN_M, TOOL_MAX_M = 0.03, 0.40   # plausible tool lengths; outside this the marks are wrong, not the tool
PLANE_WEIGHT = 1.0                    # coplanarity vs surface-size residual in the tool-length fit (both in m^2)
SIZE_FIT_WARN_CM = 1.5                # taught corner spacing may differ from the configured size by this much
REACH_MARGIN_M = 0.004                # keep the elbow off full extension / full fold: no zero-manipulability poses
LEAN_MAX_DEV_DEG = 25.0               # how far the tool's pitch may stray from the taught one
LEAN_STEP_DEG = 1.0                   # granularity of the search for a pitch that also satisfies the joint limits

TERMS = {"tool": "tool", "plane": "surface", "orient": "keeping the orientation you will work in",
         "depths": "hover and contact depth", "touching": "the tip touching the surface"}


def _wrap(a):
    """Angle folded into (-pi, pi]."""
    return math.atan2(math.sin(a), math.cos(a))


class PlaneModel:
    def __init__(self, floor, contacts, size_cm, limits=None, tool_len=None, quad=None, terms=None,
                 lean_max_dev_deg=LEAN_MAX_DEV_DEG):
        """floor: fitted FloorModel. contacts: [(u, v, pose), ...] with the tip ON the surface.
        size_cm: (width, height) of the surface the (u, v) frame spans. quad: optional
        [(name, u, v, pose), ...] of four contacts in cycle order, checked for a simple quadrilateral."""
        self.floor, self.limits = floor, limits or {}
        self.contacts = list(contacts)
        self.w_cm, self.h_cm = float(size_cm[0]), float(size_cm[1])
        self.tool_len_fixed = float(tool_len) if tool_len is not None else None
        self.quad = list(quad) if quad else None
        self.terms = {**TERMS, **(terms or {})}
        self.lean_max_dev_deg = float(lean_max_dev_deg)
        self.ok, self.error = False, None
        self.report, self.warnings = {}, []
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
        """Tool tip in the arm frame (x right, y forward, z up; metres) for a joint pose."""
        a1, a2, a3 = self._angles(pose); phi = a1 + a2 + a3
        rw, zw = self._wrist_point(a1, a2)
        r = rw + self.tool_len * math.sin(phi); z = zw + self.tool_len * math.cos(phi)
        pan = math.radians(pose["shoulder_pan"])
        return r * math.sin(pan), r * math.cos(pan), z

    def pose_to_uv(self, pose):
        """Surface coordinates (u, v in cm) and height above the surface (m) of the tip in a joint pose."""
        x, y, z = self.tip_xyz(pose)
        u, v = self._apply_h(self.H, x, y)
        return u, v, z - getattr(self, "z_plane", 0.0)

    # ------------------------------------------------------------------ fitting
    def _fit(self):
        T = self.terms
        if self.floor.params is None:
            self.error = "floor model not fitted"; return
        pts = self.contacts
        if len(pts) < 4:
            self.error = f"{len(pts)} {T['plane']} contacts taught, need the 4 corners"; return
        # 1) tool pitch = the orientation the operator used at the contacts (circular mean)
        phis, a2s, a3s = [], [], []
        for _, _, p in pts:
            a1, a2, a3 = self._angles(p); phis.append(a1 + a2 + a3); a2s.append(a2); a3s.append(a3)
        self.phi = math.atan2(sum(math.sin(f) for f in phis), sum(math.cos(f) for f in phis))
        self.elbow_sign = 1.0 if sum(a2s) >= 0 else -1.0
        self.a3_ref = float(np.mean(a3s))
        self._fit_pitch_field(pts, phis)
        # 2) tool length (see _fit_tool_len), then the corner cycle must be a simple quadrilateral
        self.tool_len, fit = self._fit_tool_len(pts, phis)
        if not (TOOL_MIN_M < self.tool_len < TOOL_MAX_M):
            self.error = (f"implausible {T['tool']} length {self.tool_len * 100:.1f} cm — re-teach the corners with "
                          f"{T['touching']}"); return
        order_error = self._corner_order_error()
        if order_error:
            self.error = order_error; return
        # the surface is where the taught contacts are, NOT the floor model's z = 0. z0 is free in the floor
        # fit unless it was measured, so its zero can be centimetres out; anchoring "on the surface" to the marks
        # the operator made with the tip on it keeps hover and contact depth right whatever z0 says.
        self.z_plane = float(np.mean([self.tip_xyz(p)[2] for _, _, p in pts]))
        # 3) arm-frame XY -> surface (u, v) homography (DLT, least squares if > 4 points)
        xy = np.array([self.tip_xyz(p)[:2] for _, _, p in pts]); uv = np.array([(u, v) for u, v, _ in pts])
        self.H = self._dlt(xy, uv)
        if self.H is None:
            self.error = "corner poses are degenerate (collinear?)"; return
        self.Hinv = np.linalg.inv(self.H)
        # 4) residuals and the warnings that tell the operator which input to distrust
        res_uv = [math.hypot(*(np.array(self._apply_h(self.H, *xy[i])) - uv[i])) for i in range(len(pts))]
        w, h = self.w_cm, self.h_cm
        pitches = [self.pitch_at(u, v) for u in (0, w) for v in (0, h)]
        self.warnings = []
        if fit["size_rms_cm"] > SIZE_FIT_WARN_CM:
            self.warnings.append(f"taught corners disagree with the configured {T['plane']} size by {fit['size_rms_cm']} cm rms "
                                 f"— check {T['plane']} width/height and the corner marks")
        if self.tool_len_fixed is None and fit["z_spread_cm"] > 5.0:
            self.warnings.append(f"the tip-on-{T['plane']} marks imply {T['tool']} lengths spanning {fit['z_spread_cm']} cm — the "
                                 f"floor model's heights are unreliable, the {T['plane']} size was used instead")
        if abs(fit["floor_z_error_cm"]) > 1.0:
            self.warnings.append(f"floor model puts the taught contacts {fit['floor_z_error_cm']:+.1f} cm off the table "
                                 f"— re-measure z0 (config/floor_config.json) for accurate {T['depths']}")
        self.report = {"contacts": len(pts), f"{T['tool']}_len_cm": round(self.tool_len * 100, 1),
                       "pitch_deg": round(math.degrees(self.phi), 1),
                       "pitch_range_deg": [round(math.degrees(min(pitches)), 1), round(math.degrees(max(pitches)), 1)],
                       "xy_rms_cm": round(float(np.sqrt(np.mean(np.square(res_uv)))), 2),
                       "xy_max_cm": round(max(res_uv), 2), f"{T['plane']}_z_cm": round(self.z_plane * 100, 1),
                       f"{T['plane']}_rms_cm": fit["size_rms_cm"], "flatness_mm": fit["flatness_mm"],
                       "warnings": self.warnings, "note": "; ".join(self.warnings)}
        self.ok = True

    def _fit_pitch_field(self, pts, phis):
        """Taught tool pitch as an affine function of the surface position, phi(u, v) = c0 + c1*u + c2*v.

        Nobody holds a tool at one angle across a whole sheet: reaching the far edge the operator stands it
        up, close in they lay it down, and the corner marks record that. Averaging the four into a single
        pitch throws it away and makes the IK command a wrist tens of degrees from anything that was ever
        demonstrated. The taught pitches are near-exactly affine in (u, v), so keep the trend and fall back to
        the circular mean if the fit is degenerate.
        """
        self.phi_coef = None
        unwrapped = [self.phi + _wrap(f - self.phi) for f in phis]
        self.phi_lo, self.phi_hi = min(unwrapped) - math.radians(5), max(unwrapped) + math.radians(5)
        if len(pts) < 3: return
        A = np.array([[1.0, u, v] for u, v, _ in pts])
        if np.linalg.matrix_rank(A) < 3: return
        coef, *_ = np.linalg.lstsq(A, np.array(unwrapped), rcond=None)
        self.phi_coef = coef

    def pitch_at(self, u, v):
        """The tool pitch the operator would have used at (u, v), clamped to the range they demonstrated."""
        if self.phi_coef is None: return self.phi
        return min(self.phi_hi, max(self.phi_lo, float(self.phi_coef[0] + self.phi_coef[1] * u + self.phi_coef[2] * v)))

    def _fit_tool_len(self, pts, phis):
        """Tool length from the two things the operator actually knows: the tip was on the surface at every
        mark, and the surface is a rectangle of the configured size.

        The obvious estimator, ``-z_wrist / cos(pitch)``, needs the floor model's *absolute* height to be right.
        It rarely is — z0 is free in the floor fit unless it was measured — and the estimate blows up as the
        tool approaches horizontal, so marks taken at different pitches disagree wildly. Here the table height
        is left free (only the *spread* of the tip heights is penalised, which is real: the surface is flat)
        and the scale is pinned by the distances between the marks, which are known in cm from the surface
        size and are immune to the pan zero. Returns (length_m, diagnostics).

        With ``tool_len`` given (a bare gripper, whose reach is a known arm dimension) nothing is fitted; the
        same diagnostics are still computed at that length so the operator sees whether the marks agree with it.
        """
        geo = []
        for (u, v, p), f in zip(pts, phis):
            a1, a2, _ = self._angles(p); rw, zw = self._wrist_point(a1, a2)
            geo.append((u, v, rw, zw, f, math.radians(p["shoulder_pan"])))
        n = len(geo)
        pairs = [(i, j, math.hypot(geo[i][0] - geo[j][0], geo[i][1] - geo[j][1]) / 100.0)
                 for i in range(n) for j in range(i + 1, n)]

        def residuals(t):
            zs, xy = [], []
            for _, _, rw, zw, f, pan in geo:
                zs.append(zw + t * math.cos(f))
                r = rw + t * math.sin(f); xy.append((r * math.sin(pan), r * math.cos(pan)))
            zm = sum(zs) / n
            flat = sum((z - zm) ** 2 for z in zs)
            size = sum((math.hypot(xy[i][0] - xy[j][0], xy[i][1] - xy[j][1]) - d) ** 2 for i, j, d in pairs)
            return flat, size, zm

        def cost(t):
            flat, size, _ = residuals(t)
            return PLANE_WEIGHT * flat + size

        def diagnose(t):
            flat, size, zm = residuals(t)
            z_ests = [-zw / math.cos(f) for _, _, _, zw, f, _ in geo if abs(math.cos(f)) > 0.3]
            return {"size_rms_cm": round(math.sqrt(size / len(pairs)) * 100, 2) if pairs else 0.0,
                    "flatness_mm": round(math.sqrt(flat / n) * 1000, 1),
                    "z_spread_cm": round((max(z_ests) - min(z_ests)) * 100, 1) if len(z_ests) > 1 else 0.0,
                    "floor_z_error_cm": round(zm * 100, 1)}

        if self.tool_len_fixed is not None:
            return self.tool_len_fixed, diagnose(self.tool_len_fixed)
        if not pairs:                                             # cannot happen with 4 corners; stay defensive
            return TOOL_DEFAULT_M, {"size_rms_cm": 0.0, "flatness_mm": 0.0, "z_spread_cm": 0.0, "floor_z_error_cm": 0.0}
        lo, hi = TOOL_MIN_M, TOOL_MAX_M
        step = (hi - lo) / 400
        t = min((lo + k * step for k in range(401)), key=cost)     # coarse sweep, then golden-section refine
        a, b = max(lo, t - step), min(hi, t + step)
        for _ in range(60):
            m1, m2 = a + (b - a) * 0.382, a + (b - a) * 0.618
            if cost(m1) < cost(m2): b = m2
            else: a = m1
        t = (a + b) / 2
        return t, diagnose(t)

    def _corner_order_error(self):
        """None, or the message to show when the taught corner cycle is not a simple quadrilateral in the arm frame.

        Marking the bottom two corners the wrong way round is easy to do and impossible to see: the homography
        still solves, but it maps the surface to a self-crossing quad, so most of it lands somewhere the arm
        cannot go and the reachability map goes red for no visible reason.
        """
        if not self.quad or len(self.quad) != 4: return None
        names = [q[0] for q in self.quad]
        quad = [self.tip_xyz(q[3])[:2] for q in self.quad]

        def simple(q):
            turns = [(q[(i + 1) % 4][0] - q[i][0]) * (q[(i + 2) % 4][1] - q[(i + 1) % 4][1]) -
                     (q[(i + 1) % 4][1] - q[i][1]) * (q[(i + 2) % 4][0] - q[(i + 1) % 4][0]) for i in range(4)]
            return all(t > 0 for t in turns) or all(t < 0 for t in turns)

        if simple(quad): return None
        layout = ", ".join(f"{n} {'top' if v == 0 else 'bottom'}-{'left' if u == 0 else 'right'}"
                           for n, u, v, _ in self.quad)
        for i, j in ((2, 3), (1, 2), (0, 1), (0, 3)):
            swapped = list(quad); swapped[i], swapped[j] = swapped[j], swapped[i]
            if simple(swapped):
                return (f"the taught corners cross over — {names[i]} and {names[j]} look swapped. Re-mark them "
                        f"({layout}, {self.terms['orient']}).")
        return f"the taught corners do not form a simple quadrilateral — re-mark them going round ({layout})."

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
    def _lean_window(self, r, z, z0, L1, L2):
        """(theta0, delta_lo, delta_hi): how far the tool lean may sit from theta0 and still let the elbow reach.

        With the tip pinned at (r, z) the wrist sits on a circle of radius tool_len around it, so the shoulder-to-
        wrist distance is D(lean)^2 = R0^2 + tool_len^2 - 2*tool_len*R0*cos(lean - theta0) with theta0 pointing
        from the shoulder to the tip. D grows monotonically with |lean - theta0|, so the elbow's reachable
        annulus [|L1-L2|, L1+L2] maps to a plain interval of |lean - theta0| — that is the whole window. Both
        bounds are pulled in by REACH_MARGIN_M so no solution ever lands on a straight or fully folded elbow,
        where the arm has no manipulability left and sags under its own weight.
        """
        R0 = math.hypot(r, z - z0)
        t = self.tool_len
        if R0 < 1e-9 or t < 1e-9: return None
        D_min, D_max = abs(L1 - L2) + REACH_MARGIN_M, L1 + L2 - REACH_MARGIN_M
        if D_min >= D_max: return None
        if R0 + t < D_min or abs(R0 - t) > D_max: return None                    # out of reach at any lean
        theta0 = math.atan2(r, z - z0)
        delta_lo = math.acos(max(-1.0, min(1.0, (R0 * R0 + t * t - D_min * D_min) / (2 * R0 * t)))) if D_min > abs(R0 - t) else 0.0
        delta_hi = math.acos(max(-1.0, min(1.0, (R0 * R0 + t * t - D_max * D_max) / (2 * R0 * t)))) if D_max < R0 + t else math.pi
        return theta0, delta_lo, delta_hi

    def _lean_candidates(self, window, phi, max_dev_deg=None):
        """Feasible tool leans, closest to the taught pitch first.

        Holding one fixed lean everywhere pins the wrist to a single point per tip position, which shrinks the
        workspace for no reason; letting it float freely is worse, because a glued-in brush tilts the bristles
        and a tilted gripper meets the piece differently. So: stay at the taught pitch wherever it works, and
        otherwise give the caller the nearby alternatives in order of preference, capped at ``max_dev_deg`` —
        it is the caller that knows whether a candidate clears the joint limits.
        """
        theta0, lo, hi = window
        cap = math.radians(self.lean_max_dev_deg if max_dev_deg is None else max_dev_deg)
        diff = _wrap(phi - theta0)
        sign = 1.0 if diff >= 0 else -1.0
        nearest = theta0 + sign * min(max(abs(diff), lo), hi)                    # closest feasible lean to the taught pitch
        if abs(_wrap(nearest - phi)) > cap: return []                            # ...and it is already too far
        out, step = [nearest], math.radians(LEAN_STEP_DEG)
        for k in range(1, int(cap / step) + 1):
            for lean in (phi + k * step, phi - k * step):
                if lo - 1e-12 <= abs(_wrap(lean - theta0)) <= hi + 1e-12: out.append(lean)
        return out

    def _pose_at_lean(self, r, z, pan, lean):
        """Joint pose putting the tool tip at arm-frame (r, z) with the tool at absolute pitch ``lean``."""
        c = self.floor._c; o1, o2, o3, z0, s1, s2, s3 = self.floor.params
        L1, L2 = c["L1"], c["L2"]
        px = r - self.tool_len * math.sin(lean); pz = z - self.tool_len * math.cos(lean) - z0
        D = math.hypot(px, pz)
        if D < 1e-9: return None
        D = max(abs(L1 - L2), min(L1 + L2, D))                                   # float noise only: _lean_window kept D inside
        a2 = self.elbow_sign * math.acos(max(-1.0, min(1.0, (D * D - L1 * L1 - L2 * L2) / (2 * L1 * L2))))
        beta = math.acos(max(-1.0, min(1.0, (L1 * L1 + D * D - L2 * L2) / (2 * L1 * D))))
        a1 = math.atan2(px, pz) - (beta if a2 > 0 else -beta)
        a3 = lean - a1 - a2
        a3 = a3 - 2 * math.pi * round((a3 - self.a3_ref) / (2 * math.pi))         # nearest wrap to the taught wrist angle
        return {"shoulder_pan": pan, "shoulder_lift": o1 + math.degrees(a1) / s1,
                "elbow_flex": o2 + math.degrees(a2) / s2, "wrist_flex": o3 + math.degrees(a3) / s3}

    def _within_limits(self, pose, allow=None):
        for j, val in pose.items():
            lo, hi = self.limits.get(j, (-1e9, 1e9))
            if allow and j in allow:                       # demonstrated by hand at this point: no further
                lo, hi = min(lo, allow[j][0]), max(hi, allow[j][1])
            if not lo <= val <= hi: return False
        return True

    def uv_to_pose(self, u, v, z_cm=0.0, check_limits=True, lean=None, max_dev_deg=None, allow=None):
        """Joint pose putting the tool tip at surface (u, v) cm, z_cm above it, at or near the taught pitch.

        ``lean`` overrides the preferred pitch (radians from vertical-down, the chain's convention); the
        search still walks outwards from it. Returns None when nothing inside the joint limits reaches.

        ``allow`` is ``{joint: [lo, hi]}`` this one point may use instead of the soft limit — the range
        the operator has actually driven the arm through at this spot, and nowhere else.
        """
        if not self.ok: return None
        x, y = self._apply_h(self.Hinv, u, v)
        r, z = math.hypot(x, y), self.z_plane + z_cm / 100.0
        pan = math.degrees(math.atan2(x, y))
        window = self._lean_window(r, z, self.floor.params[3], self.floor._c["L1"], self.floor._c["L2"])
        if window is None: return None
        phi = self.pitch_at(u, v) if lean is None else float(lean)
        for cand in self._lean_candidates(window, phi, max_dev_deg):
            pose = self._pose_at_lean(r, z, pan, cand)
            if pose is None: continue
            if check_limits and self.limits and not self._within_limits(pose, allow): continue
            return {j: round(val, 3) for j, val in pose.items()}
        return None

    def lean_at(self, u, v, z_cm=0.0, **kw):
        """The lean (degrees from vertical-down) the IK would actually use at (u, v, z), or None."""
        pose = self.uv_to_pose(u, v, z_cm, **kw)
        if pose is None: return None
        a1, a2, a3 = self._angles(pose)
        return round(math.degrees(abs(_wrap(a1 + a2 + a3 - math.pi))), 1)

    def reachability(self, step_cm=1.0, z_cm=0.0):
        """Grid over the surface: 1 where the tip can be placed, 0 where not. Returns (cols, rows, list of rows)."""
        w, h = self.w_cm, self.h_cm
        cols, rows = max(1, int(round(w / step_cm))), max(1, int(round(h / step_cm)))
        grid = [[1 if self.uv_to_pose((i + 0.5) * w / cols, (k + 0.5) * h / rows, z_cm) else 0 for i in range(cols)] for k in range(rows)]
        return cols, rows, grid

    def summary(self):
        d = {"ok": self.ok, "error": self.error, **self.report}
        if self.ok:
            cols, rows, grid = self.reachability(1.0)
            d["reachable_pct"] = round(100 * sum(map(sum, grid)) / (cols * rows), 1)
        return d
