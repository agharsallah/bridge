"""The lab's view of the world: one JSON a page draws and a model reads. Built from snapshots only.

Geometry comes from the same floor model the control loop guards with (``FloorModel.arm_points``), so
the side view shows exactly what the table guard reasons about — nothing here is a second opinion.
"""

import json

from .. import settings
from .. import tool as tool_mod
from ..floor import FLOOR_FREEZE, FLOOR_MARGIN, GRASP_Z
from ..poses import load_rest
from ..settings import ARM, JOINTS


def _hard_limits():
    try:
        from ..hardware import hard_limits  # lazy: filled by the daemon at connect, empty in tests
        return hard_limits()
    except Exception:
        return {}


def tip(floor, pose, tool=None):
    """{r_cm, z_cm, pan} of the working tip for a pose — the tool tip when a tool is given, else the fingertips."""
    if not pose or any(j not in pose for j in ("shoulder_lift", "elbow_flex", "wrist_flex")): return None
    pts = floor.arm_points(pose)
    if not pts: return None
    e = tool_mod.ends(floor, pose, tool)
    r, z = e["front"] if e else pts[-1]
    return {"r_cm": round(r * 100, 1), "z_cm": round(z * 100, 1), "pan": round(float(pose.get("shoulder_pan", 0.0)), 1)}


def tool_view(floor, pose, tool, label):
    """The tool as a segment for the side view: back end -> tip (cm), its margin, and how far each end clears."""
    e = tool_mod.ends(floor, pose, tool) if pose else None
    if not e: return None
    cm = lambda p: [round(p[0] * 100, 1), round(p[1] * 100, 1)]     # noqa: E731
    return {"state": label, "name": tool["name"], "back": cm(e["back"]), "front": cm(e["front"]), "margin_cm": tool["margin_cm"],
            "clearance_cm": round(e["front"][1] * 100 - tool["margin_cm"], 1)}


def beyond(pose, limits):
    """Joints of ``pose`` outside the soft limits — the ones that need the lab's allowance."""
    return [j for j in ARM if j in pose and j in limits and not (limits[j][0] <= pose[j] <= limits[j][1])]


def points_view(store_view, floor, limits):
    saved, eff = store_view["saved"].get("points") or {}, store_view["effective"].get("points") or {}
    out = []
    for name in sorted(set(saved) | set(eff)):
        p = eff.get(name) or saved.get(name) or {}
        status = ("deleted" if name not in eff else "new" if name not in saved
                  else "modified" if json.dumps(saved[name], sort_keys=True) != json.dumps(eff[name], sort_keys=True) else "saved")
        pose = p.get("pose") or {}
        out.append({"name": name, "role": p.get("role", ""), "note": p.get("note", ""), "pose": pose, "status": status,
                    "beyond": beyond(pose, limits), "tip": tip(floor, pose, p.get("tool")), "tool": (p.get("tool") or {}).get("name")})
    return out


def side_view(floor, present, goal, markers, tool=None):
    c = getattr(floor, "_c", None) or {}
    params = getattr(floor, "params", None)
    t = tip(floor, present, tool)
    out = {"fitted": params is not None, "contacts": getattr(floor, "n", 0),
           "rms_mm": round(floor.rms * 1000, 1) if getattr(floor, "rms", None) else None,
           "margin_cm": FLOOR_MARGIN * 100, "freeze_cm": FLOOR_FREEZE * 100, "grasp_cm": GRASP_Z * 100,
           "reach_cm": round(sum(c.get(k, 0.0) for k in ("L1", "L2", "L3")) * 100, 1) or None,
           "shoulder_z_cm": round(float(params[3]) * 100, 1) if params is not None else None,
           "arm": floor.arm_points(present) if present else None, "goal_arm": None, "goal_tip": None, "tip": t,
           "markers": markers}
    if goal and present and any(abs(goal.get(j, present[j]) - present[j]) > 0.5 for j in ARM if j in present):
        out["goal_arm"] = floor.arm_points({**present, **goal}); out["goal_tip"] = tip(floor, {**present, **goal}, tool)
    return out


def joints_view(st, envelope):
    present, goal, limits = st.get("present") or {}, st.get("goal") or {}, st.get("limits") or {}
    load, load_limit, hard = st.get("load") or {}, st.get("load_limit") or settings.LOAD_LIMIT, _hard_limits()
    rows = []
    for j in JOINTS:
        rows.append({"name": j, "present": present.get(j), "goal": goal.get(j), "load": load.get(j),
                     "load_limit": load_limit.get(j), "soft": limits.get(j), "hard": hard.get(j),
                     "envelope": envelope.get(j) if j in ARM else None})
    return rows


def guards_view(st, side, events, floor=None, tool=None):
    z = (side.get("tip") or {}).get("z_cm")
    worst = tool_mod.checks(floor, st.get("present"), tool) if floor is not None and st.get("present") else []
    clearance = round(min((zz - m) * 100 for _, zz, m in worst), 1) if worst else None
    return {"tool": tool, "estop": bool(st.get("estop")), "mode": st.get("mode"),
            "floor": {"fitted": side["fitted"], "contacts": side["contacts"], "rms_mm": side["rms_mm"],
                      "margin_cm": side["margin_cm"], "freeze_cm": side["freeze_cm"], "tip_z_cm": z,
                      "clearance_cm": clearance, "closest": min(worst, key=lambda x: x[1] - x[2])[0] if worst else None},
            "step_cap_deg": settings.MAX_STEP_DEG, "max_speed": settings.MAX_SPEED,
            "stall": {"deg": settings.STALL_DEG, "sec": settings.STALL_SEC},
            "load_limit": st.get("load_limit") or settings.LOAD_LIMIT, "loop_hz": st.get("loop_hz"),
            "events": events[-25:]}


def build(lab, ctrl, frames):
    st, _ = ctrl.snapshot()
    present, limits = st.get("present") or {}, st.get("limits") or {}
    view = lab.store.view()
    eff = view["effective"]
    points = points_view(view, ctrl.floor, limits)
    rest_pose = eff.get("rest") or load_rest()
    mounted, defined = getattr(ctrl, "tool", None), eff.get("tool")
    rest = {"source": "lab" if eff.get("rest") else "global", "pose": rest_pose, "tip": tip(ctrl.floor, rest_pose, mounted),
            "pending": any(c["path"] == "rest" for c in view["pending"]), "beyond": beyond(rest_pose, limits)}
    markers = [{"name": p["name"], "kind": "point", "status": p["status"], **p["tip"]} for p in points if p["tip"]]
    if rest["tip"]: markers.append({"name": "rest", "kind": "rest", "status": "pending" if rest["pending"] else rest["source"], **rest["tip"]})
    side = side_view(ctrl.floor, present, st.get("goal"), markers, mounted)
    side["tool"] = (tool_view(ctrl.floor, present, mounted, "mounted") if mounted
                    else tool_view(ctrl.floor, present, defined, "preview") if defined else None)
    with ctrl.lock:
        events = list(getattr(ctrl, "events", []))
    video = ctrl.video.status()
    return {"lab": lab.info(),
            "time": st.get("time"), "mode": st.get("mode"), "torque_on": st.get("torque_on"), "estop": st.get("estop"),
            "routine": ctrl.routine_status, "busy": bool(ctrl.routine_thread and ctrl.routine_thread.is_alive()),
            "present": present, "goal": st.get("goal"), "path": st.get("path"),
            "joints": joints_view(st, eff.get("envelope") or {}), "side": side,
            "guards": guards_view(st, side, events, ctrl.floor, mounted), "points": points, "rest": rest, "config": view,
            "tool": {"mounted": mounted, "defined": defined, "pending": any(c["path"].split(".")[0] == "tool" for c in view["pending"]),
                     "saved": view["saved"].get("tool"), "margin_min_cm": tool_mod.MARGIN_MIN_CM},
            "cameras": {"list": frames.info(ctrl), "stream": eff.get("cameras") or {}},
            "video": {"recording": video, "dir": str(lab.video_dir()), "raw": bool((eff.get("video") or {}).get("raw")),
                      "files": lab.videos()},
            **{k: fn(lab, ctrl, st) for k, fn in lab.extra_state.items()}}


def observe(lab, ctrl, frames, images=False, width=640):
    """A compact observation for a model or an LLM tool call: pose, fingertip, guards, points, cameras."""
    st, _ = ctrl.snapshot()
    present = st.get("present") or {}
    eff = lab.store.effective()
    mounted = getattr(ctrl, "tool", None)
    t = tip(ctrl.floor, present, mounted)
    c = tool_mod.checks(ctrl.floor, present, mounted) if present else []
    out = {"time": st.get("time"), "mode": st.get("mode"), "torque_on": st.get("torque_on"), "estop": st.get("estop"),
           "routine": ctrl.routine_status, "pose": {j: round(v, 2) for j, v in present.items()}, "tip": t,
           "tool": mounted, "tip_is": "tool tip" if mounted else "fingertips",
           "table_clearance_cm": round(min((z - m) * 100 for _, z, m in c), 1) if c else None,
           "points": {n: p.get("pose") for n, p in (eff.get("points") or {}).items()},
           "rest": eff.get("rest") or "global", "limits": st.get("limits"),
           "cameras": [{"name": c["name"], "live": c["live"], "snapshot": f"{lab.prefix}/cam/{c['name']}.jpg"} for c in frames.info(ctrl)],
           "recent_actions": lab.journal(10), "actions": f"{lab.prefix}/actions", "units": "degrees; gripper 0..100; cm"}
    if images:
        out["images"] = [g for g in (frames.grab_b64(ctrl, c, width) for c in settings.CAMS) if g]
    return out
