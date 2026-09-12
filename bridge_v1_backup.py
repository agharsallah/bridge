#!/usr/bin/env python
"""Safe file-based control bridge for the SO-101 follower arm.

Run from this directory in a macOS Terminal:
    uv run python bridge.py

Claude writes goal commands as JSON files into ./cmd/ ; this daemon
owns the arm, moves it SLOWLY toward the goal (rate-limited interpolation +
motor-side velocity/acceleration caps + soft joint limits), and continuously
writes ./state.json plus camera snapshots top.jpg / wrist.jpg.

Safety:
  * joints never commanded outside calibration range (minus margin) or the
    stricter limits in limits.json
  * per-tick step capped (deg/s), motor Goal_Velocity + Acceleration capped
  * touch ./ESTOP  -> arm freezes at its present position
  * Ctrl-C -> arm holds position (torque stays on); send {"action":"release"}
    only when the arm rests on the table to switch torque off.
"""
import json, os, shutil, signal, sys, time, traceback
from pathlib import Path

import cv2
import numpy as np

from lerobot.cameras.opencv import OpenCVCameraConfig
from lerobot.robots.so_follower import SOFollower, SOFollowerRobotConfig

ROOT = Path(__file__).resolve().parent
CMD, DONE = ROOT / "cmd", ROOT / "done"
STATE, LOG, ESTOP = ROOT / "state.json", ROOT / "bridge.log", ROOT / "ESTOP"
LIMITS_FILE = ROOT / "limits.json"

PORT = "/dev/cu.usbmodem5A460836731"
ROBOT_ID = "my_follower"
CAMS = {"top": 0, "wrist": 1}
LOOP_HZ = 30
SNAP_EVERY = 0.5           # seconds between snapshot writes
STATE_EVERY = 0.2
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
DEFAULT_SPEED = {j: 12.0 for j in JOINTS}   # deg/s (gripper: %/s)
DEFAULT_SPEED["gripper"] = 25.0
MAX_SPEED = 30.0                              # hard cap on requested speed
CAL_MARGIN_DEG = 3.0                          # stay away from calibration ends
MOTOR_GOAL_VELOCITY = 300                     # steps/s  (~26 deg/s) motor-side cap
LOAD_LIMIT = {"shoulder_pan": 300, "shoulder_lift": 400, "elbow_flex": 450, "wrist_flex": 300, "wrist_roll": 300}  # of 1000
MAX_STEP_DEG = 12.0                           # refuse goals further than this from present (per joint)
STALL_DEG = 4.0                               # tracking error that counts as resistance
STALL_SEC = 0.7                               # ...if it persists this long
MOTOR_ACCEL = 15                              # small = gentle ramps


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def atomic_write(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def load_user_limits():
    if LIMITS_FILE.is_file():
        try:
            return json.loads(LIMITS_FILE.read_text())
        except Exception as e:
            log(f"limits.json unreadable: {e}")
    return {}


def main():
    cams = {n: OpenCVCameraConfig(index_or_path=i, fps=30, width=1920, height=1080) for n, i in CAMS.items()}
    cfg = SOFollowerRobotConfig(port=PORT, id=ROBOT_ID, cameras=cams,
                                disable_torque_on_disconnect=False,
                                max_relative_target=5.0)   # 2nd guard: <=5 units per tick
    class QuietFollower(SOFollower):
        """Skip lerobot's configure() on connect: it briefly disables torque (arm could sag / drop a held
        object). Motors keep their RAM settings while powered; we re-apply gains explicitly below."""
        def configure(self) -> None:
            pass
    robot = QuietFollower(cfg)
    for attempt in range(1, 6):
        try:
            log(f"connecting (attempt {attempt})...")
            robot.connect(calibrate=False)
            robot.bus.enable_torque(num_retry=5)   # make sure torque really is on
            break
        except ConnectionError as e:
            log(f"bus hiccup: {e}")
            try:
                robot.bus.disconnect(disable_torque=False)
            except Exception:
                pass
            for cam in robot.cameras.values():
                try:
                    if cam.is_connected: cam.disconnect()
                except Exception:
                    pass
            if attempt == 5:
                raise
            time.sleep(1.0)
    if not robot.is_calibrated:
        log("Motors not calibrated with my_follower.json - writing calibration to motors")
        robot.bus.write_calibration(robot.calibration)

    # Motor-side gentleness
    robot.bus.write("Max_Torque_Limit", "gripper", 500, num_retry=5)
    robot.bus.write("Protection_Current", "gripper", 250, num_retry=5)
    for m in JOINTS:
        if m != "gripper":   # lerobot's P=16 is too weak to lift against gravity with small smooth steps
            robot.bus.write("P_Coefficient", m, 48 if m == "elbow_flex" else 32, num_retry=5)
        robot.bus.write("Acceleration", m, MOTOR_ACCEL, normalize=False, num_retry=5)
        robot.bus.write("Goal_Velocity", m, MOTOR_GOAL_VELOCITY, normalize=False, num_retry=5)

    # Soft limits from calibration (degrees mode is symmetric about mid of range)
    cal_limits = {}
    for m in JOINTS:
        c = robot.calibration[m]
        half = (c.range_max - c.range_min) / 2 * 360 / 4095
        if m == "gripper":
            cal_limits[m] = [0.0, 100.0]
        else:
            cal_limits[m] = [-half + CAL_MARGIN_DEG, half - CAL_MARGIN_DEG]
    user_limits = load_user_limits()
    limits = {}
    for m in JOINTS:
        lo, hi = cal_limits[m]
        if m in user_limits:
            lo, hi = max(lo, user_limits[m][0]), min(hi, user_limits[m][1])
        limits[m] = [lo, hi]
    log(f"limits: {json.dumps({k: [round(a,1), round(b,1)] for k,(a,b) in limits.items()})}")

    present = {k.removesuffix(".pos"): v for k, v in robot.bus.sync_read("Present_Position").items()}
    cmd = dict(present)          # what we send this tick
    goal = dict(present)         # where we are heading
    speed = dict(DEFAULT_SPEED)
    torque_on = True
    mode = "hold"
    last_cmd_name = None
    last_snap = last_state = 0.0
    seq = 0
    stall_since = None
    last_load_warn = 0.0

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    log(f"ready. present={ {k: round(v,1) for k,v in present.items()} }")

    dt = 1.0 / LOOP_HZ
    while not stop["flag"]:
        t0 = time.perf_counter()
        try:
            obs = robot.get_observation()
            present = {j: float(obs[f"{j}.pos"]) for j in JOINTS}

            # --- E-STOP
            if ESTOP.exists():
                if mode != "estop":
                    goal = dict(present); cmd = dict(present); mode = "estop"
                    log("ESTOP present -> frozen. Delete the ESTOP file to resume.")
            elif mode == "estop":
                mode = "hold"; goal = dict(present); cmd = dict(present); log("ESTOP cleared")

            # --- commands
            for f in sorted(CMD.glob("*.json")):
                try:
                    c = json.loads(f.read_text())
                except Exception as e:
                    log(f"bad cmd {f.name}: {e}"); shutil.move(f, DONE / f.name); continue
                shutil.move(f, DONE / f.name)
                last_cmd_name = f.name
                if mode == "estop":
                    log(f"ignored {f.name} (estop)"); continue
                act = c.get("action", "goal")
                if act == "hold":
                    goal = dict(present); cmd = dict(present); mode = "hold"; log("hold")
                elif act == "release":
                    goal = dict(present); cmd = dict(present); robot.bus.disable_torque(); torque_on = False
                    mode = "released"; log("torque RELEASED")
                elif act == "engage":
                    robot.bus.enable_torque(); torque_on = True; goal = dict(present); cmd = dict(present)
                    mode = "hold"; log("torque engaged")
                elif act == "goal":
                    if not torque_on:
                        robot.bus.enable_torque(); torque_on = True; cmd = dict(present)
                    g = c.get("goal", {})
                    rel = c.get("relative", False)
                    for j, v in g.items():
                        if j not in JOINTS: log(f"unknown joint {j}"); continue
                        target = present[j] + float(v) if rel else float(v)
                        if j != "gripper" and abs(target - present[j]) > MAX_STEP_DEG:
                            target = present[j] + MAX_STEP_DEG * (1 if target > present[j] else -1)
                            log(f"STEP CAP {j}: limited to {MAX_STEP_DEG} deg from present")
                        lo, hi = limits[j]
                        clamped = min(hi, max(lo, target))
                        if abs(clamped - target) > 1e-6:
                            log(f"CLAMP {j}: {target:.1f} -> {clamped:.1f} (limits {lo:.1f}..{hi:.1f})")
                        goal[j] = clamped
                    s = c.get("speed")
                    if s is not None:
                        if isinstance(s, dict):
                            for j, v in s.items(): speed[j] = min(MAX_SPEED, max(0.5, float(v)))
                        else:
                            for j in JOINTS: speed[j] = min(MAX_SPEED, max(0.5, float(s)))
                    mode = "moving"
                    log(f"goal {f.name}: { {k: round(v,1) for k,v in goal.items()} } speed={ {k: round(v,1) for k,v in speed.items()} }")
                elif act == "write":
                    allowed = {"Max_Position_Limit", "Min_Position_Limit", "P_Coefficient", "D_Coefficient",
                               "Acceleration", "Goal_Velocity", "Torque_Enable", "Lock"}
                    reg, mot, val = c.get("reg"), c.get("motor"), c.get("value")
                    if reg not in allowed or mot not in JOINTS:
                        log(f"write refused: {reg} {mot}")
                    else:
                        robot.bus.write(reg, mot, int(val), normalize=False, num_retry=5)
                        log(f"wrote {reg}[{mot}] = {val}; readback = {robot.bus.read(reg, mot, normalize=False, num_retry=3)}")
                elif act == "diag":
                    rows = {}
                    for reg in c.get("regs") or ["Torque_Enable", "Lock", "Present_Position", "Goal_Position", "Present_Load",
                                "Present_Current", "Present_Temperature", "Present_Voltage", "Moving", "Status",
                                "Max_Torque_Limit", "Acceleration", "Goal_Velocity", "Operating_Mode", "Min_Position_Limit", "Max_Position_Limit"]:
                        try:
                            rows[reg] = robot.bus.sync_read(reg, normalize=False, num_retry=3)
                        except Exception as e:
                            rows[reg] = f"ERR {e}"
                    log("DIAG:\n" + "\n".join(f"  {k}: {v}" for k, v in rows.items()))
                else:
                    log(f"unknown action {act}")

            # --- load guard: pressing against something shows up as motor load even when positions track
            if torque_on and mode in ("moving", "reached", "hold"):
                try:
                    loads = robot.bus.sync_read("Present_Load", normalize=False, num_retry=2)
                except Exception:
                    loads = {}
                hot = {j: v for j, v in loads.items() if j != "gripper" and abs(v) > LOAD_LIMIT[j]}
                if hot and mode == "moving":
                    goal = dict(present); cmd = dict(present); mode = "stalled"
                    log(f"LOAD GUARD: {hot} -> frozen at present. Send a new goal (away from the contact) to continue.")
                elif hot and (now_l := time.time()) - last_load_warn > 2.0:
                    last_load_warn = now_l
                    log(f"load warning while holding: {hot}")
            # --- stall / contact guard: never push through resistance
            if torque_on and mode == "moving":
                lag = {j: abs(cmd[j] - present[j]) for j in JOINTS if j != "gripper"}
                worst = max(lag, key=lag.get)
                if lag[worst] > STALL_DEG:
                    stall_since = stall_since or time.time()
                    if time.time() - stall_since > STALL_SEC:
                        goal = dict(present); cmd = dict(present); mode = "stalled"
                        log(f"STALL on {worst} (lag {lag[worst]:.1f} deg) -> frozen at present. Send a new goal to continue.")
                else:
                    stall_since = None
            else:
                stall_since = None

            # --- rate-limited interpolation toward goal
            if torque_on and mode in ("moving", "hold"):
                moving = False
                for j in JOINTS:
                    step = speed[j] * dt
                    d = goal[j] - cmd[j]
                    if abs(d) > step:
                        cmd[j] += step * (1 if d > 0 else -1); moving = True
                    else:
                        cmd[j] = goal[j]
                    # NOTE: limits are applied to goals when received, never to a hold pose,
                    # so a rest pose slightly outside the margin is left untouched.
                robot.send_action({f"{j}.pos": cmd[j] for j in JOINTS})
                if mode == "moving" and not moving and all(abs(present[j] - goal[j]) < 2.0 for j in JOINTS):
                    mode = "reached"; log(f"reached { {k: round(v,1) for k,v in present.items()} }")
            elif torque_on and mode in ("reached", "stalled"):
                robot.send_action({f"{j}.pos": cmd[j] for j in JOINTS})

            now = time.time()
            if now - last_snap > SNAP_EVERY:
                for n in CAMS:
                    img = obs[n]
                    small = cv2.resize(img, (960, 540), interpolation=cv2.INTER_AREA)
                    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(small, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ok: atomic_write(ROOT / f"{n}.jpg", buf.tobytes())
                last_snap = now
            if now - last_state > STATE_EVERY:
                seq += 1
                st = {"seq": seq, "time": time.strftime("%H:%M:%S"), "mode": mode, "torque_on": torque_on,
                      "present": {k: round(v, 2) for k, v in present.items()},
                      "goal": {k: round(v, 2) for k, v in goal.items()},
                      "cmd": {k: round(v, 2) for k, v in cmd.items()},
                      "speed": speed, "limits": {k: [round(a, 1), round(b, 1)] for k, (a, b) in limits.items()},
                      "last_cmd": last_cmd_name, "estop": ESTOP.exists()}
                atomic_write(STATE, json.dumps(st, indent=1).encode())
                last_state = now
        except Exception:
            log("ERROR in loop:\n" + traceback.format_exc())
            time.sleep(0.5)
        el = time.perf_counter() - t0
        if el < dt: time.sleep(dt - el)

    log("Ctrl-C: holding position, torque stays ON. Disconnecting bus/cameras.")
    try:
        robot.disconnect()
    except Exception as e:
        log(f"disconnect error: {e}")


if __name__ == "__main__":
    main()
