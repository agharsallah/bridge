"""Bus and motor bring-up: connect, set the gains the arm needs, and derive the joint limits.

Split out of the control loop so the loop file contains nothing but the 30 Hz cycle.
"""

import json
import threading
import time

from . import camera_identity
from .camera_worker import WorkerCamera
from .paths import LIMITS_FILE
from .settings import (
    CAL_MARGIN_DEG,
    CAM_FPS,
    CAM_H,
    CAM_MAX_AGE_MS,
    CAM_ON_ARM,
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
    from lerobot.robots.so_follower import SOFollower, SOFollowerRobotConfig

    class QuietFollower(SOFollower):
        """SOFollower whose configure() is a no-op: lerobot's briefly disables torque and the arm sags.

        The gains it would have written are applied by :func:`configure_motors` instead.
        """

        def configure(self) -> None:
            pass

    return SOFollowerRobotConfig, QuietFollower


def connect(attempts: int = 5):
    """Connect to the arm, then its cameras, retrying bus hiccups. Torque is never dropped.

    The cameras are attached one at a time *after* the bus, and a camera that will not start is simply
    left off: ``SOFollower.is_connected`` is false while any of its cameras is down, and ``send_action``
    refuses to run when it is — so a wedged USB camera would otherwise stop the arm being commanded at
    all. A detached camera is retried in the background and joins the running bridge when it comes back.
    """
    SOFollowerRobotConfig, QuietFollower = _lerobot()
    for n, i in CAMS.items():
        w, h = CAM_SIZE.get(n, (CAM_W, CAM_H))
        _CAM_CFG[n] = dict(name=n, index=i, width=w, height=h, fps=CAM_FPS)
    cfg = SOFollowerRobotConfig(port=PORT, id=ROBOT_ID, cameras={}, disable_torque_on_disconnect=False,
                                max_relative_target=5.0)     # cameras are ours, not lerobot's: see camera_worker
    robot = QuietFollower(cfg)
    wanted = {n: WorkerCamera(**c) for n, c in _CAM_CFG.items()}   # the bus first, cameras below
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
CAM_REOPEN_WARMUP_S = 4   # how long a respawned camera process may take to deliver its first frame


_PENDING = {}   # camera -> new device selector, queued from any thread, applied by the control loop (read_observation)
_VERIFY = {}   # camera -> when it (re)attached: the twin cameras' identity is checked after that
_TRUST = set()   # cameras the operator just assigned by hand: their next check is skipped (their word stands)
_VOTE = None   # the identity vote in progress (camera_identity.Vote), restarted whenever a camera reattaches
_IDENTITY = {"state": "idle", "message": "", "t": None}   # what the identity check last concluded, for the UI
VERIFY_AFTER_S = 1.5   # let exposure settle after a (re)attach before comparing views


def _status(state, message):
    _IDENTITY.update(state=state, message=message, t=round(time.time(), 1))


def assign_cameras(mapping, manual=True, auto=None):
    """Point cameras at other devices, ``{name: selector}`` (an int index or "model#n", see camera_ids).

    Saved to config/settings.json at once; the loop then detaches the changed cameras and reopens them.
    ``manual``: the operator's choice, never second-guessed by the next identity check. ``auto``: also save
    whether the identity check runs after reconnects (CAM_AUTO_ID).
    """
    from . import settings
    from .camera_ids import resolve
    for n, sel in mapping.items():
        if n not in _CAM_CFG: raise ValueError(f"no camera {n!r}")
        resolve(sel, [])                            # format check only: raises ValueError on a bad selector
    cams = {**settings.CAMS, **mapping}
    if len({str(v) for v in cams.values()}) != len(cams): raise ValueError("two cameras cannot use the same device")
    settings.save_override("CAMS", cams)
    if auto is not None: settings.save_override("CAM_AUTO_ID", bool(auto))
    if manual: _TRUST.update(mapping); _status("manual", "assigned by hand")
    _PENDING.update({n: s for n, s in mapping.items() if _CAM_CFG[n]["index"] != s})
    log(f"camera assignment ({'by hand' if manual else 'by motion'}): CAMS={cams}")


def swap_cameras(a, b, manual=True):
    """Swap the devices of two cameras (two identical cameras came back in the other order)."""
    if a == b or a not in _CAM_CFG or b not in _CAM_CFG: raise ValueError(f"cannot swap {a!r} and {b!r}")
    assign_cameras({a: _CAM_CFG[b]["index"], b: _CAM_CFG[a]["index"]}, manual=manual)


def camera_assignment():
    """``/state.cameras.assign``: each camera's selector, the arm camera, and what the identity check says."""
    from . import settings
    return {"cams": {n: c["index"] for n, c in _CAM_CFG.items()}, "on_arm": CAM_ON_ARM,
            "auto": bool(settings.CAM_AUTO_ID), "identity": dict(_IDENTITY)}


def _twins():
    """The two cameras selected by the same model ("model#n"): only they can be confused with each other."""
    sel = {n: c["index"].rpartition("#")[0].lower() for n, c in _CAM_CFG.items()
           if isinstance(c["index"], str) and "#" in c["index"]}
    names = list(sel)
    for x in range(len(names)):
        for y in range(x + 1, len(names)):
            a, b = sel[names[x]], sel[names[y]]
            if a in b or b in a: return names[x], names[y]
    return None


def _check_identity(obs):
    """After a (re)attach: is CAM_ON_ARM really the twin on the arm? Swap the twins if their views say no."""
    global _VOTE
    from . import settings
    pair = _twins()
    if not pair or CAM_ON_ARM not in pair: _VERIFY.clear(); return
    live = [n for n in pair if obs.get(n) is not None]
    if len(live) == 1 and not _PENDING and _IDENTITY["state"] not in ("single", "manual"):
        _status("single", f"only one of the two identical cameras is connected; it shows as {live[0]}. "
                          f"If it is really the other one, assign it by hand.")
    if _PENDING or len(live) < 2 or time.time() - max(_VERIFY.values()) < VERIFY_AFTER_S:
        return                                      # wait: a change is pending, or a twin is not delivering yet
    if _TRUST & set(pair):                          # just assigned by hand: the operator's word stands
        _TRUST.difference_update(pair); _VERIFY.clear(); _VOTE = None; return
    if not settings.CAM_AUTO_ID:
        _VERIFY.clear(); _status("off", "automatic check off: cameras stay as assigned"); return
    other = pair[1] if pair[0] == CAM_ON_ARM else pair[0]
    if _VOTE is None:
        _VOTE = camera_identity.Vote(CAM_ON_ARM, other)
        _status("checking", f"move the arm a little: checking that {CAM_ON_ARM} is the camera riding it")
    try:
        verdict = _VOTE.feed([obs[f"{m}.pos"] for m in JOINTS if m != "gripper"], {n: obs[n] for n in pair})
    except Exception as e:
        log(f"camera identity: check failed ({e})"); _VERIFY.clear(); _VOTE = None; _status("unsure", f"check failed: {e}"); return
    if verdict is None: return                      # the arm has not moved enough yet
    _VERIFY.clear(); _VOTE = None
    if verdict == "swap":
        log(f"camera identity: {other}'s view moves with the arm, {CAM_ON_ARM}'s does not (ports changed?) -> swapping")
        _status("swapped", f"{CAM_ON_ARM} and {other} were the wrong way round: swapped")
        swap_cameras(CAM_ON_ARM, other, manual=False)
    elif verdict == "ok":
        log(f"camera identity: {CAM_ON_ARM} moves with the arm, as configured"); _status("ok", f"{CAM_ON_ARM} rides the arm, as assigned")
    else:
        log(f"camera identity: could not tell which of {pair} rides the arm -> waiting for a manual assignment")
        _status("unsure", f"could not tell which camera rides the arm: pick {CAM_ON_ARM} and {other} by hand")


def _do_assign(robot):
    """Apply queued assignments — not while a camera is mid-reconnect (it would come back on the old device)."""
    if not _PENDING or any(st["reopening"] for st in _CAM.values()): return
    while _PENDING:
        n, sel = _PENDING.popitem()
        _CAM_CFG[n]["index"] = sel
        cam = robot.cameras.pop(n, None) or (_CAM.get(n) or {}).get("cam")
        _STRIKES.pop(n, None)
        _CAM[n] = {"cam": cam, "since": time.time(), "next_try": 0.0, "reopening": False,
                   "fresh": True}                    # reopened right away, on the new device: not the old port
        log(f"camera {n}: now {sel!r}, reopening")


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
    _do_assign(robot)
    for name in list(robot.cameras):
        obs[name] = _frame(robot, name)
    if _VERIFY: _check_identity(obs)
    for name, st in list(_CAM.items()):              # cameras that are down: try them again now and then
        if name not in robot.cameras:
            obs.setdefault(name, None); _retry(robot, name, st)
    return obs


def camera_status():
    """``/state.cams``: which cameras are delivering, and how long a missing one has been gone."""
    return {n: (round(time.time() - (_CAM[n]["since"] or time.time()), 1) if n in _CAM else None) for n in CAMS}


def _frame(robot, name):
    """The newest frame, or None. A camera is only given up on after CAM_STRIKES misses in a row.

    A single miss is normal — the wrist camera's cable is on a moving arm — and detaching means a respawn.
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


_ATTACH_LOCK = threading.Lock()   # one camera attaches at a time: two must never pick the same device


def _uids_in_use(robot, name):
    return {u for n, c in list(robot.cameras.items()) if n != name and (u := getattr(c, "uid", ""))}


def _attach(robot, name, cam, prefer=""):
    """Connect one camera and hand it to the robot; on failure it is left off and retried later.

    The device uids the other cameras hold are excluded, so with one of two identical cameras unplugged the
    remaining one is never opened twice (both tiles showed the same view).
    """
    global _VOTE
    try:
        with _ATTACH_LOCK:
            if not cam.is_connected: cam.connect(exclude=_uids_in_use(robot, name), prefer=prefer)
            if cam.uid and cam.uid in _uids_in_use(robot, name):
                raise ConnectionError(f"{cam} opened device {cam.uid}, which another camera already uses")
            robot.cameras[name] = cam
        _CAM.pop(name, None); _STRIKES.pop(name, None); _VERIFY[name] = time.time()
        _VOTE = None                                # a new device: any identity vote starts over
        log(f"camera {name}: connected")
        return True
    except Exception as e:
        try: cam.disconnect()
        except Exception: pass
        _CAM[name] = {"cam": cam, "since": time.time(), "next_try": time.time() + CAM_RETRY_S, "reopening": False}
        log(f"camera {name}: will not start ({e}) — running without it, retrying every {CAM_RETRY_S:.0f} s")
        return False


def _release(cam):
    try: cam.disconnect()
    except Exception: pass


def _rebuild(name):
    """A brand-new camera — a new process — from the stored config, or None if there is none.

    Reopening in this process is what never came back: after a USB drop AVFoundation keeps pointing the
    index at the vanished device, which opens but never delivers a frame (see camera_worker). A fresh
    process enumerates afresh and streams at once.
    """
    cfg = _CAM_CFG.get(name)
    return WorkerCamera(**cfg, warmup_s=CAM_REOPEN_WARMUP_S) if cfg else None


def _retry(robot, name, st):
    """Reopen a detached camera on its own thread — connect() blocks for seconds and the loop may not."""
    if st["reopening"] or time.time() < st["next_try"]: return
    st["reopening"] = True; st["next_try"] = time.time() + CAM_RETRY_S

    def work():
        down = time.time() - (st["since"] or time.time())
        try:
            prefer = "" if st.get("fresh") else getattr(st["cam"], "uid", "")   # back on the same port after a drop
            _release(st["cam"])
            if _attach(robot, name, _rebuild(name) or st["cam"], prefer=prefer):
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
