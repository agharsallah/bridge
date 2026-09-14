"""The floor model must reproduce the contact poses it was fitted on."""

import json

import numpy as np
import pytest

from so101_bridge import floor as floor_mod
from so101_bridge.floor import FloorModel


@pytest.fixture
def model(tmp_path, monkeypatch):
    """A model fitted on synthetic contacts, reading from a temporary config file."""
    contacts = [
        {"shoulder_lift": 41.3, "elbow_flex": 76.3, "wrist_flex": 0.0, "height": 0.0},
        {"shoulder_lift": 66.7, "elbow_flex": 53.6, "wrist_flex": -5.3, "height": 0.0},
        {"shoulder_lift": 50.0, "elbow_flex": 66.0, "wrist_flex": -2.0, "height": 0.0},
        {"shoulder_lift": 58.0, "elbow_flex": 60.0, "wrist_flex": -4.0, "height": 0.0},
        {"shoulder_lift": 45.0, "elbow_flex": 71.0, "wrist_flex": -1.0, "height": 0.0},
    ]
    pts = tmp_path / "floor_points.json"
    pts.write_text(json.dumps(contacts))
    monkeypatch.setattr(floor_mod, "FLOOR_FILE", pts)
    monkeypatch.setattr(floor_mod, "FLOOR_CFG", tmp_path / "floor_config.json")
    monkeypatch.setattr(floor_mod, "SEED_CONTACTS", [])
    return FloorModel(), contacts


def test_fit_converges(model):
    m, _ = model
    assert m.params is not None
    assert m.rms < 0.005                     # 5 mm over the contact set


def test_contacts_predict_table_height(model):
    m, contacts = model
    for c in contacts:
        assert abs(m.z(c)) < 0.01            # every taught contact sits on the table


def test_shipped_model_fits_and_folds_the_right_way():
    """Regression on the real contacts in config/: folding the shoulder back must raise the tips."""
    m = FloorModel()
    if m.params is None:
        pytest.skip("no floor contacts taught in config/floor_points.json")
    assert m.rms < 0.005
    contact = m.points()[-1]
    assert abs(m.z(contact)) < 0.01
    assert m.z({**contact, "shoulder_lift": contact["shoulder_lift"] - 20}) > m.z(contact) + 0.01


def test_unfitted_model_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(floor_mod, "FLOOR_FILE", tmp_path / "none.json")
    monkeypatch.setattr(floor_mod, "FLOOR_CFG", tmp_path / "none_cfg.json")
    monkeypatch.setattr(floor_mod, "SEED_CONTACTS", [])
    m = FloorModel()
    assert m.params is None
    assert m.z({"shoulder_lift": 0.0, "elbow_flex": 0.0, "wrist_flex": 0.0}) is None


def test_height_is_independent_of_pan(model):
    m, contacts = model
    a = dict(contacts[0]); b = {**a, "shoulder_pan": 40.0}
    assert m.z(a) == pytest.approx(m.z(b))


def test_seed_contacts_are_plausible():
    q = np.array([[c["shoulder_lift"], c["elbow_flex"], c["wrist_flex"]] for c in floor_mod.SEED_CONTACTS])
    assert q.shape[1] == 3


def test_arm_points_end_at_the_predicted_tip_height(model):
    m, contacts = model
    pts = m.arm_points(contacts[0])
    assert len(pts) == 4                                   # shoulder, elbow, wrist, fingertips
    assert pts[0][0] == 0.0                                # the chain starts on the shoulder axis
    assert pts[-1][1] == pytest.approx(m.z(contacts[0]), abs=1e-3)


def test_arm_points_are_none_without_a_fit(tmp_path, monkeypatch):
    monkeypatch.setattr(floor_mod, "FLOOR_FILE", tmp_path / "none.json")
    monkeypatch.setattr(floor_mod, "FLOOR_CFG", tmp_path / "none_cfg.json")
    monkeypatch.setattr(floor_mod, "SEED_CONTACTS", [])
    assert FloorModel().arm_points({"shoulder_lift": 0, "elbow_flex": 0, "wrist_flex": 0}) is None
