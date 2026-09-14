"""Bus and motor bring-up: connect, set the gains the arm needs, and derive the joint limits.

Split out of the control loop so the loop file contains nothing but the 30 Hz cycle.
"""

import json
import time

from .paths import LIMITS_FILE
from .settings import (
    CAL_MARGIN_DEG,
    CAM_FPS,
    CAM_H,
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
    """Connect to the arm and its cameras, retrying bus hiccups. Torque is never dropped."""
    OpenCVCameraConfig, SOFollowerRobotConfig, QuietFollower = _lerobot()
    cams = {n: OpenCVCameraConfig(index_or_path=i, fps=CAM_FPS, width=CAM_W, height=CAM_H) for n, i in CAMS.items()}
    cfg = SOFollowerRobotConfig(port=PORT, id=ROBOT_ID, cameras=cams, disable_torque_on_disconnect=False,
                                max_relative_target=5.0)
    robot = QuietFollower(cfg)
    for attempt in range(1, attempts + 1):
        try:
            log(f"connecting (attempt {attempt})..."); robot.connect(calibrate=False)
            robot.bus.enable_torque(num_retry=5); break
        except ConnectionError as e:
            log(f"bus hiccup: {e}")
            try: robot.bus.disconnect(disable_torque=False)
            except Exception: pass
            for cam in robot.cameras.values():
                try:
                    if cam.is_connected: cam.disconnect()
                except Exception: pass
            if attempt == attempts: raise
            time.sleep(1.0)
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
    log(f"limits: {json.dumps({k: [round(a, 1), round(b, 1)] for k, (a, b) in limits.items()})}")
    return limits
