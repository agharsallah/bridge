"""Paint overlay detector behaviour on synthetic frames — no camera, no arm."""

import cv2
import numpy as np

from so101_bridge.paint.vision import find_color_wells, find_paper, render
from so101_bridge.vision import FRAME_H, FRAME_W


def frame_with_paper(x=200, y=100, w=500, h=350, colour=(245, 245, 245)):
    img = np.zeros((FRAME_H, FRAME_W, 3), np.uint8)
    cv2.rectangle(img, (x, y), (x + w, y + h), colour, -1)
    return img


def test_finds_a_bright_low_saturation_sheet():
    p = find_paper(frame_with_paper())
    assert p is not None
    assert abs(p["cx"] - 450) <= 5 and abs(p["cy"] - 275) <= 5


def test_ignores_an_empty_scene():
    assert find_paper(np.zeros((FRAME_H, FRAME_W, 3), np.uint8)) is None


def test_ignores_a_saturated_coloured_rectangle():
    assert find_paper(frame_with_paper(colour=(0, 220, 240))) is None


def test_ignores_a_round_palette_but_finds_the_paper_beside_it():
    img = frame_with_paper(x=50, y=100, w=300, h=350)                  # the actual paper sheet
    cv2.ellipse(img, (700, 300), (220, 300), 0, 0, 360, (245, 245, 245), -1)  # bright oval mixing tray
    p = find_paper(img)
    assert p is not None
    assert abs(p["cx"] - 200) <= 10 and abs(p["cy"] - 275) <= 10


def test_finds_a_taught_colour_well():
    img = frame_with_paper()                                          # paper background
    cv2.rectangle(img, (300, 200), (330, 230), (30, 30, 200), -1)      # BGR red-ish well
    palette = [{"name": "red", "rgb": [200, 30, 30]}, {"name": "blue", "rgb": [30, 30, 200]}]
    wells = find_color_wells(img, palette)
    names = {w["name"] for w in wells}
    assert "red" in names
    red = next(w for w in wells if w["name"] == "red")
    assert abs(red["cx"] - 315) <= 5 and abs(red["cy"] - 215) <= 5


def test_no_wells_when_nothing_matches():
    assert find_color_wells(frame_with_paper(), [{"name": "red", "rgb": [200, 30, 30]}]) == []


def test_render_returns_jpeg_and_detections():
    img = frame_with_paper()
    cv2.rectangle(img, (300, 200), (330, 230), (30, 30, 200), -1)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    ws = {"stations": {"red": {"kind": "color", "rgb": [200, 30, 30], "dip": {"shoulder_pan": 0.0}}}}
    jpeg, det, bgr = render(rgb, "top", ws, "hold", "idle")
    assert jpeg is not None and jpeg[:2] == b"\xff\xd8"
    assert det["paper"] is not None
    assert any(w["name"] == "red" for w in det["wells"])
    assert bgr.shape[:2] == (540, 960)
