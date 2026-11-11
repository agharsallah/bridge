"""Reusing the lab: a scaffolded lab works out of the box, names cannot shadow routes, the journal records."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))
import new_lab  # noqa: E402

from so101_bridge.lab import BUILTIN, LAB, LABS, Lab, register  # noqa: E402
from so101_bridge.lab.store import LabStore  # noqa: E402
from test_lab import FakeCtrl  # noqa: E402


def test_a_scaffolded_lab_registers_with_the_template_and_its_example_runs(tmp_path, monkeypatch):
    (tmp_path / "labs").mkdir(); (tmp_path / "web").mkdir()
    py, js = new_lab.main(["demo_x", "--title", "Demo", "--description", "try things"], pkg=tmp_path)
    spec = importlib.util.spec_from_file_location("demo_x_lab", py)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    monkeypatch.delitem(LABS, "demo_x")
    lab = mod.LAB
    monkeypatch.setattr(lab, "store", LabStore("demo_x", lab.store.defaults, tmp_path / "demo_x.json", tmp_path / "demo_x.pending.json"))
    assert set(BUILTIN) < set(lab.actions) and "visit_points" in lab.actions
    ctrl = FakeCtrl()
    assert "no points" in lab.command(ctrl, {"action": "visit_points"})["error"]
    st = lab.state(ctrl)
    assert st["demo_x"] == {"points": 0, "dwell_s": 1.0} and st["lab"]["module"] == "/static/demo_x/panels.js"
    assert "export const panels" in js.read_text() and "$" not in js.read_text()
    with pytest.raises(SystemExit): new_lab.main(["demo_x"], pkg=tmp_path)          # never overwrites


@pytest.mark.parametrize("name", ["paint", "static", "Bad-Name", "1x"])
def test_a_lab_cannot_shadow_a_route_or_use_an_odd_name(name):
    with pytest.raises(ValueError): register(Lab(name, actions=BUILTIN))


def test_the_journal_records_what_ran_and_what_was_refused_and_feeds_observe():
    ctrl = FakeCtrl()
    LAB.command(ctrl, {"action": "point_record", "name": "a"})
    LAB.command(ctrl, {"action": "release"})                                          # asked to confirm: not run, not journaled
    LAB.command(ctrl, {"action": "goto_point", "name": "nope"})
    j = LAB.journal()
    assert [e["action"] for e in j] == ["point_record", "goto_point"]
    assert j[0]["ok"] and j[0]["args"] == {"name": "a"} and not j[1]["ok"] and "nope" in j[1]["error"]
    assert LAB.observe(ctrl)["recent_actions"] == j
