"""Paint-page camera overlay: locate the paper sheet and the taught colour wells.

Assist-only — this never drives the arm. It just gives the operator a live overlay while they teach
paper corners and stations by hand (:mod:`.workspace`), the way ``vision.py`` overlays the brick for
AUTO PICK. Detection runs on the same downscaled 960x540 frame as the brick detector.
"""

import cv2
import numpy as np

from .. import settings
from ..settings import JPEG_QUALITY
from ..vision import frame as _frame


def find_paper(bgr, min_area=None):
    """Largest low-saturation, bright, solid quadrilateral -> dict(cx, cy, w, h, box) or None.

    The paper is whatever sheet colour the operator is using: unlike the brick, it is picked out by
    being bright and nearly colourless (low S) rather than by hue, and by looking like a clean
    rectangle — both solid (high solidity) and actually rectangular (fills its rotated bounding box,
    which rejects round/oval trays such as a mixing palette that pass the solidity check just as well).
    """
    min_area = settings.PAPER_MIN_AREA if min_area is None else min_area
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (0, 0, settings.PAPER_V_MIN), (255, settings.PAPER_S_MAX, 255))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        a = cv2.contourArea(c)
        if a < min_area:
            continue
        hull = cv2.contourArea(cv2.convexHull(c)) or 1
        if a / hull < settings.PAPER_SOLIDITY:
            continue
        rect = cv2.minAreaRect(c)
        (cx, cy), (w, h), angle = rect
        if a / max(w * h, 1) < settings.PAPER_RECT_FILL:
            continue
        if best is None or a > best["area"]:
            box = cv2.boxPoints(rect).astype(int).tolist()
            best = dict(area=int(a), cx=int(cx), cy=int(cy), w=round(w, 1), h=round(h, 1),
                        angle=round(float(angle), 1), box=box)
    return best


def find_color_wells(bgr, palette, min_area=None):
    """One blob per taught colour station that is actually visible -> [dict(name, rgb, cx, cy, w, h), ...].

    ``palette`` is ``[{"name", "rgb": [r, g, b]}, ...]`` (see ``paint.planner.palette_from_workspace``).
    Each station gets its own HSV gate, a +/- hue window around its taught colour, so wells of
    different colours don't compete with each other the way a single shared threshold would.
    """
    min_area = settings.WELL_MIN_AREA if min_area is None else min_area
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    out = []
    for c in palette:
        r, g, b = c["rgb"]
        hue = int(cv2.cvtColor(np.uint8([[[b, g, r]]]), cv2.COLOR_BGR2HSV)[0, 0, 0])
        lo, hi = hue - settings.WELL_HUE_TOL, hue + settings.WELL_HUE_TOL
        if lo < 0 or hi > 179:                          # hue wraps (reds sit at both ends of the scale)
            wrap_lo = cv2.inRange(hsv, (max(0, lo) % 180, settings.WELL_S_MIN, settings.WELL_V_MIN),
                                   (179, 255, 255))
            wrap_hi = cv2.inRange(hsv, (0, settings.WELL_S_MIN, settings.WELL_V_MIN),
                                   (hi % 180, 255, 255))
            m = cv2.bitwise_or(wrap_lo, wrap_hi)
        else:
            m = cv2.inRange(hsv, (lo, settings.WELL_S_MIN, settings.WELL_V_MIN), (hi, 255, 255))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best = None
        for cn in cnts:
            a = cv2.contourArea(cn)
            if a < min_area:
                continue
            if best is None or a > best[0]:
                best = (a, cv2.boundingRect(cn))
        if best:
            a, (x, y, w, h) = best
            out.append(dict(name=c["name"], rgb=c["rgb"], area=int(a), x=x, y=y, w=w, h=h,
                             cx=x + w // 2, cy=y + h // 2))
    return out


def render(rgb, cam, ws, mode, auto_status):
    """Downscale a camera frame, overlay the detected paper outline and colour wells.

    ``ws`` is a loaded painting workspace (:func:`paint.workspace.load`). Returns
    ``(jpeg_bytes | None, {"paper": blob|None, "wells": [blob, ...]}, bgr_frame)``.
    """
    from .planner import palette_from_workspace

    bgr = _frame(rgb)
    paper = find_paper(bgr)
    if paper:
        cv2.drawContours(bgr, [np.array(paper["box"])], 0, (0, 220, 0), 2)
        cv2.putText(bgr, f"paper {paper['w']:.0f}x{paper['h']:.0f}px {paper['angle']:.0f}deg",
                    (paper["box"][1][0], max(15, paper["box"][1][1] - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 220, 0), 1)
    wells = find_color_wells(bgr, palette_from_workspace(ws))
    for w in wells:
        colour = (int(w["rgb"][2]), int(w["rgb"][1]), int(w["rgb"][0]))       # RGB -> BGR
        cv2.rectangle(bgr, (w["x"], w["y"]), (w["x"] + w["w"], w["y"] + w["h"]), colour, 2)
        cv2.putText(bgr, w["name"], (w["x"], max(15, w["y"] - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
    cv2.putText(bgr, f"{cam}  {mode}  auto:{auto_status}", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return (buf.tobytes() if ok else None), {"paper": paper, "wells": wells}, bgr
