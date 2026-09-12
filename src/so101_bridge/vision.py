"""Brick detection and the dashboard overlay drawn on every processed frame."""

import cv2
import numpy as np

from .settings import SERVO_X_BOTTOM, SERVO_X_TOP

FRAME_W, FRAME_H = 960, 540          # size the detector and the dashboard streams run at
JPEG_QUALITY = 75


def find_yellow(bgr, hmin=14, hmax=40, smin=40, vmin=80, min_area=600):
    """Largest solid yellow blob -> dict(cx, cy, w, h, long) or None. Tin label (orange, ring-like) is rejected."""
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
        if a / hull < 0.7 or a / (w * h) < 0.5:
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
