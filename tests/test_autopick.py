"""The autonomous routine, driven against a simulated arm (no hardware, no sleeps).

The simulator answers the three questions the routine asks — where is the arm, what does the wrist
camera see, how high are the fingertips — with simple geometry, so the test exercises the real
servo logic, the phase reporting and the failure paths.
"""

import pytest

from so101_bridge import autopick
from so101_bridge.controller import Controller
from so101_bridge.poses import load_rest
from so101_bridge.settings import JOINTS


class SimArm:
    """Brick sits where pan=-19 / elbow~=60 centres it; fingertips at z = 0.046 - 0.0009*lift (m)."""

    def __init__(self, ctrl, start=None, brick=True, grasp_ok=True):
        self.ctrl = ctrl
        self.pose = dict.fromkeys(JOINTS, 0.0) | (start or {"shoulder_lift": -42.5, "elbow_flex": 80.7,
                                                            "wrist_flex": 37, "shoulder_pan": -2, "gripper": 10})
        self.brick, self.grasp_ok, self.moves, self.grips = brick, grasp_ok, [], []
        ctrl.floor.params = (0, 0, 0, 0, 1, 1, 1)                    # "fitted"
        ctrl.floor.z = self.z
        ctrl.snapshot = lambda: ({"present": dict(self.pose), "mode": "reached", "estop": False}, self.blobs())
        ctrl.goto = self.goto
        ctrl.goto_far = lambda pose, speed=4.0, step=10.0: self.goto(pose, speed)
        ctrl.gripper_to = self.gripper_to
        ctrl.see = lambda cam="wrist", timeout=1.5: self.blobs().get(cam)

    def z(self, pose):
        return 0.046 - 0.0009 * pose["shoulder_lift"]

    def blobs(self):
        if not self.brick:
            return {}
        cx = 540 - 6 * (self.pose["shoulder_pan"] + 19)            # pan+ -> scene shifts left (cx decreases)
        cy = 300 + 8 * (60 - self.pose["elbow_flex"])              # elbow unfold -> brick moves down
        long = int(400 - 4000 * max(0.0, self.z(self.pose)))       # bigger as the tips descend
        b = dict(cx=int(cx), cy=int(cy), long=long, w=120, h=long, x=int(cx) - 60, y=int(cy) - long // 2, area=long * 120)
        return {"top": dict(b, long=60), "wrist": b}

    def goto(self, pose, speed=4.0, timeout=15.0):
        self.moves.append(dict(pose))
        for j, v in pose.items():
            if j in self.pose:
                self.pose[j] = float(v)
        return True

    def gripper_to(self, value, speed=15.0):
        self.grips.append(value)
        if value < 30:
            self.pan_at_grasp = self.pose["shoulder_pan"]
        self.pose["gripper"] = 20.4 if (value < 30 and self.grasp_ok) else float(value if value >= 30 else 8.0)
        return self.pose["gripper"]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(autopick.time, "sleep", lambda *_: None)


def test_full_run_succeeds_and_reports_phases():
    ctrl = Controller()
    sim = SimArm(ctrl)
    autopick.run(ctrl)
    assert ctrl.routine_status == "done"
    phases = [h["phase"] for h in ctrl.progress["history"]]
    for p in ("ready", "align", "approach", "grasp", "lift", "carry", "release", "retreat", "done"):
        assert p in phases, phases
    assert sim.grips[:2] == [10, 60]                                     # grasped, then released
    rest = load_rest()
    assert abs(sim.pose["shoulder_lift"] - rest["shoulder_lift"]) < 3    # back at rest
    # the lateral servo had converged on the brick (pan -19 centres it) by the time the gripper closed
    assert abs(sim.pan_at_grasp + 19) < 6


def test_no_brick_fails_cleanly():
    ctrl = Controller()
    SimArm(ctrl, brick=False)
    autopick.run(ctrl)
    assert ctrl.routine_status.startswith("failed")
    assert "brick" in ctrl.routine_status


def test_missed_grasp_reopens_and_fails():
    ctrl = Controller()
    sim = SimArm(ctrl, grasp_ok=False)
    autopick.run(ctrl)
    assert ctrl.routine_status.startswith("failed")
    assert "missed" in ctrl.routine_status
    assert sim.grips[-1] == 60                                           # reopened after the miss


def test_unfitted_floor_model_refuses_to_run():
    ctrl = Controller()
    SimArm(ctrl)
    ctrl.floor.params = None
    autopick.run(ctrl)
    assert "floor model" in ctrl.routine_status
