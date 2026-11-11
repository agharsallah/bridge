"""The lab template: staged changes, the per-move allowance, the action contract, the state the page draws.

No hardware: a FakeCtrl records what would have been requested from the control loop.
"""

import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from chess_synth import floor_model  # noqa: E402
from so101_bridge import video  # noqa: E402
from so101_bridge.controller import Controller  # noqa: E402
from so101_bridge.lab import LAB, motion, staging  # noqa: E402
from so101_bridge.lab import actions as lab_actions  # noqa: E402
from so101_bridge.lab.cameras import FrameServer  # noqa: E402

POSE = {"shoulder_pan": 10.0, "shoulder_lift": 20.0, "elbow_flex": 40.0, "wrist_flex": 30.0, "wrist_roll": 0.0, "gripper": 12.0}
LIMITS = {"shoulder_pan": [-110, 110], "shoulder_lift": [-90, 90], "elbow_flex": [-90, 90], "wrist_flex": [-95, 95],
          "wrist_roll": [-150, 150], "gripper": [0, 100]}


class FakeCtrl:
    def __init__(self, estop=False):
        self.lock = threading.Lock()
        self.floor = floor_model()
        self.requests, self.estop = [], estop
        self.routine_status, self.routine_thread, self.routine_abort = "idle", None, threading.Event()
        self.video, self.events, self.frame, self.frame_t = video.VideoRecorder(), deque(), {}, {}
        self.tool = None

    mount_tool, unmount_tool = Controller.mount_tool, Controller.unmount_tool
    def event(self, kind, msg): self.events.append({"kind": kind, "msg": msg})

    def snapshot(self):
        return {"present": dict(POSE), "goal": dict(POSE), "mode": "hold", "torque_on": True, "estop": self.estop,
                "limits": LIMITS, "load": {}, "time": "12:00:00"}, {}

    def request(self, cmd): self.requests.append(cmd)
    def abort_auto(self, why=""): pass


# ------------------------------------------------------------------ staging
def test_staging_keeps_one_change_per_path_with_the_original_before():
    saved = {"motion": {"speed": 4.0}}
    p = staging.stage([], saved, "motion.speed", 6.0)
    p = staging.stage(p, saved, "motion.speed", 8.0)
    assert len(p) == 1 and p[0]["before"] == 4.0 and p[0]["value"] == 8.0
    assert staging.stage(p, saved, "motion.speed", 4.0) == []            # back to the saved value: nothing pending


def test_staging_delete_and_overlay():
    saved = {"points": {"a": {"pose": POSE}}}
    p = staging.stage([], saved, "points.b", {"pose": POSE})
    assert p[0]["is_new"]
    assert staging.stage(p, saved, "points.b", op="delete") == []         # deleting an unsaved point just un-stages it
    p = staging.stage(p, saved, "points.a", op="delete")
    eff = staging.overlay(saved, p)
    assert set(eff["points"]) == {"b"} and set(saved["points"]) == {"a"}  # the saved dict is never mutated


def test_staging_flags_a_change_whose_file_moved_underneath():
    p = staging.stage([], {"rest": None}, "rest", POSE)
    assert staging.stale(p, {"rest": None}) == []
    assert staging.stale(p, {"rest": {"shoulder_pan": 1.0}}) == [p[0]["id"]]


def test_store_applies_only_what_was_chosen():
    st = LAB.store
    st.stage("motion.speed", 6.0); st.stage("points.a", {"pose": POSE})
    speed = next(c for c in st.pending() if c["path"] == "motion.speed")
    assert st.apply([speed["id"]]) == 1
    assert st.saved()["motion"]["speed"] == 6.0 and "a" not in st.saved()["points"]
    assert [c["path"] for c in st.pending()] == ["points.a"]
    assert "a" in st.effective()["points"]                                 # staged points are already visitable


# ------------------------------------------------------------------ allowance
def test_allowance_covers_the_envelope_and_the_target_but_never_the_gripper():
    a = motion.allow_for({"elbow_flex": [-95, 95], "gripper": [0, 120]}, {"wrist_flex": 99.0, "elbow_flex": 0.0, "gripper": 110})
    assert a["elbow_flex"] == [-95.0, 95.0]
    assert a["wrist_flex"] == [98.5, 99.5]
    assert "gripper" not in a


# ------------------------------------------------------------------ actions
def test_release_needs_an_explicit_confirm():
    ctrl = FakeCtrl()
    res = LAB.command(ctrl, {"action": "release"})
    assert res["ok"] is False and res["needs_confirm"] and ctrl.requests == []
    assert LAB.command(ctrl, {"action": "release", "confirm": "yes"})["ok"]
    assert ctrl.requests[-1]["action"] == "release"


def test_moves_are_refused_under_estop_and_unknown_actions_explain_themselves():
    ctrl = FakeCtrl(estop=True)
    assert "ESTOP" in LAB.command(ctrl, {"action": "jog", "joint": "elbow_flex", "delta": 1})["error"]
    assert ctrl.requests == []
    assert "unknown action" in LAB.command(ctrl, {"action": "nope"})["error"]


def test_jog_carries_the_lab_envelope_and_never_skips_the_table_guard():
    LAB.store.stage("envelope.wrist_flex", [-100, 100])
    ctrl = FakeCtrl()
    assert LAB.command(ctrl, {"action": "jog", "joint": "wrist_flex", "delta": 2, "speed": 3})["ok"]
    req = ctrl.requests[-1]
    assert req["relative"] and req["goal"] == {"wrist_flex": 2.0} and req["allow"]["wrist_flex"] == [-100.0, 100.0]
    assert req["src"] != "floor_ok"


def test_recording_a_point_stages_the_present_pose_without_saving_it():
    ctrl = FakeCtrl()
    assert LAB.command(ctrl, {"action": "point_record", "name": "far corner", "role": "calibration"})["ok"]
    assert LAB.store.saved()["points"] == {}
    p = LAB.store.effective()["points"]["far_corner"]
    assert p["pose"]["elbow_flex"] == 40.0 and p["pose"]["gripper"] == 12.0 and p["role"] == "calibration"


def test_every_builtin_action_describes_itself():
    for a in LAB.describe():
        assert a["name"] in lab_actions.BUILTIN and a["description"] and isinstance(a["params"], dict)


# ------------------------------------------------------------------ tool
PEN = {"action": "tool_define", "name": "pen", "length_cm": 14, "out_cm": 3, "margin_cm": 0, "grip": 4}


def test_a_tool_is_mounted_only_once_saved_and_confirmed_then_locks_the_gripper():
    ctrl = FakeCtrl()
    assert LAB.command(ctrl, PEN)["ok"]
    res = LAB.command(ctrl, {"action": "tool_mount", "confirm": "yes"})
    assert not res["ok"] and "no saved tool" in res["error"]                            # staged is not enough
    LAB.command(ctrl, {"action": "changes_apply"})
    q = LAB.command(ctrl, {"action": "tool_mount"})
    assert q["needs_confirm"] and "3.0 cm" in q["needs_confirm"] and ctrl.tool is None
    assert LAB.command(ctrl, {"action": "tool_mount", "confirm": "yes"})["ok"]
    assert ctrl.tool["out_cm"] == 3.0 and ctrl.requests[-1] == {"action": "goal", "goal": {"gripper": 4.0}, "speed": 15.0, "src": "tool"}
    assert "mounted" in LAB.command(ctrl, {"action": "gripper", "value": 80})["error"]
    LAB.command(ctrl, {"action": "point_record", "name": "on paper"})
    assert LAB.store.effective()["points"]["on_paper"]["tool"]["name"] == "pen"
    st = LAB.state(ctrl)
    assert st["side"]["tool"]["state"] == "mounted" and st["tool"]["mounted"]["name"] == "pen"
    assert st["side"]["tip"]["z_cm"] == st["side"]["tool"]["front"][1]                    # the working tip is the pen tip
    assert st["guards"]["floor"]["closest"] in ("tips", "pen tip", "pen back")
    assert LAB.command(ctrl, {"action": "tool_unmount", "confirm": "yes"})["ok"] and ctrl.tool is None


# ------------------------------------------------------------------ state contract
def test_state_has_what_the_page_draws():
    ctrl = FakeCtrl()
    LAB.command(ctrl, {"action": "point_record", "name": "p1"})
    st = LAB.state(ctrl)
    assert [j["name"] for j in st["joints"]][:2] == ["shoulder_pan", "shoulder_lift"] and len(st["joints"]) == 6
    assert st["side"]["fitted"] and len(st["side"]["arm"]) == 4 and st["side"]["reach_cm"] > 30
    assert {m["name"] for m in st["side"]["markers"]} == {"p1", "rest"}
    assert st["points"][0]["status"] == "new" and st["points"][0]["tip"]["z_cm"] == st["side"]["tip"]["z_cm"]
    assert st["rest"]["source"] == "global"
    assert st["guards"]["floor"]["clearance_cm"] == round(st["side"]["tip"]["z_cm"] - st["side"]["margin_cm"], 1)
    assert len(st["config"]["pending"]) == 1 and st["video"]["dir"].endswith("videos/lab")


# ------------------------------------------------------------------ transport
def test_frames_are_encoded_once_per_new_frame_and_stale_ones_are_not_served():
    ctrl, fs = FakeCtrl(), FrameServer()
    ctrl.frame["top"] = np.zeros((90, 160, 3), np.uint8); ctrl.frame_t["top"] = time.time()
    a, t = fs.jpeg(ctrl, "top", 160, 70)
    b, _ = fs.jpeg(ctrl, "top", 160, 70)
    assert a and a is b and a[:2] == b"\xff\xd8"
    ctrl.frame_t["top"] = t - 60
    assert fs.jpeg(ctrl, "top", 160, 70)[0] is None


def test_a_raw_recording_goes_to_the_folder_it_was_given(tmp_path):
    rec = video.VideoRecorder()
    rec.start("lab clip", directory=tmp_path / "clips", raw=True)
    for i in range(5):
        rec.write({"top": np.zeros((54, 96, 3), np.uint8)}, raw={"top": np.full((108, 192, 3), i * 20, np.uint8), "wrist": None})
    assert rec.status()["dir"] == str(tmp_path / "clips") and rec.status()["raw"]
    name = rec.stop()
    assert (tmp_path / "clips" / name).stat().st_size > 0
    assert video.VideoRecorder.catalog(tmp_path / "clips")[0]["file"] == name


@pytest.fixture(autouse=True)
def _no_real_video_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(video, "VIDEO_DIR", tmp_path / "videos")
