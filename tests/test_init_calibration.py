"""Calibration file recovery: the part of /init that decides where 0 deg of every joint is."""

import json

import pytest

from so101_bridge.initialisation import calibration as calib
from so101_bridge.settings import JOINTS

# the arm's registers on 2026-11-11 (after a power cycle, so no widened limits)
MOTORS = {
    "shoulder_pan": dict(id=1, homing_offset=1896, range_min=1078, range_max=3234),
    "shoulder_lift": dict(id=2, homing_offset=1937, range_min=2034, range_max=3092),
    "elbow_flex": dict(id=3, homing_offset=-1650, range_min=150, range_max=2047),
    "wrist_flex": dict(id=4, homing_offset=-1239, range_min=810, range_max=2785),
    "wrist_roll": dict(id=5, homing_offset=250, range_min=0, range_max=4095),
    "gripper": dict(id=6, homing_offset=-1480, range_min=2046, range_max=3330),
}
EXTEND = {"shoulder_lift": [-47, 133.0], "elbow_flex": [-86.0, 84.5]}
LOG = ("11:15:40 EXTENDED shoulder_lift: soft -47.0..133.0 deg, motor limits 2017..4087 (cal was 2034..3092)\n"
       "11:15:40 EXTENDED elbow_flex: soft -86.0..84.5 deg, motor limits 108..2071 (cal was 150..2047)\n")


def test_rebuild_from_unwidened_registers_is_exact():
    cal, notes = calib.from_motors(MOTORS, EXTEND, LOG)
    assert notes == [] and calib.problem(cal) is None
    assert {j: (c["homing_offset"], c["range_min"], c["range_max"]) for j, c in cal.items()} == \
           {j: (m["homing_offset"], m["range_min"], m["range_max"]) for j, m in MOTORS.items()}


def test_widened_registers_fall_back_to_logged_range():
    widened = {**MOTORS, "shoulder_lift": {**MOTORS["shoulder_lift"], "range_min": 2017, "range_max": 4087}}
    cal, notes = calib.from_motors(widened, EXTEND, LOG)
    assert (cal["shoulder_lift"]["range_min"], cal["shoulder_lift"]["range_max"]) == (2034, 3092)
    assert cal["shoulder_lift"]["homing_offset"] == 1937 and len(notes) == 1


def test_extended_joint_without_log_is_flagged():
    _, notes = calib.from_motors(MOTORS, EXTEND, "")
    assert sorted(n.split(":")[0] for n in notes) == ["elbow_flex", "shoulder_lift"]


@pytest.mark.parametrize("broken", [
    {}, {j: MOTORS[j] for j in JOINTS[:-1]},
    {**MOTORS, "gripper": {**MOTORS["gripper"], "drive_mode": 0, "range_min": 3330, "range_max": 2046}},
])
def test_incomplete_calibration_starts_init_mode(tmp_path, monkeypatch, broken):
    f = tmp_path / "my_follower.json"; f.write_text(json.dumps(broken))
    monkeypatch.setattr(calib, "file_path", lambda: f)
    assert calib.startup_problem() is not None


def test_missing_file_starts_init_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(calib, "file_path", lambda: tmp_path / "nope.json")
    assert "does not exist" in calib.startup_problem()


def test_save_backs_up_old_file_and_mirrors(tmp_path, monkeypatch):
    f = tmp_path / "cache" / "my_follower.json"; f.parent.mkdir(); f.write_text("{}")
    monkeypatch.setattr(calib, "file_path", lambda: f)
    monkeypatch.setattr(calib, "MIRROR", tmp_path / "config" / "my_follower.json")
    monkeypatch.setattr(calib, "BACKUP_DIR", tmp_path / "backups")
    cal, _ = calib.from_motors(MOTORS, {}, "")
    calib.save(cal)
    assert json.loads(f.read_text()) == cal == json.loads(calib.MIRROR.read_text())
    assert [p.read_text() for p in (tmp_path / "backups").iterdir()] == ["{}"]
    with pytest.raises(ValueError):
        calib.save({})


def test_degrees_match_lerobot_normalisation():
    pos = {"elbow_flex": 2047, "shoulder_pan": (1078 + 3234) // 2, "gripper": 2046}
    deg = calib.degrees(MOTORS, pos)
    assert deg == {"elbow_flex": round((2047 - 1098.5) * 360 / 4095, 1), "shoulder_pan": 0.0, "gripper": 0.0}
