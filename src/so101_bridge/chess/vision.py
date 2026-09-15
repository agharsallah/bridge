"""Reading the board from the overhead camera.

The camera is never asked what a piece *is*. At 29 cm across on a 1080p frame a square is about 72 px,
which is plenty for "something is standing here" and usually enough for "it is a light piece", and that
is all the rules engine needs: the position before the move is known, so a move is identified by which
legal move produces the occupancy now on the table (:func:`chess.game.resolve`).

Pipeline, on a raw frame captured **at rest** — while the arm is moving it covers the board:

    1. homography from the four taught image corners (the centres of a1, h1, h8, a8 clicked on /chess),
       or from ``cv2.findChessboardCorners`` on the empty board
    2. warp to a square canvas in the robot's own orientation: u to the right, v towards the robot,
       so warped cell (col, row) is a square and nothing downstream thinks about board rotation again
    3. per-square features on the middle of each cell: mean absolute difference from the empty-board
       reference, edge density, mean luminance
    4. occupancy by thresholding the difference, with the threshold refitted every time the true
       position is known (the start of the game, and after every verified robot move)
    5. two gates before any reading is believed: it must be *stable* over several frames, and the
       change from the previous position must be *sane* (few squares, consistent with a legal move).
       A hand over the board fails both, which is the point.
"""

import numpy as np

from ..settings import CHESS_STABLE_DIFF, CHESS_STABLE_N
from ..util import log
from . import workspace as ws_mod

WARP_PX = 512                  # canonical warped board: 8 cells of WARP_PX/8
CENTRE_FRAC = 0.6              # fraction of a cell used for its features — ignores the neighbours' shadows
OCC_MIN_SEPARATION = 8.0       # empty and occupied clusters must differ by this much for a fitted threshold
OCC_FALLBACK = 18.0            # mean absolute difference that counts as "occupied" before anything is calibrated
MAX_CHANGED_SQUARES = 4        # a legal move changes at most 4 squares (castling); more means something fell over


def _cv2():
    import cv2
    return cv2


# ---------------------------------------------------------------- geometry
def corner_targets(d, px=WARP_PX):
    """Where the four taught corner squares land in the warped image: {"a1": (x, y), ...}.

    The warped canvas is the board itself, 8 cells across, in the robot's orientation — so a square's
    cell indices come straight from its (u, v) and no code below this line knows which way round the
    board is."""
    cell = px / 8.0
    pitch = ws_mod.pitch_cm(d)
    out = {}
    for name in ws_mod.CORNERS:
        u, v = ws_mod.square_uv(d, name)
        out[name] = ((u / pitch + 0.5) * cell, (v / pitch + 0.5) * cell)
    return out


def homography(d, px=WARP_PX):
    """Raw frame -> warped board, from the taught image corners. None if they are not all taught."""
    cv2 = _cv2()
    ic = d["board"].get("image_corners") or {}
    if not all(n in ic for n in ws_mod.CORNERS): return None
    dst = corner_targets(d, px)
    src = np.array([ic[n] for n in ws_mod.CORNERS], dtype=np.float32)
    tgt = np.array([dst[n] for n in ws_mod.CORNERS], dtype=np.float32)
    return cv2.getPerspectiveTransform(src, tgt)


def find_grid(frame):
    """``cv2.findChessboardCorners`` on an empty board: the 7x7 inner corners, or None.

    Useful to *seed* the four image corners without clicking, but the detector cannot tell which end of
    the board the robot sits at — the caller must confirm the orientation before it is stored."""
    cv2 = _cv2()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    ok, corners = cv2.findChessboardCorners(gray, (7, 7),
                                            cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not ok: return None
    corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1),
                               (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001))
    return corners.reshape(7, 7, 2)


def corners_from_grid(grid):
    """The four board-corner square centres extrapolated from a detected 7x7 inner-corner grid.

    The grid's corner (0, 0) sits one and a half cells inside the board corner, so each outer centre is
    that point pushed out along both grid directions by 1.5 cells. Returned in the grid's own order —
    which corner is a1 is the operator's call."""
    g = np.asarray(grid, dtype=float)
    out = []
    for i, j, di, dj in ((0, 0, 1, 1), (0, 6, 1, -1), (6, 6, -1, -1), (6, 0, -1, 1)):
        p = g[i, j]
        step_i = (g[i + di, j] - p)
        step_j = (g[i, j + dj] - p)
        out.append(tuple(p - 1.5 * step_i - 1.5 * step_j))
    return out


def warp(frame, H, px=WARP_PX):
    cv2 = _cv2()
    return cv2.warpPerspective(frame, H, (px, px))


# ---------------------------------------------------------------- per-square features
def cell_boxes(px=WARP_PX, centre_frac=CENTRE_FRAC):
    """(row, col) -> (y0, y1, x0, x1) of the central patch of each cell in the warped image."""
    cell = px / 8.0
    m = (1.0 - centre_frac) / 2.0
    boxes = {}
    for r in range(8):
        for c in range(8):
            y0, x0 = (r + m) * cell, (c + m) * cell
            boxes[(r, c)] = (int(y0), int(y0 + centre_frac * cell), int(x0), int(x0 + centre_frac * cell))
    return boxes


def square_names(d, px=WARP_PX):
    """(row, col) -> square name. Row 0 is the far rank as the robot sees it."""
    pitch = ws_mod.pitch_cm(d)
    return {(r, c): ws_mod.uv_square(d, c * pitch, r * pitch) for r in range(8) for c in range(8)}


def features(warped, d, reference=None):
    """Per square: ``{"diff", "edges", "luma"}``.

    ``diff`` is the mean absolute difference from the empty-board reference (the occupancy signal),
    ``edges`` the density of Canny edges (a piece has a silhouette, an empty square has none) and
    ``luma`` the mean brightness of the pixels that differ, which is what tells a light piece from a
    dark one on either colour of square."""
    cv2 = _cv2()
    px = warped.shape[0]
    gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY) if warped.ndim == 3 else warped
    ref = None
    if reference is not None:
        ref = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY) if reference.ndim == 3 else reference
        if ref.shape != gray.shape: ref = cv2.resize(ref, (gray.shape[1], gray.shape[0]))
    edges = cv2.Canny(gray, 60, 160)
    names = square_names(d, px)
    out = {}
    for (r, c), box in cell_boxes(px, float(d["vision"].get("centre_frac", CENTRE_FRAC))).items():
        y0, y1, x0, x1 = box
        patch = gray[y0:y1, x0:x1].astype(np.float32)
        e = edges[y0:y1, x0:x1]
        if ref is None:
            dif = np.zeros_like(patch)
        else:
            dif = np.abs(patch - ref[y0:y1, x0:x1].astype(np.float32))
        mask = dif > (dif.mean() if ref is not None else 0)
        out[names[(r, c)]] = {"diff": float(dif.mean()), "edges": float((e > 0).mean()),
                              "luma": float(patch[mask].mean()) if mask.any() else float(patch.mean()),
                              "cell": [r, c]}
    return out


def occupancy(feats, threshold=None):
    """{square: bool} from the per-square difference, at a fitted or fallback threshold."""
    t = OCC_FALLBACK if threshold is None else float(threshold)
    return {s: f["diff"] >= t for s, f in feats.items()}


def fit_threshold(feats, known_occ):
    """Refit the occupancy threshold from a position whose truth is known — the midpoint between the
    occupied and empty clusters. Returns None when the two clusters are not separated enough to trust."""
    occ = [feats[s]["diff"] for s, v in known_occ.items() if v and s in feats]
    emp = [feats[s]["diff"] for s, v in known_occ.items() if not v and s in feats]
    if len(occ) < 4 or len(emp) < 4: return None
    lo, hi = float(np.percentile(emp, 90)), float(np.percentile(occ, 10))
    if hi - lo < OCC_MIN_SEPARATION:
        log(f"chess: occupancy clusters overlap (empty<={lo:.1f}, occupied>={hi:.1f}) — threshold not updated")
        return None
    return round((lo + hi) / 2.0, 2)


def piece_colours(feats, occ, light_dark=None):
    """{square: 'white'|'black'|None} for the occupied squares, split on brightness.

    ``light_dark`` is the (light, dark) pair learned from a known position; without it the split is the
    midpoint of the occupied squares' own brightness, which is right whenever both colours are on the
    board and meaningless when only one is — hence :func:`fit_colours`, used at the start position."""
    vals = [feats[s]["luma"] for s, v in occ.items() if v]
    if not vals: return {s: None for s in occ}
    if light_dark: mid = (float(light_dark[0]) + float(light_dark[1])) / 2.0
    else: mid = (min(vals) + max(vals)) / 2.0
    return {s: (None if not v else ("white" if feats[s]["luma"] >= mid else "black")) for s, v in occ.items()}


def fit_colours(feats, known_colours):
    """(mean light, mean dark) brightness from a position whose colours are known — the start position."""
    light = [feats[s]["luma"] for s, c in known_colours.items() if c == "white" and s in feats]
    dark = [feats[s]["luma"] for s, c in known_colours.items() if c == "black" and s in feats]
    if len(light) < 4 or len(dark) < 4: return None
    return (round(float(np.mean(light)), 1), round(float(np.mean(dark)), 1))


# ---------------------------------------------------------------- the two gates
def board_diff(a, b):
    """Mean per-square change between two feature readings — the stability measure."""
    keys = [s for s in a if s in b]
    if not keys: return 1e9
    return float(np.mean([abs(a[s]["diff"] - b[s]["diff"]) for s in keys]))


def is_stable(history, n=None, tol=None):
    """True when the last ``n`` readings agree to within ``tol`` — nothing is moving over the board."""
    n = CHESS_STABLE_N if n is None else int(n)
    tol = CHESS_STABLE_DIFF if tol is None else float(tol)
    if len(history) < n: return False
    recent = history[-n:]
    return all(board_diff(recent[i], recent[i + 1]) <= tol for i in range(len(recent) - 1))


def sanity(before_occ, after_occ, max_changed=MAX_CHANGED_SQUARES):
    """Is this change even worth resolving? ``(ok, reason)``.

    Rejects the two things that are not moves: nothing changed, and half the board changed because a
    hand, a sleeve or a toppled piece is in the frame."""
    changed = [s for s in before_occ if bool(before_occ[s]) != bool(after_occ.get(s))]
    if not changed: return False, "no change"
    if len(changed) > max_changed: return False, f"{len(changed)} squares changed — hand in frame or a piece knocked over"
    return True, f"{len(changed)} squares changed: {' '.join(changed)}"


# ---------------------------------------------------------------- the object a routine holds
class BoardReader:
    """Holds the homography, the empty-board reference and the calibration across a whole game."""

    def __init__(self, d):
        self.d = d
        self.px = int(d["vision"].get("warp_px", WARP_PX))
        self.H = homography(d, self.px)
        self.reference = None
        self.threshold = d["vision"].get("occ_threshold")
        self.light_dark = d["vision"].get("light_dark")
        self.history = []

    @property
    def ok(self):
        return self.H is not None

    def error(self):
        if self.H is None: return "the board's image corners have not been taught (click them on /chess)"
        if self.reference is None: return "no empty-board reference captured yet"
        return None

    def set_reference(self, frame):
        """Capture the empty board. Everything downstream is a difference from this."""
        if self.H is None: return False
        self.reference = warp(frame, self.H, self.px)
        return True

    def read(self, frame):
        """One reading: ``{"features", "occupancy", "colours", "warped"}``, or None if not set up."""
        if self.H is None or self.reference is None: return None
        w = warp(frame, self.H, self.px)
        feats = features(w, self.d, self.reference)
        self.history.append(feats); self.history = self.history[-(CHESS_STABLE_N + 2):]
        occ = occupancy(feats, self.threshold)
        return {"warped": w, "features": feats, "occupancy": occ,
                "colours": piece_colours(feats, occ, self.light_dark)}

    def stable(self):
        return is_stable(self.history)

    def calibrate(self, feats, board):
        """Refit the thresholds against a position we know is on the table (start, verified robot move)."""
        from . import game as rules
        t = fit_threshold(feats, rules.occupancy(board))
        if t is not None: self.threshold = t
        lc = fit_colours(feats, rules.colours(board))
        if lc is not None: self.light_dark = lc
        return {"occ_threshold": self.threshold, "light_dark": self.light_dark}

    def persist(self):
        """Write the calibration back into config/chess.json so the next game starts calibrated."""
        d = ws_mod.load()
        d["vision"]["occ_threshold"] = self.threshold
        d["vision"]["light_dark"] = self.light_dark
        ws_mod.save(d)
        return d["vision"]
