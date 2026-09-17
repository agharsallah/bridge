"""Bus and motor bring-up: connect, set the gains the arm needs, and derive the joint limits.

Split out of the control loop so the loop file contains nothing but the 30 Hz cycle.
"""

import json
import threading
import time

from .paths import LIMITS_FILE
from .settings import (
    CAL_MARGIN_DEG,
    CAM_FOURCC,
    CAM_FPS,
    CAM_H,
    CAM_MAX_AGE_MS,
    CAM_RETRY_S,
    CAM_SIZE,
    CAM_W,
    CAMS,
    JOINTS,
    MOTOR_ACCEL,
    MOTOR_GOAL_VELOCITY,
    P_GAIN,
    P_GAIN_DEFAULT,
    PORT,
    ROBOT_ID,
)
from .util import log


def _lerobot():
    """Import lerobot only when an arm is actually connected, so the package (and its tests) load without it."""
    from lerobot.cameras.opencv import OpenCVCameraConfig
    from lerobot.robots.so_follower import SOFollower, SOFollowerRobotConfig

    class QuietFollower(SOFollower):
        """SOFollower whose configure() is a no-op: lerobot's briefly disables torque and the arm sags.

        The gains it would have written are applied by :func:`configure_motors` instead.
        """

        def configure(self) -> None:
            pass

    return OpenCVCameraConfig, SOFollowerRobotConfig, QuietFollower


def connect(attempts: int = 5):
    """Connect to the arm, then its cameras, retrying bus hiccups. Torque is never dropped.

    The cameras are attached one at a time *after* the bus, and a camera that will not start is simply
    left off: ``SOFollower.is_connected`` is false while any of its cameras is down, and ``send_action``
    refuses to run when it is — so a wedged USB camera would otherwise stop the arm being commanded at
    all. A detached camera is retried in the background and joins the running bridge when it comes back.
    """
    OpenCVCameraConfig, SOFollowerRobotConfig, QuietFollower = _lerobot()
    cams = {n: OpenCVCameraConfig(index_or_path=i, fps=CAM_FPS, fourcc=CAM_FOURCC,
                                  width=CAM_SIZE.get(n, (CAM_W, CAM_H))[0],
                                  height=CAM_SIZE.get(n, (CAM_W, CAM_H))[1])
            for n, i in CAMS.items()}
    _CAM_CFG.update(cams)
    cfg = SOFollowerRobotConfig(port=PORT, id=ROBOT_ID, cameras=cams, disable_torque_on_disconnect=False,
                                max_relative_target=5.0)
    robot = QuietFollower(cfg)
    wanted = dict(robot.cameras); robot.cameras = {}        # the bus first, cameras below
    for attempt in range(1, attempts + 1):
        try:
            log(f"connecting (attempt {attempt})..."); robot.connect(calibrate=False)
            robot.bus.enable_torque(num_retry=5); break
        except ConnectionError as e:
            log(f"bus hiccup: {e}")
            try: robot.bus.disconnect(disable_torque=False)
            except Exception: pass
            if attempt == attempts: raise
            time.sleep(1.0)
    for name, cam in wanted.items():
        _attach(robot, name, cam)
    if not robot.is_calibrated:
        # homing offsets / limits in the motors differ from the file (normal after an 'extend'): restore the file's
        # values first, the extension below re-applies on top.
        log("motor calibration registers differ from file -> restoring file values"); robot.bus.write_calibration(robot.calibration)
    return robot


def configure_motors(robot):
    """Gentle gripper torque and the position gains that hold small steps against gravity."""
    robot.bus.write("Max_Torque_Limit", "gripper", 500, num_retry=5)
    robot.bus.write("Protection_Current", "gripper", 250, num_retry=5)
    for m in JOINTS:
        if m != "gripper":
            robot.bus.write("P_Coefficient", m, P_GAIN.get(m, P_GAIN_DEFAULT), num_retry=5)
        robot.bus.write("Acceleration", m, MOTOR_ACCEL, normalize=False, num_retry=5)
        robot.bus.write("Goal_Velocity", m, MOTOR_GOAL_VELOCITY, normalize=False, num_retry=5)


# ------------------------------------------------------------------------------- reading the arm and the cameras
_CAM = {}   # per camera: the object, when it went quiet, when to try it next, whether a retry is running
_STRIKES = {}   # per camera: consecutive missed reads, reset by the first good frame
_CAM_CFG = {}   # per camera: the config it was built from, so a dead one can be rebuilt rather than reopened
CAM_STRIKES = 3   # consecutive missed frames before a camera is detached — one hiccup must not cost the session
CAM_REOPEN_WARMUP_S = 4   # how long a rebuilt camera may take to deliver its first frame (default 1 s is too short after a USB drop)


def read_observation(robot):
    """Joint positions from the bus; camera frames best-effort.

    lerobot's ``get_observation`` raises when a camera hands back a stale frame, and its background read
    thread gives up for good after ten consecutive failures — either way the exception would come out of
    the 30 Hz loop, which then commands nothing for half a second because a USB camera hiccupped. Here
    the bus read is the only thing allowed to raise: a late or dead camera yields ``None`` for its frame,
    is detached so it cannot block ``send_action``, and is retried in the background.
    """
    obs = {f"{m}.pos": v for m, v in
           robot.bus.sync_read("Present_Position", num_retry=robot.config.num_read_retries).items()}
    for name in list(robot.cameras):
        obs[name] = _frame(robot, name)
    for name, st in list(_CAM.items()):              # cameras that are down: try them again now and then
        if name not in robot.cameras:
            obs.setdefault(name, None); _retry(robot, name, st)
    return obs


def camera_status():
    """``/state.cams``: which cameras are delivering, and how long a missing one has been gone."""
    return {n: (round(time.time() - (_CAM[n]["since"] or time.time()), 1) if n in _CAM else None) for n in CAMS}


def _frame(robot, name):
    """The newest frame, or None. A camera is only given up on after CAM_STRIKES misses in a row.

    A single miss is normal — the wrist camera's cable is on a moving arm — and detaching costs the whole
    session, because lerobot's read loop spins on a broken capture session and never recovers by itself.
    """
    cam = robot.cameras[name]
    try:
        frame = cam.read_latest(max_age_ms=CAM_MAX_AGE_MS)
    except Exception as e:
        strikes = _STRIKES.get(name, 0) + 1
        _STRIKES[name] = strikes
        if strikes < CAM_STRIKES:
            return None                             # a hiccup: keep the camera, the next read usually lands
        st = _CAM.setdefault(name, {"cam": cam, "since": None, "next_try": 0.0, "reopening": False})
        st.update(cam=cam, since=time.time(), next_try=time.time() + CAM_RETRY_S)
        robot.cameras.pop(name, None)                # detached: is_connected stays true, the arm keeps moving
        log(f"camera {name}: no frames after {strikes} tries ({e}) — detached, the bridge keeps retrying")
        return None
    _STRIKES.pop(name, None)
    if name in _CAM: _CAM.pop(name, None)
    return frame


def _attach(robot, name, cam):
    """Connect one camera and hand it to the robot; on failure it is left off and retried later."""
    try:
        if not cam.is_connected: cam.connect()
        robot.cameras[name] = cam
        _CAM.pop(name, None); _STRIKES.pop(name, None)
        log(f"camera {name}: connected")
        return True
    except Exception as e:
        try: cam.disconnect()
        except Exception: pass
        _CAM[name] = {"cam": cam, "since": time.time(), "next_try": time.time() + CAM_RETRY_S, "reopening": False}
        log(f"camera {name}: will not start ({e}) — running without it, retrying every {CAM_RETRY_S:.0f} s")
        return False


def _release(cam):
    """Let go of the device completely: lerobot's disconnect() leaves the VideoCapture for the GC."""
    try: cam.disconnect()
    except Exception: pass
    vc = getattr(cam, "videocapture", None)
    if vc is not None:
        try: vc.release()
        except Exception: pass
        cam.videocapture = None


def _rebuild(name):
    """A brand-new camera object from the stored config, or None if it cannot be built.

    Reopening the *same* object is what never came back: once its capture session is broken, connect()
    re-warms against the dead session and times out every time. A fresh object opens cleanly — a plain
    VideoCapture on the same index delivers full frame rate while the other camera streams.
    """
    cfg = _CAM_CFG.get(name)
    if cfg is None: return None
    try:
        import dataclasses
        from lerobot.cameras.opencv import OpenCVCamera
        # A camera that fell off USB and came back is enumerated again before AVFoundation will stream
        # from it: the first frame can take a few seconds, and lerobot's default 1 s warmup gives up first.
        return OpenCVCamera(dataclasses.replace(cfg, warmup_s=CAM_REOPEN_WARMUP_S))
    except Exception as e:
        log(f"camera {name}: could not rebuild ({e})")
        return None


def _retry(robot, name, st):
    """Reopen a detached camera on its own thread — connect() blocks for seconds and the loop may not."""
    if st["reopening"] or time.time() < st["next_try"]: return
    st["reopening"] = True; st["next_try"] = time.time() + CAM_RETRY_S

    def work():
        down = time.time() - (st["since"] or time.time())
        try:
            _release(st["cam"])
            if _attach(robot, name, _rebuild(name) or st["cam"]):
                log(f"camera {name}: back after {down:.0f} s")
        except Exception as e:
            log(f"camera {name}: retry failed: {e}")
        finally:
            st["reopening"] = False

    threading.Thread(target=work, name=f"cam-{name}", daemon=True).start()


_HARD = {}   # the sweep itself, margin included: what the motor registers will actually accept


def hard_limits():
    """What the motors can be commanded to, margin and all — the ceiling on any per-point allowance.

    ``joint_limits`` takes the calibration margin off; a point the operator has driven to by hand may
    use that margin back, but nothing may go past the sweep the registers hold.
    """
    return dict(_HARD)


def joint_limits(robot):
    """Soft limits per joint: the calibration sweep minus a margin, tightened or extended by config/limits.json."""
    limits = {}
    user_limits = json.loads(LIMITS_FILE.read_text()) if LIMITS_FILE.is_file() else {}
    # limits.json: {"joint": [lo, hi]} tightens;  {"extend": {"joint": [lo, hi]}} may WIDEN beyond the calibration
    # sweep (the motor's own Min/Max_Position_Limit registers are updated to match, with a 1 deg margin).
    extend = user_limits.get("extend", {})
    for m in JOINTS:
        c = robot.calibration[m]; half = (c.range_max - c.range_min) / 2 * 360 / 4095
        lo, hi = ([0.0, 100.0] if m == "gripper" else [-half + CAL_MARGIN_DEG, half - CAL_MARGIN_DEG])
        if m in user_limits and m != "extend": lo, hi = max(lo, user_limits[m][0]), min(hi, user_limits[m][1])
        if m in extend and m != "gripper":
            lo, hi = min(lo, float(extend[m][0])), max(hi, float(extend[m][1]))
            mid = (c.range_min + c.range_max) / 2
            raw_lo = int(mid + (lo - 1.0) * 4095 / 360); raw_hi = int(mid + (hi + 1.0) * 4095 / 360)
            raw_lo, raw_hi = max(0, raw_lo), min(4095, raw_hi)
            robot.bus.write("Min_Position_Limit", m, raw_lo, normalize=False, num_retry=5)
            robot.bus.write("Max_Position_Limit", m, raw_hi, normalize=False, num_retry=5)
            log(f"EXTENDED {m}: soft {lo:.1f}..{hi:.1f} deg, motor limits {raw_lo}..{raw_hi} (cal was {c.range_min}..{c.range_max})")
        limits[m] = [lo, hi]
        # what the MOTOR will accept, which is where any per-point allowance stops: the registers as they
        # were just written for an extended joint, and the calibration sweep (margin included) otherwise.
        if m == "gripper": _HARD[m] = [0.0, 100.0]
        elif m in extend:
            mid = (c.range_min + c.range_max) / 2
            _HARD[m] = [(raw_lo - mid) * 360 / 4095, (raw_hi - mid) * 360 / 4095]
        else: _HARD[m] = [-half, half]
    log(f"limits: {json.dumps({k: [round(a, 1), round(b, 1)] for k, (a, b) in limits.items()})}")
    return limits
