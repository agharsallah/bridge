"""Pose helpers and the configurable rest pose."""

import json

from so101_bridge import poses
from so101_bridge.poses import REST_DEFAULT, P, load_rest
from so101_bridge.settings import JOINTS


def test_P_drops_unset_joints():
    assert P(pan=1.0, grip=10) == {"shoulder_pan": 1.0, "gripper": 10}


def test_P_only_emits_known_joints():
    assert set(P(pan=0, lift=0, elbow=0, wrist=0, roll=0, grip=0)) == set(JOINTS)


def test_load_rest_falls_back_to_the_default(tmp_path, monkeypatch):
    monkeypatch.setattr(poses, "REST_FILE", tmp_path / "missing.json")
    assert load_rest() == REST_DEFAULT


def test_load_rest_merges_the_file_over_the_default(tmp_path, monkeypatch):
    f = tmp_path / "rest.json"; f.write_text(json.dumps({"shoulder_pan": 12.5, "bogus": 1}))
    monkeypatch.setattr(poses, "REST_FILE", f)
    rest = load_rest()
    assert rest["shoulder_pan"] == 12.5
    assert "bogus" not in rest
    assert rest["elbow_flex"] == REST_DEFAULT["elbow_flex"]


def test_load_rest_survives_a_corrupt_file(tmp_path, monkeypatch):
    f = tmp_path / "rest.json"; f.write_text("{not json")
    monkeypatch.setattr(poses, "REST_FILE", f)
    assert load_rest() == REST_DEFAULT


def test_waypoint_paths_stay_within_the_known_joints():
    for path in (poses.UNFOLD, poses.DESCENT, poses.LIFT, poses.TRANSIT, poses.RETREAT):
        for wp in path:
            assert set(wp) <= set(JOINTS)
