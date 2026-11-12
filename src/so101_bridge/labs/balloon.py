"""Balloon lab: find the orange balloon in the wrist camera, aim the needle at it, thrust until it pops.

Built on the lab template (docs/lab.md). A pop is one *run*: ready pose -> aim (pan, by camera) -> thrust
(tool tip on a straight horizontal line, stopped the moment the orange disappears) -> retract. Every run is
filmed and saved with its plan, every path it sent and the telemetry (var/lab/balloon_runs/), so it can be
replayed from the dashboard, optionally shifted. Every move is a request to the control loop: never
src="floor_ok", never a motor register.
"""

import json
import math
import time

import cv2
import numpy as np

from so101_bridge import tool as tool_mod
from so101_bridge.lab import BUILTIN, Lab, motion, register
from so101_bridge.lab.cameras import FRAMES
from so101_bridge.lab.state import tip as tip_of
from so101_bridge.settings import ARM
from so101_bridge.shared.motion import densify_joint
from so101_bridge.util import log

DEFAULTS = {
    "dwell_s": 1.0,
    "z_cm": 13.5, "pitch_deg": 90.0,            # the thrust line: tool-tip height, gripper axis from vertical (90 = level)
    "r0_cm": 18.0, "r_max_cm": 35.0,            # tool-tip radius at the ready pose / the deepest the thrust may go
    "thrust_speed": 15.0, "move_speed": 10.0,   # deg/s
    "past_cm": 1.0, "lead_cm": 4.0,             # a replay starts lead_cm before where the balloon popped and goes past_cm beyond
    "aim_x": 0.45, "aim_tol": 0.06,             # where the needle points in the wrist image (fraction of its width)
    "deg_per_width": 55.0,                      # first guess of pan degrees per image width; measured while aiming
    "hsv_lo": [2, 70, 100], "hsv_hi": [22, 255, 255],       # loose on saturation: the camera washes the balloon out up close
    "min_orange": 0.02, "pop_drop": 0.4,        # popped = the orange blob falls below pop_drop x its peak
}
STEP_CM, MAX_SPEED, AIM_STEP_DEG, MAX_JOINT_STEP = 0.5, 25.0, 8.0, 8.0
LAST = {"aim": None}
_SUMMARIES = {}

LAB = register(Lab(
    "balloon", "Balloon lab", actions=BUILTIN, ui="panels.js", description="aim the needle at the orange balloon and pop it; record and replay runs",
    defaults={"balloon": DEFAULTS},        # config/balloon.json starts from this; edit it via staged changes
))


def bcfg(lab):
    return {**DEFAULTS, **(lab.cfg().get("balloon") or {})}


def runs_dir(lab):
    return lab.journal_file.parent / f"{lab.name}_runs"


# ------------------------------------------------------------------ vision
def orange(ctrl, c, cam="wrist"):
    """The largest orange blob in a camera: {"frac" of the image, "x", "y" centre as fractions, "t"} or None (no frame)."""
    frame, t = FRAMES.raw(ctrl, cam)
    if frame is None: return None
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (320, max(1, round(h * 320 / w))), interpolation=cv2.INTER_AREA)
    m = cv2.inRange(cv2.cvtColor(small, cv2.COLOR_RGB2HSV), tuple(c["hsv_lo"]), tuple(c["hsv_hi"]))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _, stats, cen = cv2.connectedComponentsWithStats(m)
    if n < 2: return {"frac": 0.0, "x": None, "y": None, "t": t}
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return {"frac": round(float(stats[i, cv2.CC_STAT_AREA]) / m.size, 4), "x": round(float(cen[i][0]) / m.shape[1], 3),
            "y": round(float(cen[i][1]) / m.shape[0], 3), "t": t}


# ------------------------------------------------------------------ geometry (the floor model's planar chain)
def ik(floor, tool, r_cm, z_cm, pitch_deg):
    """{lift, elbow, wrist} putting the tool tip at (r, z) with the gripper axis ``pitch_deg`` from vertical; None if out of reach."""
    if floor.params is None: return None
    o1, o2, o3, z0, s1, s2, s3 = (float(x) for x in floor.params)
    l1, l2 = floor._c["L1"], floor._c["L2"]
    l3 = floor._c["L3"] + (tool["out_cm"] / 100.0 if tool else 0.0)
    ph = math.radians(pitch_deg)
    wr, wz = r_cm / 100.0 - l3 * math.sin(ph), z_cm / 100.0 - z0 - l3 * math.cos(ph)
    k = (wr * wr + wz * wz - l1 * l1 - l2 * l2) / (2 * l1 * l2)
    if abs(k) > 1: return None
    a2 = math.acos(k)                                           # the elbow only folds this way
    a1 = math.atan2(wr, wz) - math.atan2(l2 * math.sin(a2), l1 + l2 * math.cos(a2))
    a1 = (a1 + math.pi) % (2 * math.pi) - math.pi
    a3 = (ph - a1 - a2 + math.pi) % (2 * math.pi) - math.pi
    return {"shoulder_lift": round(o1 + s1 * math.degrees(a1), 2), "elbow_flex": round(o2 + s2 * math.degrees(a2), 2),
            "wrist_flex": round(o3 + s3 * math.degrees(a3), 2)}


def allowed(ctrl, limits, pose):
    """Inside the soft limits and clear of the table — what the loop would accept unchanged."""
    return (all(limits[j][0] <= v <= limits[j][1] for j, v in pose.items() if j in limits)
            and tool_mod.table_ok(ctrl.floor, pose, ctrl.tool))


def line(ctrl, limits, plan, pan):
    """The thrust as dense poses: tool tip from r0 to r1 at height z. Stops early where the arm cannot follow."""
    pts, r = [], plan["r0_cm"]
    while r <= plan["r1_cm"] + 1e-6:
        p = ik(ctrl.floor, ctrl.tool, r, plan["z_cm"], plan["pitch_deg"])
        if p is None or not allowed(ctrl, limits, p): break
        pts.append({"shoulder_pan": round(pan, 2), **p}); r += STEP_CM
    return pts


def make_plan(lab, ctrl, args, base=None):
    """The numbers of one run. ``base`` is a saved run to repeat (no aiming: its pan, its depth), shifted by d*."""
    c, f = bcfg(lab), lambda k, d=0.0: float(args.get(k) or d)
    if base:
        b, pop = base["plan"], base.get("pop") or {}
        r1 = round((pop["r_cm"] + c["past_cm"] if pop else b["r1_cm"]) + f("dr_cm"), 1)
        plan = {**b, "aim": False, "pan": round(float(base["pan"]) + f("dpan"), 2), "z_cm": b["z_cm"] + f("dz_cm"), "r1_cm": r1,
                "r0_cm": max(b["r0_cm"], r1 - c["past_cm"] - c["lead_cm"]) if pop else b["r0_cm"], "of": base["file"]}
    else:
        plan = {"aim": args.get("aim", True) is not False, "pan": None, "z_cm": c["z_cm"], "pitch_deg": c["pitch_deg"],
                "r0_cm": c["r0_cm"], "r1_cm": c["r_max_cm"], "move_speed": c["move_speed"]}
    plan["speed"] = max(1.0, min(MAX_SPEED, f("speed", plan.get("speed") or c["thrust_speed"])))
    st, _ = ctrl.snapshot()
    pan = plan["pan"] if plan["pan"] is not None else st["present"]["shoulder_pan"]
    pts = line(ctrl, st["limits"], plan, pan)
    if len(pts) < 2: raise ValueError("the thrust line is out of reach (check z_cm / r0_cm, the floor model and the tool)")
    if max(abs(q[j] - p[j]) for p, q in zip(pts, pts[1:]) for j in p) > MAX_JOINT_STEP:
        raise ValueError("the thrust line starts too close to the folded arm (raise r0_cm)")
    lo, hi = st["limits"]["shoulder_pan"]
    if not lo <= pan <= hi: raise ValueError(f"pan {pan:.1f} is outside the soft limits")
    plan["r1_cm"] = round(plan["r0_cm"] + STEP_CM * (len(pts) - 1), 1)       # as deep as the arm can really go
    return plan


# ------------------------------------------------------------------ one recorded run
class Run:
    def __init__(self, lab, ctrl, name, plan):
        self.lab, self.ctrl, self.c, self.plan = lab, ctrl, bcfg(lab), plan
        self.t0, self.stamp = time.time(), time.strftime("%Y%m%d_%H%M%S")
        self.name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(name or "pop"))[:30]
        self.steps, self.samples, self.peak, self.low_t = [], [], 0.0, []

    def now(self):
        return round(time.time() - self.t0, 3)

    def present(self):
        return {j: round(v, 2) for j, v in self.ctrl.snapshot()[0]["present"].items() if j in ARM}

    def sample(self):
        st, _ = self.ctrl.snapshot(); o = orange(self.ctrl, self.c)
        self.samples.append({"t": self.now(), "mode": st.get("mode"), "orange": o and o["frac"],
                             "pose": {j: round(v, 2) for j, v in st["present"].items() if j in ARM}, "load": st.get("load") or {}})
        return st, o

    def popped(self, o):
        """True once two different frames show the orange blob collapsed from its peak."""
        if not o: return False
        self.peak = max(self.peak, o["frac"])
        if self.peak < self.c["min_orange"] or o["frac"] >= self.c["pop_drop"] * self.peak: self.low_t = []; return False
        if o["t"] not in self.low_t: self.low_t.append(o["t"])
        return len(self.low_t) >= 2

    def follow(self, label, points, speed, watch=False):
        """Send one path and sample until it ends: "reached", "popped" (watch) or "stopped" (abort, ESTOP, guard, timeout)."""
        if not points: return "reached"
        self.steps.append({"label": label, "t": self.now(), "speed": speed, "points": points})
        self.ctrl.request({"action": "path", "points": points, "speed": speed, "src": "auto"})
        self.ctrl._wait_moving()
        travel = sum(max(abs(b[j] - a[j]) for j in b if j in a) for a, b in zip([self.present()] + points, points))
        deadline, still = time.time() + 8.0 + 2.0 * travel / speed, None
        while time.time() < deadline:
            if self.ctrl.routine_abort.is_set(): return "stopped"
            st, o = self.sample()
            if st.get("estop") or st.get("mode") == "stalled": return "stopped"
            if watch and self.popped(o):
                self.ctrl.request({"action": "hold"}); return "popped"
            if st.get("mode") in ("reached", "hold"):
                still = still or time.time()
                if time.time() - still > 0.15: return "reached"
            else: still = None
            time.sleep(0.03)
        return "stopped"

    def aim(self):
        """Pan until the balloon sits where the needle points in the wrist image. The aimed pan, or None (no balloon / stopped)."""
        c, k, lo_hi = self.c, self.c["deg_per_width"], self.ctrl.snapshot()[0]["limits"]["shoulder_pan"]
        for i in range(6):
            time.sleep(0.3); o = orange(self.ctrl, c); pan = self.present()["shoulder_pan"]
            if not o or o["x"] is None or o["frac"] < c["min_orange"]: return None
            err = o["x"] - c["aim_x"]
            LAST["aim"] = {"pan": pan, "x": o["x"], "frac": o["frac"], "deg_per_width": round(k, 1), "steps": i}
            if abs(err) <= c["aim_tol"]: return pan
            goal = max(lo_hi[0], min(lo_hi[1], pan + max(-AIM_STEP_DEG, min(AIM_STEP_DEG, err * k))))
            if self.follow(f"aim {i + 1}", [{"shoulder_pan": round(goal, 2)}], self.plan.get("move_speed", c["move_speed"])) != "reached": return None
            time.sleep(0.7)                                             # "reached" is within 3 deg: let the pan finish
            o2 = orange(self.ctrl, c); moved = self.present()["shoulder_pan"] - pan
            if o2 and o2["x"] is not None and abs(moved) > 1.0 and abs(o2["x"] - o["x"]) > 0.005:
                k = max(30.0, min(200.0, abs(moved / (o2["x"] - o["x"])))) * (-1 if moved * (o2["x"] - o["x"]) > 0 else 1)
        return None

    def execute(self):
        ctrl, plan, v = self.ctrl, self.plan, self.lab.cfg().get("video") or {}
        own_video = ctrl.video.status() is None
        if own_video: ctrl.video.start(f"pop_{self.name}", directory=self.lab.video_dir(), raw=bool(v.get("raw")))
        result, pop, pan = "stopped", None, plan["pan"]
        try:
            st, _ = ctrl.snapshot(); start = self.present()
            if not plan["aim"] and pan is None: pan = start["shoulder_pan"]
            first = line(ctrl, st["limits"], plan, pan if pan is not None else start["shoulder_pan"])
            if self.follow("ready", densify_joint(start, first[0]), plan["move_speed"]) == "reached":
                if plan["aim"]: pan = self.aim()
                if pan is None: result = "no balloon seen"
                else:
                    pts = line(ctrl, st["limits"], plan, pan)
                    self.sample(); self.popped(orange(ctrl, self.c))                    # the peak before the thrust
                    result = self.follow("thrust", pts[1:], plan["speed"], watch=True)
                    if result != "stopped":
                        at = tip_of(ctrl.floor, self.present(), ctrl.tool)
                        if result == "popped": pop = {"t": self.now(), **at}
                        else: result = f"not popped (reached {at['r_cm']} cm)"
                        if self.follow("retract", densify_joint(self.present(), pts[0]), plan["move_speed"]) != "reached": result += ", retract stopped"
        finally:
            video = ctrl.video.stop() if own_video else (ctrl.video.status() or {}).get("file")
            self.save(result, pop, pan, video)
        return result == "popped"

    def save(self, result, pop, pan, video):
        d = runs_dir(self.lab); d.mkdir(parents=True, exist_ok=True)
        f = d / f"{self.stamp}_{self.name}.json"
        data = {"file": f.name, "name": self.name, "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.t0)), "result": result,
                "popped": pop is not None, "pop": pop, "pan": pan, "duration_s": self.now(), "video": video, "plan": self.plan,
                "tool": self.ctrl.tool, "events": list(self.ctrl.events)[-10:], "steps": self.steps, "samples": self.samples}
        f.write_text(json.dumps(data))
        self.lab.note("run", {"file": f.name}, {"ok": pop is not None, "message": result})
        log(f"BALLOON run {f.name}: {result} in {data['duration_s']} s (video {video})")


def load_run(lab, name):
    f = runs_dir(lab) / str(name).replace("/", "_")
    if not f.is_file(): raise ValueError(f"no such run: {name}")
    return json.loads(f.read_text())


def summaries(lab, n=10):
    out = []
    for f in sorted(runs_dir(lab).glob("*.json"), reverse=True)[:n]:
        key = (str(f), f.stat().st_mtime)
        if key not in _SUMMARIES:
            r = json.loads(f.read_text())
            _SUMMARIES[key] = {k: r.get(k) for k in ("file", "name", "time", "result", "popped", "pop", "pan", "duration_s", "video", "plan")}
        out.append(_SUMMARIES[key])
    return out


# ------------------------------------------------------------------ actions
def _ask(what):
    return lambda lab, ctrl, a: "" if a.get("dry") else f"{what} The arm moves fast and the needle goes into whatever is in front of it."


def _start(lab, ctrl, plan, name, args):
    if args.get("dry"): return {"message": f"plan: thrust r {plan['r0_cm']}..{plan['r1_cm']} cm at z {plan['z_cm']} cm, {plan['speed']} deg/s", "plan": plan}
    if not ctrl.tool: raise ValueError("no tool mounted: mount the needle first (Tool tab)")
    run = Run(lab, ctrl, name, plan)
    motion.run_move(ctrl, f"balloon {run.name}", run.execute)
    return {"message": f"run {run.name} started", "plan": plan}


@LAB.action("look", "Look", "Where the orange balloon is in each camera (no move).")
def look(lab, ctrl, args):
    c = bcfg(lab); st, _ = ctrl.snapshot()
    return {"message": "looked", "wrist": orange(ctrl, c, "wrist"), "top": orange(ctrl, c, "top"), "tip": tip_of(ctrl.floor, st["present"], ctrl.tool)}


@LAB.action("aim", "Aim", "Pan until the needle points at the orange balloon (wrist camera). Only the pan moves.", moves=True)
def aim(lab, ctrl, args):
    run = Run(lab, ctrl, "aim", {})
    motion.run_move(ctrl, "balloon aim", lambda: run.aim() is not None)
    return "aiming"


@LAB.action("pop", "Pop the balloon", "Ready pose, aim by camera, thrust until the orange disappears, retract. Filmed and saved as a run.",
            {"name": "string?", "speed": "number?", "aim": "bool?", "dry": "bool?"}, confirm=_ask("Pop the balloon now?"), moves=True)
def pop(lab, ctrl, args):
    return _start(lab, ctrl, make_plan(lab, ctrl, args), args.get("name") or "pop", args)


@LAB.action("replay", "Replay a run", "Repeat a saved run without aiming: same pan and depth, optionally shifted (dpan deg, dr_cm, dz_cm).",
            {"run": "string", "dpan": "number?", "dr_cm": "number?", "dz_cm": "number?", "speed": "number?", "dry": "bool?"},
            confirm=_ask("Replay this run?"), moves=True)
def replay(lab, ctrl, args):
    base = load_run(lab, args.get("run"))
    if base.get("pan") is None: raise ValueError("that run never aimed: nothing to replay")
    return _start(lab, ctrl, make_plan(lab, ctrl, args, base), f"replay_{base['name']}"[:30], args)


@LAB.action("visit_points", "Visit points", "Go to every point (saved or staged) in name order, pausing at each.",
            {"speed": "number?"}, moves=True)
def visit_points(lab, ctrl, args):
    """Example of a multi-step move: it runs in the routine slot, so STOP / HOLD / ABORT interrupt it."""
    cfg = lab.cfg()
    points = [p for _, p in sorted((cfg.get("points") or {}).items())]
    if not points: raise ValueError("no points yet: record some in the Points tab")
    dwell, speed = float(bcfg(lab)["dwell_s"]), float(args.get("speed") or 4.0)
    keep_grip = getattr(ctrl, "tool", None) is None

    def run():
        for p in points:
            pose = motion.clean_pose(p["pose"], with_gripper=keep_grip)
            if not ctrl.goto_far(pose, speed, allow=motion.allow_for(cfg.get("envelope"), pose)): return False
            if ctrl.routine_abort.wait(dwell): return False
        return True
    motion.run_move(ctrl, "visit points", run)
    return f"visiting {len(points)} point(s)"


@LAB.state_key("balloon")
def summary(lab, ctrl, st):
    """Whatever this lab's panel needs every poll — keep it small, it is sent ~2.5 times a second."""
    c = bcfg(lab)
    return {"orange": orange(ctrl, c), "aim": LAST["aim"], "runs": summaries(lab), "speed": c["thrust_speed"], "aim_x": c["aim_x"]}
