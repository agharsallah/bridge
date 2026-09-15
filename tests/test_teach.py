"""Guided teaching: the daemon asks, the operator drives, the daemon records — without an arm."""

import json
import sys
import threading

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from chess_synth import floor_model, taught, true_pose  # noqa: E402
from so101_bridge.chess import workspace as ws  # noqa: E402
from so101_bridge.shared import teach  # noqa: E402


class FakeCtrl:
    """Just enough controller for a teaching session: a pose to read and a place to put requests."""

    def __init__(self, pose=None):
        self.lock = threading.Lock()
        self.pose = pose or {"shoulder_pan": 1.0, "shoulder_lift": 2.0, "elbow_flex": 3.0,
                             "wrist_flex": 4.0, "wrist_roll": 5.0, "gripper": 6.0}
        self.requests = []
        self.teach = None
        self.routine_thread = None
        self.floor = floor_model()

    def snapshot(self):
        return {"present": dict(self.pose), "limits": {}}, {}

    def request(self, cmd):
        self.requests.append(cmd)


SESSION = {"name": "t", "label": "Test session", "store": "chess", "steps": [
    {"id": "hello", "kind": "note", "prompt": "Ready?"},
    {"id": "a1", "kind": "contact", "to": "board.corners.a1", "prompt": "a1"},
    {"id": "h1", "kind": "contact", "to": "board.corners.h1", "prompt": "h1"},
    {"id": "e4", "kind": "contact", "append": "board.extra", "match": "square", "with": {"square": "e4"},
     "prompt": "e4"},
    {"id": "grasp_p", "kind": "grasp", "to": "pieces.p", "square": "e4", "prompt": "pawn"},
    {"id": "feed", "kind": "contact", "to": "slots.feed.pose", "slot": "feed", "prompt": "feed"},
    {"id": "probe", "kind": "probe", "uv": [12.7, 12.7], "z_cm": 1.0, "prompt": "probe"},
]}


@pytest.fixture
def session(tmp_path, monkeypatch):
    (tmp_path / "teach").mkdir(exist_ok=True)
    (tmp_path / "teach" / "t.json").write_text(json.dumps(SESSION))
    return SESSION


def start(ctrl):
    return teach.start(ctrl, "t")


def test_a_session_walks_the_steps_and_records_each_one(session):
    ctrl = FakeCtrl()
    s = start(ctrl)
    assert s.state()["id"] == "hello" and s.state()["steps"] == 7
    s.done()
    assert s.state()["id"] == "a1" and s.state()["step"] == 2
    s.done()
    d = ws.load()
    assert d["board"]["corners"]["a1"]["shoulder_pan"] == 1.0
    assert "gripper" not in d["board"]["corners"]["a1"]           # a contact records the arm, not the jaws
    assert s.state()["recorded"]["a1"]


def test_each_step_is_written_immediately_so_an_abort_keeps_what_it_learned(session):
    ctrl = FakeCtrl()
    s = start(ctrl); s.done(); s.done()                           # note, a1
    s.abort()
    assert "a1" in ws.load()["board"]["corners"]


def test_append_steps_replace_rather_than_duplicate(session):
    ctrl = FakeCtrl()
    s = start(ctrl)
    for _ in range(4): s.done()                                   # note, a1, h1, e4
    assert [e["square"] for e in ws.load()["board"]["extra"]] == ["e4"]
    s.fix("e4"); ctrl.pose["shoulder_pan"] = 9.0; s.done()
    extra = ws.load()["board"]["extra"]
    assert len(extra) == 1 and extra[0]["pose"]["shoulder_pan"] == 9.0


def test_a_grasp_records_the_jaw_opening_and_the_height_above_the_board(session):
    """The pose is only the raw material: what chess needs is a height and a lean it can reuse anywhere."""
    d = taught(grasps=())                                          # corners taught, no grasps yet
    ws.save(d)
    ctrl = FakeCtrl(pose={**true_pose(*ws.square_uv(d, "e4"), 3.0), "wrist_roll": 0.0, "gripper": 7.5})
    s = start(ctrl)
    s.fix("grasp_p"); s.done()
    p = ws.load()["pieces"]["p"]
    assert p["close"] == 7.5
    assert p["grasp_z_cm"] == pytest.approx(3.0, abs=0.1)
    assert p["lean_deg"] is not None and p["taught_on"] == "e4"


def test_a_slot_gets_its_board_coordinates_when_the_model_is_fitted(session):
    d = taught(); d["slots"] = {}; ws.save(d)
    ctrl = FakeCtrl(pose={**true_pose(-4.0, 12.7, 1.4), "wrist_roll": 0.0, "gripper": 8.0})
    s = start(ctrl)
    s.fix("feed"); s.done()
    slot = ws.load()["slots"]["feed"]
    assert slot["pose"] and slot["uv"] == pytest.approx([-4.0, 12.7], abs=0.2)


def test_a_probe_records_where_the_tip_really_touched(session):
    ctrl = FakeCtrl()
    s = start(ctrl); s.fix("probe"); s.done()
    assert ws.load()["probes"] == [{"u": 12.7, "v": 12.7, "z_touch_cm": 1.0, "note": "probe"}]


def test_skip_back_and_fix_move_around_without_losing_anything(session):
    ctrl = FakeCtrl()
    s = start(ctrl); s.done()                                      # at a1
    s.skip()
    assert s.state()["skipped"] == ["a1"] and s.state()["id"] == "h1"
    s.back()
    assert s.state()["id"] == "a1"
    s.done()
    assert s.state()["skipped"] == [] and "a1" in s.state()["recorded"]
    assert s.fix("nope")["message"].startswith("no step")


def test_the_session_reports_what_was_never_recorded(session):
    ctrl = FakeCtrl()
    s = start(ctrl)
    for _ in range(len(SESSION["steps"])): s.skip()
    assert s.state()["finished"]
    assert "not recorded" in s.state()["message"] and "grasp_p" in s.state()["message"]


def test_free_and_engage_go_through_the_control_loop_like_any_other_command(session):
    ctrl = FakeCtrl()
    teach.command(ctrl, "teach_start", session="t")
    teach.command(ctrl, "teach_free")
    teach.command(ctrl, "teach_engage")
    assert [c["action"] for c in ctrl.requests] == ["release", "engage"]


def test_commands_are_ignored_rather_than_crashing_with_no_session(session):
    ctrl = FakeCtrl()
    assert teach.command(ctrl, "teach_done") is None


def test_a_broken_session_file_is_refused_by_name(tmp_path):
    (tmp_path / "teach").mkdir(exist_ok=True)
    (tmp_path / "teach" / "bad.json").write_text(json.dumps({"steps": [{"id": "x", "kind": "wat"}]}))
    with pytest.raises(ValueError, match="unknown kind"):
        teach.load_session("bad")
    with pytest.raises(FileNotFoundError):
        teach.load_session("missing")


def test_the_shipped_chess_session_matches_the_schema_it_writes_into():
    """The session file in config/teach is part of the product: every path it writes must exist in chess.json."""
    from so101_bridge.paths import CONFIG_DIR
    spec = json.loads((CONFIG_DIR / "teach" / "chess_board.json").read_text())
    for step in spec["steps"]:
        kind = step.get("kind", "contact")
        assert kind in teach.KINDS
        if kind == "note": continue
        target = step.get("to") or step.get("append")
        assert target, step["id"]
        root = target.split(".")[0]
        assert root in ws.DEFAULTS, (step["id"], root)
        if kind == "grasp": assert step["to"].split(".")[1] in ws.PIECES
