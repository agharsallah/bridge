"""The lerobot calibration file: where it lives, checking it, comparing it with the motors, restoring it.

Only files are written here, never a motor register. Format and location are lerobot's own
(``Robot.calibration_fpath``): ``{joint: {id, drive_mode, homing_offset, range_min, range_max}}`` in
``$HF_LEROBOT_CALIBRATION/robots/so_follower/<ROBOT_ID>.json``. Every save also mirrors the file to
``config/calibration/`` so a cleared cache can be restored without touching the arm.
"""

import json
import re
import shutil
import time
from pathlib import Path

from ..paths import CONFIG_DIR, LOG_FILE, VAR_DIR
from ..settings import JOINTS, ROBOT_ID

FIELDS = ("id", "drive_mode", "homing_offset", "range_min", "range_max")
MIRROR = CONFIG_DIR / "calibration" / f"{ROBOT_ID}.json"
BACKUP_DIR = VAR_DIR / "calibration_backups"
_LOGGED = re.compile(r"EXTENDED (\w+): .*\(cal was (-?\d+)\.\.(-?\d+)\)")


def file_path() -> Path:
    """lerobot's path for this robot's calibration file (honours $HF_LEROBOT_CALIBRATION)."""
    try:
        from lerobot.utils.constants import HF_LEROBOT_CALIBRATION
        base = Path(HF_LEROBOT_CALIBRATION)
    except ImportError:  # tests run without lerobot
        base = Path("~/.cache/huggingface/lerobot/calibration").expanduser()
    return base / "robots" / "so_follower" / f"{ROBOT_ID}.json"


def problem(cal) -> str | None:
    """Why ``cal`` cannot drive the arm, or None when it is complete."""
    if not isinstance(cal, dict): return "not a JSON object"
    for j in JOINTS:
        c = cal.get(j)
        if not isinstance(c, dict): return f"{j} missing"
        missing = [f for f in FIELDS if not isinstance(c.get(f), int)]
        if missing: return f"{j}: {', '.join(missing)} missing or not an integer"
        if c["range_min"] >= c["range_max"]: return f"{j}: range_min {c['range_min']} >= range_max {c['range_max']}"
    return None


def load(path: Path) -> tuple[dict | None, str | None]:
    """(calibration, problem). A missing or unreadable file is a problem, never an exception."""
    if not path.is_file(): return None, f"{path} does not exist"
    try: cal = json.loads(path.read_text())
    except (OSError, ValueError) as e: return None, f"{path.name} unreadable: {e}"
    return cal, problem(cal)


def startup_problem() -> str | None:
    """Why the daemon cannot start normally (calibration file missing or broken), or None."""
    _, err = load(file_path())
    return err


def compare(file_cal: dict | None, motors: dict) -> list[dict]:
    """One row per joint: file vs motor values of homing_offset/range_min/range_max, and whether they match."""
    rows = []
    for j in JOINTS:
        f, m = (file_cal or {}).get(j) or {}, motors.get(j) or {}
        fields = {k: {"file": f.get(k), "motor": m.get(k)} for k in ("homing_offset", "range_min", "range_max")}
        rows.append({"joint": j, **fields, "ok": all(v["file"] == v["motor"] for v in fields.values())})
    return rows


def degrees(cal: dict, positions: dict) -> dict:
    """Raw positions -> the bridge's joint units, as lerobot's ``_normalize`` does: degrees about the middle of
    min..max (homing is already inside the raw position), gripper 0..100 across its range."""
    out = {}
    for j, pos in positions.items():
        c = cal[j]; lo, hi = c["range_min"], c["range_max"]
        out[j] = (round(min(max((pos - lo) / (hi - lo), 0.0), 1.0) * 100, 1) if j == "gripper"
                  else round((pos - (lo + hi) / 2) * 360 / 4095, 1))
    return out


def logged_ranges(log_text: str) -> dict[str, list[int]]:
    """The calibration range of each extended joint, as the bridge logged it on start ('cal was a..b'); last wins."""
    return {j: [int(a), int(b)] for j, a, b in _LOGGED.findall(log_text)}


def from_motors(motors: dict, extend: dict, log_text: str) -> tuple[dict, list[str]]:
    """Rebuild the file from what the motors hold. Returns (calibration, notes).

    Homing offsets are always the motors'. The min/max registers of a joint listed under ``extend`` in
    limits.json may hold the bridge's widened limits (written to RAM while the arm stays powered); lerobot
    puts 0 deg at the middle of min/max, so taking them would shift that joint. For those joints the range
    the bridge logged on start wins when it differs from the registers.
    """
    logged = logged_ranges(log_text); cal, notes = {}, []
    for j in JOINTS:
        m = motors[j]
        lo, hi = m["range_min"], m["range_max"]
        if j in extend and j in logged and logged[j] != [lo, hi]:
            notes.append(f"{j}: registers {lo}..{hi} look widened by the bridge; using logged range {logged[j][0]}..{logged[j][1]}")
            lo, hi = logged[j]
        elif j in extend and j not in logged:
            notes.append(f"{j}: extended joint with no logged range; registers {lo}..{hi} used, check against the arm")
        cal[j] = {"id": int(m["id"]), "drive_mode": int(m.get("drive_mode", 0)), "homing_offset": int(m["homing_offset"]),
                  "range_min": int(lo), "range_max": int(hi)}
    return cal, notes


def save(cal: dict) -> str:
    """Write the calibration file (old one kept in var/calibration_backups/) and its config/ mirror."""
    err = problem(cal)
    if err: raise ValueError(f"refusing to save an incomplete calibration: {err}")
    path, text = file_path(), json.dumps(cal, indent=4) + "\n"
    if path.is_file():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, BACKUP_DIR / f"{ROBOT_ID}-{time.strftime('%Y%m%d-%H%M%S')}.json")
    for p in (path, MIRROR):
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
    return str(path)


def mirror_if_missing() -> bool:
    """Copy a valid calibration file to config/calibration/ when no mirror exists yet (called on a normal start)."""
    cal, err = load(file_path())
    if err or MIRROR.is_file(): return False
    MIRROR.parent.mkdir(parents=True, exist_ok=True); MIRROR.write_text(json.dumps(cal, indent=4) + "\n")
    return True


def read_log() -> str:
    try: return LOG_FILE.read_text(errors="replace")
    except OSError: return ""
