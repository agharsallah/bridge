"""Drawing lab: draw on a sheet of paper with a pen held in the gripper; preview, record and redraw

Built on the lab template (docs/lab.md); docs/draw.md is the how-to. The pen is a mounted tool. A drawing is a
list of strokes (labs/draw_shapes.py), placed on a rectangle of the table in front of the arm. Table
coordinates are cm from the arm's base axis: x to the arm's right, y away from it. The paper's height is
*measured* by touching it (``calibrate``), never assumed. This module is the lab, its geometry and what is
going to be drawn; labs/draw_run.py moves the arm. Every move goes through the control loop: never
src="floor_ok", never a motor register.
"""

import json
import math
import time

import numpy as np

from so101_bridge.lab import BUILTIN, Lab, register
from so101_bridge.lab.cameras import FRAMES
from so101_bridge.lab.state import tip as tip_of
from so101_bridge.labs import draw_shapes as shapes
from so101_bridge.labs.balloon import allowed, ik  # the planar tool-tip IK, shared with the balloon and jenga labs

PEN = {"name": "pen", "length_cm": 14.0, "out_cm": 8.0, "margin_cm": -0.5, "grip": 5.0}   # taped to the fixed jaw: the grip value only parks the other jaw
DEFAULTS = {
    "area": {"x": [-9.0, 9.0], "y": [26.0, 38.0]},      # the rectangle drawn in; with the pen at 45 degrees the arm moves smoothly from 26 to 39 cm
    "reach_offset_cm": 6.0,                             # the real pen tip is this much further from the base axis than the floor model says (measured)
    "pitch_deg": 135.0,                                 # pen axis from vertical-up (90 = level, 180 = straight down)
    "paper_z_cm": 0.0, "touch": [],                     # tip height on the paper before calibration; then the measured [[x, y, z]]
    "press_cm": 0.45, "lift_cm": 2.5, "hover_cm": 4.0,  # how far below the touch height the pen draws (0.2-0.3 left gaps); pen-up height; height between jobs
    "land_cm": 0.8, "ramp_cm": 0.6, "settle_s": 0.3,    # the pen comes down over the first land_cm of the line, from ramp_cm above the paper; pause before landing
    "park_pan_deg": -55.0,                              # where the arm swings to after a drawing, out of the top camera's view of the sheet
    "speed": 8.0, "travel_speed": 12.0, "step_cm": 0.3,     # deg/s pen down / pen up; spacing of the points sent to the loop
    "probe_from_cm": 3.0, "probe_step_cm": 0.2, "probe_speed": 3.0,
    "touch_err_cm": 0.25,                               # touched = the tip lags its usual sag by this much on two steps in a row
    "pan_sign": 1.0, "pan0_deg": 0.0,                   # pan = pan0 + sign * angle to the right of straight ahead (pan0 squares the frame to the sheet)
    "scale": 0.9, "camera": "top",
}
LIVE = {"phase": "idle", "stroke": None, "trace": [], "name": "robot", "place": {}, "rev": 0}
_CACHE = {}

LAB = register(Lab(
    "draw", "Drawing lab", actions=BUILTIN, ui="panels.js", description="draw on a sheet of paper with a pen held in the gripper; preview, record and redraw",
    defaults={"draw": DEFAULTS, "tool": PEN},        # config/draw.json starts from this; edit it via staged changes
))


def dcfg(lab):
    return {**DEFAULTS, **(lab.cfg().get("draw") or {})}


def files(lab, kind):
    """var/lab/draw_runs or var/lab/draw_drawings."""
    return lab.journal_file.parent / f"{lab.name}_{kind}"


# ------------------------------------------------------------------ geometry
def paper_z(c, x, y):
    """Tool-tip height (as the arm is asked for it) that just touches the paper at (x, y). It follows the reach: it
    rises fast just outside the folded arm, slowly in the middle, fast again at full stretch (measured). So: straight
    lines between the touch points [[x, y, z]] by distance from the base, flat beyond the first and the last."""
    t = sorted((math.hypot(p[0], p[1]), p[2]) for p in c.get("touch") or [])
    if not t: return float(c["paper_z_cm"])
    return float(np.interp(math.hypot(x, y), [p[0] for p in t], [p[1] for p in t]))


def pose_at(ctrl, c, tool, x, y, z):
    """The four joints that put the pen tip at table point (x, y) and height z, pen at the lab's pitch; None if out of reach."""
    p = ik(ctrl.floor, tool, math.hypot(x, y) - c["reach_offset_cm"], z, c["pitch_deg"])
    if p is None: return None
    return {"shoulder_pan": round(c["pan0_deg"] + c["pan_sign"] * math.degrees(math.atan2(x, y)), 2), **p}


def xy_of(c, at):
    """Table (x, y) of a tip reading {r_cm, pan}."""
    a, r = math.radians((at["pan"] - c["pan0_deg"]) / c["pan_sign"]), at["r_cm"] + c["reach_offset_cm"]
    return round(r * math.sin(a), 1), round(r * math.cos(a), 1)


# ------------------------------------------------------------------ what is going to be drawn
def names(lab):
    return list(shapes.SHAPES) + sorted(f.stem for f in files(lab, "drawings").glob("*.json") if f.stem not in shapes.SHAPES)


def drawing(lab, name):
    if name in shapes.SHAPES: return shapes.SHAPES[name]()
    f = files(lab, "drawings") / f"{name}.json"
    if not f.is_file(): raise ValueError(f"no drawing called {name!r}: one of {', '.join(names(lab))}")
    return shapes.clean(json.loads(f.read_text())["strokes"])


def select(lab, name=None, strokes=None, **place):
    """Choose what the next ``draw`` draws (and what the panel previews). ``strokes`` saves a new drawing under ``name``."""
    c = dcfg(lab)
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(name or (time.strftime("sketch_%H%M%S") if strokes else LIVE["name"])))[:30]
    if strokes is not None:
        if name in shapes.SHAPES: raise ValueError(f"{name!r} is a built-in drawing: pick another name")
        d = files(lab, "drawings"); d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.json").write_text(json.dumps({"name": name, "strokes": shapes.clean(strokes)}))
    drawing(lab, name)                                   # must exist
    old = LIVE["place"] if name == LIVE["name"] else {}
    num = lambda k, d: float(place[k]) if place.get(k) is not None else float(old.get(k, d))     # noqa: E731
    scale = num("scale", c["scale"])
    if not 0.05 <= scale <= 1.5: raise ValueError("scale is a fraction of the drawing area: 0.05 to 1.5")
    LIVE.update(name=name, place={"scale": scale, "dx_cm": num("dx_cm", 0.0), "dy_cm": num("dy_cm", 0.0)}, rev=LIVE["rev"] + 1)
    return name


def plan(lab, ctrl, limits):
    """The selected drawing placed on the table: {name, place, strokes (cm), length_cm, problem}. Cached: the panel asks every poll."""
    c, tool = dcfg(lab), ctrl.tool or lab.cfg().get("tool")
    place = {"scale": c["scale"], "dx_cm": 0.0, "dy_cm": 0.0, **LIVE["place"]}
    key = json.dumps([LIVE["name"], LIVE["rev"], place, c, tool, limits], sort_keys=True)
    if _CACHE.get("key") != key:
        strokes = shapes.fit(drawing(lab, LIVE["name"]), c["area"], **place)
        problem = ""
        for x, y in (p for s in strokes for p in shapes.resample(s, 1.0)):
            z = paper_z(c, x, y)
            for h in (z - c["press_cm"], z + c["lift_cm"]):
                p = pose_at(ctrl, c, tool, x, y, h)
                if not problem and (p is None or not allowed(ctrl, limits, p)):
                    problem = f"the pen cannot reach x {x:.1f}, y {y:.1f} cm (height {h:.1f}): make it smaller or move it"
        _CACHE.update(key=key, plan={"name": LIVE["name"], "place": place, "strokes": strokes, "length_cm": round(shapes.length(strokes), 1),
                                     "problem": problem})
    return _CACHE["plan"]


# ------------------------------------------------------------------ actions that do not move the arm
@LAB.action("show", "Choose a drawing", "Choose what will be drawn and where (preview only, no move). `strokes` ([[[x, y], ...], ...], "
            "x right, y away, any unit) saves a new drawing under `name`. scale is a fraction of the area; dx/dy shift it in cm.",
            {"name": "string?", "strokes": "list?", "scale": "number?", "dx_cm": "number?", "dy_cm": "number?"})
def show(lab, ctrl, args):
    name = select(lab, args.get("name"), args.get("strokes"), **{k: args.get(k) for k in ("scale", "dx_cm", "dy_cm")})
    p = plan(lab, ctrl, ctrl.snapshot()[0]["limits"])
    return {"message": p["problem"] or f"{name}: {len(p['strokes'])} strokes, {p['length_cm']:g} cm of line", "plan": p}


@LAB.action("photo", "Take a photo", "Save a still of the paper from a camera next to the runs (var/lab/draw_runs).", {"cam": "string?", "name": "string?"})
def photo(lab, ctrl, args):
    f = snap(lab, ctrl, str(args.get("name") or "photo"), args.get("cam"))
    if f is None: raise RuntimeError("no frame from that camera")
    return {"message": f"saved {f.name}", "file": str(f)}


def snap(lab, ctrl, name, cam=None):
    data, _ = FRAMES.jpeg(ctrl, cam or dcfg(lab)["camera"], 1280, 90)
    if not data: return None
    d = files(lab, "runs"); d.mkdir(parents=True, exist_ok=True)
    f = d / f"{time.strftime('%Y%m%d_%H%M%S')}_{name}.jpg"
    f.write_bytes(data); return f


_SUMMARIES = {}


@LAB.state_key("draw")
def summary(lab, ctrl, st):
    """What the panel draws every poll: the area, the plan, where the pen is, the last runs."""
    c = dcfg(lab)
    try: p = plan(lab, ctrl, st.get("limits") or {})
    except (ValueError, OSError) as e: p = {"name": LIVE["name"], "strokes": [], "problem": str(e)}
    at = tip_of(ctrl.floor, st.get("present"), ctrl.tool) if st.get("present") else None
    pen = None
    if at:
        x, y = xy_of(c, at)
        pen = {"x": x, "y": y, "up_cm": round(at["z_cm"] - paper_z(c, x, y), 1)}
    runs = []
    for f in sorted(files(lab, "runs").glob("*.json"), reverse=True)[:8]:
        key = (str(f), f.stat().st_mtime)
        if key not in _SUMMARIES:
            r = json.loads(f.read_text())
            _SUMMARIES[key] = {k: r.get(k) for k in ("file", "name", "time", "result", "duration_s", "video", "place")}
        runs.append(_SUMMARIES[key])
    return {"area": c["area"], "plan": p, "pen": pen, "touch": c["touch"], "names": names(lab), "runs": runs, "camera": c["camera"],
            "live": {k: LIVE[k] for k in ("phase", "stroke", "trace")}}
