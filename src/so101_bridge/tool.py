"""A tool held in the gripper (pen, brush, probe): its geometry for the table guard, and the gripper lock.

The jaws hold the tool along the gripper axis (the floor model's last link, wrist axis -> fingertips).
``out_cm`` of it sticks out past the fingertips (the working tip); the rest, ``length_cm - out_cm``, points
back from there. While a tool is *mounted* the control loop guards three things instead of one:

* the fingertips — the usual ``FLOOR_MARGIN``, unchanged;
* the tool tip — the tool's own ``margin_cm`` (it may be set down to ``MARGIN_MIN_CM`` so a pen can touch
  the paper; the fingertips never can);
* the tool's back end — ``FLOOR_MARGIN``, like the fingertips;

and the gripper is locked: nothing but the mount itself (``src="tool"``) may move it — opening the jaws
would drop the tool. The mounted tool is kept in var/tool.json, because after a restart it is still in
the gripper. With no tool mounted every function here reduces to the plain fingertip guard.
"""

import json
import math

from .floor import FLOOR_FREEZE, FLOOR_MARGIN
from .paths import VAR_DIR
from .util import log

TOOL_FILE = VAR_DIR / "tool.json"
MARGIN_MIN_CM = -0.5            # the deepest a tool tip may be asked to press into the plane
MAX_LENGTH_CM = 40.0
FREEZE_SLACK = FLOOR_MARGIN - FLOOR_FREEZE    # how far below its margin a point may be predicted before the arm freezes


def normalize(d):
    """A validated tool dict, or ValueError with a sentence the operator can act on."""
    d = dict(d or {})
    name = str(d.get("name") or "tool").strip()[:40]
    length, out = float(d.get("length_cm", 0)), float(d.get("out_cm", 0))
    if not 0 < length <= MAX_LENGTH_CM: raise ValueError(f"tool length must be in (0, {MAX_LENGTH_CM:g}] cm")
    if not 0 <= out <= length: raise ValueError("the part sticking out cannot be longer than the tool")
    margin = float(d.get("margin_cm", FLOOR_MARGIN * 100))
    if margin < MARGIN_MIN_CM: raise ValueError(f"tool tip margin cannot be below {MARGIN_MIN_CM} cm")
    grip = max(0.0, min(100.0, float(d.get("grip", 0.0))))
    return {"name": name, "length_cm": round(length, 2), "out_cm": round(out, 2), "margin_cm": round(margin, 2), "grip": round(grip, 1)}


def load():
    try:
        return normalize(json.loads(TOOL_FILE.read_text())) if TOOL_FILE.is_file() else None
    except Exception as e:
        log(f"var/tool.json unreadable ({e}): no tool assumed"); return None


def save(tool):
    if tool is None:
        TOOL_FILE.unlink(missing_ok=True); return None
    TOOL_FILE.parent.mkdir(parents=True, exist_ok=True); TOOL_FILE.write_text(json.dumps(tool, indent=1))
    return tool


def ends(floor, pose, tool):
    """{"front": (r, z), "back": (r, z)} in metres along the gripper axis, or None (no tool / no model)."""
    if not tool: return None
    pts = floor.arm_points(pose)
    if not pts: return None
    (wr, wz), (tr, tz) = pts[2], pts[3]
    n = math.hypot(tr - wr, tz - wz) or 1.0
    ur, uz = (tr - wr) / n, (tz - wz) / n
    out, back = tool["out_cm"] / 100.0, (tool["length_cm"] - tool["out_cm"]) / 100.0
    return {"front": (tr + ur * out, tz + uz * out), "back": (tr - ur * back, tz - uz * back)}


def checks(floor, pose, tool):
    """[(label, z_m, margin_m)] for everything the table guard watches in this pose (empty: no model)."""
    z = floor.z(pose)
    if z is None: return []
    out = [("tips", z, FLOOR_MARGIN)]
    e = ends(floor, pose, tool)
    if e:
        out += [(f"{tool['name']} tip", e["front"][1], tool["margin_cm"] / 100.0), (f"{tool['name']} back", e["back"][1], FLOOR_MARGIN)]
    return out


def table_ok(floor, pose, tool):
    """True when every guarded point clears its margin (False when the floor model is not fitted)."""
    c = checks(floor, pose, tool)
    return bool(c) and all(z >= m for _, z, m in c)


def worst(floor, pose, tool):
    """(label, z_m) of the point closest to (or furthest past) its margin — for the guard event."""
    c = checks(floor, pose, tool)
    if not c: return ("tips", float("nan"))
    label, z, _ = min(c, key=lambda x: x[1] - x[2])
    return label, z


def frozen(floor, pose, tool):
    """A reason to freeze when a guarded point is predicted more than FREEZE_SLACK below its margin, else None."""
    for label, z, m in checks(floor, pose, tool):
        if z < m - FREEZE_SLACK: return f"predicted {label} height {z * 100:.1f} cm"
    return None


def lock_gripper(target, tool, src):
    """``target`` without its gripper key while a tool is mounted (unless the mount itself asks); and whether it was dropped."""
    if not tool or "gripper" not in target or src == "tool": return target, False
    return {j: v for j, v in target.items() if j != "gripper"}, True
