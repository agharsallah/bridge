"""Jenga lab: push one brick out of the tower with a stick, slowly, without making the tower fall

Built on the lab template (docs/lab.md). The stick is a mounted tool; the operator (or an agent looking at
the cameras) lines its tip up just in front of the brick with ``nudge`` and the pan jog, then ``push``
slides the tip forward on a straight level line, waits, and comes back the same way. The gripper keeps
its angle, so the tip only translates: a stick that is not quite on the gripper axis still pushes straight.
Every push is filmed and saved (var/lab/jenga_runs/). Every move goes through the control loop: never
src="floor_ok", never a motor register.
"""

import json
import math
import time

from so101_bridge.lab import BUILTIN, Lab, motion, register
from so101_bridge.lab.state import tip as tip_of
from so101_bridge.labs.balloon import allowed, ik  # the planar tool-tip IK (to promote to the template when a third lab needs it)
from so101_bridge.settings import ARM
from so101_bridge.util import log

DEFAULTS = {"depth_cm": 3.0, "speed": 3.0, "dwell_s": 1.0,      # how far the brick is pushed, joint deg/s, pause before coming back
            "nudge_max_cm": 2.0,
            "brick_cm": [7.0, 2.0], "row_cm": 1.5, "depth_max_cm": 3.5,                # brick length x width; `push` never goes past half a brick
            # --- what the 2026-11-12 sessions measured (docs/jenga.md); `turn` plays from these
            "pan_deg": -6.2, "face_r_cm": 24.5,         # where the tower is: pan that centres the tip on a middle brick, reach at first contact
            "tip_z_offset_cm": -3.35,                   # model tip height minus real height (the stick sits above the gripper axis)
            "sag_cm": 0.9, "slack_cm": 0.45,            # goal height above the wanted reading; what a small reversal of direction loses
            "standoff_cm": 0.5, "align_tol_cm": 0.15,   # where the height is judged by camera, and how close to the row's middle it must be
            "turn_depth_cm": 4.5, "turn_depth_max_cm": 5.5,     # 3.3 leaves the brick in the tower, 4.5 drops it out the back
            "push_speed": 2.0, "pre_lower_cm": 0.4,     # deg/s in contact; the tip is lowered this much before it is pulled out
            "end_rows": "odd", "min_row": 11, "vision": {}}     # below row 11 the arm is too folded at this tower distance
STEP_CM, PLANE = 0.25, ("shoulder_lift", "elbow_flex", "wrist_flex")
LIVE = {"phase": "idle", "depth_cm": None}      # what the push is doing right now, for the panel
_SUMMARIES = {}

LAB = register(Lab(
    "jenga", "Jenga lab", actions=BUILTIN, ui="panels.js", description="push one brick out of the tower with a stick, slowly, without making the tower fall",
    defaults={"jenga": DEFAULTS},        # config/jenga.json starts from this; edit it via staged changes
))


def jcfg(lab):
    return {**DEFAULTS, **(lab.cfg().get("jenga") or {})}


def runs_dir(lab):
    return lab.journal_file.parent / f"{lab.name}_runs"


def pitch_of(floor, pose):
    """The gripper axis in degrees from vertical (90 = level), from the floor model's chain."""
    o1, o2, o3, _, s1, s2, s3 = (float(x) for x in floor.params)
    return s1 * (pose["shoulder_lift"] - o1) + s2 * (pose["elbow_flex"] - o2) + s3 * (pose["wrist_flex"] - o3)


def held(ctrl):
    """The pose the arm is holding (its goal, not the sagged reading), or an error the operator can act on."""
    st, _ = ctrl.snapshot()
    if not st.get("torque_on", True): raise RuntimeError("torque is off: press Torque on first")
    if ctrl.floor.params is None: raise RuntimeError("the floor model is not fitted")
    if st.get("mode") == "moving": raise RuntimeError("still moving: wait for the last move to finish")    # its goal is only a waypoint
    return st, {j: float(v) for j, v in (st.get("goal") or st["present"]).items() if j in ARM}


def shifted(ctrl, st, pose, dr_cm, dz_cm):
    """Poses moving the tool tip by (dr, dz) cm on a straight line, gripper angle unchanged. Refuses what the loop would clip."""
    at, pitch = tip_of(ctrl.floor, pose, ctrl.tool), pitch_of(ctrl.floor, pose)
    base = ik(ctrl.floor, ctrl.tool, at["r_cm"], at["z_cm"], pitch)
    n, pts = max(1, math.ceil(math.hypot(dr_cm, dz_cm) / STEP_CM)), []
    for k in range(1, n + 1):
        p = ik(ctrl.floor, ctrl.tool, at["r_cm"] + dr_cm * k / n, at["z_cm"] + dz_cm * k / n, pitch)
        if base is None or p is None: raise ValueError("out of reach")
        p = {j: round(pose[j] + p[j] - base[j], 2) for j in PLANE}          # relative: the rounding of the tip readout cancels
        if not allowed(ctrl, st["limits"], p): raise ValueError("that would leave the soft limits or go into the table")
        pts.append(p)
    if max(abs(b[j] - a[j]) for a, b in zip([pose] + pts, pts) for j in PLANE) > 6.0:
        raise ValueError("too close to the folded arm for a straight move here: the joints would have to jump")
    return pts


@LAB.action("nudge", "Nudge the tip", "Move the tool tip a little, straight: dr_cm forward (+) / back, dz_cm up (+) / down. The gripper angle stays.",
            {"dr_cm": "number?", "dz_cm": "number?", "speed": "number?"}, moves=True)
def nudge(lab, ctrl, args):
    c, dr, dz = jcfg(lab), float(args.get("dr_cm") or 0), float(args.get("dz_cm") or 0)
    if max(abs(dr), abs(dz)) > c["nudge_max_cm"]: raise ValueError(f"a nudge is at most {c['nudge_max_cm']:g} cm")
    if motion.busy(ctrl): raise RuntimeError("busy: a move is running")
    st, pose = held(ctrl)
    pts = shifted(ctrl, st, pose, dr, dz)
    ctrl.request({"action": "path", "points": pts, "speed": max(0.5, min(6.0, float(args.get("speed") or c["speed"]))), "src": "web"})
    return f"tip {dr:+g} cm forward, {dz:+g} cm up"


def line_to(ctrl, st, pose, r1, z1, p1=90.0):
    """Poses taking the tool tip from where ``pose`` has it to (r1, z1) on a straight line, the stick ending at pitch p1."""
    at, p0 = tip_of(ctrl.floor, pose, ctrl.tool), pitch_of(ctrl.floor, pose)
    n, pts = max(1, math.ceil(max(math.hypot(r1 - at["r_cm"], z1 - at["z_cm"]) / STEP_CM, abs(p1 - p0) / 1.0))), []
    for k in range(1, n + 1):
        q = ik(ctrl.floor, ctrl.tool, at["r_cm"] + (r1 - at["r_cm"]) * k / n, at["z_cm"] + (z1 - at["z_cm"]) * k / n, p0 + (p1 - p0) * k / n)
        if q is None or not allowed(ctrl, st["limits"], q): raise ValueError(f"out of reach or into the table {k}/{n} of the way there")
        pts.append(q)
    if max(abs(b[j] - a[j]) for a, b in zip([pose] + pts, pts) for j in PLANE) > 6.0:
        raise ValueError("that line passes too close to the folded arm: go there in two moves")
    return pts


def row_z(lab, row):
    """Height of the middle of a row, counted from the table (row 1 is the bottom one)."""
    return (float(row) - 0.5) * jcfg(lab)["row_cm"]


@LAB.action("place", "Place the tip", "Bring the tool tip to a reach and height (or a row, counted from the table) on a straight line, "
            "ending with the stick at pitch_deg from vertical (90 = level).",
            {"r_cm": "number", "z_cm": "number?", "row": "number?", "pitch_deg": "number?", "speed": "number?", "dry": "bool?"}, moves=True)
def place(lab, ctrl, args):
    if motion.busy(ctrl): raise RuntimeError("busy: a move is running")
    st, pose = held(ctrl)
    at = tip_of(ctrl.floor, pose, ctrl.tool)
    r1 = float(args["r_cm"]); z1 = row_z(lab, args["row"]) if args.get("row") else float(args.get("z_cm") or at["z_cm"])
    p1 = float(args.get("pitch_deg") or 90.0)
    pts = line_to(ctrl, st, pose, r1, z1, p1)
    target = {"r_cm": round(r1, 1), "z_cm": round(z1, 2), "pitch_deg": p1}
    if args.get("dry"): return {"message": f"plan: tip to {target} in {len(pts)} steps", "target": target, "end": pts[-1]}
    ctrl.request({"action": "path", "points": pts, "speed": max(0.5, min(8.0, float(args.get("speed") or 4.0))), "src": "web"})
    return {"message": f"tip to r {r1:g} cm, z {z1:g} cm, pitch {p1:g}", "target": target}


def _ask(lab, ctrl, a):
    return "" if a.get("dry") else "Push now? The stick slides forward into whatever is in front of it, then comes back."


@LAB.action("push", "Push the brick", "Slide the tip forward depth_cm on a level line, wait, come back. Filmed and saved as a run.",
            {"depth_cm": "number?", "speed": "number?", "name": "string?", "dry": "bool?"}, confirm=_ask, moves=True)
def push(lab, ctrl, args):
    c = jcfg(lab)
    depth, speed = float(args.get("depth_cm") or c["depth_cm"]), max(0.5, min(8.0, float(args.get("speed") or c["speed"])))
    if not 0 < depth <= c["depth_max_cm"]: raise ValueError(f"depth must be in (0, {c['depth_max_cm']:g}] cm")
    st, start = held(ctrl)
    out = shifted(ctrl, st, start, depth, 0.0)
    plan = {"depth_cm": depth, "speed": speed, "dwell_s": c["dwell_s"], "start": start, "tip": tip_of(ctrl.floor, start, ctrl.tool)}
    if args.get("dry"): return {"message": f"plan: push {depth:g} cm at {speed:g} deg/s from tip {plan['tip']}", "plan": plan, "points": len(out)}
    if not ctrl.tool: raise ValueError("no tool mounted: mount the stick first (Tool tab)")
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(args.get("name") or "push"))[:30]
    back = out[-2::-1] + [{j: start[j] for j in PLANE}]
    motion.run_move(ctrl, f"jenga {name}", lambda: _run(lab, ctrl, name, plan, out, back))
    return {"message": f"pushing {depth:g} cm", "plan": plan}


def _run(lab, ctrl, name, plan, out, back):
    t0, stamp, samples, steps = time.time(), time.strftime("%Y%m%d_%H%M%S"), [], []
    v, own_video = lab.cfg().get("video") or {}, ctrl.video.status() is None
    if own_video: ctrl.video.start(f"jenga_{name}", directory=lab.video_dir(), raw=bool(v.get("raw")))

    def sample():
        st, _ = ctrl.snapshot()
        samples.append({"t": round(time.time() - t0, 3), "mode": st.get("mode"), "load": st.get("load") or {},
                        "pose": {j: round(x, 2) for j, x in st["present"].items() if j in ARM}})

    def wait(seconds):
        end = time.time() + seconds
        while time.time() < end:
            sample()
            if ctrl.routine_abort.wait(0.05): return False
        return True

    def follow(label, points):
        steps.append({"label": label, "t": round(time.time() - t0, 3), "points": points}); LIVE["phase"] = label
        ctrl.request({"action": "path", "points": points, "speed": plan["speed"], "src": "auto"})
        ctrl._wait_moving()
        end, still = time.time() + 20.0 + 4.0 * len(points) / plan["speed"], None
        while time.time() < end:
            sample(); st, _ = ctrl.snapshot()
            if ctrl.routine_abort.is_set() or st.get("estop") or st.get("mode") == "stalled": return False
            if st.get("mode") in ("reached", "hold"):
                still = still or time.time()
                if time.time() - still > 0.3: return True
            else: still = None
            time.sleep(0.05)
        return False

    result = "stopped"; LIVE.update(phase="filming", depth_cm=plan["depth_cm"])
    try:
        if wait(1.0) and follow("push", out):                      # a second of film before the stick moves
            LIVE["phase"] = "hold"
            result = "pushed" if wait(plan["dwell_s"]) and follow("retract", back) and wait(1.0) else "pushed, retract stopped"
    finally:
        LIVE["phase"] = result
        video = ctrl.video.stop() if own_video else (ctrl.video.status() or {}).get("file")
        d = runs_dir(lab); d.mkdir(parents=True, exist_ok=True)
        f = d / f"{stamp}_{name}.json"
        f.write_text(json.dumps({"file": f.name, "name": name, "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)), "result": result,
                                 "duration_s": round(time.time() - t0, 2), "video": video, "plan": plan, "tool": ctrl.tool,
                                 "events": list(ctrl.events)[-10:], "steps": steps, "samples": samples}))
        lab.note("run", {"file": f.name}, {"ok": result == "pushed", "message": result})
        log(f"JENGA run {f.name}: {result} (video {video})")
    return result == "pushed"


@LAB.state_key("jenga")
def summary(lab, ctrl, st):
    """Whatever this lab's panel needs every poll — keep it small, it is sent ~2.5 times a second."""
    c = jcfg(lab)
    runs = []
    for f in sorted(runs_dir(lab).glob("*.json"), reverse=True)[:8]:
        key = (str(f), f.stat().st_mtime)
        if key not in _SUMMARIES:
            r = json.loads(f.read_text())
            _SUMMARIES[key] = {k: r.get(k) for k in ("file", "name", "time", "result", "duration_s", "video")} | {"depth_cm": r["plan"].get("depth_cm")}
        runs.append(_SUMMARIES[key])
    return {"depth_cm": c["depth_cm"], "speed": c["speed"], "runs": runs, "live": LIVE}
