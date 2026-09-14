"""Compile strokes into a replayable program of guarded 'path' steps, and store programs on disk.

A program contains precomputed joint poses for every step, so replaying it needs neither the picture,
the tool model nor the cameras — only the arm in the same workspace (same paper and station placement).

    program = compile_program(plan, workspace, tool, name, image_name, dry_run=False)
    save(program) -> paintings/<name>.json ;  load(name) ; catalog()

Step ops: "travel" (hover moves), "stroke" (down, along the stroke, up), "dip" (station), "dwell" (seconds).
"""

import json
import math
import re
import time

from ..paths import ROOT
from ..settings import ARM
from ..util import log

PAINTINGS_DIR = ROOT / "paintings"
STROKE_RES_CM = 0.5            # densify strokes to this spacing on the paper
JOINT_STEP_DEG = 8.0           # densify joint-space transitions so every path point is within the step cap
SAFE_LIFT_DEG = 10.0           # go "up" this much (shoulder back) before travelling between paper and stations


def _densify_joint(a, b, step=JOINT_STEP_DEG):
    n = max(1, int(math.ceil(max(abs(b[j] - a[j]) for j in ARM if j in a and j in b) / step)))
    return [{j: round(a[j] + (b[j] - a[j]) * k / n, 3) for j in ARM if j in a and j in b} for k in range(1, n + 1)]


def _up(pose, d=SAFE_LIFT_DEG):
    p = dict(pose); p["shoulder_lift"] = pose["shoulder_lift"] - d; p["wrist_flex"] = pose.get("wrist_flex", 0) + 0.7 * d
    return p


def _station_poses(ws, name):
    st = ws["stations"][name]
    dip = {j: st["dip"][j] for j in ARM if j in st["dip"]}
    hover = {j: st["hover"][j] for j in ARM if j in st["hover"]} if st.get("hover") else _up(dip, 8.0)
    return hover, dip


def compile_program(plan, ws, tool, name, image_name="", dry_run=False):
    if not tool.ok:
        raise ValueError(f"tool model: {tool.error}")
    brush = ws["brush"]
    hover_z, press_z = float(brush["hover_cm"]), -float(brush["press_cm"])
    stroke_z = hover_z if dry_run else press_z
    stations = ws["stations"]
    water = [n for n, s in stations.items() if s.get("kind") == "water" and s.get("dip")]
    towel = [n for n, s in stations.items() if s.get("kind") == "towel" and s.get("dip")]
    steps, skipped, painted_since_dip, cur = [], [], 0.0, None
    stats = {"strokes": 0, "dips": 0, "rinses": 0, "skipped": 0, "travel_points": 0}

    def path_to(target, speed, op, label, **extra):
        nonlocal cur
        pts = _densify_joint(cur, target) if cur else [target]
        steps.append({"op": op, "label": label, "points": pts, "speed": speed, **extra}); cur = dict(target)
        stats["travel_points"] += len(pts)

    def visit_station(sname, dwell, label):
        hover, dip = _station_poses(ws, sname)
        if cur is not None:
            path_to(_up(cur), brush["travel_speed"], "travel", f"up before {sname}")
        path_to(_up(hover), brush["travel_speed"], "travel", f"to {sname}")
        path_to(hover, brush["travel_speed"], "travel", f"above {sname}")
        if not dry_run:
            path_to(dip, brush["stroke_speed"], "dip", label, station=sname)
            steps.append({"op": "dwell", "label": f"{label} dwell", "seconds": float(dwell)})
            path_to(hover, brush["stroke_speed"], "dip", f"lift from {sname}", station=sname)
        else:
            steps.append({"op": "dwell", "label": f"(dry) {label}", "seconds": 0.3})

    def rinse():
        for w in water[:1]:
            for k in range(int(brush.get("rinse_dips", 2))):
                visit_station(w, brush["dip_dwell_s"], f"rinse {k + 1}")
        for t in towel[:1]:
            visit_station(t, 0.5, "dab towel")
        stats["rinses"] += 1

    def dip(color):
        visit_station(color, brush["dip_dwell_s"], f"dip {color}")
        stats["dips"] += 1

    color = None
    for i, s in enumerate(plan["strokes"]):
        (u0, v0), (u1, v1) = s["points"][0], s["points"][-1]
        L = math.hypot(u1 - u0, v1 - v0)
        n = max(1, int(math.ceil(L / STROKE_RES_CM)))
        line = [(u0 + (u1 - u0) * k / n, v0 + (v1 - v0) * k / n) for k in range(n + 1)]
        down = [tool.uv_to_pose(u, v, stroke_z) for u, v in line]
        hov0, hov1 = tool.uv_to_pose(u0, v0, hover_z), tool.uv_to_pose(u1, v1, hover_z)
        if any(p is None for p in down) or hov0 is None or hov1 is None:
            skipped.append(i); stats["skipped"] += 1; continue
        if s["color"] != color:
            if color is not None: rinse()
            color = s["color"]; dip(color); painted_since_dip = 0.0
        elif painted_since_dip >= float(brush["dip_every_cm"]):
            dip(color); painted_since_dip = 0.0
        # travel at hover height to the stroke start (straight in paper space where possible)
        if cur is not None:
            cu, cv, cz = tool.pose_to_uv(cur)
            mids = [tool.uv_to_pose(cu + (u0 - cu) * k / 3, cv + (v0 - cv) * k / 3, hover_z + 0.5) for k in (1, 2)]
            for mp in mids:
                if mp is not None: path_to(mp, brush["travel_speed"], "travel", "to stroke")
        path_to(hov0, brush["travel_speed"], "travel", f"above stroke {i}")
        pts = down + [hov1]
        steps.append({"op": "stroke", "label": f"{color} stroke {i}", "points": pts, "speed": brush["stroke_speed"],
                      "color": color, "index": i, "uv": s["points"]})
        cur = dict(hov1); stats["strokes"] += 1; painted_since_dip += L
    if cur is not None:
        path_to(_up(cur), brush["travel_speed"], "travel", "finish: up")
    est = sum(len(st.get("points", [])) for st in steps) * 0.9 + sum(st.get("seconds", 0) for st in steps)
    return {"name": name, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "image": image_name, "dry_run": dry_run,
            "paper": {k: ws["paper"][k] for k in ("width_cm", "height_cm")}, "brush": dict(brush),
            "palette": plan["palette"], "grid": plan["grid"], "strokes_uv": plan["strokes"], "skipped": skipped,
            "stats": {**stats, "steps": len(steps), "estimated_s": int(est)}, "steps": steps}


# ---------------------------------------------------------------- store
def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())[:60] or "painting"


def save(program):
    PAINTINGS_DIR.mkdir(parents=True, exist_ok=True)
    program["name"] = _safe(program["name"])
    path = PAINTINGS_DIR / f"{program['name']}.json"
    path.write_text(json.dumps(program)); log(f"paint: program saved -> {path.name} ({program['stats']})")
    return program["name"]


def load(name):
    path = PAINTINGS_DIR / f"{_safe(name)}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def delete(name):
    path = PAINTINGS_DIR / f"{_safe(name)}.json"
    if path.is_file(): path.unlink(); log(f"paint: program {name} deleted")


def catalog():
    out = []
    if PAINTINGS_DIR.is_dir():
        for p in sorted(PAINTINGS_DIR.glob("*.json")):
            try:
                d = json.loads(p.read_text())
                out.append({"name": d["name"], "created": d.get("created"), "image": d.get("image"), "dry_run": d.get("dry_run"),
                            "stats": d.get("stats"), "palette": [c["name"] for c in d.get("palette", [])]})
            except Exception as e:
                out.append({"name": p.stem, "error": str(e)})
    return out
