"""Init mode: the daemon owns the bus but runs no control loop. Health checks and guided recalibration.

Nothing here moves the arm. Opening the bus writes nothing and closing it leaves the torque as it is.
Registers are written and torque changed only by the wizard steps, each behind an explicit confirm
from the operator (``confirm=yes``), in lerobot's own order (``SerialMotorsBus.record_calibration``):
release -> half-turn homing -> range sweep -> write + save -> engage.
"""

import json
import threading
import time
from pathlib import Path

from ..floor import FLOOR_MARGIN, GRASP_Z
from ..paths import ESTOP, FLOOR_FILE, LIMITS_FILE, REST_FILE
from ..settings import JOINTS, PORT
from ..util import log
from . import calibration as calib
from .cameras import CameraPump
from .docs import lerobot_docs

FULL_TURN = ["wrist_roll"]          # lerobot gives it the whole encoder instead of a sweep
MIN_VOLTS = 6.0                     # STS3215 runs on 6-8.4 V; ~5 V means only USB power reaches the bus
CONFIRMED = {"release", "homing", "save", "engage", "apply_file", "restore_motors", "restore_mirror"}


def _extend() -> dict:
    try: return json.loads(LIMITS_FILE.read_text()).get("extend", {})
    except (OSError, ValueError): return {}


class InitSession:
    def __init__(self, reason: str, ctrl=None):
        self.reason, self.lock, self.bus, self.error = reason, threading.Lock(), None, None
        self.ctrl = ctrl; self.cams = CameraPump(ctrl) if ctrl is not None else None
        self._pos: dict = {}; self.notes: list[str] = []; self.step = "idle"
        self.homings: dict | None = None; self.sweep: dict | None = None; self._sweeping = False
        self.docs = lerobot_docs()

    # --- bus -------------------------------------------------------------------------------------
    def _open(self):
        if self.bus is not None: return self.bus
        from lerobot.motors import Motor, MotorNormMode, SerialMotorsBus
        motors = {j: Motor(i + 1, "sts3215", MotorNormMode.RANGE_0_100 if j == "gripper" else MotorNormMode.DEGREES)
                  for i, j in enumerate(JOINTS)}
        bus = SerialMotorsBus(port=PORT, motors=motors)
        bus.connect()                   # handshake only: pings the six ids, writes nothing
        self.bus = bus; log("init: bus open (no writes, torque untouched)")
        return bus

    def close(self):
        self._sweeping = False
        if self.cams: self.cams.close()
        with self.lock:
            if self.bus is not None:
                try: self.bus.disconnect(disable_torque=False)
                except Exception as e: log(f"init: close: {e}")
                self.bus = None

    def _motors(self) -> dict:
        """Per joint: calibration registers + torque, position, voltage, temperature (raw)."""
        bus = self._open(); cal = bus.read_calibration(); out = {}
        for j in JOINTS:
            r = {k: bus.read(k, j, normalize=False, num_retry=3)
                 for k in ("Torque_Enable", "Present_Position", "Present_Voltage", "Present_Temperature")}
            c = cal[j]
            out[j] = {"id": c.id, "drive_mode": c.drive_mode, "homing_offset": c.homing_offset,
                      "range_min": c.range_min, "range_max": c.range_max, "torque": bool(r["Torque_Enable"]),
                      "position": r["Present_Position"], "volts": r["Present_Voltage"] / 10, "temp_c": r["Present_Temperature"]}
        return out

    # --- state -----------------------------------------------------------------------------------
    def state(self) -> dict:
        motors, bus_err = {}, None
        if not self._sweeping:
            try:
                with self.lock: motors = self._motors()
            except Exception as e:
                bus_err = str(e); self.close_quiet()
        file_cal, file_err = calib.load(calib.file_path())
        mirror_cal, mirror_err = calib.load(calib.MIRROR)
        extend = _extend()
        if motors: self._pos = {j: m["position"] for j, m in motors.items()}
        elif self.sweep: self._pos.update({j: v["pos"] for j, v in self.sweep.items()})
        ref = file_cal if file_err is None else ({j: m for j, m in motors.items()} if motors else None)
        pose = calib.degrees(ref, self._pos) if ref and len(self._pos) == len(JOINTS) else {}
        side = self._side(pose)
        rows = calib.compare(file_cal, motors) if motors else []
        for r in rows: r["extended"] = r["joint"] in extend
        checks = [
            {"name": "serial port", "ok": Path(PORT).exists(), "detail": PORT},
            {"name": "motors respond", "ok": bool(motors) or self._sweeping, "detail": bus_err or f"{len(motors)}/6 on the bus"},
            {"name": "motor supply voltage", "ok": bool(motors) and min(m["volts"] for m in motors.values()) >= MIN_VOLTS,
             "detail": (f"{min(m['volts'] for m in motors.values()):.1f} V (need >= {MIN_VOLTS} V: is the power supply plugged in?)"
                        if motors else "needs the bus")},
            {"name": "calibration file", "ok": file_err is None, "detail": file_err or str(calib.file_path())},
            {"name": "file matches motors", "ok": bool(rows) and all(r["ok"] for r in rows),
             "detail": "see the table" if rows else "needs the file and the bus"},
            {"name": "backup copy in config/", "ok": mirror_err is None and mirror_cal == file_cal,
             "detail": mirror_err or ("same as the file" if mirror_cal == file_cal else "differs from the file")},
            {"name": "no ESTOP", "ok": not ESTOP.exists(), "detail": "ESTOP file present" if ESTOP.exists() else ""},
            {"name": "config files", "ok": all(p.is_file() for p in (LIMITS_FILE, REST_FILE, FLOOR_FILE)),
             "detail": ", ".join(p.name for p in (LIMITS_FILE, REST_FILE, FLOOR_FILE) if not p.is_file()) or "limits, rest, floor"},
            {"name": "lerobot", "ok": bool(self.docs.get("version")), "detail": f"{self.docs.get('version')} at {self.docs.get('path')}"},
        ]
        if self.cams: checks += [{"name": f"camera {n}", "ok": c["ok"], "detail": c["detail"]} for n, c in self.cams.status().items()]
        return {"mode": "init", "reason": self.reason, "checks": checks, "motors": motors, "file": file_cal,
                "compare": rows, "notes": self.notes, "pose": pose, "side": side,
                "pose_from": "file" if file_err is None else "motor registers" if pose else None, "step": self.step, "sweep": self.sweep, "error": self.error,
                "docs": self.docs}

    def _side(self, pose: dict) -> dict:
        """The floor-model side view, same shape as /state.floor on the control dashboard."""
        floor = getattr(self.ctrl, "floor", None)
        arm = floor.arm_points(pose) if floor is not None and pose else None
        tip = floor.z(pose) if arm else None
        return {"arm": arm, "margin_cm": FLOOR_MARGIN * 100, "grasp_cm": GRASP_Z * 100,
                "tip_z_cm": None if tip is None else round(tip * 100, 1)}

    def close_quiet(self):
        try:
            if self.bus is not None: self.bus.disconnect(disable_torque=False)
        except Exception: pass
        self.bus = None

    # --- commands --------------------------------------------------------------------------------
    def command(self, a: str, args: dict) -> dict:
        if a in CONFIRMED and args.get("confirm") != "yes":
            return {"error": f"'{a}' needs confirm=yes"}
        fn = getattr(self, f"cmd_{a}", None)
        if fn is None: return {"error": f"unknown init command {a!r}"}
        try:
            self.error = None; res = fn() or {}
            log(f"init: {a} done"); return res
        except Exception as e:
            self.error = f"{a}: {e}"; log(f"init: {a} failed: {e}"); return {"error": str(e)}

    def cmd_cameras(self):
        """Retry any camera that is not streaming now (they also retry on their own every 5 s)."""
        if self.cams: self.cams.retry_now()

    def cmd_restore_motors(self):
        """Rebuild the file from the motor registers. Writes the file only."""
        with self.lock: motors = self._motors()
        cal, self.notes = calib.from_motors(motors, _extend(), calib.read_log())
        return {"saved": calib.save(cal), "notes": self.notes}

    def cmd_restore_mirror(self):
        cal, err = calib.load(calib.MIRROR)
        if err: raise ValueError(err)
        return {"saved": calib.save(cal)}

    # wizard, lerobot's record_calibration split into operator-confirmed steps
    def cmd_release(self):
        with self.lock:
            bus = self._open(); bus.disable_torque(num_retry=3); bus.set_operating_mode("position")
        self.step, self.homings, self.sweep = "released", None, None

    def _torque_off(self, bus):
        on = [j for j in JOINTS if bus.read("Torque_Enable", j, normalize=False, num_retry=3)]
        if on: raise RuntimeError(f"torque still on for {on}: do step 1 (release) first")

    def cmd_homing(self):
        with self.lock:
            bus = self._open(); self._torque_off(bus)
            self.homings = {j: int(v) for j, v in bus.set_half_turn_homings(JOINTS).items()}
        self.step, self.sweep = "homed", None

    def cmd_sweep_start(self):
        if self.step not in ("homed", "swept"): raise RuntimeError("set the homing (step 2) first")
        recorded = [j for j in JOINTS if j not in FULL_TURN]
        with self.lock: start = self.bus.sync_read("Present_Position", recorded, normalize=False, num_retry=5)
        self.sweep = {j: {"min": int(v), "pos": int(v), "max": int(v)} for j, v in start.items()}
        self._sweeping, self.step = True, "sweeping"
        threading.Thread(target=self._sweep_loop, args=(recorded,), daemon=True).start()

    def _sweep_loop(self, recorded):
        while self._sweeping:
            try:
                with self.lock: pos = self.bus.sync_read("Present_Position", recorded, normalize=False, num_retry=5)
            except Exception as e:
                self.error = f"sweep: {e}"; time.sleep(0.2); continue
            for j, v in pos.items():
                s = self.sweep[j]; s["pos"] = int(v); s["min"] = min(s["min"], int(v)); s["max"] = max(s["max"], int(v))
            time.sleep(0.02)

    def cmd_sweep_stop(self):
        self._sweeping = False; time.sleep(0.1)
        still = [j for j, s in (self.sweep or {}).items() if s["min"] == s["max"]]
        self.step = "swept"
        if still: raise ValueError(f"these joints did not move: {still} — start the sweep again")

    def cmd_save(self):
        from lerobot.motors import MotorCalibration
        if self.step != "swept" or not self.homings or not self.sweep: raise RuntimeError("finish the sweep (step 3) first")
        res = self.bus.resolution("sts3215") - 1
        cal = {j: {"id": i + 1, "drive_mode": 0, "homing_offset": self.homings[j],
                   "range_min": 0 if j in FULL_TURN else self.sweep[j]["min"],
                   "range_max": res if j in FULL_TURN else self.sweep[j]["max"]} for i, j in enumerate(JOINTS)}
        with self.lock:
            self._torque_off(self.bus)
            self.bus.write_calibration({j: MotorCalibration(**c) for j, c in cal.items()})
        self.step = "saved"
        return {"saved": calib.save(cal)}

    def cmd_apply_file(self):
        """lerobot's 'use provided calibration file': write the file's values into the motors (torque must be off)."""
        from lerobot.motors import MotorCalibration
        cal, err = calib.load(calib.file_path())
        if err: raise ValueError(err)
        with self.lock:
            bus = self._open(); self._torque_off(bus)
            bus.write_calibration({j: MotorCalibration(**cal[j]) for j in JOINTS})
        self.step = "saved"

    def cmd_engage(self):
        with self.lock: self._open().enable_torque(num_retry=3)
        self.step = "idle"


SESSION: InitSession | None = None


def normal_state(cams: dict) -> dict:
    """What /init can show while the control loop owns the bus: the files, the cameras, lerobot's docs."""
    file_cal, file_err = calib.load(calib.file_path()); mirror_cal, mirror_err = calib.load(calib.MIRROR)
    checks = [
        {"name": "calibration file", "ok": file_err is None, "detail": file_err or str(calib.file_path())},
        {"name": "backup copy in config/", "ok": mirror_err is None and mirror_cal == file_cal,
         "detail": mirror_err or ("same as the file" if mirror_cal == file_cal else "differs from the file")},
        {"name": "no ESTOP", "ok": not ESTOP.exists(), "detail": "ESTOP file present" if ESTOP.exists() else ""},
    ] + [{"name": f"camera {n}", "ok": down is None, "detail": "streaming" if down is None else f"down {down:.0f} s"}
         for n, down in cams.items()]
    return {"mode": "normal", "reason": None, "checks": checks, "motors": {}, "file": file_cal, "compare": [],
            "notes": [], "step": "idle", "sweep": None, "error": None, "docs": lerobot_docs()}


def run(reason: str, serve, ctrl=None) -> None:
    """Init mode main: serve the dashboard (``serve()``), wait for Ctrl-C, close the bus without touching torque."""
    global SESSION
    SESSION = InitSession(reason, ctrl)
    log(f"INIT MODE ({reason}): no control loop; open http://localhost:8765/init")
    serve()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        SESSION.close(); log("init mode: bus closed, torque left as it was")
