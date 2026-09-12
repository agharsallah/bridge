#!/usr/bin/env python
"""SO-101 follower: safe control bridge v2 — live dashboard + autonomous pick-and-place.

Run from this directory in a macOS Terminal:
    uv run python bridge.py
Then open  http://localhost:8765  in a browser: live video of both cameras (brick detection drawn in),
joint state, and buttons: STOP / RESUME / HOLD / AUTO PICK / ABORT / RELEASE.

Control paths (all go through the same limits, step cap, speed cap and guards):
  * JSON files dropped in ./cmd/   (as before)
  * the dashboard buttons
  * the AUTO PICK routine (thread) — replays today's proven joint waypoints while the wrist camera
    servos the pan so the yellow brick sits between the jaws, stops the descent when the brick's apparent
    size says the jaws are at brick height, verifies the grip, carries to the confirmed tin pose, releases,
    and returns to rest.

Safety (unchanged + tightened):
  * joints clamped to calibration range - margin (and limits.json if present); goals capped to 12 deg/step
  * rate-limited interpolation (deg/s), motor-side velocity/acceleration caps, gentle gripper torque
  * stall guard (tracking lag) and per-joint load guard -> freeze at present, routine aborts
  * ESTOP file (STOP.command / dashboard STOP) -> freeze; RESUME clears
  * torque is never interrupted (no lerobot configure() on connect); Ctrl-C -> hold, torque stays on
"""
import json, os, shutil, signal, threading, time, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

from lerobot.cameras.opencv import OpenCVCameraConfig
from lerobot.robots.so_follower import SOFollower, SOFollowerRobotConfig

ROOT = Path(__file__).resolve().parent
CMD, DONE = ROOT / "cmd", ROOT / "done"
STATE, LOG, ESTOP = ROOT / "state.json", ROOT / "bridge.log", ROOT / "ESTOP"
LIMITS_FILE = ROOT / "limits.json"
REC_DIR, WP_FILE = ROOT / "recordings", ROOT / "waypoints.json"

PORT = "/dev/cu.usbmodem5A460836731"
ROBOT_ID = "my_follower"
CAMS = {"top": 0, "wrist": 1}
HTTP_PORT = 8765
LOOP_HZ = 30
VISION_EVERY = 3            # process/encode frames every N loops (~10 fps)
STATE_EVERY = 0.2
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
ARM = JOINTS[:-1]
DEFAULT_SPEED = {j: 5.0 for j in JOINTS}; DEFAULT_SPEED["gripper"] = 20.0
MAX_SPEED = 30.0
CAL_MARGIN_DEG = 3.0
MAX_STEP_DEG = 12.0
LOAD_LIMIT = {"shoulder_pan": 300, "shoulder_lift": 400, "elbow_flex": 450, "wrist_flex": 300, "wrist_roll": 300}
STALL_DEG, STALL_SEC = 4.0, 0.7
REACH_TOL = 3.0             # deg; elbow sags ~2 deg under gravity even at P=48
MOTOR_GOAL_VELOCITY, MOTOR_ACCEL = 300, 15
P_GAIN = {"elbow_flex": 48}; P_GAIN_DEFAULT = 32          # lerobot's 16 is too weak for small steps vs gravity

# ---------------------------------------------------------------- proven poses (degrees; gripper 0..100)
def P(pan=None, lift=None, elbow=None, wrist=None, roll=None, grip=None):
    d = {"shoulder_pan": pan, "shoulder_lift": lift, "elbow_flex": elbow, "wrist_flex": wrist,
         "wrist_roll": roll, "gripper": grip}
    return {k: v for k, v in d.items() if v is not None}

REST_FILE = ROOT / "rest.json"
REST_DEFAULT = P(pan=-2, lift=-42.5, elbow=80.7, wrist=37.1, roll=-7.5, grip=10)
def load_rest():
    try:
        r = json.loads(REST_FILE.read_text()) if REST_FILE.is_file() else {}
        return {**REST_DEFAULT, **{k: float(v) for k, v in r.items() if k in JOINTS}}
    except Exception as e:
        log(f"rest.json unreadable ({e}) -> default"); return dict(REST_DEFAULT)
REST = None   # set in main() after log() is available
UNFOLD = [P(lift=-32, elbow=80.7, wrist=37), P(lift=-22, elbow=78, wrist=36), P(lift=-12, elbow=74, wrist=35),
          P(lift=-3, elbow=70.5, wrist=36), P(lift=0, elbow=70.5, wrist=37.5, grip=60)]                       # READY: looking straight down
DESCENT = [P(lift=10, elbow=66, wrist=37.5), P(lift=18, elbow=61, wrist=37.5), P(lift=26, elbow=57, wrist=36),
           P(lift=34, elbow=54, wrist=32), P(lift=40.4, elbow=53.6, wrist=28), P(lift=41.2, elbow=52, wrist=24),
           P(lift=41.5, elbow=58, wrist=18), P(lift=42, elbow=64, wrist=12), P(lift=42.1, elbow=69, wrist=8)]
LIFT = [P(lift=33, elbow=65, wrist=8), P(lift=24, elbow=65, wrist=8), P(lift=13.5, elbow=69.4, wrist=8)]
TRANSIT = [P(pan=5), P(lift=13.8, elbow=62.3, wrist=5), P(lift=18.2, elbow=54.1, wrist=5),
           P(pan=6.8, lift=26.5, elbow=47.7, wrist=5)]                     # DROP pose (confirmed today)
RETREAT = [P(lift=20, elbow=56, wrist=5), P(pan=-2, grip=10), P(lift=10, elbow=66, wrist=15),
           P(lift=0, elbow=68.5, wrist=22), P(lift=-10, elbow=72, wrist=30), P(lift=-20, elbow=77, wrist=35),
           P(lift=-30, elbow=80, wrist=37), P(lift=-42, elbow=80.7, wrist=37)]
# wrist-camera servo targets (960x540 frame): brick centre x between the jaws, from READY (top) to GRASP
SERVO_X_TOP, SERVO_X_BOTTOM, SERVO_TOL = 470, 540, 30
SERVO_GAIN_DEG_PER_PX = 0.04            # pan+  -> scene shifts left in the wrist image
GRASP_SIZE_PX = 360                     # brick long side in px when the jaws are at brick height
CY_MIN, CY_MAX = 170, 340               # brick centre y band along the approach direction
CY_READY = 280                          # brick centre y at READY in the manual run (reach reference)
REACH_GAIN_DEG_PER_PX = 0.07            # elbow-  (unfold) -> brick moves DOWN in the wrist image
SERVO_MAX_ITERS = 30
CY_TARGET = 300                         # brick centre y we aim for during the approach (jaw tips ~ y 350-400)
LIFT_STEP, LIFT_MAX = 5.0, 95.0         # shoulder step per iteration (down) and forward cap (limits.json allows 98)
WRIST_PER_LIFT = 0.7                    # wrist- per lift+ to keep the camera looking down (READY->GRASP data)


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def atomic_write(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp"); tmp.write_bytes(data); os.replace(tmp, path)


def find_yellow(bgr, hmin=14, hmax=40, smin=40, vmin=80, min_area=600):
    """Largest solid yellow blob -> dict(cx, cy, w, h, long) or None. Tin label (orange, ring-like) is rejected."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (hmin, smin, vmin), (hmax, 255, 255))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8))   # merge stud columns / shaded faces
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        a = cv2.contourArea(c)
        if a < min_area:
            continue
        x, y, w, h = cv2.boundingRect(c)
        hull = cv2.contourArea(cv2.convexHull(c)) or 1
        if a / hull < 0.7 or a / (w * h) < 0.5:
            continue
        if best is None or a > best["area"]:
            best = dict(area=int(a), x=x, y=y, w=w, h=h, cx=x + w // 2, cy=y + h // 2, long=max(w, h))
    return best


# ---------------------------------------------------------------- floor model (planar 3-link kinematics, fitted to
# poses where the gripper tips touch the table). Height is independent of pan. Link lengths from the SO-101 design;
# the fitted unknowns are the joint zero-offsets, the axis directions and the shoulder height.
FLOOR_FILE = ROOT / "floor_points.json"
FLOOR_CFG = ROOT / "floor_config.json"     # optional: {"z0": shoulder-axis height above table (m), "L1","L2","L3": link lengths (m)}
L1, L2, L3 = 0.1159, 0.1350, 0.110          # m: shoulder->elbow, elbow->wrist, wrist axis -> fingertip (defaults)
FLOOR_MARGIN = 0.008                       # m: never command the tips below this height
FLOOR_FREEZE = 0.0                         # m: freeze if the predicted height ever drops below this while moving
GRASP_Z = 0.010                            # m: tip height for grasping a Duplo brick (its top is ~19 mm)
SEED_CONTACTS = [                          # contacts observed today (lift, elbow, wrist)
    {"shoulder_lift": 41.3, "elbow_flex": 76.3, "wrist_flex": 0.0},
    {"shoulder_lift": 66.7, "elbow_flex": 53.6, "wrist_flex": -5.3},
]


class FloorModel:
    def __init__(self):
        self.params = None; self.rms = None; self.n = 0
        self.load()

    def cfg(self):
        try: return json.loads(FLOOR_CFG.read_text()) if FLOOR_CFG.is_file() else {}
        except Exception as e: log(f"floor_config.json unreadable: {e}"); return {}

    def height(self, q, p):
        o1, o2, o3, z0, s1, s2, s3 = p
        c = self._c
        a1 = s1 * np.radians(q[..., 0] - o1); a2 = s2 * np.radians(q[..., 1] - o2); a3 = s3 * np.radians(q[..., 2] - o3)
        return z0 + c["L1"] * np.cos(a1) + c["L2"] * np.cos(a1 + a2) + c["L3"] * np.cos(a1 + a2 + a3)

    def points(self):
        pts = list(SEED_CONTACTS)
        if FLOOR_FILE.is_file():
            try: pts += json.loads(FLOOR_FILE.read_text())
            except Exception as e: log(f"floor_points.json unreadable: {e}")
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


class Controller:
    """Owns the arm. Only the main loop touches the bus; other threads use request_*() and snapshot()."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pending = []            # command dicts from files / http / routine
        self.jpeg = {}               # latest encoded frames for the dashboard
        self.blob = {}               # latest detections per camera
        self.state = {}
        self.routine_thread = None
        self.routine_abort = threading.Event()
        self.routine_status = "idle"
        self.rec_file, self.rec_t0, self.rec_name = None, 0.0, None
        self.floor = FloorModel()

    # ------------------------------------------------------------- recording / waypoints (teach by demonstration)
    def rec_start(self):
        with self.lock:
            if self.rec_file: return
            REC_DIR.mkdir(exist_ok=True)
            name = REC_DIR / f"rec_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
            self.rec_file = open(name, "a"); self.rec_t0 = time.time(); self.rec_name = name.name
        log(f"REC start -> {self.rec_name}")

    def rec_stop(self):
        with self.lock:
            f, self.rec_file = self.rec_file, None
        if f: f.close(); log(f"REC stop -> {self.rec_name}")

    def rec_write(self, row: dict):
        with self.lock:
            f = self.rec_file
            if not f: return
            row = {"t": round(time.time() - self.rec_t0, 3), **row}
            f.write(json.dumps(row) + "\n"); f.flush()

    def wp_load(self):
        try: return json.loads(WP_FILE.read_text()) if WP_FILE.is_file() else {}
        except Exception: return {}

    def wp_save(self, name):
        st, _ = self.snapshot(); pose = st.get("present")
        if not pose or not name: return
        wps = self.wp_load(); wps[name] = {k: round(v, 2) for k, v in pose.items()}
        WP_FILE.write_text(json.dumps(wps, indent=1)); log(f"waypoint saved: {name} = {wps[name]}")

    def rest_save(self):
        st, _ = self.snapshot(); pose = st.get("present")
        if not pose: return
        REST_FILE.write_text(json.dumps({k: round(v, 2) for k, v in pose.items()}, indent=1))
        global REST; REST = None   # set in main() after log() is available; log(f"REST saved: {REST}")

    def floor_mark(self, height_cm=0.0):
        st, _ = self.snapshot(); pose = st.get("present")
        if pose: self.floor.add_point(pose, height_cm / 100.0)

    def wp_delete(self, name):
        wps = self.wp_load(); wps.pop(name, None); WP_FILE.write_text(json.dumps(wps, indent=1)); log(f"waypoint deleted: {name}")

    def wp_goto(self, name):
        pose = self.wp_load().get(name)
        if not pose: log(f"no waypoint {name}"); return
        if self.routine_thread and self.routine_thread.is_alive():
            log("busy: a routine is running (ABORT first)"); return
        self.routine_abort.clear(); self.routine_status = f"goto {name}"
        def run():
            ok = self._goto_far(pose, 4.0)
            self.routine_status = f"goto {name}: {'done' if ok else 'interrupted'}"; log(self.routine_status)
        self.routine_thread = threading.Thread(target=run, daemon=True); self.routine_thread.start()

    def go_rest(self):
        """Return to REST from anywhere: first up to the mid pose (gripper closed), then the proven fold-down path."""
        if self.routine_thread and self.routine_thread.is_alive():
            log("busy: a routine is running (ABORT first)"); return
        self.routine_abort.clear(); self.routine_status = "going to rest"
        def run():
            rest = load_rest()
            ok = self._goto_far(P(pan=rest["shoulder_pan"], lift=0, elbow=68.5, wrist=22, grip=10), 4.0)
            for wp in RETREAT[4:-1]:
                if not ok: break
                ok = self._goto(wp, 5.0)
            if ok: ok = self._goto_far(rest, 4.0)
            self.routine_status = "rest: " + ("done" if ok else "interrupted"); log(self.routine_status)
        self.routine_thread = threading.Thread(target=run, daemon=True); self.routine_thread.start()

    def request(self, cmd: dict):
        with self.lock:
            self.pending.append(cmd)

    def snapshot(self):
        with self.lock:
            return dict(self.state), dict(self.blob)

    # ------------------------------------------------------------- autonomous routine (runs in a thread)
    def start_auto(self):
        if self.routine_thread and self.routine_thread.is_alive():
            log("AUTO already running"); return
        self.routine_abort.clear()
        self.routine_thread = threading.Thread(target=self._auto_pick, daemon=True); self.routine_thread.start()

    def abort_auto(self, why="abort requested"):
        if self.routine_thread and self.routine_thread.is_alive():
            self.routine_abort.set(); log(f"AUTO abort: {why}")

    def _wait(self, timeout=15.0, settle=0.3):
        """Wait until the arm reports reached (or hold) — returns False on stall/estop/abort/timeout."""
        t0 = time.time(); reached_since = None
        while time.time() - t0 < timeout:
            if self.routine_abort.is_set(): return False
            st, _ = self.snapshot()
            if st.get("estop") or st.get("mode") == "stalled": return False
            if st.get("mode") in ("reached", "hold"):
                reached_since = reached_since or time.time()
                if time.time() - reached_since > settle: return True
            else:
                reached_since = None
            time.sleep(0.05)
        log("AUTO: timeout waiting for move"); return False

    def _goto(self, pose, speed=4.0, timeout=15.0):
        self.request({"action": "goal", "goal": pose, "speed": speed, "src": "auto"})
        time.sleep(0.15)
        return self._wait(timeout)

    def _goto_far(self, pose, speed=4.0, step=10.0):
        """Reach a pose from anywhere, splitting into <=step deg moves (respects the step cap)."""
        for _ in range(20):
            st, _ = self.snapshot(); pr = st["present"]
            todo = {j: v for j, v in pose.items() if j != "gripper" and abs(v - pr[j]) > 2.5}
            if not todo:
                if "gripper" in pose and not self._goto({"gripper": pose["gripper"]}, speed): return False
                return True
            part = {j: pr[j] + max(-step, min(step, v - pr[j])) for j, v in todo.items()}
            if not self._goto(part, speed): return False
        return False

    def _gripper(self, value, speed=15.0):
        """Close/open; returns the settled gripper position (it stalls on an object)."""
        self.request({"action": "goal", "goal": {"gripper": value}, "speed": speed, "src": "auto"})
        last, stable = None, 0
        for _ in range(120):
            time.sleep(0.1)
            if self.routine_abort.is_set(): return None
            st, _ = self.snapshot(); g = st.get("present", {}).get("gripper")
            if g is None: continue
            if last is not None and abs(g - last) < 0.3:
                stable += 1
                if stable >= 8: return g
            else:
                stable = 0
            last = g
        return last

    def _see(self, cam="wrist", timeout=1.5):
        """Latest detection for a camera, waiting up to `timeout` s for one (detector runs at ~10 fps)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            _, blob = self.snapshot(); b = blob.get(cam)
            if b: return b
            if self.routine_abort.is_set(): return None
            time.sleep(0.1)
        return None

    def _servo_pan(self, target_x, tries=3):
        for _ in range(tries):
            st, _ = self.snapshot(); b = self._see("wrist")
            if not b:
                return None
            err = b["cx"] - target_x
            if abs(err) <= SERVO_TOL:
                return b
            delta = max(-4.0, min(4.0, SERVO_GAIN_DEG_PER_PX * err))
            log(f"AUTO servo: brick x={b['cx']} target={target_x} -> pan {delta:+.1f}")
            if not self._goto({"shoulder_pan": st["present"]["shoulder_pan"] + delta}, speed=3.0):
                return None
            time.sleep(0.4)
        return self._see("wrist")

    def _auto_pick(self):
        def fail(msg):
            log(f"AUTO FAILED: {msg}"); self.routine_status = f"failed: {msg}"
            self.request({"action": "hold", "src": "auto"})
        try:
            self.routine_status = "running"; log("AUTO: start")
            if self.floor.params is None:
                return fail("floor model not taught yet — mark >= 4 table-contact poses first")
            st, _ = self.snapshot()
            if not self._see("top", 3.0) and not self._see("wrist", 1.0):
                return fail("no yellow brick visible in either camera")
            # 1) to READY (looking down, gripper open): the proven unfold path from rest, or capped steps from anywhere
            if st["present"]["shoulder_lift"] < -35:
                for wp in UNFOLD:
                    if not self._goto(wp, 5.0): return fail("unfold interrupted")
            else:
                log("AUTO: not at rest -> moving to READY in capped steps")
                if not self._goto_far({**UNFOLD[-1], "shoulder_pan": REST["shoulder_pan"]}, 4.0): return fail("move to READY interrupted")
            time.sleep(0.6)
            # 2+3) servo down to grasp height. Height comes from the FLOOR MODEL (fingertip z), the cameras only
            #      steer: pan <- brick x, elbow <- brick y (unfold = forward). A sudden size collapse is a bad
            #      detection (washed-out colour), never "still far".
            b = self._servo_pan(SERVO_X_TOP)
            if not b: return fail("brick not seen by wrist camera at READY")
            last_size, bad = b["long"], 0; grasped_height = False
            for it in range(SERVO_MAX_ITERS):
                b = self._see("wrist", 2.0)
                if not b: return fail(f"brick lost (servo iteration {it})")
                if b["long"] < 0.6 * last_size:
                    bad += 1; log(f"AUTO: suspicious detection (size {b['long']} vs {last_size}) — retrying")
                    if bad >= 4: return fail("detector unreliable (brick colour washed out?)")
                    time.sleep(0.3); continue
                bad = 0; last_size = max(last_size, b["long"])
                st, _ = self.snapshot(); pr = st["present"]; z = self.floor.z(pr)
                goal = {}
                ex = b["cx"] - SERVO_X_BOTTOM
                if abs(ex) > SERVO_TOL:
                    gain = SERVO_GAIN_DEG_PER_PX if z > 0.08 else SERVO_GAIN_DEG_PER_PX * 0.6
                    goal["shoulder_pan"] = pr["shoulder_pan"] + max(-4.0, min(4.0, gain * ex))
                ey = CY_TARGET - b["cy"]
                if abs(ey) > 60:
                    goal["elbow_flex"] = pr["elbow_flex"] + max(-6.0, min(6.0, -REACH_GAIN_DEG_PER_PX * ey))
                if z <= GRASP_Z + 0.004 and abs(ex) <= SERVO_TOL and abs(ey) <= 90:
                    grasped_height = True
                    log(f"AUTO: at grasp height (tip z {z * 100:.1f} cm), brick size {b['long']}px"); break
                if z > GRASP_Z:
                    # shoulder forward = down; take a step sized to the remaining height (~2.5 mm per degree near the table)
                    dl = min(LIFT_STEP, max(1.0, (z - GRASP_Z) / 0.0025), LIFT_MAX - pr["shoulder_lift"])
                    if dl > 0.5:
                        goal["shoulder_lift"] = pr["shoulder_lift"] + dl
                        goal["wrist_flex"] = pr["wrist_flex"] - WRIST_PER_LIFT * dl
                    elif not goal:
                        return fail("shoulder at its forward limit before reaching grasp height")
                log(f"AUTO servo {it}: brick x={b['cx']} y={b['cy']} size={b['long']} tip z={z * 100:.1f}cm -> "
                    f"{ {k: round(v, 1) for k, v in goal.items()} }")
                if not goal:
                    time.sleep(0.3); continue
                if not self._goto(goal, 4.0): return fail(f"servo move interrupted (iteration {it})")
                time.sleep(0.4)
            if not grasped_height:
                return fail("could not converge to grasp height")
            if last_size < 280:
                return fail(f"at grasp height but the brick looks too small ({last_size}px) — not between the jaws?")
            time.sleep(0.5)
            # 4) grasp and verify
            g = self._gripper(10, 15.0)
            if g is None: return fail("grasp interrupted")
            if g < 14:
                log(f"AUTO: gripper closed to {g:.1f} -> nothing grasped; opening")
                self._gripper(60, 20.0)
                return fail("missed the brick")
            log(f"AUTO: holding (gripper stalled at {g:.1f})")
            # 5) lift (relative: shoulder back = up), then transit to the confirmed drop pose in capped steps
            for _ in range(2):
                st, _ = self.snapshot(); pr = st["present"]
                up = {"shoulder_lift": pr["shoulder_lift"] - 10.0, "wrist_flex": pr["wrist_flex"] + 5.0}
                if not self._goto(up, 4.0): return fail("lift interrupted")
            if not self._goto_far({**TRANSIT[-1]}, 4.0): return fail("carry interrupted")
            time.sleep(1.0)                                                # last chance to STOP before release
            if self.routine_abort.is_set(): return fail("aborted before release")
            self._gripper(60, 25.0); time.sleep(0.8); log("AUTO: released")
            # 6) retreat to the configured rest pose
            for wp in RETREAT[:-1]:
                if not self._goto(wp, 5.0): return fail("retreat interrupted")
            if not self._goto_far(load_rest(), 4.0): return fail("final rest move interrupted")
            self.routine_status = "done"; log("AUTO: done")
        except Exception:
            log("AUTO exception:\n" + traceback.format_exc()); self.routine_status = "error"
            self.request({"action": "hold", "src": "auto"})


# --------------------------------------------------------------------------------- dashboard (HTTP)
PAGE = """<!doctype html><meta charset=utf-8><title>SO-101 bridge</title>
<style>body{font-family:system-ui;background:#111;color:#eee;margin:0;padding:12px}
.row{display:flex;gap:12px;flex-wrap:wrap}.row img{width:48%;min-width:320px;background:#000;border-radius:6px}
button{font-size:16px;padding:10px 14px;margin:3px;border:0;border-radius:8px;color:#fff;cursor:pointer;background:#444}
#stop{background:#c0392b;font-size:26px;padding:18px 34px}#resume{background:#27ae60}#hold{background:#7f8c8d}
#auto{background:#2980b9}#abort{background:#8e44ad}#release{background:#d35400}#engage{background:#16a085}
#rest{background:#2c3e50}#rec{background:#c0392b}#rec.on{background:#e74c3c;animation:b 1s infinite}@keyframes b{50%{opacity:.5}}
table{border-collapse:collapse}td{padding:4px 8px}input[type=range]{width:260px}.small{font-size:13px;color:#aaa}
pre{background:#1b1b1b;padding:8px;border-radius:6px;font-size:12px;max-height:260px;overflow:auto}
.card{background:#1b1b1b;border-radius:8px;padding:10px;margin:8px 0}</style>
<h2>SO-101 follower — live <span id=banner style="display:none;background:#c0392b;padding:6px 14px;border-radius:8px;margin-left:16px;font-size:18px">ESTOP ACTIVE — all commands ignored — press RESUME</span></h2>
<div><button id=stop onclick="c('stop')">STOP</button><button id=resume onclick="c('resume')">RESUME</button>
<button id=hold onclick="c('hold')">HOLD</button><button id=auto onclick="c('auto')">AUTO PICK</button>
<button id=abort onclick="c('abort')">ABORT</button><button id=rest onclick="c('rest')">GO TO REST</button><button onclick="if(confirm('Save the CURRENT pose as the rest position?'))c('rest_save')">Save as REST</button>
<button id=release onclick="if(confirm('Torque OFF (free-drive). Hold the arm — it will sag under gravity. Continue?'))c('release')">FREE-DRIVE (torque off)</button>
<button id=engage onclick="c('engage')">ENGAGE (torque on, hold)</button></div>
<div class=row><img src=/top.mjpg><img src=/wrist.mjpg></div>
<div class=card><b>Joint control</b> &nbsp; speed <select id=spd><option>2</option><option selected>4</option><option>8</option></select> °/s
<span class=small> — buttons are relative steps; sliders send an absolute goal on release (all limited & guarded)</span>
<table id=jt></table></div>
<div class=card><b>Teach</b> &nbsp;<button id=rec onclick="rec()">● REC</button> <span id=recname class=small></span>
&nbsp; | &nbsp; waypoint name <input id=wpn placeholder="e.g. above_brick"> <button onclick="c('wp_save&name='+encodeURIComponent(document.getElementById('wpn').value))">Save current pose</button>
<div id=wps></div>
<div style="margin-top:8px"><b>Floor model</b> &nbsp; tips height <input id=fh value=0 size=4> cm
<button onclick="if(confirm('Are the gripper tips resting at exactly that height right now?'))c('floor_mark&h='+document.getElementById('fh').value)">Mark contact</button>
<span id=floor class=small></span></div></div>
<pre id=s></pre>
<script>
const J=["shoulder_pan","shoulder_lift","elbow_flex","wrist_flex","wrist_roll","gripper"];let lim={},st={},dragging=null;
function c(a){fetch('/cmd?a='+a)}
function spd(){return document.getElementById('spd').value}
function step(j,d){c('goal&j='+j+'&v='+d+'&rel=1&speed='+spd())}
function abs(j,v){c('goal&j='+j+'&v='+v+'&rel=0&speed='+spd())}
function rec(){c(st.rec?'rec_stop':'rec_start')}
function build(){let h='';for(const j of J){h+=`<tr><td>${j}</td><td><b id="v_${j}">-</b></td>
<td><button onclick="step('${j}',-5)">−5</button><button onclick="step('${j}',-1)">−1</button>
<input type=range id="r_${j}" step=0.5 onmousedown="dragging='${j}'" ontouchstart="dragging='${j}'" onchange="abs('${j}',this.value);dragging=null">
<button onclick="step('${j}',1)">+1</button><button onclick="step('${j}',5)">+5</button></td><td class=small id="l_${j}"></td></tr>`}
document.getElementById('jt').innerHTML=h}
build();
setInterval(()=>fetch('/state').then(r=>r.json()).then(j=>{st=j;
 for(const k of J){const v=j.present[k];document.getElementById('v_'+k).textContent=v.toFixed(1);
  const r=document.getElementById('r_'+k);const L=j.limits[k];r.min=L[0];r.max=L[1];document.getElementById('l_'+k).textContent=L[0]+' … '+L[1];
  if(dragging!==k)r.value=v}
 document.getElementById('banner').style.display=j.estop?'inline':'none';
 const f=j.floor||{};document.getElementById('floor').textContent=f.fitted?`${f.points} contact points, fit rms ${f.rms_mm} mm — predicted tip height now: ${f.tip_z_cm} cm`:`${f.points} contact points (need ≥4 to fit)`;
 const b=document.getElementById('rec');b.className=j.rec?'on':'';b.textContent=j.rec?'■ STOP REC':'● REC';
 document.getElementById('recname').textContent=j.rec?('recording → recordings/'+j.rec):'';
 const view={time:j.time,mode:j.mode,torque_on:j.torque_on,auto:j.auto,estop:j.estop,tip_z_cm:(j.floor||{}).tip_z_cm,present:j.present,blob:j.blob};
 document.getElementById('s').textContent=JSON.stringify(view,null,1)}),300);
setInterval(()=>fetch('/waypoints').then(r=>r.json()).then(w=>{let h='<table>';for(const n in w){h+=`<tr><td><b>${n}</b></td><td class=small>${J.map(k=>w[n][k]).join(' / ')}</td>
 <td><button onclick="c('wp_goto&name='+encodeURIComponent('${n}'))">Go</button><button onclick="if(confirm('delete ${n}?'))c('wp_delete&name='+encodeURIComponent('${n}'))">✕</button></td></tr>`}
 document.getElementById('wps').innerHTML=h+'</table>'}),1500);
</script>"""


def make_handler(ctrl: Controller):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                body = PAGE.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            elif u.path == "/state":
                st, blob = ctrl.snapshot(); st["blob"] = blob; st["auto"] = ctrl.routine_status
                st["rec"] = ctrl.rec_name if ctrl.rec_file else None
                z = ctrl.floor.z(st["present"]) if st.get("present") else None
                st["floor"] = {"points": ctrl.floor.n, "fitted": ctrl.floor.params is not None,
                               "rms_mm": round(ctrl.floor.rms * 1000, 1) if ctrl.floor.rms else None,
                               "tip_z_cm": round(z * 100, 1) if z is not None else None}
                body = json.dumps(st).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            elif u.path in ("/top.mjpg", "/wrist.mjpg"):
                name = u.path[1:-5]
                self.send_response(200); self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f"); self.end_headers()
                try:
                    while True:
                        with ctrl.lock:
                            frame = ctrl.jpeg.get(name)
                        if frame:
                            self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame))
                            self.wfile.write(frame); self.wfile.write(b"\r\n")
                        time.sleep(0.1)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            elif u.path == "/waypoints":
                body = json.dumps(ctrl.wp_load()).encode(); self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            elif u.path == "/cmd":
                q = parse_qs(u.query); a = q.get("a", [""])[0]; g = lambda k, d=None: q.get(k, [d])[0]
                if a == "goal":
                    j, v = g("j"), g("v")
                    if j in JOINTS and v is not None:
                        ctrl.request({"action": "goal", "goal": {j: float(v)}, "relative": g("rel") == "1",
                                      "speed": float(g("speed", 4)), "src": "web"})
                elif a == "engage": ctrl.request({"action": "engage", "src": "web"})
                elif a == "rest": ctrl.go_rest()
                elif a == "rest_save": ctrl.rest_save()
                elif a == "floor_mark": ctrl.floor_mark(float(g("h", 0) or 0))
                elif a == "rec_start": ctrl.rec_start()
                elif a == "rec_stop": ctrl.rec_stop()
                elif a == "wp_save": ctrl.wp_save(g("name", "").strip()[:40])
                elif a == "wp_goto": ctrl.wp_goto(g("name", ""))
                elif a == "wp_delete": ctrl.wp_delete(g("name", ""))
                elif a == "stop": ESTOP.touch(); ctrl.abort_auto("STOP button")
                elif a == "resume": ESTOP.unlink(missing_ok=True)
                elif a == "hold": ctrl.abort_auto("HOLD button"); ctrl.request({"action": "hold", "src": "web"})
                elif a == "release": ctrl.abort_auto("RELEASE button"); ctrl.request({"action": "release", "src": "web"})
                elif a == "auto": ctrl.start_auto()
                elif a == "abort": ctrl.abort_auto("ABORT button"); ctrl.request({"action": "hold", "src": "web"})
                self.send_response(204); self.end_headers()
            else:
                self.send_response(404); self.end_headers()
    return H


# --------------------------------------------------------------------------------------- main loop
def main():
    global REST
    REST = load_rest(); log(f"rest pose: {REST}")
    ctrl = Controller()
    cams = {n: OpenCVCameraConfig(index_or_path=i, fps=30, width=1920, height=1080) for n, i in CAMS.items()}
    cfg = SOFollowerRobotConfig(port=PORT, id=ROBOT_ID, cameras=cams, disable_torque_on_disconnect=False,
                                max_relative_target=5.0)

    class QuietFollower(SOFollower):
        def configure(self) -> None:      # lerobot's configure() briefly disables torque -> skip; gains set below
            pass
    robot = QuietFollower(cfg)

    for attempt in range(1, 6):
        try:
            log(f"connecting (attempt {attempt})..."); robot.connect(calibrate=False)
            robot.bus.enable_torque(num_retry=5); break
        except ConnectionError as e:
            log(f"bus hiccup: {e}")
            try: robot.bus.disconnect(disable_torque=False)
            except Exception: pass
            for cam in robot.cameras.values():
                try:
                    if cam.is_connected: cam.disconnect()
                except Exception: pass
            if attempt == 5: raise
            time.sleep(1.0)
    if not robot.is_calibrated:
        # homing offsets / limits in the motors differ from the file (normal after an 'extend'): restore the file's
        # values first, the extension below re-applies on top.
        log("motor calibration registers differ from file -> restoring file values"); robot.bus.write_calibration(robot.calibration)

    robot.bus.write("Max_Torque_Limit", "gripper", 500, num_retry=5)
    robot.bus.write("Protection_Current", "gripper", 250, num_retry=5)
    for m in JOINTS:
        if m != "gripper":
            robot.bus.write("P_Coefficient", m, P_GAIN.get(m, P_GAIN_DEFAULT), num_retry=5)
        robot.bus.write("Acceleration", m, MOTOR_ACCEL, normalize=False, num_retry=5)
        robot.bus.write("Goal_Velocity", m, MOTOR_GOAL_VELOCITY, normalize=False, num_retry=5)

    limits = {}
    user_limits = json.loads(LIMITS_FILE.read_text()) if LIMITS_FILE.is_file() else {}
    # limits.json: {"joint": [lo, hi]} tightens;  {"extend": {"joint": [lo, hi]}} may WIDEN beyond the calibration
    # sweep (the motor's own Min/Max_Position_Limit registers are updated to match, with a 1 deg margin).
    extend = user_limits.get("extend", {})
    for m in JOINTS:
        c = robot.calibration[m]; half = (c.range_max - c.range_min) / 2 * 360 / 4095
        lo, hi = ([0.0, 100.0] if m == "gripper" else [-half + CAL_MARGIN_DEG, half - CAL_MARGIN_DEG])
        if m in user_limits and m != "extend": lo, hi = max(lo, user_limits[m][0]), min(hi, user_limits[m][1])
        if m in extend and m != "gripper":
            lo, hi = min(lo, float(extend[m][0])), max(hi, float(extend[m][1]))
            mid = (c.range_min + c.range_max) / 2
            raw_lo = int(mid + (lo - 1.0) * 4095 / 360); raw_hi = int(mid + (hi + 1.0) * 4095 / 360)
            raw_lo, raw_hi = max(0, raw_lo), min(4095, raw_hi)
            robot.bus.write("Min_Position_Limit", m, raw_lo, normalize=False, num_retry=5)
            robot.bus.write("Max_Position_Limit", m, raw_hi, normalize=False, num_retry=5)
            log(f"EXTENDED {m}: soft {lo:.1f}..{hi:.1f} deg, motor limits {raw_lo}..{raw_hi} (cal was {c.range_min}..{c.range_max})")
        limits[m] = [lo, hi]
    log(f"limits: {json.dumps({k: [round(a, 1), round(b, 1)] for k, (a, b) in limits.items()})}")

    server = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), make_handler(ctrl)); server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f"dashboard: http://localhost:{HTTP_PORT}")

    present = {k.removesuffix(".pos"): v for k, v in robot.bus.sync_read("Present_Position").items()}
    cmd, goal, speed = dict(present), dict(present), dict(DEFAULT_SPEED)
    torque_on, mode, last_cmd_name = True, "hold", None
    last_state, seq, loop_i = 0.0, 0, 0
    stall_since, last_load_warn = None, 0.0
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    log(f"ready. present={ {k: round(v, 1) for k, v in present.items()} }")

    def apply_goal(c):
        nonlocal goal, cmd, mode, torque_on
        if not torque_on:
            robot.bus.enable_torque(num_retry=5); torque_on = True; cmd = dict(present)
        for j, v in c.get("goal", {}).items():
            if j not in JOINTS: log(f"unknown joint {j}"); continue
            target = present[j] + float(v) if c.get("relative") else float(v)
            if j != "gripper" and abs(target - present[j]) > MAX_STEP_DEG:
                target = present[j] + MAX_STEP_DEG * (1 if target > present[j] else -1)
                log(f"STEP CAP {j}: limited to {MAX_STEP_DEG} deg from present")
            lo, hi = limits[j]; clamped = min(hi, max(lo, target))
            if abs(clamped - target) > 1e-6: log(f"CLAMP {j}: {target:.1f} -> {clamped:.1f}")
            goal[j] = clamped
        # floor guard: shrink the step until the predicted fingertip height stays above the margin
        if ctrl.floor.params is not None and c.get("src") != "floor_ok":
            for frac in (1.0, 0.75, 0.5, 0.25, 0.0):
                trial = {j: present[j] + frac * (goal[j] - present[j]) for j in JOINTS}
                zt = ctrl.floor.z(trial)
                if zt is not None and zt >= FLOOR_MARGIN: break
            if frac < 1.0:
                log(f"FLOOR GUARD: goal would put the tips at {ctrl.floor.z(goal) * 100:.1f} cm -> step scaled to {frac:.2f}")
                for j in ARM: goal[j] = trial[j]
        s = c.get("speed")
        if s is not None:
            for j in JOINTS:
                v = s.get(j) if isinstance(s, dict) else s
                if v is not None: speed[j] = min(MAX_SPEED, max(0.5, float(v)))
        mode = "moving"
        ctrl.rec_write({"event": "goal", "src": c.get("src", "file"), "goal": {k: round(v, 2) for k, v in goal.items()}, "speed": s})
        log(f"goal[{c.get('src', 'file')}]: { {k: round(v, 1) for k, v in c.get('goal', {}).items()} } "
            f"rel={bool(c.get('relative'))} speed={s}")

    dt = 1.0 / LOOP_HZ
    while not stop["flag"]:
        t0 = time.perf_counter(); loop_i += 1
        try:
            obs = robot.get_observation()
            present = {j: float(obs[f"{j}.pos"]) for j in JOINTS}

            # ---- E-STOP
            if ESTOP.exists():
                if mode != "estop":
                    goal, cmd, mode = dict(present), dict(present), "estop"; ctrl.abort_auto("ESTOP file")
                    log("ESTOP -> frozen. RESUME (or delete the ESTOP file) to continue.")
            elif mode == "estop":
                mode, goal, cmd = "hold", dict(present), dict(present); log("ESTOP cleared")

            # ---- gather commands (files + dashboard + routine)
            cmds = []
            for f in sorted(CMD.glob("*.json")):
                try: cmds.append(json.loads(f.read_text()))
                except Exception as e: log(f"bad cmd {f.name}: {e}")
                shutil.move(f, DONE / f.name); last_cmd_name = f.name
            with ctrl.lock:
                cmds += ctrl.pending; ctrl.pending = []
            for c in cmds:
                act = c.get("action", "goal")
                if mode == "estop" and act not in ("diag",):
                    log(f"ignored {act} (estop)"); continue
                if act == "goal": apply_goal(c)
                elif act == "hold": goal, cmd, mode = dict(present), dict(present), "hold"; log("hold")
                elif act == "release":
                    goal, cmd = dict(present), dict(present); robot.bus.disable_torque(); torque_on = False
                    mode = "released"; log("torque RELEASED"); ctrl.rec_write({"event": "release"})
                elif act == "engage":
                    robot.bus.enable_torque(num_retry=5); torque_on = True; goal, cmd, mode = dict(present), dict(present), "hold"
                    log("torque engaged (holding current pose)"); ctrl.rec_write({"event": "engage"})
                elif act == "auto": ctrl.start_auto()
                elif act == "rest": ctrl.go_rest()
                elif act == "write":
                    allowed = {"Max_Position_Limit", "Min_Position_Limit", "P_Coefficient", "D_Coefficient",
                               "Acceleration", "Goal_Velocity", "Torque_Enable", "Lock"}
                    reg, mot, val = c.get("reg"), c.get("motor"), c.get("value")
                    if reg in allowed and mot in JOINTS:
                        robot.bus.write(reg, mot, int(val), normalize=False, num_retry=5)
                        log(f"wrote {reg}[{mot}] = {val}; readback = {robot.bus.read(reg, mot, normalize=False, num_retry=3)}")
                    else: log(f"write refused: {reg} {mot}")
                elif act == "diag":
                    rows = {}
                    for reg in c.get("regs") or ["Torque_Enable", "Present_Position", "Goal_Position", "Present_Load",
                                                 "Present_Current", "Present_Temperature", "Status"]:
                        try: rows[reg] = robot.bus.sync_read(reg, normalize=False, num_retry=3)
                        except Exception as e: rows[reg] = f"ERR {e}"
                    log("DIAG:\n" + "\n".join(f"  {k}: {v}" for k, v in rows.items()))
                else: log(f"unknown action {act}")

            # ---- load guard
            if torque_on and mode in ("moving", "reached", "hold"):
                try: loads = robot.bus.sync_read("Present_Load", normalize=False, num_retry=2)
                except Exception: loads = {}
                hot = {j: v for j, v in loads.items() if j != "gripper" and abs(v) > LOAD_LIMIT[j]}
                if hot and mode == "moving":
                    goal, cmd, mode = dict(present), dict(present), "stalled"; ctrl.abort_auto("load guard")
                    log(f"LOAD GUARD: {hot} -> frozen at present.")
                elif hot and time.time() - last_load_warn > 2.0:
                    last_load_warn = time.time(); log(f"load warning while holding: {hot}")
            # ---- floor guard (runtime): predicted tip height below the table plane -> freeze
            zp = ctrl.floor.z(present)
            if zp is not None and torque_on and mode == "moving" and zp < FLOOR_FREEZE:
                goal, cmd, mode = dict(present), dict(present), "stalled"; ctrl.abort_auto("floor guard")
                log(f"FLOOR FREEZE: predicted tip height {zp * 100:.1f} cm")
            # ---- stall guard
            if torque_on and mode == "moving":
                lag = {j: abs(cmd[j] - present[j]) for j in ARM}; worst = max(lag, key=lag.get)
                if lag[worst] > STALL_DEG:
                    stall_since = stall_since or time.time()
                    if time.time() - stall_since > STALL_SEC:
                        goal, cmd, mode = dict(present), dict(present), "stalled"; ctrl.abort_auto("stall guard")
                        log(f"STALL on {worst} (lag {lag[worst]:.1f}) -> frozen at present.")
                else: stall_since = None
            else: stall_since = None

            # ---- rate-limited interpolation (limits apply to goals, never to a hold pose)
            if torque_on and mode in ("moving", "hold"):
                moving = False
                for j in JOINTS:
                    step = speed[j] * dt; d = goal[j] - cmd[j]
                    if abs(d) > step: cmd[j] += step * (1 if d > 0 else -1); moving = True
                    else: cmd[j] = goal[j]
                robot.send_action({f"{j}.pos": cmd[j] for j in JOINTS})
                if mode == "moving" and not moving and all(abs(present[j] - goal[j]) < REACH_TOL for j in ARM):
                    mode = "reached"; log(f"reached { {k: round(v, 1) for k, v in present.items()} }")
            elif torque_on and mode in ("reached", "stalled"):
                robot.send_action({f"{j}.pos": cmd[j] for j in JOINTS})

            # ---- vision + streams
            if loop_i % VISION_EVERY == 0:
                blobs = {}
                for n in CAMS:
                    small = cv2.cvtColor(cv2.resize(obs[n], (960, 540), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2BGR)
                    b = find_yellow(small)
                    if b:
                        blobs[n] = b
                        cv2.rectangle(small, (b["x"], b["y"]), (b["x"] + b["w"], b["y"] + b["h"]), (0, 200, 255), 2)
                        cv2.putText(small, f"brick {b['cx']},{b['cy']} {b['long']}px", (b["x"], max(15, b["y"] - 6)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
                    if n == "wrist" and ctrl.routine_status == "running":     # pan-servo target guides, AUTO only
                        for x in (SERVO_X_TOP, SERVO_X_BOTTOM): cv2.line(small, (x, 0), (x, 540), (80, 80, 80), 1)
                    cv2.putText(small, f"{n}  {mode}  auto:{ctrl.routine_status}", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                (255, 255, 255), 2)
                    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if ok:
                        with ctrl.lock: ctrl.jpeg[n] = buf.tobytes()
                        if loop_i % (VISION_EVERY * 5) == 0: atomic_write(ROOT / f"{n}.jpg", buf.tobytes())
                with ctrl.lock: ctrl.blob = blobs

            if loop_i % 3 == 0:            # ~10 Hz trajectory log while recording
                ctrl.rec_write({"time": time.strftime("%H:%M:%S"), "mode": mode, "torque": torque_on,
                                "pos": {k: round(v, 2) for k, v in present.items()}})
            now = time.time()
            if now - last_state > STATE_EVERY:
                seq += 1
                st = {"seq": seq, "time": time.strftime("%H:%M:%S"), "mode": mode, "torque_on": torque_on,
                      "present": {k: round(v, 2) for k, v in present.items()},
                      "goal": {k: round(v, 2) for k, v in goal.items()}, "cmd": {k: round(v, 2) for k, v in cmd.items()},
                      "speed": speed, "limits": {k: [round(a, 1), round(b, 1)] for k, (a, b) in limits.items()},
                      "last_cmd": last_cmd_name, "estop": ESTOP.exists(),
                      "tip_z_cm": round(zp * 100, 1) if zp is not None else None}
                with ctrl.lock: ctrl.state = st
                atomic_write(STATE, json.dumps(st, indent=1).encode()); last_state = now
        except Exception:
            log("ERROR in loop:\n" + traceback.format_exc()); time.sleep(0.5)
        el = time.perf_counter() - t0
        if el < dt: time.sleep(dt - el)

    ctrl.abort_auto("shutdown")
    log("Ctrl-C: holding position, torque stays ON. Disconnecting bus/cameras.")
    try: robot.disconnect()
    except Exception as e: log(f"disconnect error: {e}")


if __name__ == "__main__":
    main()
