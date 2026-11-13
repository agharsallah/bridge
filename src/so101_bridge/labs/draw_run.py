"""Drawing lab, the moving part: the pen tip in table coordinates, the touch that measures the paper, one drawing run.

A run: get the pen above the first stroke, then for each stroke travel pen-up, lower, draw, lift. It is filmed,
the tip is traced from the arm's own readings while the pen is down, and everything is saved
(var/lab/draw_runs/) so the same drawing can be drawn again, moved or resized.
"""

import json
import math
import threading
import time

from so101_bridge.lab import motion
from so101_bridge.lab.state import tip as tip_of
from so101_bridge.labs import draw_shapes as shapes
from so101_bridge.labs.balloon import allowed
from so101_bridge.labs.draw import LAB, LIVE, dcfg, files, paper_z, plan, pose_at, select, snap, xy_of
from so101_bridge.labs.jenga import pitch_of
from so101_bridge.settings import ARM
from so101_bridge.util import log

PLANE = ("shoulder_lift", "elbow_flex", "wrist_flex")
CLEAR_Z, WRIST_UP, MAX_JUMP = 6.0, 24.0, 6.0     # a tip lower than this is lifted by the wrist first; degrees; the largest joint step between two points


def reading(ctrl):
    """(state, present arm pose) after checking the arm can be driven."""
    st, _ = ctrl.snapshot()
    if not st.get("torque_on", True): raise RuntimeError("torque is off: press Torque on first")
    if ctrl.floor.params is None: raise RuntimeError("the floor model is not fitted")
    return st, {j: float(v) for j, v in st["present"].items() if j in ARM}


class Pen:
    """The pen tip as (x, y, z) on the table. ``at`` is where it was last sent."""

    def __init__(self, lab, ctrl):
        self.lab, self.ctrl, self.at, self.last = lab, ctrl, None, None

    @property
    def c(self):
        return dcfg(self.lab)                       # read again each time: a staged calibration applies at once

    def poses(self, pts):
        """Dense poses on straight segments from ``at`` through ``pts``. Refuses what the loop would clip or jump on."""
        c, limits, out, a, prev = self.c, self.ctrl.snapshot()[0]["limits"], [], self.at, self.last
        for b in pts:
            n = max(1, math.ceil(math.dist(a, b) / c["step_cm"]))
            for k in range(1, n + 1):
                q = [a[i] + (b[i] - a[i]) * k / n for i in range(3)]
                p = pose_at(self.ctrl, c, self.ctrl.tool, *q)
                if p is None or not allowed(self.ctrl, limits, p):
                    raise ValueError(f"the pen cannot go to x {q[0]:.1f}, y {q[1]:.1f}, height {q[2]:.1f} cm")
                if prev and max(abs(p[j] - prev[j]) for j in PLANE) > MAX_JUMP:
                    raise ValueError(f"too close to the folded arm at x {q[0]:.1f}, y {q[1]:.1f} cm: the joints would have to jump")
                out.append(p); prev = p
            a = b
        return out

    def go(self, pts, speed):
        poses = self.poses(pts)
        if not self.ctrl.path(poses, speed) or self.ctrl.routine_abort.is_set(): return False
        self.at, self.last = tuple(pts[-1]), poses[-1]
        return True

    def ready(self, x, y):
        """From wherever the arm is to ``hover_cm`` above (x, y). Low and not in drawing attitude: the wrist lifts the tip first."""
        c, ctrl = self.c, self.ctrl
        st, pose = reading(ctrl)
        goal = (x, y, paper_z(c, x, y) + c["hover_cm"])
        target = pose_at(ctrl, c, ctrl.tool, *goal)
        if target is None: raise ValueError(f"x {x:.1f}, y {y:.1f} cm is out of reach")
        at = tip_of(ctrl.floor, pose, ctrl.tool)
        px, py = xy_of(c, at)
        here = pose_at(ctrl, c, ctrl.tool, px, py, at["z_cm"])
        over = c["area"]["x"][0] - 3 <= px <= c["area"]["x"][1] + 3 and py >= c["area"]["y"][0] - 3
        if here and over and abs(pitch_of(ctrl.floor, pose) - c["pitch_deg"]) < 5.0:      # already drawing: straight up, then across
            self.at, self.last = (px, py, at["z_cm"]), here
            return self.go([(px, py, max(at["z_cm"], paper_z(c, px, py) + c["lift_cm"])), goal], c["travel_speed"])
        if at["z_cm"] < CLEAR_Z:
            up = max(st["limits"]["wrist_flex"][0] + 1.0, pose["wrist_flex"] - WRIST_UP)
            if not ctrl.goto_far({"wrist_flex": up}, 6.0): return False
        a = c["area"]; high = ((a["x"][0] + a["x"][1]) / 2, a["y"][0] + 1.0)              # joints straight to a far, low point dip into the table:
        high = (*high, paper_z(c, *high) + 2 * c["hover_cm"])                          # go to a high point near the arm first, then across in a line
        via = pose_at(ctrl, c, ctrl.tool, *high)
        if via is None: raise ValueError("the drawing area is out of reach")
        if not ctrl.goto_far({j: via[j] for j in PLANE}, 8.0) or not ctrl.goto_far({"shoulder_pan": via["shoulder_pan"]}, 8.0): return False
        self.at, self.last = high, via
        return self.go([goal], c["travel_speed"])

    def touch(self, x, y):
        """Lower the tip onto the paper at (x, y) in small steps until it stops following: its reading sits a steady
        sag below what is asked, and on the paper that gap closes. Loads are logged only (too noisy to trigger on).
        -> {x, y, z (height asked for at first contact, None if nothing was felt), steps}. Ends pen-up."""
        c, ctrl, steps, z, found, hit = self.c, self.ctrl, [], self.c["paper_z_cm"] + self.c["probe_from_cm"], None, None
        if not self.go([(x, y, z)], c["travel_speed"]): return None
        while found is None:
            z -= c["probe_step_cm"]
            try: ok = self.go([(x, y, z)], c["probe_speed"])
            except ValueError: break                                   # as low as the table guard lets the tip go
            if not ok: return None
            time.sleep(0.4)
            st, pose = reading(ctrl)
            err = tip_of(ctrl.floor, pose, ctrl.tool)["z_cm"] - z
            load = {j: float((st.get("load") or {}).get(j, 0.0)) for j in PLANE}
            steps.append({"z": round(z, 2), "err": round(err, 2), "load": load})
            if len(steps) > 4:
                lag = err - sorted(s["err"] for s in steps[:-2])[(len(steps) - 2) // 2]          # against the median sag so far
                if lag <= c["touch_err_cm"]: hit = None
                elif hit is None: hit = round(z + lag, 2)
                else: found = hit
        top = (found if found is not None else z) + c["lift_cm"]
        if not self.go([(x, y, top)], c["probe_speed"]): return None
        return {"x": x, "y": y, "z": found, "steps": steps}


class Run:
    def __init__(self, lab, ctrl, p, speed=None):
        self.lab, self.ctrl, self.plan, self.pen = lab, ctrl, p, Pen(lab, ctrl)
        self.speed = max(1.0, min(20.0, float(speed or dcfg(lab)["speed"])))
        self.t0, self.down, self.done = time.time(), False, threading.Event()

    def watch(self):
        """Trace where the arm says the tip is while the pen is down (for the panel and the saved run)."""
        while not self.done.wait(0.1):
            if not self.down: continue
            st, _ = self.ctrl.snapshot()
            at = tip_of(self.ctrl.floor, st.get("present"), self.ctrl.tool)
            if at and LIVE["trace"]: LIVE["trace"][-1].append(list(xy_of(dcfg(self.lab), at)))

    def stroke(self, i, s):
        """Travel pen-up, land, draw, leave — landing and leaving *on the line itself*. The tip comes down over the
        first ``land_cm`` of the stroke while already moving along it, and a closed shape is left by running over its
        first ``land_cm`` again while rising. Whatever the pen marks before or after the nominal contact then lies on
        the line: landing beside it left a leg on every stroke, which ruined anything small."""
        c, pen = dcfg(self.lab), self.pen
        pts, d = shapes.resample(s, c["step_cm"]), [0.0]
        for a, b in zip(pts, pts[1:]): d.append(d[-1] + math.dist(a, b))
        land = min(c["land_cm"], d[-1] / 2) or 1.0
        z = lambda p, k: (p[0], p[1], paper_z(c, *p) + k)          # noqa: E731
        slope = lambda t: c["ramp_cm"] - (c["ramp_cm"] + c["press_cm"]) * min(1.0, t / land)      # noqa: E731  height on the way down
        path = [z(p, slope(t)) for p, t in zip(pts, d)]
        if len(pts) > 3 and math.dist(pts[0], pts[-1]) < 1e-6:     # closed: once more over the start, rising
            path += [z(p, slope(land - t)) for p, t in zip(pts[1:], d[1:]) if t <= land]
        LIVE.update(stroke=i, phase="travel")
        if not pen.go([z(pts[0], c["lift_cm"])], c["travel_speed"]) or self.ctrl.routine_abort.wait(c["settle_s"]): return False
        LIVE["phase"] = "draw"; LIVE["trace"].append([]); self.down = True
        try: ok = pen.go(path[:1], c["probe_speed"]) and pen.go(path[1:], self.speed)
        finally: self.down = False
        return ok and pen.go([(*path[-1][:2], paper_z(c, *path[-1][:2]) + c["lift_cm"])], c["travel_speed"])

    def play(self):
        lab, ctrl, p, c = self.lab, self.ctrl, self.plan, dcfg(self.lab)
        v, own_video = lab.cfg().get("video") or {}, ctrl.video.status() is None
        if own_video: ctrl.video.start(f"draw_{p['name']}", directory=lab.video_dir(), raw=bool(v.get("raw")))
        LIVE.update(phase="ready", stroke=None, trace=[]); result, photo = "stopped", None
        threading.Thread(target=self.watch, daemon=True, name="draw-trace").start()
        try:
            if self.pen.ready(*p["strokes"][0][0]) and all(self.stroke(i, s) for i, s in enumerate(p["strokes"])):
                a = c["area"]; mid = ((a["x"][0] + a["x"][1]) / 2, a["y"][0])           # up above the near edge, then swing aside: the camera sees the sheet
                parked = self.pen.go([(*mid, paper_z(c, *mid) + 2 * c["hover_cm"])], c["travel_speed"]) and ctrl.goto_far({"shoulder_pan": c["park_pan_deg"]}, 15.0)
                result = "drawn" if parked else "drawn, not parked"
        except (ValueError, RuntimeError) as e:
            result = f"stopped: {e}"
        finally:
            self.done.set(); LIVE["phase"] = result
            if result.startswith("drawn"):
                time.sleep(0.5); f = snap(lab, ctrl, p["name"]); photo = f.name if f else None
            video = ctrl.video.stop() if own_video else (ctrl.video.status() or {}).get("file")
            d = files(lab, "runs"); d.mkdir(parents=True, exist_ok=True)
            f = d / f"{time.strftime('%Y%m%d_%H%M%S', time.localtime(self.t0))}_{p['name']}.json"
            f.write_text(json.dumps({"file": f.name, "name": p["name"], "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.t0)),
                                     "result": result, "duration_s": round(time.time() - self.t0, 1), "video": video, "photo": photo,
                                     "place": p["place"], "strokes": p["strokes"], "speed": self.speed, "trace": LIVE["trace"],
                                     "touch": c["touch"], "tool": ctrl.tool, "events": list(ctrl.events)[-10:]}))
            lab.note("run", {"file": f.name}, {"ok": result == "drawn", "message": result})
            log(f"DRAW run {f.name}: {result} (video {video})")
        return result == "drawn"


def _start(lab, ctrl, label, fn):
    if not ctrl.tool: raise ValueError("no pen mounted: mount it first (Tool tab)")
    reading(ctrl)
    motion.run_move(ctrl, label, fn)


@LAB.action("draw", "Draw", "Draw the chosen drawing (or `name`, or a saved `run` again — optionally resized or moved). Filmed and saved as a run.",
            {"name": "string?", "run": "string?", "scale": "number?", "dx_cm": "number?", "dy_cm": "number?", "speed": "number?", "dry": "bool?"},
            confirm=lambda lab, ctrl, a: "" if a.get("dry") else "Draw now? The arm lowers the pen onto the paper and draws by itself.", moves=True)
def draw(lab, ctrl, args):
    place = {k: args.get(k) for k in ("scale", "dx_cm", "dy_cm")}
    if args.get("run"):
        f = files(lab, "runs") / str(args["run"]).replace("/", "_")
        if not f.is_file(): raise ValueError(f"no run called {args['run']!r}")
        r = json.loads(f.read_text())
        select(lab, r["name"], **{**r["place"], **{k: v for k, v in place.items() if v is not None}})
    else:
        select(lab, args.get("name"), **place)
    p = plan(lab, ctrl, ctrl.snapshot()[0]["limits"])
    if p["problem"]: raise ValueError(p["problem"])
    if args.get("dry"): return {"message": f"plan: {p['name']}, {len(p['strokes'])} strokes, {p['length_cm']:g} cm of line", "plan": p}
    _start(lab, ctrl, f"draw {p['name']}", Run(lab, ctrl, p, args.get("speed")).play)
    return {"message": f"drawing {p['name']}", "plan": p}


@LAB.action("calibrate", "Measure the paper", "Touch the paper at the corners of the drawing area (or at `points` [[x, y], ...]) to learn its "
            "height. Each touch leaves a dot. The result is staged as draw.touch and used at once.", {"points": "list?"},
            confirm="Measure the paper now? The arm lowers the pen until it touches, at each corner of the drawing area.", moves=True)
def calibrate(lab, ctrl, args):
    a = dcfg(lab)["area"]
    pts = [(float(p[0]), float(p[1])) for p in args.get("points") or [(x, y) for y in a["y"] for x in a["x"]]]

    def run():
        pen, found, probes = Pen(lab, ctrl), [], []
        LIVE.update(phase="measure", stroke=None)
        try:
            if not pen.ready(*pts[0]): return False
            for x, y in pts:
                t = pen.touch(x, y)
                if t is None: return False
                probes.append(t)
                if t["z"] is not None: found.append([x, y, t["z"]])
        finally:
            d = files(lab, "probes"); d.mkdir(parents=True, exist_ok=True)
            (d / f"{time.strftime('%Y%m%d_%H%M%S')}.json").write_text(json.dumps(probes))
            LIVE["phase"] = f"measured {len(found)}/{len(pts)}"
            if found: lab.store.stage("draw.touch", found, label="paper height")
            lab.note("calibrated", {"points": len(pts)}, {"ok": len(found) == len(pts), "message": LIVE["phase"]})
        return len(found) == len(pts)
    _start(lab, ctrl, "measure the paper", run)
    return f"touching the paper at {len(pts)} point(s)"


@LAB.action("ramp", "Height test line", "Draw one line from (x0, y0) to (x1, y1) cm while the tip comes down from z0_cm to z1_cm (heights as "
            "the arm is asked for them). Where the ink starts is the paper's height at that reach: the reliable way to measure it.",
            {"x0": "number", "y0": "number", "x1": "number", "y1": "number", "z0_cm": "number", "z1_cm": "number"},
            confirm="Draw a height test line now? The pen comes down onto the paper along that line.", moves=True)
def ramp(lab, ctrl, args):
    a, b = ((float(args[f"x{i}"]), float(args[f"y{i}"]), float(args[f"z{i}_cm"])) for i in (0, 1))

    def run():
        pen, c = Pen(lab, ctrl), dcfg(lab)
        return (pen.ready(a[0], a[1]) and pen.go([a], c["travel_speed"]) and pen.go([b], c["probe_speed"])
                and pen.go([(b[0], b[1], b[2] + c["lift_cm"] + 1.0)], c["travel_speed"]))
    _start(lab, ctrl, "height test line", run)
    return f"test line from x {a[0]:g}, y {a[1]:g} ({a[2]:g} cm up) to x {b[0]:g}, y {b[1]:g} ({b[2]:g} cm up)"


@LAB.action("pen", "Move the pen", "Put the pen tip above table point (x, y) cm — pen up by default, `down` to touch the paper, or `up_cm` "
            "above it. Without x, y: above the middle of the area.", {"x_cm": "number?", "y_cm": "number?", "down": "bool?", "up_cm": "number?"}, moves=True)
def pen_to(lab, ctrl, args):
    c = dcfg(lab); a = c["area"]
    x = float(args["x_cm"]) if args.get("x_cm") is not None else (a["x"][0] + a["x"][1]) / 2
    y = float(args["y_cm"]) if args.get("y_cm") is not None else (a["y"][0] + a["y"][1]) / 2
    up = -c["press_cm"] if args.get("down") else float(args["up_cm"]) if args.get("up_cm") is not None else c["hover_cm"]
    if not -1.0 <= up <= 15.0: raise ValueError("up_cm is the height above the paper: -1 to 15")

    def run():
        pen = Pen(lab, ctrl)
        return pen.ready(x, y) and pen.go([(x, y, paper_z(c, x, y) + up)], c["probe_speed"] if up < c["lift_cm"] else c["travel_speed"])
    _start(lab, ctrl, "move the pen", run)
    return f"pen to x {x:g}, y {y:g} cm, {up:+g} cm from the paper"
