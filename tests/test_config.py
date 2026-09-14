"""Configuration overlays: config/settings.json and config/poses.json replace defaults without code edits."""

import importlib
import json

from so101_bridge import poses, settings


def test_settings_overlay_merges_dicts_and_replaces_scalars(tmp_path, monkeypatch):
    cfg = tmp_path / "settings.json"
    cfg.write_text(json.dumps({"HTTP_PORT": 9999, "LOAD_LIMIT": {"elbow_flex": 123}, "NOT_A_SETTING": 1,
                               "TARGET_HSV": {"s_min": 55}}))
    monkeypatch.setenv("SO101_SETTINGS", str(cfg))
    try:
        importlib.reload(settings)
        assert settings.HTTP_PORT == 9999
        assert settings.LOAD_LIMIT["elbow_flex"] == 123 and settings.LOAD_LIMIT["shoulder_pan"] == 300   # merged
        assert settings.TARGET_HSV == {"h": [14, 40], "s_min": 55, "v_min": 80}
        assert "NOT_A_SETTING" not in settings.OVERRIDDEN
        assert sorted(settings.OVERRIDDEN) == ["HTTP_PORT", "LOAD_LIMIT", "TARGET_HSV"]
    finally:
        monkeypatch.setenv("SO101_SETTINGS", str(tmp_path / "missing.json"))
        importlib.reload(settings)
        assert settings.HTTP_PORT == 8765 and settings.OVERRIDDEN == []


def test_poses_overlay_replaces_a_path_in_place(tmp_path, monkeypatch):
    before = list(poses.TRANSIT); before_retreat = list(poses.RETREAT)
    monkeypatch.setattr(poses, "POSES_FILE", tmp_path / "poses.json")
    (tmp_path / "poses.json").write_text(json.dumps({
        "TRANSIT": [{"shoulder_pan": 1}, {"shoulder_lift": 2, "bogus": 3}],          # invalid joint -> ignored
        "RETREAT": [{"shoulder_pan": 4.5}],
        "NOPE": [{"shoulder_pan": 0}],
    }))
    try:
        poses.OVERRIDDEN.clear(); poses._apply_overrides()
        assert poses.TRANSIT == before                       # rejected as a whole
        assert poses.RETREAT == [{"shoulder_pan": 4.5}]      # replaced in place (same list object)
        assert poses.OVERRIDDEN == ["RETREAT"]
    finally:
        poses.RETREAT[:] = before_retreat; poses.OVERRIDDEN.clear()   # in place: other modules hold the same list
        assert len(poses.RETREAT) > 1
