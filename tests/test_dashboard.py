"""The dashboard must serve and route without an arm attached."""

import json
import urllib.request

import pytest

from so101_bridge import dashboard
from so101_bridge.controller import Controller


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "ESTOP", tmp_path / "ESTOP")
    ctrl = Controller()
    srv = dashboard.serve(ctrl, port=0)          # port 0: the OS picks a free one
    yield f"http://127.0.0.1:{srv.server_address[1]}", ctrl, tmp_path / "ESTOP"
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read()


def test_page_is_served(server):
    base, _, _ = server
    status, body = get(base + "/")
    assert status == 200 and b"SO-101" in body


def test_state_is_json(server):
    base, _, _ = server
    status, body = get(base + "/state")
    assert status == 200
    assert "floor" in json.loads(body)


def test_stop_button_raises_the_estop_flag(server):
    base, _, estop = server
    assert not estop.exists()
    get(base + "/cmd?a=stop")
    assert estop.exists()
    get(base + "/cmd?a=resume")
    assert not estop.exists()


def test_goal_button_queues_a_command(server):
    base, ctrl, _ = server
    get(base + "/cmd?a=goal&j=shoulder_pan&v=2&rel=1&speed=4")
    assert ctrl.pending and ctrl.pending[-1]["goal"] == {"shoulder_pan": 2.0}
    assert ctrl.pending[-1]["relative"] is True


def test_unknown_joint_is_refused(server):
    base, ctrl, _ = server
    get(base + "/cmd?a=goal&j=not_a_joint&v=2")
    assert not ctrl.pending


def test_log_tail_is_returned_as_json(server, tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "LOG_FILE", tmp_path / "bridge.log")
    (tmp_path / "bridge.log").write_text("\n".join(f"line {i}" for i in range(300)))
    base, _, _ = server
    status, body = get(base + "/log?n=5")
    assert status == 200
    assert json.loads(body) == [f"line {i}" for i in range(295, 300)]


def test_log_tail_survives_a_missing_file(server, tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "LOG_FILE", tmp_path / "gone.log")
    base, _, _ = server
    assert json.loads(get(base + "/log")[1]) == []


def test_state_carries_the_floor_geometry(server):
    base, _, _ = server
    f = json.loads(get(base + "/state")[1])["floor"]
    assert {"points", "fitted", "margin_cm", "grasp_cm", "arm"} <= set(f)


def test_unknown_route_is_404(server):
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/nope")
    assert e.value.code == 404


def test_paint_page_and_state(server):
    base, _, _ = server
    status, body = get(base + "/paint")
    assert status == 200 and b"painting" in body
    status, body = get(base + "/paint/state")
    st = json.loads(body)
    assert status == 200 and "workspace" in st and "tool" in st and st["tool"]["ok"] is False


def test_paint_plan_without_picture_reports_error(server):
    base, _, _ = server
    req = urllib.request.Request(base + "/paint/plan?name=x", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        assert "error" in json.loads(r.read())
