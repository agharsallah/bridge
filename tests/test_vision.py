"""Detector behaviour on synthetic frames — no camera, no arm."""

import cv2
import numpy as np

from so101_bridge.vision import FRAME_H, FRAME_W, find_yellow, render


def frame_with_brick(x=400, y=200, w=180, h=120, colour=(0, 220, 240)):
    img = np.zeros((FRAME_H, FRAME_W, 3), np.uint8)
    cv2.rectangle(img, (x, y), (x + w, y + h), colour, -1)   # BGR: a solid yellow brick
    return img


def test_finds_a_solid_yellow_brick():
    b = find_yellow(frame_with_brick())
    assert b is not None
    assert abs(b["cx"] - 490) <= 5 and abs(b["cy"] - 260) <= 5
    assert b["long"] == max(b["w"], b["h"]) >= 170


def test_ignores_an_empty_scene():
    assert find_yellow(np.zeros((FRAME_H, FRAME_W, 3), np.uint8)) is None


def test_ignores_blue():
    assert find_yellow(frame_with_brick(colour=(240, 60, 0))) is None


def test_ignores_a_blob_below_the_area_threshold():
    assert find_yellow(frame_with_brick(w=10, h=10)) is None


def test_render_returns_jpeg_and_blob():
    rgb = cv2.cvtColor(frame_with_brick(), cv2.COLOR_BGR2RGB)
    jpeg, blob = render(rgb, "wrist", "hold", "idle", show_servo_guides=True)
    assert jpeg is not None and jpeg[:2] == b"\xff\xd8"      # JPEG SOI marker
    assert blob is not None
