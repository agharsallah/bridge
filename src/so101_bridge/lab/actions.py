"""The actions every lab starts with: safety, motion, points, rest, envelope, config changes, video.

Anything that edits the lab config is *staged* (see staging.py) — it shows up as a pending change the
operator can edit, apply or discard. Moves go to the control loop as requests (motion.py).
"""

from pathlib import Path

from .. import tool as tool_mod
from ..paths import ESTOP
from ..settings import ARM, JOINTS
from ..shared.store import pose_round, safe_name
from ..util import log
from . import motion
from .core import Lab

_REG = Lab("_builtin")          # a scratch lab just to collect the decorated built-ins
act = _REG.action
RELEASE_Q = "Torque OFF (free-drive). The arm sags under gravity — hold it before you confirm."


def _speed(lab, args):
    s = float(args.get("speed") or (lab.cfg().get("motion") or {}).get("speed") or 4.0)
    return max(0.5, min(15.0, s))


def _present(ctrl, with_gripper=True):
    st, _ = ctrl.snapshot(); pose = st.get("present")
    if not pose: raise RuntimeError("no pose yet: the arm has not reported")
    out = pose_round(pose)
    if with_gripper and "gripper" in pose: out["gripper"] = round(float(pose["gripper"]), 2)
    return out


def _tool(ctrl):
    return getattr(ctrl, "tool", None)


def _name(args, key="name"):
    n = safe_name(args.get(key, ""), 40, "")
    if not n: raise ValueError(f"{key} is required")
    return n


# ------------------------------------------------------------------ safety (not staged, immediate)
@act("stop", "STOP", "Emergency stop: freeze now, abort any routine (touches ESTOP).", group="safety")
def stop(lab, ctrl, args):
    ESTOP.touch(); ctrl.abort_auto("LAB STOP"); return "ESTOP set"


@act("resume", "Resume", "Clear the emergency stop.", group="safety")
def resume(lab, ctrl, args):
    ESTOP.unlink(missing_ok=True); return "ESTOP cleared"


@act("hold", "Hold", "Abort whatever runs and hold the present pose.", group="safety")
def hold(lab, ctrl, args):
    ctrl.abort_auto("LAB HOLD"); ctrl.request({"action": "hold", "src": "web"}); return "holding"


@act("engage", "Torque on", "Torque on, holding where the arm is.", group="safety")
def engage(lab, ctrl, args):
    ctrl.request({"action": "engage", "src": "web"}); return "torque on"


@act("release", "Free-drive", "Torque off so the arm can be moved by hand.", confirm=RELEASE_Q, group="safety")
def release(lab, ctrl, args):
    ctrl.abort_auto("LAB RELEASE"); ctrl.request({"action": "release", "src": "web"}); return "torque off"


# ------------------------------------------------------------------ motion
@act("jog", "Jog", "Relative step on one joint.", {"joint": "string", "delta": "number", "speed": "number?"}, moves=True, group="motion")
def jog(lab, ctrl, args):
    if motion.busy(ctrl): raise RuntimeError(f"busy: {ctrl.routine_status} (HOLD first)")
    motion.jog(ctrl, args.get("joint"), float(args["delta"]), _speed(lab, args), lab.cfg().get("envelope"))
    return f"{args.get('joint')} {float(args['delta']):+g}°"


@act("goto_pose", "Go to pose", "Move to a (partial) joint pose in degrees.", {"pose": "pose", "speed": "number?"}, moves=True, group="motion")
def goto_pose(lab, ctrl, args):
    pose = motion.clean_pose(args.get("pose"), with_gripper=not _tool(ctrl))
    motion.goto_pose(ctrl, pose, _speed(lab, args), lab.cfg().get("envelope"), "goto pose")
    return "moving"


@act("goto_point", "Go to point", "Move to a named lab point (saved or staged).",
     {"name": "string", "with_gripper": "bool?", "speed": "number?"}, moves=True, group="motion")
def goto_point(lab, ctrl, args):
    p = (lab.cfg().get("points") or {}).get(args.get("name"))
    if not p: raise ValueError(f"no point {args.get('name')!r}")
    pose = motion.clean_pose(p.get("pose"), with_gripper=bool(args.get("with_gripper")) and not _tool(ctrl))
    motion.goto_pose(ctrl, pose, _speed(lab, args), lab.cfg().get("envelope"), f"goto {args['name']}")
    return f"moving to {args['name']}"


@act("gripper", "Gripper", "Open/close the gripper (0..100).", {"value": "number"}, moves=True, group="motion")
def gripper(lab, ctrl, args):
    if _tool(ctrl): raise RuntimeError(f"the {ctrl.tool['name']} is mounted: the gripper stays closed on it (unmount first)")
    v = max(0.0, min(100.0, float(args["value"])))
    ctrl.request({"action": "goal", "goal": {"gripper": v}, "speed": 20.0, "src": "web"}); return f"gripper -> {v:g}"


@act("rest", "Go to rest", "Go to this lab's rest (or the global REST when the lab has none).", {"speed": "number?"},
     moves=True, group="motion")
def rest(lab, ctrl, args):
    rest_pose = lab.cfg().get("rest")
    if rest_pose and _tool(ctrl): rest_pose = motion.clean_pose(rest_pose, with_gripper=False)
    motion.go_rest(ctrl, rest_pose, _speed(lab, args), lab.cfg().get("envelope")); return "going to rest"


@act("routine", "Run routine", "Start a registered routine (see routines.py).", {"name": "string"}, moves=True, group="motion")
def run_routine(lab, ctrl, args):
    if not ctrl.start_routine(str(args.get("name", ""))): raise RuntimeError("not started: unknown routine or busy")
    return f"routine {args['name']} started"


# ------------------------------------------------------------------ points / rest / envelope (staged)
@act("point_record", "Record point", "Stage the present pose as a named point.",
     {"name": "string", "role": "string?", "note": "string?"}, group="points")
def point_record(lab, ctrl, args):
    name = _name(args); old = (lab.cfg().get("points") or {}).get(name) or {}
    value = {"pose": _present(ctrl), "role": args.get("role", old.get("role", "reference")), "note": args.get("note", old.get("note", "")),
             "tool": _tool(ctrl)}             # the tool held when it was taught: its tip is what the point means
    lab.store.stage(f"points.{name}", value, label=f"record {name}")
    return f"{name} staged"


@act("point_edit", "Edit point", "Stage new role / note / pose for a point.",
     {"name": "string", "role": "string?", "note": "string?", "pose": "pose?"}, group="points")
def point_edit(lab, ctrl, args):
    name = _name(args); p = dict((lab.cfg().get("points") or {}).get(name) or {})
    if not p: raise ValueError(f"no point {name!r}")
    for k in ("role", "note"):
        if k in args: p[k] = str(args[k])[:200]
    if args.get("pose"): p["pose"] = {j: round(float(v), 2) for j, v in args["pose"].items() if j in JOINTS}
    lab.store.stage(f"points.{name}", p, label=f"edit {name}"); return f"{name} edit staged"


@act("point_rename", "Rename point", "Stage a rename.", {"name": "string", "new": "string"}, group="points")
def point_rename(lab, ctrl, args):
    name, new = _name(args), _name(args, "new")
    pts = lab.cfg().get("points") or {}
    if name not in pts: raise ValueError(f"no point {name!r}")
    if new in pts: raise ValueError(f"{new!r} exists already")
    lab.store.stage(f"points.{new}", pts[name], label=f"rename {name} -> {new}")
    lab.store.stage(f"points.{name}", op="delete", label=f"rename {name} -> {new}")
    return f"rename to {new} staged"


@act("point_delete", "Delete point", "Stage the removal of a point.", {"name": "string"}, group="points")
def point_delete(lab, ctrl, args):
    name = _name(args); lab.store.stage(f"points.{name}", op="delete", label=f"delete {name}"); return f"delete {name} staged"


@act("rest_record", "Record rest", "Stage the present pose as this lab's rest.", group="points")
def rest_record(lab, ctrl, args):
    lab.store.stage("rest", _present(ctrl), label="lab rest = present"); return "lab rest staged"


@act("rest_clear", "Use global rest", "Stage: this lab goes back to the global REST.", group="points")
def rest_clear(lab, ctrl, args):
    lab.store.stage("rest", None, label="lab rest -> global"); return "staged"


@act("envelope_set", "Set envelope", "Stage a lab-only range for one joint (capped by the motor sweep).",
     {"joint": "string", "lo": "number", "hi": "number"}, group="points")
def envelope_set(lab, ctrl, args):
    j, lo, hi = args.get("joint"), float(args["lo"]), float(args["hi"])
    if j not in ARM: raise ValueError(f"{j!r} is not an arm joint")
    if lo >= hi: raise ValueError("lo must be below hi")
    lab.store.stage(f"envelope.{j}", [round(lo, 1), round(hi, 1)], label=f"envelope {j}"); return "staged"


@act("envelope_clear", "Clear envelope", "Stage: back to the soft limit for one joint.", {"joint": "string"}, group="points")
def envelope_clear(lab, ctrl, args):
    lab.store.stage(f"envelope.{args.get('joint')}", op="delete", label=f"envelope {args.get('joint')} cleared"); return "staged"


# ------------------------------------------------------------------ tool held in the gripper (see tool.py)
MOUNT_Q = ("Mount the {name}: it sits in the jaws with {out_cm} cm sticking out past the fingertips ({length_cm} cm long)? "
           "The gripper closes to {grip} and stays locked; the table guard watches the tool tip (margin {margin_cm} cm) "
           "and both of its ends from now on, on every page.")


@act("tool_define", "Define tool", "Stage the tool held in the gripper: total length, the part sticking out past the "
     "fingertips, the tip's own table margin (may be ≤ 0 to touch the paper) and the gripper value that holds it.",
     {"name": "string", "length_cm": "number", "out_cm": "number", "margin_cm": "number?", "grip": "number?"}, group="tool")
def tool_define(lab, ctrl, args):
    lab.store.stage("tool", tool_mod.normalize(args), label=f"tool {args.get('name') or ''}".strip()); return "tool staged"


@act("tool_clear", "Remove tool definition", "Stage: this lab uses no tool.", group="tool")
def tool_clear(lab, ctrl, args):
    lab.store.stage("tool", None, label="no tool"); return "staged"


@act("tool_grip_record", "Record grip", "Stage the present gripper opening as the value that holds the tool.", group="tool")
def tool_grip_record(lab, ctrl, args):
    t = lab.cfg().get("tool")
    if not t: raise ValueError("define the tool first")
    g = _present(ctrl).get("gripper")
    if g is None: raise RuntimeError("no gripper reading")
    lab.store.stage("tool", tool_mod.normalize({**t, "grip": g}), label="tool grip = present"); return f"grip {g:g} staged"


@act("tool_mount", "Mount tool", "Lock the gripper on the defined tool; the table guard then watches the tool tip.",
     confirm=lambda lab, ctrl, args: MOUNT_Q.format(**t) if (t := lab.store.saved().get("tool")) else "",
     group="tool")
def tool_mount(lab, ctrl, args):
    view = lab.store.view(); t = view["saved"].get("tool")
    if not t: raise ValueError("no saved tool: define it and apply the change first")
    if any(c["path"].split(".")[0] == "tool" for c in view["pending"]): raise ValueError("the tool has pending changes: apply or discard them first")
    t = ctrl.mount_tool(t)                                   # guard first, then close: never a moment with an unguarded tip
    ctrl.request({"action": "goal", "goal": {"gripper": t["grip"]}, "speed": 15.0, "src": "tool"})
    return f"{t['name']} mounted: guard on its tip, gripper -> {t['grip']:g} and locked"


@act("tool_unmount", "Unmount tool", "Back to the fingertip guard; the gripper unlocks but does not move.",
     confirm="Unmount the tool? The guard goes back to the fingertips only and the gripper unlocks. It stays closed: "
             "hold the tool before you open the jaws.", group="tool")
def tool_unmount(lab, ctrl, args):
    if not _tool(ctrl): return "no tool was mounted"
    name = ctrl.tool["name"]; ctrl.unmount_tool(); return f"{name} unmounted"


# ------------------------------------------------------------------ the change set
@act("config_set", "Set config value", "Stage any value by dotted path (e.g. motion.speed, video.dir).",
     {"path": "string", "value": "any"}, group="changes")
def config_set(lab, ctrl, args):
    path = str(args.get("path") or "")
    if path.split(".")[0] in ("", "updated"): raise ValueError(f"bad path {path!r}")
    lab.store.stage(path, args.get("value"), label=args.get("label", f"set {path}")); return f"{path} staged"


@act("change_edit", "Edit change", "Replace the value of a pending change.", {"id": "string", "value": "any"}, group="changes")
def change_edit(lab, ctrl, args):
    c = next((c for c in lab.store.pending() if c["id"] == args.get("id")), None)
    if not c: raise ValueError("no such pending change")
    if c["op"] == "delete": raise ValueError("a delete has no value: discard it instead")
    lab.store.stage(c["path"], args.get("value"), label=c["label"]); return "updated"


@act("changes_apply", "Apply changes", "Write pending changes (all, or `ids`) to the config file.", {"ids": "list?"}, group="changes")
def changes_apply(lab, ctrl, args):
    n = lab.store.apply(args.get("ids")); return f"{n} change(s) saved to {lab.store.view()['file']}"


@act("changes_discard", "Discard changes", "Drop pending changes (all, or `ids`).", {"ids": "list?"}, group="changes")
def changes_discard(lab, ctrl, args):
    lab.store.discard(args.get("ids")); return "discarded"


# ------------------------------------------------------------------ cameras
@act("cameras_swap", "Swap cameras", "Swap two cameras' devices (default: the first two) when a replug flipped "
     "two identical cameras. Saved to config/settings.json; both reopen in a few seconds.", {"a": "string?", "b": "string?"},
     group="cameras")
def cameras_swap(lab, ctrl, args):
    from .. import hardware, settings  # lazy: the daemon's camera bookkeeping
    names = list(settings.CAMS)
    a, b = args.get("a") or names[0], args.get("b") or (names[1] if len(names) > 1 else "")
    hardware.swap_cameras(a, b); return f"swapping {a} <-> {b}: both cameras reopen in a few seconds"


# ------------------------------------------------------------------ video
@act("video_start", "Record video", "Start recording the cameras into the lab's video dir.", {"name": "string?"}, group="video")
def video_start(lab, ctrl, args):
    v = lab.cfg().get("video") or {}
    stem = ctrl.video.start(safe_name(args.get("name") or lab.name, 40, lab.name), directory=lab.video_dir(), raw=bool(v.get("raw")))
    return {"message": f"recording -> {lab.video_dir() / stem}", "dir": str(lab.video_dir())}


@act("video_stop", "Stop video", "Stop the recording.", group="video")
def video_stop(lab, ctrl, args):
    name = ctrl.video.stop(); return f"saved {name}" if name else "was not recording"


@act("video_delete", "Delete video", "Delete a clip from the lab's video dir.", {"file": "string"},
     confirm="Delete this clip from disk? This cannot be undone.", group="video")
def video_delete(lab, ctrl, args):
    f = lab.video_dir() / Path(str(args.get("file", ""))).name
    if not f.is_file() or f.suffix not in (".mp4", ".avi"): raise ValueError("no such clip")
    f.unlink(); log(f"LAB video deleted {f}"); return f"deleted {f.name}"


BUILTIN = dict(_REG.actions)
