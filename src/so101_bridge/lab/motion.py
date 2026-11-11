"""Lab moves. Every one of them is a request to the control loop, so all of its guards still apply.

The loop holds each joint to its soft limit (calibration sweep minus a margin). A lab may reach further
for a pose the operator has *taught* — that is the ``allow`` the loop already understands, capped by the
motor's own sweep: it spends the calibration margin and nothing more, writes no register and no shared
config, and lives only on the command that carries it. The lab's ``envelope`` is that allowance, saved.
Nothing here ever sends ``src="floor_ok"``: the table guard is never bypassed from a lab.
"""

import threading

from ..poses import P, load_rest
from ..settings import ARM, JOINTS
from ..util import log

PAD_DEG = 0.5          # a taught pose carries its own allowance plus this much
MID = {"shoulder_lift": 0.0, "elbow_flex": 68.5, "wrist_flex": 22.0}    # the high, folded pose rest_path goes through


def allow_for(envelope, pose=None, pad=PAD_DEG):
    """Per-joint ``allow`` for a move: the lab envelope, widened to include ``pose`` itself (± pad)."""
    out = {j: [float(v[0]), float(v[1])] for j, v in (envelope or {}).items() if j in ARM and isinstance(v, (list, tuple)) and len(v) == 2}
    for j, v in (pose or {}).items():
        if j not in ARM: continue
        lo, hi = out.get(j, [float(v) - pad, float(v) + pad])
        out[j] = [min(lo, float(v) - pad), max(hi, float(v) + pad)]
    return out


def clean_pose(pose, with_gripper=True):
    """Keep known joints with numeric values (the gripper only when asked)."""
    keep = JOINTS if with_gripper else ARM
    return {j: float(v) for j, v in (pose or {}).items() if j in keep and isinstance(v, (int, float))}


def busy(ctrl):
    return bool(ctrl.routine_thread and ctrl.routine_thread.is_alive())


def run_move(ctrl, label, fn):
    """Run ``fn() -> bool`` in the controller's single routine slot, so STOP/HOLD/ABORT interrupt it."""
    if busy(ctrl):
        raise RuntimeError(f"busy: {ctrl.routine_status} (ABORT first)")
    ctrl.routine_abort.clear(); ctrl.routine_status = label

    def run():
        try: ok = fn()
        except Exception as e:
            log(f"LAB move error: {e}"); ok = False
        ctrl.routine_status = f"{label}: {'done' if ok else 'interrupted'}"; log(ctrl.routine_status)
    ctrl.routine_thread = threading.Thread(target=run, daemon=True, name="lab-move"); ctrl.routine_thread.start()
    return True


def jog(ctrl, joint, delta, speed, envelope):
    """A relative step on one joint — straight to the loop (step cap, limits, table guard all apply)."""
    if joint not in JOINTS: raise ValueError(f"unknown joint {joint!r}")
    ctrl.request({"action": "goal", "goal": {joint: float(delta)}, "relative": True, "speed": float(speed), "src": "web",
                  **({"allow": allow_for(envelope)} if joint in ARM and envelope else {})})


def goto_pose(ctrl, pose, speed, envelope, label="goto"):
    """Reach a full or partial pose from anywhere in <=10° steps, the pose carrying its own allowance."""
    pose = clean_pose(pose)
    if not pose: raise ValueError("empty pose")
    return run_move(ctrl, label, lambda: ctrl.goto_far(pose, float(speed), allow=allow_for(envelope, pose)))


def go_rest(ctrl, rest, speed, envelope):
    """This lab's rest: up to the high folded pose above it, then down onto it. No lab rest = the global REST."""
    if not rest:
        keep_grip = getattr(ctrl, "tool", None) is not None          # a mounted tool: the jaws stay on it
        return run_move(ctrl, "rest (global)", lambda: ctrl.rest_path(gripper=None if keep_grip else "rest"))
    rest = clean_pose(rest)
    mid = {**P(pan=rest.get("shoulder_pan", load_rest()["shoulder_pan"])), **MID}

    def fn():
        allow = allow_for(envelope, rest)
        return ctrl.goto_far(mid, float(speed), allow=allow) and ctrl.goto_far(rest, float(speed), allow=allow)
    return run_move(ctrl, "rest (lab)", fn)
