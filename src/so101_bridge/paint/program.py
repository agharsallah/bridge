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


def z_correction(ws):
    """Height correction (cm) to add to any paper z the tool model is asked for, as a function of (u, v).

    The tool model is geometric and its corners were taught in free-drive; under torque the arm sags and the
    brush ends up lower than planned (1.5-2 cm on this arm). ``config/painting.json["probes"]`` holds where
    the brush *actually* touched the paper at a *commanded* height: {"u", "v", "z_touch_cm"}. Between probes
    the correction is inverse-distance interpolated; with no probes it falls back to ``brush.z_offset_cm``."""
    probes = [p for p in ws.get("probes", []) if "z_touch_cm" in p]
    const = float(ws["brush"].get("z_offset_cm", 0.0))
    if not probes:
        return lambda u, v: const

    def corr(u, v):
        w = [1.0 / max(0.25, math.hypot(u - float(p["u"]), v - float(p["v"]))) ** 2 for p in probes]
        return sum(wi * float(p["z_touch_cm"]) for wi, p in zip(w, probes)) / sum(w)
    return corr


def compile_program(plan, ws, tool, name, image_name="", dry_run=False):
    if not tool.ok:
        raise ValueError(f"tool model: {tool.error}")
    brush = ws["brush"]
    zcorr = z_correction(ws)                           # measured: where the brush really touches, per paper position
    hover_z, press_z = float(brush["hover_cm"]), -float(brush["press_cm"])
    stroke_z = hover_z if dry_run else press_z
    stations = ws["stations"]
    water = [n for n, s in stations.items() if s.get("kind") == "water" and s.get("dip")]
    towel = [n for n, s in stations.items() if s.get("kind") == "towel" and s.get("dip")]
    steps, skipped, painted_since_dip, cur = [], [], 0.0, None
    state = {"from_station": False}
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
        # straight up out of the station before anything else moves: a wet brush must not sweep sideways
        path_to(_up(hover), brush["travel_speed"], "travel", f"up from {sname}")
        state["from_station"] = True

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
        # a stroke is a polyline in paper cm: the brush goes down at the first point, follows every vertex
        # and lifts at the last. Straight hatch lines have two points; hand-written plans may have many.
        verts = [tuple(map(float, pt)) for pt in s["points"]]
        (u0, v0), (u1, v1) = verts[0], verts[-1]
        line, L = [verts[0]], 0.0
        for (a0, b0), (a1, b1) in zip(verts, verts[1:]):
            seg = math.hypot(a1 - a0, b1 - b0); L += seg
            n = max(1, int(math.ceil(seg / STROKE_RES_CM)))
            line += [(a0 + (a1 - a0) * k / n, b0 + (b1 - b0) * k / n) for k in range(1, n + 1)]
        down = [tool.uv_to_pose(u, v, stroke_z + zcorr(u, v)) for u, v in line]
        hov0, hov1 = tool.uv_to_pose(u0, v0, hover_z + zcorr(u0, v0)), tool.uv_to_pose(u1, v1, hover_z + zcorr(u1, v1))
        if any(p is None for p in down) or hov0 is None or hov1 is None:
            skipped.append(i); stats["skipped"] += 1; continue
        if s["color"] != color:
            if color is not None: rinse()
            color = s["color"]; dip(color); painted_since_dip = 0.0
        elif painted_since_dip >= float(brush["dip_every_cm"]):
            dip(color); painted_since_dip = 0.0
        # travel to the stroke start. Coming from a station: we are already up — turn the pan to the stroke's
        # bearing first (nothing else moves), then descend onto its hover point. Otherwise travel at hover height
        # (straight in paper space where possible).
        if state["from_station"]:
            turn = dict(cur); turn["shoulder_pan"] = hov0["shoulder_pan"]
            path_to(turn, brush["travel_speed"], "travel", f"turn to stroke {i}")
            state["from_station"] = False
        elif cur is not None:
            cu, cv, cz = tool.pose_to_uv(cur)
            mids = [tool.uv_to_pose(mu, mv, hover_z + 0.5 + zcorr(mu, mv))
                    for mu, mv in ((cu + (u0 - cu) * k / 3, cv + (v0 - cv) * k / 3) for k in (1, 2))]
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


def compile_plan(plan_in, ws, tool, name, dry_run=False):
    """Compile a hand-written plan — {"strokes": [{"color": name, "points": [[u, v], ...]}, ...]} in paper cm —
    into a program. Colours must be taught stations; the palette and a coarse preview grid are derived here so
    the result looks like a planner output to the dashboard and the routine. Used by the `paint_compile` file
    command, which lets an agent (or a shell script) paint without the picture pipeline or the browser."""
    from .planner import palette_from_workspace
    palette = palette_from_workspace(ws)
    names = {c["name"] for c in palette}
    strokes = []
    for i, s in enumerate(plan_in.get("strokes", [])):
        if s.get("color") not in names:
            raise ValueError(f"stroke {i}: colour {s.get('color')!r} is not a taught station ({sorted(names)})")
        pts = [[round(float(u), 2), round(float(v), 2)] for u, v in s["points"]]
        if len(pts) < 2:
            raise ValueError(f"stroke {i}: needs at least 2 points")
        strokes.append({"color": s["color"], "points": pts})
    W, H = float(ws["paper"]["width_cm"]), float(ws["paper"]["height_cm"])
    cols, rows = max(1, int(W)), max(1, int(H))
    plan = {"palette": palette, "strokes": strokes,
            "grid": {"cols": cols, "rows": rows, "cells": [[-1] * cols for _ in range(rows)],
                     "origin_cm": [0, 0], "size_cm": [W, H]}}
    return compile_program(plan, ws, tool, name, plan_in.get("image", "plan"), dry_run=dry_run)


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
