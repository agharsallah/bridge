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
from pathlib import Path

from . import dashboard, hardware, paths, vision
from .chess import kinematics as chess_kin
from .chess import physical as chess_phys
from .chess import workspace as chess_ws
from .chess.log import GameLog
from .controller import Controller
from .floor import FLOOR_FREEZE, FLOOR_MARGIN
from .paint import kinematics as paint_kin
from .paint import program as paint_program
from .paint import vision as paint_vision
from .paint import workspace as paint_ws
from .paths import CMD_DIR, DONE_DIR, ESTOP, SNAPSHOT, STATE_FILE
from .poses import load_rest
from .settings import (
    ARM,
    CAMS,
    CHESS_CAM,
    DEFAULT_SPEED,
    JOINTS,
    LOAD_LIMIT,
    LOOP_HZ,
    MAX_SPEED,
    MAX_STEP_DEG,
    REACH_TOL,
    SETTLE_SEC,
    STALL_DEG,
    STALL_SEC,
    STATE_EVERY,
    VISION_EVERY,
)
from .shared import teach as teach_mod
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


# ------------------------------------------------------------------------------- chess command surface
_CHESS_STATE = {"mtime": None, "summary": None}


def chess_state():
    """``/state.chess``: the current game in one small dict, re-read only when the game file changes."""
    from .paths import GAMES_DIR
    try:
        files = sorted(GAMES_DIR.glob("*.json"), key=lambda q: q.stat().st_mtime) if GAMES_DIR.is_dir() else []
        if not files: return None
        mtime = files[-1].stat().st_mtime
        if _CHESS_STATE["mtime"] != mtime:
            g = GameLog.load(files[-1].stem)
            _CHESS_STATE.update(mtime=mtime, summary=g.summary() if g else None)
        return _CHESS_STATE["summary"]
    except Exception:
        return _CHESS_STATE["summary"]


def chess_command(ctrl, act, c, limits, present):
    """Every ``chess_*`` file/HTTP command. Teaching and geometry act now; the rest talk to the routine."""
    def model(d=None):
        return chess_kin.GripperModel(ctrl.floor, d or chess_ws.load(), limits)

    def ask(**kw):
        ctrl.chess_request = {**(getattr(ctrl, "chess_request", None) or {}), **kw}

    if act == "chess_board":
        chess_ws.set_board(size_cm=c.get("size_cm"), robot_side=c.get("robot_side"), cam=c.get("cam"))
    elif act == "chess_mark":                      # present pose = closed fingertips on a square centre
        sq = str(c["square"]).lower()
        (chess_ws.mark_corner if sq in chess_ws.CORNERS else chess_ws.mark_extra)(sq, present)
    elif act == "chess_clear":
        chess_ws.clear_board_marks()
    elif act == "chess_image_corners":
        chess_ws.set_image_corners({k: tuple(v) for k, v in c["corners"].items()})
    elif act == "chess_slot":
        chess_ws.set_slot(str(c["name"]), present)
    elif act == "chess_compile":                   # fit, report, and record which squares are out of reach
        d = chess_ws.load(); m = model(d)
        if not m.ok: log(f"chess: board model not fitted — {m.error}"); return
        log(f"chess: board model {m.summary()}")
        log(chess_kin.report_text(m.reachability_report(), d))
        chess_ws.set_unreachable(chess_phys.unreachable_squares(m, d))
    elif act == "chess_goto":                      # jaws over a square at a height, through the guards
        d = chess_ws.load(); m = model(d)
        z = float(c.get("z", d["heights"]["hover_cm"]))
        pose = m.pose_for(str(c["square"]).lower(), z) if m.ok else None
        if pose is None: log(f"chess_goto: {c.get('square')} at z={z} cm is unreachable ({m.error or 'no pose'})")
        else:
            far = max(abs(pose[j] - present[j]) for j in pose); n = int(far // 8.0) + 1
            pts = [{j: present[j] + (pose[j] - present[j]) * k / n for j in pose} for k in range(1, n + 1)]
            log(f"chess_goto: {c['square']} z={z} cm -> { {k: round(v, 1) for k, v in pose.items()} } in {n} steps")
            ctrl.request({"action": "path", "points": pts, "speed": float(c.get("speed", 4.0)), "src": "file"})
    elif act == "chess_grid":                      # EMPTY board: find the 7x7 inner corners, store the four corner-square centres
        from .chess import vision as chess_vision
        d = chess_ws.load()
        frame = ctrl.latest_frame(c.get("cam") or d["board"].get("cam") or CHESS_CAM)
        if frame is None: log("chess: no frame from that camera yet"); return
        corners = chess_vision.auto_image_corners(frame, d)
        if corners is None: log("chess: no chessboard grid found — is the board empty and fully in view?"); return
        chess_ws.set_image_corners(corners); ctrl.chess_reader = None
        log(f"chess: image corners from the detected grid: {corners} — check /chess/warp.jpg, then chess_reference")
    elif act == "chess_reference":                 # capture the EMPTY board: every reading is a diff from it
        from .chess import vision as chess_vision
        d = chess_ws.load()
        frame = ctrl.latest_frame(c.get("cam") or d["board"].get("cam") or CHESS_CAM)
        if frame is None: log("chess: no frame from that camera yet"); return
        reader = chess_vision.BoardReader(d)
        if not reader.ok: log(f"chess: {reader.error()}"); return
        if c.get("from_position"):                 # pieces are on the board: synthesize the empty board from the known position
            glog = GameLog.latest()
            if glog is None: log("chess: no game to take the position from"); return
            if not reader.set_reference_from_position(frame, glog.board()): log("chess: could not synthesize the reference"); return
            read = reader.read(frame)
            if read: reader.calibrate(read["features"], glog.board())
            ctrl.chess_reader = reader
            log(f"chess: reference synthesized from the position of game {glog.id} — occupancy readings are live")
            return
        if not reader.set_reference(frame): log("chess: could not warp the board"); return
        ctrl.chess_reader = reader
        log("chess: empty-board reference captured — occupancy readings are live from now on")
    elif act == "chess_apply":                     # apply one suggestion from the history
        from .chess import history as chess_history
        try: log(f"chess: {chess_history.apply(c['id'])}")
        except ValueError as e: log(f"chess: {e}")
    elif act == "chess_check_view":                # can the camera see the board at all?
        d = chess_ws.load()
        frame = ctrl.latest_frame(c.get("cam") or d["board"].get("cam") or CHESS_CAM)
        if frame is None: log("chess: no frame from that camera yet"); return
        from .chess import vision as chess_vision
        if chess_vision.homography(d) is not None: log("chess: board located from the taught image corners")
        else:
            grid = chess_vision.find_grid(frame)
            log("chess: found a 7x7 grid — teach the image corners to lock the orientation" if grid is not None
                else "chess: no board found in the frame (light? whole board in view? empty board?)")
    elif act == "chess_new":
        GameLog.new(mode=c.get("mode", "play"), robot=c.get("robot", chess_ws.load()["board"]["robot_side"]),
                    players=c.get("players"), start_fen=c.get("fen"))
    elif act == "chess_move": ask(move=str(c.get("move", "")).strip().lower())
    elif act == "chess_confirm": ask(confirm=True)
    elif act in ("chess_play", "chess_setup", "chess_puzzle", "chess_verify"):
        ask(**{k: v for k, v in c.items() if k != "action"})
        ctrl.start_routine(act)
    else:
        log(f"unknown chess action {act}")


def control_loop(robot, ctrl: Controller, limits, max_iters=None):
    """Run until SIGINT (or, in tests, until ``max_iters`` cycles have run)."""
    present = {k.removesuffix(".pos"): v for k, v in robot.bus.sync_read("Present_Position").items()}
    cmd, goal, speed = dict(present), dict(present), dict(DEFAULT_SPEED)
    torque_on, mode, last_cmd_name = True, "hold", None
    last_state, seq, loop_i = 0.0, 0, 0
    stall_since, last_load_warn = None, 0.0
    settle_since, last_settle_pose = None, None
    hot_prev = set()
    last_loads, loop_hz = {}, float(LOOP_HZ)
    path_queue, path_total = [], 0          # continuous waypoint execution ({"action": "path"})
    stop = {"flag": False}
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("flag", True))
    log(f"ready. present={ {k: round(v, 1) for k, v in present.items()} }")

    def bounds(j, allow):
        """The limits one joint is held to, widened for a point the operator has driven to by hand.

        ``allow`` comes with the command ({joint: [lo, hi]}) and is the only way past the soft limit —
        it is capped by the motor's own sweep, so it can spend the calibration margin and nothing more,
        it changes no stored configuration, and it applies to that command alone.
        """
        lo, hi = limits[j]
        want = (allow or {}).get(j)
        if not want: return lo, hi
        hlo, hhi = hardware.hard_limits().get(j, (lo, hi))
        return max(hlo, min(lo, float(want[0]))), min(hhi, max(hi, float(want[1])))

    def guard_point(target_pose, ref, src, allow=None):
        """Step cap, soft limits and floor guard for one goal pose relative to a reference pose."""
        out = dict(ref)
        for j, v in target_pose.items():
            if j not in JOINTS: log(f"unknown joint {j}"); continue
            target = float(v)
            if j != "gripper" and abs(target - ref[j]) > MAX_STEP_DEG:
                target = ref[j] + MAX_STEP_DEG * (1 if target > ref[j] else -1)
                ctrl.event("STEP CAP", f"{j}: limited to {MAX_STEP_DEG} deg ({src})")
            lo, hi = bounds(j, allow)
            clamped = min(hi, max(lo, target))
            if abs(clamped - target) > 1e-6: ctrl.event("CLAMP", f"{j}: {target:.1f} -> {clamped:.1f} ({src})")
            out[j] = clamped
        if ctrl.floor.params is not None and src != "floor_ok":
            for frac in (1.0, 0.75, 0.5, 0.25, 0.0):
                trial = {j: ref[j] + frac * (out[j] - ref[j]) for j in JOINTS}
                zt = ctrl.floor.z(trial)
                if zt is not None and zt >= FLOOR_MARGIN: break
            if frac < 1.0:
                ctrl.event("FLOOR GUARD", f"goal would put the tips at {ctrl.floor.z(out) * 100:.1f} cm -> step scaled to {frac:.2f} ({src})")
                for j in ARM: out[j] = trial[j]
        return out

    def apply_path(c):
        """A dense list of poses followed back-to-back by the interpolator (no stop at each point)."""
        nonlocal goal, cmd, mode, torque_on, path_queue, path_total
        if not torque_on:
            robot.bus.enable_torque(num_retry=5); torque_on = True; cmd = dict(present)
        pts, ref, src = [], dict(present), c.get("src", "file")
        for p in c.get("points", []):
            ref = guard_point(p, ref, src, c.get("allow")); pts.append(ref)
        if not pts: log("path: no points"); return
        s = c.get("speed")
        if s is not None:
            for j in JOINTS: speed[j] = min(MAX_SPEED, max(0.5, float(s)))
        path_queue, path_total = pts[1:], len(pts)
        goal = dict(pts[0]); mode = "moving"
        ctrl.rec_write({"event": "path", "src": src, "n": len(pts), "speed": s})
        log(f"path[{src}]: {len(pts)} points at {s} deg/s")

    def apply_goal(c):
        nonlocal goal, cmd, mode, torque_on, path_queue
        path_queue = []
        if not torque_on:
            robot.bus.enable_torque(num_retry=5); torque_on = True; cmd = dict(present)
        for j, v in c.get("goal", {}).items():
            if j not in JOINTS: log(f"unknown joint {j}"); continue
            target = present[j] + float(v) if c.get("relative") else float(v)
            if j != "gripper" and abs(target - present[j]) > MAX_STEP_DEG:
                target = present[j] + MAX_STEP_DEG * (1 if target > present[j] else -1)
                ctrl.event("STEP CAP", f"{j}: limited to {MAX_STEP_DEG} deg from present")
            lo, hi = bounds(j, c.get("allow"))
            clamped = min(hi, max(lo, target))
            if abs(clamped - target) > 1e-6: ctrl.event("CLAMP", f"{j}: {target:.1f} -> {clamped:.1f}")
            goal[j] = clamped
        # floor guard: shrink the step until the predicted fingertip height stays above the margin
        if ctrl.floor.params is not None and c.get("src") != "floor_ok":
            for frac in (1.0, 0.75, 0.5, 0.25, 0.0):
                trial = {j: present[j] + frac * (goal[j] - present[j]) for j in JOINTS}
                zt = ctrl.floor.z(trial)
                if zt is not None and zt >= FLOOR_MARGIN: break
            if frac < 1.0:
                ctrl.event("FLOOR GUARD", f"goal would put the tips at {ctrl.floor.z(goal) * 100:.1f} cm -> step scaled to {frac:.2f}")
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
            obs = hardware.read_observation(robot)
            present = {j: float(obs[f"{j}.pos"]) for j in JOINTS}

            # ---- E-STOP
            if ESTOP.exists():
                if mode != "estop":
                    path_queue = []; goal, cmd, mode = dict(present), dict(present), "estop"; ctrl.abort_auto("ESTOP file")
                    ctrl.event("ESTOP", "frozen. RESUME (or delete the ESTOP file) to continue.")
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
                elif act == "path": apply_path(c)
                elif act == "hold": path_queue = []; goal, cmd, mode = dict(present), dict(present), "hold"; log("hold")
                elif act == "release":
                    goal, cmd = dict(present), dict(present); robot.bus.disable_torque(); torque_on = False
                    mode = "released"; log("torque RELEASED"); ctrl.rec_write({"event": "release"})
                elif act == "engage":
                    robot.bus.enable_torque(num_retry=5); torque_on = True; goal, cmd, mode = dict(present), dict(present), "hold"
                    log("torque engaged (holding current pose)"); ctrl.rec_write({"event": "engage"})
                elif act == "auto": ctrl.start_auto()
                elif act == "routine":
                    if c.get("program"): ctrl.paint_request = {"name": c["program"]}
                    ctrl.start_routine(c.get("name", "pick_place"))
                elif act == "paint":                       # run a saved painting program from a file command
                    ctrl.paint_request = {"name": c.get("program"), "from_stroke": c.get("from_stroke")}; ctrl.start_routine("paint")
                elif act == "paint_compile":               # hand-written plan (paper cm) -> saved program
                    try:
                        plan = c.get("plan")
                        if isinstance(plan, str): plan = json.loads(Path(plan).read_text())
                        ws = paint_ws.load(); tool = paint_kin.ToolModel(ctrl.floor, ws, limits)
                        prog = paint_program.compile_plan(plan, ws, tool, c.get("name", "plan"), dry_run=bool(c.get("dry")))
                        paint_program.save(prog)
                        if prog["skipped"]: log(f"paint: compile skipped unreachable strokes {prog['skipped']}")
                    except Exception as e:
                        log(f"paint compile error: {e}")
                elif act == "paint_goto":                  # brush tip to paper (u, v) cm at z cm, via the tool model
                    try:
                        ws = paint_ws.load(); tool = paint_kin.ToolModel(ctrl.floor, ws, limits)
                        z = float(c.get("z", ws["brush"]["hover_cm"]))
                        if not c.get("raw"): z += paint_program.z_correction(ws)(float(c["u"]), float(c["v"]))
                        pose = tool.uv_to_pose(float(c["u"]), float(c["v"]), z)
                        if pose is None: log(f"paint_goto: ({c['u']}, {c['v']}, z={z}) unreachable")
                        else:
                            far = max(abs(pose[j] - present[j]) for j in pose); n = int(far // 8.0) + 1
                            pts = [{j: present[j] + (pose[j] - present[j]) * k / n for j in pose} for k in range(1, n + 1)]
                            log(f"paint_goto: ({c['u']}, {c['v']}) z={z:.2f} cm -> { {k: round(v, 1) for k, v in pose.items()} } in {n} steps")
                            apply_path({"points": pts, "speed": float(c.get("speed", 4.0)), "src": "file"})
                    except Exception as e: log(f"paint_goto error: {e}")
                elif act == "paint_mark_extra":            # present pose = brush tip ON the paper at (u, v)
                    try: paint_ws.mark_extra(float(c["u"]), float(c["v"]), present)
                    except Exception as e: log(f"paint_mark_extra error: {e}")
                elif act == "paint_probe":                 # record: brush touched the paper at (u, v) when commanded z (raw)
                    try:
                        d = paint_ws.load(); d.setdefault("probes", [])
                        d["probes"] = [p for p in d["probes"] if (p["u"], p["v"]) != (float(c["u"]), float(c["v"]))]
                        d["probes"].append({"u": float(c["u"]), "v": float(c["v"]), "z_touch_cm": float(c["z"]), "note": c.get("note", "")})
                        paint_ws.save(d); log(f"paint: probe ({c['u']}, {c['v']}) touches at commanded z={c['z']} cm ({len(d['probes'])} probes)")
                    except Exception as e: log(f"paint_probe error: {e}")
                elif act == "paint_brush":                 # brush parameters, e.g. {"action":"paint_brush","z_offset_cm":1.2}
                    paint_ws.set_brush(**{k: v for k, v in c.items() if k != "action"})
                elif act.startswith("teach_"):             # guided teaching, see shared/teach.py
                    try: teach_mod.command(ctrl, act, **{k: v for k, v in c.items() if k != "action"})
                    except Exception as e: log(f"{act} error: {e}")
                elif act.startswith("chess_"):
                    try: chess_command(ctrl, act, c, limits, present)
                    except Exception as e: log(f"{act} error: {e}")
                elif act == "video_start": ctrl.video.start(c.get("name", "clip"))
                elif act == "video_stop": ctrl.video.stop()
                elif act == "rest": ctrl.go_rest()
                elif act == "abort": ctrl.abort_auto("abort (file)"); path_queue = []; goal, cmd, mode = dict(present), dict(present), "hold"
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
                garbage = [j for j, v in loads.items() if abs(v) > 900]
                if len(garbage) >= 2:                  # corrupt Feetech packet: several joints near +-1000 at once
                    log(f"load sample rejected (garbage packet): { {j: loads[j] for j in garbage} }"); loads = {}
                if loads: last_loads = {j: int(v) for j, v in loads.items()}
                hot = {j: v for j, v in loads.items() if j != "gripper" and abs(v) > LOAD_LIMIT[j]}
                # a single over-limit sample is not trusted: the Feetech bus occasionally returns a garbage packet
                # (three joints at once near +-1000, or a joint that is not moving) — a real overload persists,
                # so freeze only when the same joint is hot on two consecutive ticks
                hot_now = set(hot); hot = {j: v for j, v in hot.items() if j in hot_prev}; hot_prev = hot_now
                if hot_now and not hot: log(f"load spike ignored (single sample): { {j: loads[j] for j in hot_now} }")
                if hot and mode == "moving":
                    path_queue = []; goal, cmd, mode = dict(present), dict(present), "stalled"; ctrl.abort_auto("load guard")
                    ctrl.event("LOAD GUARD", f"{hot} -> frozen at present.")
                elif hot and time.time() - last_load_warn > 2.0:
                    last_load_warn = time.time(); log(f"load warning while holding: {hot}")
            # ---- floor guard (runtime): predicted tip height below the table plane -> freeze
            zp = ctrl.floor.z(present)
            if zp is not None and torque_on and mode == "moving" and zp < FLOOR_FREEZE:
                path_queue = []; goal, cmd, mode = dict(present), dict(present), "stalled"; ctrl.abort_auto("floor guard")
                ctrl.event("FLOOR FREEZE", f"predicted tip height {zp * 100:.1f} cm")
            # ---- stall guard
            if torque_on and mode == "moving":
                lag = {j: abs(cmd[j] - present[j]) for j in ARM}; worst = max(lag, key=lag.get)
                if lag[worst] > STALL_DEG:
                    stall_since = stall_since or time.time()
                    if time.time() - stall_since > STALL_SEC:
                        path_queue = []; goal, cmd, mode = dict(present), dict(present), "stalled"; ctrl.abort_auto("stall guard")
                        ctrl.event("STALL", f"{worst} lag {lag[worst]:.1f} deg -> frozen at present.")
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
                if mode == "moving" and not moving and path_queue:
                    goal = dict(path_queue.pop(0)); moving = True          # next waypoint, no stop
                if mode == "moving" and not moving and all(abs(present[j] - goal[j]) < REACH_TOL for j in ARM):
                    mode = "reached"; settle_since = None; log(f"reached { {k: round(v, 1) for k, v in present.items()} }")
                elif mode == "moving" and not moving and not path_queue:
                    # interpolation finished, nothing queued, but a joint sags between REACH_TOL and the stall
                    # threshold (the elbow does this when the arm is stretched out): once the arm has stopped
                    # moving for SETTLE_SEC it *is* where it is going to be — report it, with the residual.
                    if last_settle_pose and max(abs(present[j] - last_settle_pose[j]) for j in ARM) < 0.5:
                        settle_since = settle_since or time.time()
                        if time.time() - settle_since > SETTLE_SEC:
                            res = {j: round(present[j] - goal[j], 1) for j in ARM if abs(present[j] - goal[j]) >= REACH_TOL}
                            mode = "reached"; settle_since = None
                            log(f"reached (settled, residual {res}) { {k: round(v, 1) for k, v in present.items()} }")
                    else: settle_since = None
                    last_settle_pose = dict(present)
                else: settle_since = None
            elif torque_on and mode in ("reached", "stalled"):
                robot.send_action({f"{j}.pos": cmd[j] for j in JOINTS})

            # ---- vision + streams
            if loop_i % VISION_EVERY == 0:
                blobs, frames = {}, {}
                ws = paint_ws.load()
                for n in CAMS:
                    if obs.get(n) is None: continue             # that camera is quiet; its last frame keeps its age
                    jpeg, b, bgr = vision.render(obs[n], n, mode, ctrl.routine_status,
                                                 show_servo_guides=(n == "wrist" and ctrl.routine_status == "running"))
                    frames[n] = bgr
                    with ctrl.lock:                             # raw frame: the chess board reader warps this one
                        ctrl.frame[n] = obs[n]; ctrl.frame_t[n] = time.time()
                    if b: blobs[n] = b
                    if jpeg:
                        with ctrl.lock: ctrl.jpeg[n] = jpeg
                        if loop_i % (VISION_EVERY * 5) == 0: atomic_write(SNAPSHOT[n], jpeg)
                    paint_jpeg, _, _ = paint_vision.render(obs[n], n, ws, mode, ctrl.routine_status)
                    if paint_jpeg:
                        with ctrl.lock: ctrl.paint_jpeg[n] = paint_jpeg
                with ctrl.lock: ctrl.blob = blobs
                ctrl.video.write(frames)

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
                      "tip_z_cm": round(zp * 100, 1) if zp is not None else None,
                      "load": last_loads, "load_limit": LOAD_LIMIT, "loop_hz": round(loop_hz, 1),
                      "path": {"remaining": len(path_queue), "total": path_total} if (path_queue or mode == "moving") and path_total else None,
                      "auto": ctrl.routine_status,
                      "progress": {k: ctrl.progress.get(k) for k in ("routine", "phase", "detail", "paint")},
                      "teach": ctrl.teach.state() if ctrl.teach else None, "chess": chess_state(),
                      "video": ctrl.video.status(), "cams": hardware.camera_status()}
                with ctrl.lock: ctrl.state = st
                atomic_write(STATE_FILE, json.dumps(st, indent=1).encode()); last_state = now
        except Exception:
            log("ERROR in loop:\n" + traceback.format_exc()); time.sleep(0.5)
        el = time.perf_counter() - t0
        loop_hz = 0.9 * loop_hz + 0.1 / max(el, dt)
        if el < dt: time.sleep(dt - el)
