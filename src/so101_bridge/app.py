"""Entry point: bring the arm up, serve the dashboard, run the 30 Hz control loop.

The loop is the only place that talks to the motor bus. Every command — a JSON file in var/cmd/,
a dashboard button, a step of the AUTO PICK routine — arrives through the controller and passes
the same chain of guards: step cap, soft limits, floor model, load guard, stall guard, rate limit.
"""

import json
import shutil
import signal
import threading
import time
import traceback

from . import dashboard, hardware, paths, vision
from .controller import Controller
from .floor import FLOOR_FREEZE, FLOOR_MARGIN
from .paths import CMD_DIR, DONE_DIR, ESTOP, SNAPSHOT, STATE_FILE
from .poses import load_rest
from .settings import (
    ARM,
    CAMS,
    DEFAULT_SPEED,
    JOINTS,
    LOAD_LIMIT,
    LOOP_HZ,
    MAX_SPEED,
    MAX_STEP_DEG,
    REACH_TOL,
    STALL_DEG,
    STALL_SEC,
    STATE_EVERY,
    VISION_EVERY,
)
from .util import atomic_write, log


def main():
    paths.ensure_dirs()
    log(f"rest pose: {load_rest()}")
    ctrl = Controller()
    robot = hardware.connect()
    hardware.configure_motors(robot)
    limits = hardware.joint_limits(robot)
    dashboard.serve(ctrl)
    try:
        control_loop(robot, ctrl, limits)
    finally:
        ctrl.abort_auto("shutdown")
        log("shutdown: holding position, torque stays ON. Disconnecting bus/cameras.")
        try: robot.disconnect()
        except Exception as e: log(f"disconnect error: {e}")


def control_loop(robot, ctrl: Controller, limits, max_iters=None):
    """Run until SIGINT (or, in tests, until ``max_iters`` cycles have run)."""
    present = {k.removesuffix(".pos"): v for k, v in robot.bus.sync_read("Present_Position").items()}
    cmd, goal, speed = dict(present), dict(present), dict(DEFAULT_SPEED)
    torque_on, mode, last_cmd_name = True, "hold", None
    last_state, seq, loop_i = 0.0, 0, 0
    stall_since, last_load_warn = None, 0.0
    stop = {"flag": False}
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    log(f"ready. present={ {k: round(v, 1) for k, v in present.items()} }")

    def apply_goal(c):
        nonlocal goal, cmd, mode, torque_on
        if not torque_on:
            robot.bus.enable_torque(num_retry=5); torque_on = True; cmd = dict(present)
        for j, v in c.get("goal", {}).items():
            if j not in JOINTS: log(f"unknown joint {j}"); continue
            target = present[j] + float(v) if c.get("relative") else float(v)
            if j != "gripper" and abs(target - present[j]) > MAX_STEP_DEG:
                target = present[j] + MAX_STEP_DEG * (1 if target > present[j] else -1)
                log(f"STEP CAP {j}: limited to {MAX_STEP_DEG} deg from present")
            lo, hi = limits[j]; clamped = min(hi, max(lo, target))
            if abs(clamped - target) > 1e-6: log(f"CLAMP {j}: {target:.1f} -> {clamped:.1f}")
            goal[j] = clamped
        # floor guard: shrink the step until the predicted fingertip height stays above the margin
        if ctrl.floor.params is not None and c.get("src") != "floor_ok":
            for frac in (1.0, 0.75, 0.5, 0.25, 0.0):
                trial = {j: present[j] + frac * (goal[j] - present[j]) for j in JOINTS}
                zt = ctrl.floor.z(trial)
                if zt is not None and zt >= FLOOR_MARGIN: break
            if frac < 1.0:
                log(f"FLOOR GUARD: goal would put the tips at {ctrl.floor.z(goal) * 100:.1f} cm -> step scaled to {frac:.2f}")
                for j in ARM: goal[j] = trial[j]
        s = c.get("speed")
        if s is not None:
            for j in JOINTS:
                v = s.get(j) if isinstance(s, dict) else s
                if v is not None: speed[j] = min(MAX_SPEED, max(0.5, float(v)))
        mode = "moving"
        ctrl.rec_write({"event": "goal", "src": c.get("src", "file"), "goal": {k: round(v, 2) for k, v in goal.items()}, "speed": s})
        log(f"goal[{c.get('src', 'file')}]: { {k: round(v, 1) for k, v in c.get('goal', {}).items()} } "
            f"rel={bool(c.get('relative'))} speed={s}")

    dt = 1.0 / LOOP_HZ
    while not stop["flag"] and (max_iters is None or loop_i < max_iters):
        t0 = time.perf_counter(); loop_i += 1
        try:
            obs = robot.get_observation()
            present = {j: float(obs[f"{j}.pos"]) for j in JOINTS}

            # ---- E-STOP
            if ESTOP.exists():
                if mode != "estop":
                    goal, cmd, mode = dict(present), dict(present), "estop"; ctrl.abort_auto("ESTOP file")
                    log("ESTOP -> frozen. RESUME (or delete the ESTOP file) to continue.")
            elif mode == "estop":
                mode, goal, cmd = "hold", dict(present), dict(present); log("ESTOP cleared")

            # ---- gather commands (files + dashboard + routine)
            cmds = []
            for f in sorted(CMD_DIR.glob("*.json")):
                try: cmds.append(json.loads(f.read_text()))
                except Exception as e: log(f"bad cmd {f.name}: {e}")
                shutil.move(f, DONE_DIR / f.name); last_cmd_name = f.name
            with ctrl.lock:
                cmds += ctrl.pending; ctrl.pending = []
            for c in cmds:
                act = c.get("action", "goal")
                if mode == "estop" and act not in ("diag",):
                    log(f"ignored {act} (estop)"); continue
                if act == "goal": apply_goal(c)
                elif act == "hold": goal, cmd, mode = dict(present), dict(present), "hold"; log("hold")
                elif act == "release":
                    goal, cmd = dict(present), dict(present); robot.bus.disable_torque(); torque_on = False
                    mode = "released"; log("torque RELEASED"); ctrl.rec_write({"event": "release"})
                elif act == "engage":
                    robot.bus.enable_torque(num_retry=5); torque_on = True; goal, cmd, mode = dict(present), dict(present), "hold"
                    log("torque engaged (holding current pose)"); ctrl.rec_write({"event": "engage"})
                elif act == "auto": ctrl.start_auto()
                elif act == "rest": ctrl.go_rest()
                elif act == "write":
                    allowed = {"Max_Position_Limit", "Min_Position_Limit", "P_Coefficient", "D_Coefficient",
                               "Acceleration", "Goal_Velocity", "Torque_Enable", "Lock"}
                    reg, mot, val = c.get("reg"), c.get("motor"), c.get("value")
                    if reg in allowed and mot in JOINTS:
                        robot.bus.write(reg, mot, int(val), normalize=False, num_retry=5)
                        log(f"wrote {reg}[{mot}] = {val}; readback = {robot.bus.read(reg, mot, normalize=False, num_retry=3)}")
                    else: log(f"write refused: {reg} {mot}")
                elif act == "diag":
                    rows = {}
                    for reg in c.get("regs") or ["Torque_Enable", "Present_Position", "Goal_Position", "Present_Load",
                                                 "Present_Current", "Present_Temperature", "Status"]:
                        try: rows[reg] = robot.bus.sync_read(reg, normalize=False, num_retry=3)
                        except Exception as e: rows[reg] = f"ERR {e}"
                    log("DIAG:\n" + "\n".join(f"  {k}: {v}" for k, v in rows.items()))
                else: log(f"unknown action {act}")

            # ---- load guard
            if torque_on and mode in ("moving", "reached", "hold"):
                try: loads = robot.bus.sync_read("Present_Load", normalize=False, num_retry=2)
                except Exception: loads = {}
                hot = {j: v for j, v in loads.items() if j != "gripper" and abs(v) > LOAD_LIMIT[j]}
                if hot and mode == "moving":
                    goal, cmd, mode = dict(present), dict(present), "stalled"; ctrl.abort_auto("load guard")
                    log(f"LOAD GUARD: {hot} -> frozen at present.")
                elif hot and time.time() - last_load_warn > 2.0:
                    last_load_warn = time.time(); log(f"load warning while holding: {hot}")
            # ---- floor guard (runtime): predicted tip height below the table plane -> freeze
            zp = ctrl.floor.z(present)
            if zp is not None and torque_on and mode == "moving" and zp < FLOOR_FREEZE:
                goal, cmd, mode = dict(present), dict(present), "stalled"; ctrl.abort_auto("floor guard")
                log(f"FLOOR FREEZE: predicted tip height {zp * 100:.1f} cm")
            # ---- stall guard
            if torque_on and mode == "moving":
                lag = {j: abs(cmd[j] - present[j]) for j in ARM}; worst = max(lag, key=lag.get)
                if lag[worst] > STALL_DEG:
                    stall_since = stall_since or time.time()
                    if time.time() - stall_since > STALL_SEC:
                        goal, cmd, mode = dict(present), dict(present), "stalled"; ctrl.abort_auto("stall guard")
                        log(f"STALL on {worst} (lag {lag[worst]:.1f}) -> frozen at present.")
                else: stall_since = None
            else: stall_since = None

            # ---- rate-limited interpolation (limits apply to goals, never to a hold pose)
            if torque_on and mode in ("moving", "hold"):
                moving = False
                for j in JOINTS:
                    step = speed[j] * dt; d = goal[j] - cmd[j]
                    if abs(d) > step: cmd[j] += step * (1 if d > 0 else -1); moving = True
                    else: cmd[j] = goal[j]
                robot.send_action({f"{j}.pos": cmd[j] for j in JOINTS})
                if mode == "moving" and not moving and all(abs(present[j] - goal[j]) < REACH_TOL for j in ARM):
                    mode = "reached"; log(f"reached { {k: round(v, 1) for k, v in present.items()} }")
            elif torque_on and mode in ("reached", "stalled"):
                robot.send_action({f"{j}.pos": cmd[j] for j in JOINTS})

            # ---- vision + streams
            if loop_i % VISION_EVERY == 0:
                blobs = {}
                for n in CAMS:
                    jpeg, b = vision.render(obs[n], n, mode, ctrl.routine_status,
                                            show_servo_guides=(n == "wrist" and ctrl.routine_status == "running"))
                    if b: blobs[n] = b
                    if jpeg:
                        with ctrl.lock: ctrl.jpeg[n] = jpeg
                        if loop_i % (VISION_EVERY * 5) == 0: atomic_write(SNAPSHOT[n], jpeg)
                with ctrl.lock: ctrl.blob = blobs

            if loop_i % 3 == 0:            # ~10 Hz trajectory log while recording
                ctrl.rec_write({"time": time.strftime("%H:%M:%S"), "mode": mode, "torque": torque_on,
                                "pos": {k: round(v, 2) for k, v in present.items()}})
            now = time.time()
            if now - last_state > STATE_EVERY:
                seq += 1
                st = {"seq": seq, "time": time.strftime("%H:%M:%S"), "mode": mode, "torque_on": torque_on,
                      "present": {k: round(v, 2) for k, v in present.items()},
                      "goal": {k: round(v, 2) for k, v in goal.items()}, "cmd": {k: round(v, 2) for k, v in cmd.items()},
                      "speed": speed, "limits": {k: [round(a, 1), round(b, 1)] for k, (a, b) in limits.items()},
                      "last_cmd": last_cmd_name, "estop": ESTOP.exists(),
                      "tip_z_cm": round(zp * 100, 1) if zp is not None else None}
                with ctrl.lock: ctrl.state = st
                atomic_write(STATE_FILE, json.dumps(st, indent=1).encode()); last_state = now
        except Exception:
            log("ERROR in loop:\n" + traceback.format_exc()); time.sleep(0.5)
        el = time.perf_counter() - t0
        if el < dt: time.sleep(dt - el)
