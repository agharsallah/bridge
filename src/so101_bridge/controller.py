"""Controller: the shared state between the control loop, the dashboard and the routines.

Only the main loop (``app.control_loop``) talks to the motor bus. Every other thread — the HTTP
handler, the AUTO PICK routine, the file watcher — goes through :meth:`Controller.request` and
:meth:`Controller.snapshot`, both guarded by ``self.lock``.
"""

import json
import threading
import time
from collections import deque

from . import settings
from .floor import FloorModel
from .paths import REC_DIR, REST_FILE, WP_FILE
from .poses import RETREAT, P, load_rest
from .util import log
from .video import VideoRecorder


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
        self.video = VideoRecorder()
        # --- observability: what the routine is doing, what the guards did, whether we may start
        self.progress = self._fresh_progress()
        self.events = deque(maxlen=60)           # guard / safety events: {"t", "time", "kind", "msg"}

    # ------------------------------------------------------------- observability
    @staticmethod
    def _fresh_progress(routine=None):
        return {"routine": routine, "phase": None, "detail": "", "started": None, "finished": None,
                "history": [], "servo": None}

    def phase(self, name, detail=""):
        """Routines call this at every step boundary; the dashboard shows the sequence and timings."""
        with self.lock:
            pr = self.progress
            now = time.time()
            if pr["history"]:
                pr["history"][-1]["dt"] = round(now - pr["history"][-1]["t"], 1)
            pr["history"].append({"phase": name, "detail": detail, "t": now, "time": time.strftime("%H:%M:%S")})
            pr["phase"], pr["detail"] = name, detail
        log(f"[{pr['routine'] or 'routine'}] {name}{': ' + detail if detail else ''}")

    def servo_progress(self, **kw):
        with self.lock:
            self.progress["servo"] = kw

    def event(self, kind, msg):
        """Record a guard/safety event (also logged)."""
        with self.lock:
            self.events.append({"t": time.time(), "time": time.strftime("%H:%M:%S"), "kind": kind, "msg": msg})
        log(f"{kind}: {msg}")

    def preflight(self, state, blobs):
        """Checklist for starting the autonomous routine; each item {name, ok, detail}."""
        busy = self.routine_thread is not None and self.routine_thread.is_alive()
        mode = state.get("mode")
        checks = [
            ("floor model fitted", self.floor.params is not None,
             f"{self.floor.n} contacts, rms {self.floor.rms * 1000:.1f} mm" if self.floor.rms else f"{self.floor.n} contacts (need 4)"),
            ("brick visible (top camera)", "top" in blobs, f"x {blobs['top']['cx']} y {blobs['top']['cy']}" if "top" in blobs else "no detection"),
            ("no emergency stop", not state.get("estop"), "ESTOP file present" if state.get("estop") else "clear"),
            ("torque on", bool(state.get("torque_on", True)), "" if state.get("torque_on", True) else "press ENGAGE"),
            ("arm not frozen by a guard", mode != "stalled", "send HOLD or a goal away from the obstacle" if mode == "stalled" else ""),
            ("no routine running", not busy, self.routine_status if busy else ""),
        ]
        return [{"name": n, "ok": bool(ok), "detail": d} for n, ok, d in checks]

    # ------------------------------------------------------------- recording / waypoints (teach by demonstration)
    def rec_start(self):
        with self.lock:
            if self.rec_file: return
            REC_DIR.mkdir(parents=True, exist_ok=True)
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
        log(f"REST saved: { {k: round(v, 2) for k, v in pose.items()} }")

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
            ok = self.goto_far(pose, 4.0)
            self.routine_status = f"goto {name}: {'done' if ok else 'interrupted'}"; log(self.routine_status)
        self.routine_thread = threading.Thread(target=run, daemon=True); self.routine_thread.start()

    def go_rest(self):
        """Return to REST from anywhere: first up to the mid pose (gripper closed), then the proven fold-down path."""
        if self.routine_thread and self.routine_thread.is_alive():
            log("busy: a routine is running (ABORT first)"); return
        self.routine_abort.clear(); self.routine_status = "going to rest"
        def run():
            rest = load_rest()
            ok = self.goto_far(P(pan=rest["shoulder_pan"], lift=0, elbow=68.5, wrist=22, grip=10), 4.0)
            for wp in RETREAT[4:-1]:
                if not ok: break
                ok = self.goto(wp, 5.0)
            if ok: ok = self.goto_far(rest, 4.0)
            self.routine_status = "rest: " + ("done" if ok else "interrupted"); log(self.routine_status)
        self.routine_thread = threading.Thread(target=run, daemon=True); self.routine_thread.start()

    def request(self, cmd: dict):
        with self.lock:
            self.pending.append(cmd)

    def snapshot(self):
        with self.lock:
            return dict(self.state), dict(self.blob)

    # ------------------------------------------------------------- autonomous routine (runs in a thread)
    def start_routine(self, name):
        """Run a registered routine (see routines.py) in its own thread."""
        from .routines import get as get_routine  # local import: routines drive the controller, not the reverse
        spec = get_routine(name)
        if spec is None:
            log(f"unknown routine {name!r}"); return False
        if self.routine_thread and self.routine_thread.is_alive():
            log(f"busy: {self.routine_status} (ABORT first)"); return False
        self.routine_abort.clear()
        with self.lock:
            self.progress = self._fresh_progress(name); self.progress["started"] = time.time()
        self.routine_status = "running"
        if settings.AUTO_RECORD_ROUTINES and self.video.status() is None:
            self.video.start(name, auto=True)

        def run():
            try:
                spec.run(self)
            finally:
                with self.lock:
                    self.progress["finished"] = time.time()
                if self.video.auto:
                    self.video.stop()
        self.routine_thread = threading.Thread(target=run, daemon=True, name=f"routine-{name}")
        self.routine_thread.start()
        return True

    def start_auto(self):
        """Backwards-compatible alias: the built-in pick-and-place."""
        return self.start_routine("pick_place")

    def abort_auto(self, why="abort requested"):
        if self.routine_thread and self.routine_thread.is_alive():
            self.routine_abort.set(); log(f"AUTO abort: {why}")

    def wait_reached(self, timeout=15.0, settle=0.3):
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

    def goto(self, pose, speed=4.0, timeout=15.0):
        self.request({"action": "goal", "goal": pose, "speed": speed, "src": "auto"})
        time.sleep(0.15)
        return self.wait_reached(timeout)

    def path(self, points, speed=6.0, timeout=None):
        """Follow a dense list of poses continuously (the loop's 'path' command); wait until the end is reached."""
        if not points: return True
        self.request({"action": "path", "points": [dict(p) for p in points], "speed": speed, "src": "auto"})
        time.sleep(0.15)
        if timeout is None:
            travel = sum(max(abs(b.get(j, a.get(j, 0)) - a.get(j, 0)) for j in a) for a, b in zip(points, points[1:])) if len(points) > 1 else 12
            timeout = 10.0 + 2.0 * (travel + 12) / max(speed, 0.5)
        return self.wait_reached(timeout)

    def goto_far(self, pose, speed=4.0, step=10.0):
        """Reach a pose from anywhere, splitting into <=step deg moves (respects the step cap)."""
        for _ in range(20):
            st, _ = self.snapshot(); pr = st["present"]
            todo = {j: v for j, v in pose.items() if j != "gripper" and abs(v - pr[j]) > 2.5}
            if not todo:
                if "gripper" in pose and not self.goto({"gripper": pose["gripper"]}, speed): return False
                return True
            part = {j: pr[j] + max(-step, min(step, v - pr[j])) for j, v in todo.items()}
            if not self.goto(part, speed): return False
        return False

    def gripper_to(self, value, speed=15.0):
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

    def see(self, cam="wrist", timeout=1.5):
        """Latest detection for a camera, waiting up to `timeout` s for one (detector runs at ~10 fps)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            _, blob = self.snapshot(); b = blob.get(cam)
            if b: return b
            if self.routine_abort.is_set(): return None
            time.sleep(0.1)
        return None
