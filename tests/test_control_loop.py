"""The control loop and its guards, driven against a fake motor bus."""

import json

import numpy as np
import pytest

from so101_bridge import app
from so101_bridge.controller import Controller
from so101_bridge.settings import JOINTS, MAX_STEP_DEG


class FakeBus:
    def __init__(self, pos):
        self.pos = pos
        self.torque = True
        self.writes = []
        self.load = dict.fromkeys(JOINTS, 0)

    def sync_read(self, reg, normalize=True, num_retry=0):
        if reg == "Present_Position":
            return {f"{j}.pos": v for j, v in self.pos.items()}
        if reg == "Present_Load":
            return dict(self.load)
        return dict.fromkeys(JOINTS, 0)

    def write(self, reg, motor, value, normalize=True, num_retry=0):
        self.writes.append((reg, motor, value))

    def read(self, reg, motor, normalize=True, num_retry=0):
        return 0

    def enable_torque(self, num_retry=0):
        self.torque = True

    def disable_torque(self):
        self.torque = False


class FakeRobot:
    """Moves instantly to whatever the loop commands, so `present` tracks `cmd` without lag."""

    def __init__(self):
        self.pos = {j: 0.0 for j in JOINTS}
        self.bus = FakeBus(self.pos)
        self.actions = []
        self.disconnected = False

    def get_observation(self):
        frame = np.zeros((540, 960, 3), np.uint8)
        return {**{f"{j}.pos": v for j, v in self.pos.items()}, "top": frame, "wrist": frame}

    def send_action(self, action):
        self.actions.append(dict(action))
        for k, v in action.items():
            self.pos[k.removesuffix(".pos")] = v

    def disconnect(self):
        self.disconnected = True


@pytest.fixture
def rig(tmp_path, monkeypatch):
    for name, path in [("CMD_DIR", tmp_path / "cmd"), ("DONE_DIR", tmp_path / "done"),
                       ("STATE_FILE", tmp_path / "state.json"), ("ESTOP", tmp_path / "ESTOP")]:
        path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(app, name, path)
    (tmp_path / "cmd").mkdir(exist_ok=True); (tmp_path / "done").mkdir(exist_ok=True)
    monkeypatch.setattr(app, "SNAPSHOT", {c: tmp_path / f"{c}.jpg" for c in ("top", "wrist")})
    ctrl = Controller()
    monkeypatch.setattr(ctrl.floor, "params", None)      # floor guard off: this rig has no geometry
    limits = {j: [-90.0, 90.0] for j in JOINTS}
    limits["gripper"] = [0.0, 100.0]
    return FakeRobot(), ctrl, limits, tmp_path


def state_of(tmp_path):
    return json.loads((tmp_path / "state.json").read_text())


def test_loop_writes_state_and_holds_still(rig):
    robot, ctrl, limits, tmp = rig
    app.control_loop(robot, ctrl, limits, max_iters=12)
    st = state_of(tmp)
    assert st["mode"] == "hold" and st["torque_on"] is True
    assert all(abs(v) < 1e-6 for v in st["present"].values())


def test_goal_moves_the_arm_and_reaches(rig):
    robot, ctrl, limits, tmp = rig
    ctrl.request({"action": "goal", "goal": {"shoulder_pan": 5.0}, "speed": 30.0, "src": "test"})
    app.control_loop(robot, ctrl, limits, max_iters=40)
    assert robot.pos["shoulder_pan"] == pytest.approx(5.0, abs=0.1)
    assert state_of(tmp)["mode"] == "reached"


def test_goal_beyond_the_step_cap_is_capped(rig):
    robot, ctrl, limits, tmp = rig
    ctrl.request({"action": "goal", "goal": {"shoulder_pan": 80.0}, "speed": 30.0, "src": "test"})
    app.control_loop(robot, ctrl, limits, max_iters=5)
    assert state_of(tmp)["goal"]["shoulder_pan"] == pytest.approx(MAX_STEP_DEG, abs=0.01)


def test_goal_outside_the_limits_is_clamped(rig):
    robot, ctrl, limits, tmp = rig
    limits["shoulder_pan"] = [-4.0, 4.0]
    ctrl.request({"action": "goal", "goal": {"shoulder_pan": 50.0}, "speed": 30.0, "src": "test"})
    app.control_loop(robot, ctrl, limits, max_iters=5)
    assert state_of(tmp)["goal"]["shoulder_pan"] == pytest.approx(4.0)


def test_estop_freezes_and_ignores_commands(rig):
    robot, ctrl, limits, tmp = rig
    (tmp / "ESTOP").touch()
    ctrl.request({"action": "goal", "goal": {"shoulder_pan": 5.0}, "speed": 30.0, "src": "test"})
    app.control_loop(robot, ctrl, limits, max_iters=12)
    st = state_of(tmp)
    assert st["mode"] == "estop" and st["estop"] is True
    assert robot.pos["shoulder_pan"] == 0.0


def test_command_files_are_consumed_and_archived(rig):
    robot, ctrl, limits, tmp = rig
    (tmp / "cmd" / "001.json").write_text(json.dumps({"action": "goal", "goal": {"wrist_roll": 3.0}, "speed": 30.0}))
    app.control_loop(robot, ctrl, limits, max_iters=30)
    assert not list((tmp / "cmd").glob("*.json"))
    assert (tmp / "done" / "001.json").is_file()
    assert robot.pos["wrist_roll"] == pytest.approx(3.0, abs=0.1)


def test_release_drops_torque_and_engage_restores_it(rig):
    robot, ctrl, limits, tmp = rig
    ctrl.request({"action": "release", "src": "test"})
    app.control_loop(robot, ctrl, limits, max_iters=4)
    assert robot.bus.torque is False and state_of(tmp)["mode"] == "released"
    ctrl.request({"action": "engage", "src": "test"})
    app.control_loop(robot, ctrl, limits, max_iters=4)
    assert robot.bus.torque is True and state_of(tmp)["mode"] == "hold"


def test_load_guard_freezes_a_move(rig):
    robot, ctrl, limits, tmp = rig
    robot.bus.load = {j: 999 for j in JOINTS}
    ctrl.request({"action": "goal", "goal": {"shoulder_lift": 8.0}, "speed": 30.0, "src": "test"})
    app.control_loop(robot, ctrl, limits, max_iters=6)
    assert state_of(tmp)["mode"] == "stalled"


def test_unknown_joint_is_ignored(rig):
    robot, ctrl, limits, tmp = rig
    ctrl.request({"action": "goal", "goal": {"nope": 5.0}, "speed": 30.0, "src": "test"})
    app.control_loop(robot, ctrl, limits, max_iters=6)
    assert "nope" not in state_of(tmp)["goal"]


def test_register_write_is_whitelisted(rig):
    robot, ctrl, limits, _ = rig
    ctrl.request({"action": "write", "reg": "Torque_Enable", "motor": "gripper", "value": 1})
    ctrl.request({"action": "write", "reg": "Firmware_Version", "motor": "gripper", "value": 1})
    app.control_loop(robot, ctrl, limits, max_iters=4)
    written = [w for w in robot.bus.writes if w[0] in ("Torque_Enable", "Firmware_Version")]
    assert ("Torque_Enable", "gripper", 1) in written
    assert not [w for w in written if w[0] == "Firmware_Version"]
