"""A camera named "model#n" is that model's n-th device or nothing — never the laptop camera in its slot."""

from so101_bridge.camera_ids import resolve

BOTH = [(0, "a", "Innomaker-U20CAM-1080p-S1"), (1, "b", "Innomaker-U20CAM-1080p-S1"), (2, "c", "FaceTime HD Camera")]
ONE_DROPPED = [(0, "a", "Innomaker-U20CAM-1080p-S1"), (1, "c", "FaceTime HD Camera"), (2, "d", "OBS Virtual Camera")]


def test_model_and_position_pick_the_index():
    assert resolve("Innomaker#0", BOTH) == 0 and resolve("innomaker#1", BOTH) == 1


def test_a_missing_camera_opens_nothing_instead_of_another_device():
    assert resolve("Innomaker#1", ONE_DROPPED) is None      # index 1 is now the laptop camera: not taken
    assert resolve("Innomaker#0", []) is None               # no listing at all: still nothing


def test_a_plain_index_is_used_as_before():
    assert resolve(1, ONE_DROPPED) == 1
