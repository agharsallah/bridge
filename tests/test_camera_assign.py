"""Assigning cameras by hand is saved, validated, and queued for the control loop — no camera is opened here."""

import json

import pytest

from so101_bridge import hardware, settings


@pytest.fixture
def cams(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"; f.write_text(json.dumps({"PORT": "keep-me"}))
    monkeypatch.setenv("SO101_SETTINGS", str(f))
    monkeypatch.setattr(settings, "CAMS", {"top": "Innomaker#1", "wrist": "Innomaker#0"})
    monkeypatch.setattr(hardware, "_CAM_CFG", {n: {"name": n, "index": s} for n, s in settings.CAMS.items()})
    monkeypatch.setattr(hardware, "_PENDING", {}); monkeypatch.setattr(hardware, "_TRUST", set())
    return f


def test_swap_is_saved_and_queued_and_other_keys_kept(cams):
    hardware.swap_cameras("top", "wrist")
    saved = json.loads(cams.read_text())
    assert saved["CAMS"] == {"top": "Innomaker#0", "wrist": "Innomaker#1"} and saved["PORT"] == "keep-me"
    assert hardware._PENDING == {"top": "Innomaker#0", "wrist": "Innomaker#1"}
    assert hardware._TRUST == {"top", "wrist"}                    # by hand: the next identity check stands down


def test_two_cameras_on_one_device_or_a_bad_selector_is_refused(cams):
    with pytest.raises(ValueError): hardware.assign_cameras({"top": "Innomaker#0"})
    with pytest.raises(ValueError): hardware.assign_cameras({"top": "no-number"})
    assert "CAMS" not in json.loads(cams.read_text()) and not hardware._PENDING
