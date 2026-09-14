"""Brick detection and the dashboard overlay drawn on every processed frame."""

import cv2
import numpy as np

from . import settings
from .settings import FRAME_H, FRAME_W, JPEG_QUALITY, SERVO_X_BOTTOM, SERVO_X_TOP


def find_yellow(bgr, hmin=None, hmax=None, smin=None, vmin=None, min_area=None):
    """Largest solid target-coloured blob -> dict(cx, cy, w, h, long) or None.

    Gates default to ``settings.TARGET_HSV`` (overridable in config/settings.json); the tin's orange,
    ring-shaped label is rejected by hue and by the solidity/fill filters.
    """
    t = settings.TARGET_HSV
    hmin = t["h"][0] if hmin is None else hmin; hmax = t["h"][1] if hmax is None else hmax
    smin = t["s_min"] if smin is None else smin; vmin = t["v_min"] if vmin is None else vmin
    min_area = settings.TARGET_MIN_AREA if min_area is None else min_area
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (hmin, smin, vmin), (hmax, 255, 255))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8))   # merge stud columns / shaded faces
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        a = cv2.contourArea(c)
        if a < min_area:
            continue
        x, y, w, h = cv2.boundingRect(c)
        hull = cv2.contourArea(cv2.convexHull(c)) or 1
        if a / hull < settings.TARGET_SOLIDITY or a / (w * h) < settings.TARGET_FILL:
            continue
        if best is None or a > best["area"]:
            best = dict(area=int(a), x=x, y=y, w=w, h=h, cx=x + w // 2, cy=y + h // 2, long=max(w, h))
    return best




def render(rgb, cam, mode, auto_status, show_servo_guides=False):
    """Downscale a camera frame, detect the brick, draw the overlay.

    Returns ``(jpeg_bytes | None, blob | None)`` where blob is the detection in frame coordinates.
    """
    bgr = cv2.cvtColor(cv2.resize(rgb, (FRAME_W, FRAME_H), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2BGR)
    b = find_yellow(bgr)
    if b:
        cv2.rectangle(bgr, (b["x"], b["y"]), (b["x"] + b["w"], b["y"] + b["h"]), (0, 200, 255), 2)
        cv2.putText(bgr, f"brick {b['cx']},{b['cy']} {b['long']}px", (b["x"], max(15, b["y"] - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
    if show_servo_guides:                      # pan-servo target guides, AUTO only
        for x in (SERVO_X_TOP, SERVO_X_BOTTOM):
            cv2.line(bgr, (x, 0), (x, FRAME_H), (80, 80, 80), 1)
    cv2.putText(bgr, f"{cam}  {mode}  auto:{auto_status}", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return (buf.tobytes() if ok else None), b
