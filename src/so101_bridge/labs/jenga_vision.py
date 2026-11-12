"""Jenga vision: where the stick tip sits against the rows of the tower, from one wrist-camera frame.

The stick is rigid in the wrist image (a dark bar coming up from the bottom), the tower behind it is light
wood with a darker seam between rows. Close to the face (under ~1 cm) the tip's place in the picture is
where it will touch, so this is what decides the height before a push — the arm model's own tip height
is off by centimetres with a stick strapped to one jaw (docs/jenga.md).
"""

import cv2
import numpy as np

DEFAULTS = {"tip_px": [523, 292], "tip_w": 30, "dark": 75, "seam_drop": 5.0, "row_px": [34, 70]}      # at a 1280 px wide frame


def _runs(mask):
    """[(start, end)] of the True runs in a 1-D bool array."""
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def find_tip(gray, c, s):
    """(x centre, y top, width) of the stick's far end: follow the dark bar up from below the expected tip to find its top."""
    x0, y0 = c["tip_px"][0] * s, c["tip_px"][1] * s
    lo, hi = int(x0 - 70 * s), int(x0 + 90 * s)
    y, last = int(y0 + 60 * s), None
    if y >= gray.shape[0] or hi > gray.shape[1]: return None                       # not the wrist picture this was set up for
    while y > max(1, int(y0 - 60 * s)):
        runs = [(a, b) for a, b in _runs(gray[y, lo:hi] < c["dark"]) if b - a >= 12 * s]
        if last is not None:
            runs = [r for r in runs if r[0] < last[1] and r[1] > last[0]]           # the same bar as the row below
        if not runs: break
        last = max(runs, key=lambda r: r[1] - r[0]); y -= 1
    if last is None: return None                                                    # no stick where it should be
    return (x0, float(y + 1), c["tip_w"] * s)           # x and width from config: close to the face the tip's shadow merges with it


def find_seams(gray, x_lo, x_hi, y_lo, y_hi, drop):
    """Where one row ends and the next begins: a thin darker line, or a step in brightness between two bricks."""
    p = cv2.blur(gray[y_lo:y_hi, x_lo:x_hi].mean(axis=1).reshape(-1, 1), (1, 3)).ravel()
    k, found = 7, []
    for i in range(k, len(p) - k):
        above, below = np.median(p[i - k:i - 1]), np.median(p[i + 2:i + k + 1])
        line = (above + below) / 2.0 - p[i] if p[i] == p[i - 2:i + 3].min() else 0.0
        step = abs(below - above) if abs(p[i + 1] - p[i - 1]) == np.abs(p[i - 1:i + 4] - p[i - 3:i + 2]).max() else 0.0
        score = max(line / drop, step / (2.5 * drop))
        if score >= 1.0: found.append((score, y_lo + i))
    out = []
    for score, y in sorted(found, reverse=True):                    # strongest first; one seam per 8 px
        if all(abs(y - o) > 8 for o in out): out.append(y)
    return sorted(out)


def measure(rgb, cfg=None):
    """The tip against the rows: ``{"offset_cm" (+ = tip below the row's middle), "end_row", "row_px", "tip", "row"}`` or None.

    ``row`` is the pair of seams around the tip's centre; ``end_row`` says whether that row shows brick ends
    (a vertical seam beside the stick) rather than the long side of one brick.
    """
    c = {**DEFAULTS, **(cfg or {})}
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    s = gray.shape[1] / 1280.0
    tip = find_tip(gray, c, s)
    if tip is None: return None
    x, top, d = tip
    yc = top + d / 2.0                                                              # the end of the stick is about as tall as it is wide
    seams = find_seams(gray, int(x - 3.3 * d), int(x - 0.7 * d), max(0, int(top - 190 * s)), min(gray.shape[0], int(top + 190 * s)), c["seam_drop"])
    rows = [(a, b) for a, b in zip(seams, seams[1:]) if c["row_px"][0] * s <= b - a <= c["row_px"][1] * s]
    row = next(((a, b) for a, b in rows if a <= yc < b), None)
    if row is None: return None
    h = float(row[1] - row[0])
    cols = gray[row[0] + 4:row[1] - 3, int(x - 1.25 * h):int(x - 0.45 * h)].mean(axis=0)   # where the middle brick's left edge would be
    end_row = bool(len(cols) > 6 and np.median(cols) - cols.min() >= c["seam_drop"])
    return {"offset_cm": round((yc - (row[0] + row[1]) / 2.0) / h * 1.5, 2), "end_row": end_row, "row_px": round(h, 1),
            "tip": [round(x, 1), round(top, 1), round(d, 1)], "row": [int(row[0]), int(row[1])]}
