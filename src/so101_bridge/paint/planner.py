"""Picture -> brush strokes in paper coordinates.

The image is fitted onto the paper (letterboxed, with a margin), sampled on a grid whose pitch is the
brush width, and every cell is assigned the nearest taught colour — or "paper" (left unpainted) when it
is light. Each colour is then covered with horizontal hatch strokes along runs of cells (serpentine order,
split at a maximum length). Colours are painted light to dark, as watercolour wants.

    plan = plan_image(bgr, workspace, params) ->
      {"palette": [{"name", "rgb"}], "strokes": [{"color", "points": [[u, v], ...]}],
       "grid": {"cols", "rows", "cells": [[palette index | -1]]}, "stats": {...}, "params": {...}}
"""

import cv2
import numpy as np

DEFAULTS = {"margin_cm": 1.0, "overlap": 0.8, "max_stroke_cm": 8.0, "white_threshold": 225,
            "min_run_cells": 1, "detail": 1.0}


def palette_from_workspace(ws):
    return [{"name": n, "rgb": [int(x) for x in s.get("rgb", [128, 128, 128])]}
            for n, s in ws["stations"].items() if s.get("kind") == "color" and s.get("dip")]


def _lab(rgb_rows):
    arr = np.array(rgb_rows, dtype=np.uint8).reshape(-1, 1, 3)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(float)


def plan_image(bgr, ws, params=None):
    p = {**DEFAULTS, **(params or {})}
    palette = palette_from_workspace(ws)
    if not palette:
        raise ValueError("no colour stations taught (kind=color with a dip pose)")
    W, H = float(ws["paper"]["width_cm"]), float(ws["paper"]["height_cm"])
    pitch = max(0.2, float(ws["brush"]["width_mm"]) / 10.0 * float(p["overlap"]) / max(0.25, float(p["detail"])))
    m = float(p["margin_cm"]); dw, dh = W - 2 * m, H - 2 * m
    if dw <= 0 or dh <= 0:
        raise ValueError("margin larger than the paper")
    ih, iw = bgr.shape[:2]
    scale = min(dw / iw, dh / ih)                       # fit the picture inside the drawable area
    pw, ph = iw * scale, ih * scale                     # picture size on paper (cm)
    ox, oy = m + (dw - pw) / 2, m + (dh - ph) / 2       # picture origin on paper (cm)
    cols, rows = max(1, int(pw / pitch)), max(1, int(ph / pitch))
    small = cv2.resize(bgr, (cols, rows), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(float)
    pal_lab = _lab([c["rgb"] for c in palette])
    # nearest palette colour per cell; light cells stay paper
    d = np.linalg.norm(lab[:, None, :] - pal_lab[None, :, :], axis=2)
    idx = d.argmin(axis=1)
    light = (rgb.reshape(-1, 3).astype(int).min(axis=1) >= int(p["white_threshold"]))
    idx = np.where(light, -1, idx).reshape(rows, cols)
    # paint order: light colours first (Lab L descending)
    order = sorted(range(len(palette)), key=lambda i: -pal_lab[i][0])
    cell_w, cell_h = pw / cols, ph / rows
    strokes, per_color = [], {}
    max_cells = max(1, int(float(p["max_stroke_cm"]) / cell_w))
    for ci in order:
        name = palette[ci]["name"]; n_before = len(strokes)
        for r in range(rows):
            v = oy + (r + 0.5) * cell_h
            row = idx[r] == ci
            c = 0
            runs = []
            while c < cols:
                if row[c]:
                    s = c
                    while c < cols and row[c] and c - s < max_cells: c += 1
                    if c - s >= int(p["min_run_cells"]):
                        runs.append((s, c - 1))
                else:
                    c += 1
            if r % 2: runs = [(b, a) for a, b in reversed(runs)]           # serpentine
            for a, b in runs:
                u0, u1 = ox + (a + 0.5) * cell_w, ox + (b + 0.5) * cell_w
                strokes.append({"color": name, "points": [[round(u0, 2), round(v, 2)], [round(u1, 2), round(v, 2)]]})
        per_color[name] = len(strokes) - n_before
    length = sum(abs(s["points"][1][0] - s["points"][0][0]) + cell_w for s in strokes)
    return {"palette": palette, "strokes": strokes,
            "grid": {"cols": cols, "rows": rows, "cells": idx.tolist(), "origin_cm": [round(ox, 2), round(oy, 2)],
                     "size_cm": [round(pw, 2), round(ph, 2)]},
            "stats": {"strokes": len(strokes), "per_color": per_color, "painted_cm": round(length, 1),
                      "pitch_cm": round(pitch, 2), "cells": int(cols * rows), "paper_cells": int((idx == -1).sum())},
            "params": p}


def preview_png(plan, scale=6):
    """Quantised picture as PNG bytes (paper white where nothing is painted)."""
    g = plan["grid"]; cells = np.array(g["cells"]); pal = plan["palette"]
    img = np.full((g["rows"], g["cols"], 3), 245, np.uint8)
    for i, c in enumerate(pal):
        img[cells == i] = c["rgb"][::-1]                       # BGR for imencode
    img = cv2.resize(img, (g["cols"] * scale, g["rows"] * scale), interpolation=cv2.INTER_NEAREST)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes() if ok else b""
