"""A camera named "model#n" is that model's n-th device or nothing — never the laptop camera in its slot."""

from so101_bridge.camera_ids import choices, pick, resolve

BOTH = [(0, "a", "Innomaker-U20CAM-1080p-S1"), (1, "b", "Innomaker-U20CAM-1080p-S1"), (2, "c", "FaceTime HD Camera")]
ONE_DROPPED = [(0, "a", "Innomaker-U20CAM-1080p-S1"), (1, "c", "FaceTime HD Camera"), (2, "d", "OBS Virtual Camera")]


def test_model_and_position_pick_the_index():
    assert resolve("Innomaker#0", BOTH) == 0 and resolve("innomaker#1", BOTH) == 1


def test_a_missing_camera_never_falls_back_to_another_model():
    assert resolve("Innomaker#1", ONE_DROPPED) == 0          # the remaining twin, never index 1 (the laptop camera)
    assert resolve("Innomaker#0", []) is None               # no listing at all: nothing


def test_a_plain_index_is_used_as_before():
    assert resolve(1, ONE_DROPPED) == 1


def test_every_device_gets_a_selector_that_resolves_back_to_it():
    for c in choices(BOTH):
        assert resolve(c["selector"], BOTH) == c["index"]
    assert [c["selector"] for c in choices(BOTH)][:2] == ["Innomaker-U20CAM-1080p-S1#0", "Innomaker-U20CAM-1080p-S1#1"]


def test_two_cameras_never_get_the_same_device():
    # wrist took the only Innomaker ("a") while the other was unplugged; top must not open it too
    assert resolve("Innomaker#1", ONE_DROPPED, exclude={"a"}) is None
    assert resolve("Innomaker#0", ONE_DROPPED, exclude={"a"}) is None
    # the twin is back, but positions shifted so top's slot is the busy one: top takes the free twin
    assert resolve("Innomaker#1", BOTH, exclude={"b"}) == 0


def test_a_camera_back_from_a_drop_returns_to_its_own_port():
    assert pick("Innomaker#0", BOTH, prefer="b") == (1, "b")
    assert pick("Innomaker#0", BOTH, exclude={"b"}, prefer="b") == (0, "a")     # its port is busy: never shared
