"""Drawings for the drawing lab: a drawing is a list of strokes, a stroke a list of [x, y] (x to the right, y away
from the arm, any unit). ``fit`` places one on the paper; nothing here knows about the arm."""

import math

from so101_bridge.labs.draw_kufic import jumua


def circle(cx, cy, r, a0=0.0, a1=360.0, n=24):
    """An arc as a stroke: angles in degrees, counter-clockwise from the +x axis."""
    return [[cx + r * math.cos(math.radians(a0 + (a1 - a0) * k / n)), cy + r * math.sin(math.radians(a0 + (a1 - a0) * k / n))] for k in range(n + 1)]


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def robot():
    return [box(0.2, 0.2, 0.8, 0.72), circle(0.37, 0.52, 0.07), circle(0.63, 0.52, 0.07), circle(0.5, 0.42, 0.16, 215, 325, 10),
            [[0.5, 0.72], [0.5, 0.88]], circle(0.5, 0.93, 0.05), box(0.1, 0.38, 0.2, 0.56), box(0.8, 0.38, 0.9, 0.56)]


def star():
    pts = [[0.5 + 0.5 * math.sin(math.radians(144 * k)), 0.5 + 0.5 * math.cos(math.radians(144 * k))] for k in range(6)]
    return [pts]


def heart():
    t = [2 * math.pi * k / 60 for k in range(61)]
    return [[[16 * math.sin(a) ** 3, 13 * math.cos(a) - 5 * math.cos(2 * a) - 2 * math.cos(3 * a) - math.cos(4 * a)] for a in t]]


def house():
    return [box(0.15, 0.0, 0.85, 0.55), [[0.05, 0.55], [0.5, 1.0], [0.95, 0.55], [0.05, 0.55]], box(0.42, 0.0, 0.6, 0.32), box(0.22, 0.28, 0.36, 0.42)]


def spiral():
    return [[[a / 20 * math.cos(a), a / 20 * math.sin(a)] for a in (0.25 * k for k in range(4, 76))]]


def frame():
    """The test pattern: a square with its diagonals — shows at a glance how square and how closed the arm draws."""
    return [box(0, 0, 1, 1), [[0, 0], [1, 1]], [[0, 1], [1, 0]]]


def wave(x0, x1, y, amp=0.35, n=40):
    return [[x0 + (x1 - x0) * k / n, y + amp * math.sin(1.6 * (x0 + (x1 - x0) * k / n))] for k in range(n + 1)]


def sun(cx, cy, r, ray):
    """A sun in one stroke: round the circle, and at every eighth of a turn out along a ray and back to the circle."""
    out = []
    for k in range(8):
        a = math.radians(90 + 45 * (k + 1))
        out += circle(cx, cy, r, 90 + 45 * k, 90 + 45 * (k + 1), 6)[:-1]
        out += [[cx + r * math.cos(a), cy + r * math.sin(a)], [cx + (r + ray) * math.cos(a), cy + (r + ray) * math.sin(a)], [cx + r * math.cos(a), cy + r * math.sin(a)]]
    return out


def sailboat():
    """A scene, 18 x 12, in six strokes: each thing is one line that starts where the last part ended, so the pen
    lands six times instead of nineteen. Nothing smaller than a centimetre."""
    boat = [[5.3, 3.3], [6.5, 1.9], [11.6, 1.9], [13.0, 3.3], [9.0, 3.3],          # hull, then along the deck to the mast
            [9.0, 10.2], [10.4, 9.7], [9.0, 9.2], [9.0, 9.0],                       # up the mast, round the flag, back down it
            [12.6, 3.9], [9.0, 3.9], [6.0, 3.9], [9.0, 8.4],                        # main sail, across the boom, jib
            [9.0, 3.3], [5.3, 3.3]]                                                 # down the mast and along the deck: the hull is closed
    return [[[0.5, 4.2], [2.4, 7.4], [3.5, 5.8], [4.4, 6.8], [5.6, 4.6]],          # mountains
            [[2.0, 10.0], [2.7, 10.6], [3.2, 10.0], [3.7, 10.6], [4.4, 10.0]], [[5.0, 11.0], [5.6, 11.5], [6.0, 11.0], [6.4, 11.5], [7.0, 11.0]],
            sun(14.6, 9.2, 1.4, 1.4), boat,
            wave(17.5, 0.5, 1.1) + wave(1.0, 17.0, 0.2, 0.25)]                      # the sea: one way, then back underneath


SHAPES = {"jumua": jumua, "sailboat": sailboat, "robot": robot, "star": star, "heart": heart, "house": house, "spiral": spiral, "frame": frame}


def clean(strokes):
    """Strokes as lists of [float, float], or ValueError with a sentence the operator can act on."""
    try:
        out = [[[float(p[0]), float(p[1])] for p in s] for s in strokes or []]
    except (TypeError, ValueError, IndexError) as e:
        raise ValueError("a drawing is a list of strokes, each a list of [x, y]") from e
    out = [s for s in out if s]
    if not out or sum(len(s) for s in out) > 4000: raise ValueError("a drawing needs 1 to 4000 points")
    return out


def fit(strokes, area, scale=1.0, dx_cm=0.0, dy_cm=0.0):
    """The strokes in cm on the table: scaled (aspect kept) to ``scale`` of the area {x: [lo, hi], y: [lo, hi]},
    centred, then shifted by (dx, dy)."""
    xs, ys = [p[0] for s in strokes for p in s], [p[1] for s in strokes for p in s]
    w, h = (max(xs) - min(xs)) or 1e-9, (max(ys) - min(ys)) or 1e-9
    k = float(scale) * min((area["x"][1] - area["x"][0]) / w, (area["y"][1] - area["y"][0]) / h)
    cx, cy = (area["x"][0] + area["x"][1]) / 2 + dx_cm, (area["y"][0] + area["y"][1]) / 2 + dy_cm
    mx, my = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    return [[[round(cx + (p[0] - mx) * k, 2), round(cy + (p[1] - my) * k, 2)] for p in s] for s in strokes]


def resample(stroke, step_cm):
    """The stroke with a point at most every ``step_cm`` (corners kept)."""
    out = [list(stroke[0])]
    for a, b in zip(stroke, stroke[1:]):
        n = max(1, math.ceil(math.hypot(b[0] - a[0], b[1] - a[1]) / step_cm))
        out += [[a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n] for k in range(1, n + 1)]
    return out


def length(strokes):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for s in strokes for a, b in zip(s, s[1:]))
