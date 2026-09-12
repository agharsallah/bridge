"""AUTO PICK: the autonomous pick-and-place routine.

It replays the proven joint waypoints while the wrist camera servos the pan so the brick sits
between the jaws, stops the descent when the floor model says the fingertips are at brick height,
verifies the grip, carries to the confirmed drop pose, releases and returns to rest.

Every move still goes through the controller, so all the limits and guards of the control loop
apply — this module can only ask for goals, never bypass them.
"""

import time
import traceback

from .floor import GRASP_Z
from .poses import RETREAT, TRANSIT, UNFOLD, load_rest
from .settings import (
    CY_TARGET,
    LIFT_MAX,
    LIFT_STEP,
    REACH_GAIN_DEG_PER_PX,
    SERVO_GAIN_DEG_PER_PX,
    SERVO_MAX_ITERS,
    SERVO_TOL,
    SERVO_X_BOTTOM,
    SERVO_X_TOP,
    WRIST_PER_LIFT,
)
from .util import log


def _servo_pan(ctrl, target_x, tries=3):
    for _ in range(tries):
        st, _ = ctrl.snapshot(); b = ctrl.see("wrist")
        if not b:
            return None
        err = b["cx"] - target_x
        if abs(err) <= SERVO_TOL:
            return b
        delta = max(-4.0, min(4.0, SERVO_GAIN_DEG_PER_PX * err))
        log(f"AUTO servo: brick x={b['cx']} target={target_x} -> pan {delta:+.1f}")
        if not ctrl.goto({"shoulder_pan": st["present"]["shoulder_pan"] + delta}, speed=3.0):
            return None
        time.sleep(0.4)
    return ctrl.see("wrist")


def run(ctrl):
    def fail(msg):
        log(f"AUTO FAILED: {msg}"); ctrl.routine_status = f"failed: {msg}"
        ctrl.request({"action": "hold", "src": "auto"})
    try:
        ctrl.routine_status = "running"; log("AUTO: start")
        if ctrl.floor.params is None:
            return fail("floor model not taught yet — mark >= 4 table-contact poses first")
        st, _ = ctrl.snapshot()
        if not ctrl.see("top", 3.0) and not ctrl.see("wrist", 1.0):
            return fail("no yellow brick visible in either camera")
        # 1) to READY (looking down, gripper open): the proven unfold path from rest, or capped steps from anywhere
        if st["present"]["shoulder_lift"] < -35:
            for wp in UNFOLD:
                if not ctrl.goto(wp, 5.0): return fail("unfold interrupted")
        else:
            log("AUTO: not at rest -> moving to READY in capped steps")
            if not ctrl.goto_far({**UNFOLD[-1], "shoulder_pan": load_rest()["shoulder_pan"]}, 4.0): return fail("move to READY interrupted")
        time.sleep(0.6)
        # 2+3) servo down to grasp height. Height comes from the FLOOR MODEL (fingertip z), the cameras only
        #      steer: pan <- brick x, elbow <- brick y (unfold = forward). A sudden size collapse is a bad
        #      detection (washed-out colour), never "still far".
        b = ctrl._servo_pan(SERVO_X_TOP)
        if not b: return fail("brick not seen by wrist camera at READY")
        last_size, bad = b["long"], 0; grasped_height = False
        for it in range(SERVO_MAX_ITERS):
            b = ctrl.see("wrist", 2.0)
            if not b: return fail(f"brick lost (servo iteration {it})")
            if b["long"] < 0.6 * last_size:
                bad += 1; log(f"AUTO: suspicious detection (size {b['long']} vs {last_size}) — retrying")
                if bad >= 4: return fail("detector unreliable (brick colour washed out?)")
                time.sleep(0.3); continue
            bad = 0; last_size = max(last_size, b["long"])
            st, _ = ctrl.snapshot(); pr = st["present"]; z = ctrl.floor.z(pr)
            goal = {}
            ex = b["cx"] - SERVO_X_BOTTOM
            if abs(ex) > SERVO_TOL:
                gain = SERVO_GAIN_DEG_PER_PX if z > 0.08 else SERVO_GAIN_DEG_PER_PX * 0.6
                goal["shoulder_pan"] = pr["shoulder_pan"] + max(-4.0, min(4.0, gain * ex))
            ey = CY_TARGET - b["cy"]
            if abs(ey) > 60:
                goal["elbow_flex"] = pr["elbow_flex"] + max(-6.0, min(6.0, -REACH_GAIN_DEG_PER_PX * ey))
            if z <= GRASP_Z + 0.004 and abs(ex) <= SERVO_TOL and abs(ey) <= 90:
                grasped_height = True
                log(f"AUTO: at grasp height (tip z {z * 100:.1f} cm), brick size {b['long']}px"); break
            if z > GRASP_Z:
                # shoulder forward = down; take a step sized to the remaining height (~2.5 mm per degree near the table)
                dl = min(LIFT_STEP, max(1.0, (z - GRASP_Z) / 0.0025), LIFT_MAX - pr["shoulder_lift"])
                if dl > 0.5:
                    goal["shoulder_lift"] = pr["shoulder_lift"] + dl
                    goal["wrist_flex"] = pr["wrist_flex"] - WRIST_PER_LIFT * dl
                elif not goal:
                    return fail("shoulder at its forward limit before reaching grasp height")
            log(f"AUTO servo {it}: brick x={b['cx']} y={b['cy']} size={b['long']} tip z={z * 100:.1f}cm -> "
                f"{ {k: round(v, 1) for k, v in goal.items()} }")
            if not goal:
                time.sleep(0.3); continue
            if not ctrl.goto(goal, 4.0): return fail(f"servo move interrupted (iteration {it})")
            time.sleep(0.4)
        if not grasped_height:
            return fail("could not converge to grasp height")
        if last_size < 280:
            return fail(f"at grasp height but the brick looks too small ({last_size}px) — not between the jaws?")
        time.sleep(0.5)
        # 4) grasp and verify
        g = ctrl.gripper_to(10, 15.0)
        if g is None: return fail("grasp interrupted")
        if g < 14:
            log(f"AUTO: gripper closed to {g:.1f} -> nothing grasped; opening")
            ctrl.gripper_to(60, 20.0)
            return fail("missed the brick")
        log(f"AUTO: holding (gripper stalled at {g:.1f})")
        # 5) lift (relative: shoulder back = up), then transit to the confirmed drop pose in capped steps
        for _ in range(2):
            st, _ = ctrl.snapshot(); pr = st["present"]
            up = {"shoulder_lift": pr["shoulder_lift"] - 10.0, "wrist_flex": pr["wrist_flex"] + 5.0}
            if not ctrl.goto(up, 4.0): return fail("lift interrupted")
        if not ctrl.goto_far({**TRANSIT[-1]}, 4.0): return fail("carry interrupted")
        time.sleep(1.0)                                                # last chance to STOP before release
        if ctrl.routine_abort.is_set(): return fail("aborted before release")
        ctrl.gripper_to(60, 25.0); time.sleep(0.8); log("AUTO: released")
        # 6) retreat to the configured rest pose
        for wp in RETREAT[:-1]:
            if not ctrl.goto(wp, 5.0): return fail("retreat interrupted")
        if not ctrl.goto_far(load_rest(), 4.0): return fail("final rest move interrupted")
        ctrl.routine_status = "done"; log("AUTO: done")
    except Exception:
        log("AUTO exception:\n" + traceback.format_exc()); ctrl.routine_status = "error"
        ctrl.request({"action": "hold", "src": "auto"})
