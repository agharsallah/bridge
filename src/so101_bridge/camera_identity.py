"""Tell two identical cameras apart by how their views move, so changing USB ports cannot swap them.

The tower and wrist cameras are the same model with the same USB serial: nothing in software identifies
them except the port, and ports change. But one of them rides the arm. While the arm moves, the arm camera's
whole view changes; the fixed camera only sees the arm moving through part of its view. So after a camera
(re)connects, a :class:`Vote` compares how much each view changes (tiny 48x27 grayscale thumbnails, 5 per
second, only while the arm joints move). If the camera named as the arm camera changes clearly less than its
twin, the two are swapped. If the arm does not move, nothing is decided and nothing changes; if the rounds
keep disagreeing the verdict is 'unsure' and the operator assigns the cameras by hand (lab camera panel).

Tuned on recorded lab clips (2026-11-13), even without the joint-motion gate: rounds of 20 samples, a 1.5x
margin and two agreeing rounds in a row gave no wrong verdict, where single rounds were sometimes fooled by
hands or falling blocks in the tower view. An unclear round decides nothing.

A one-off check after a reconnect, not stream processing: the streams stay raw.
"""

import time

import numpy as np

W, H = 48, 27
SAMPLE_S = 0.2       # one thumbnail pair every 0.2 s
MOVING_DEG = 1.5     # a sample counts only if an arm joint moved at least this much since the last one
SAMPLES = 20         # moving samples summed per round (~4 s of arm motion)
CLEAR = 1.5          # a round counts when one camera's summed change is 1.5x the other's
AGREE = 2            # consecutive rounds that must agree before deciding
MAX_ROUNDS = 6       # rounds (~25 s of arm motion) without agreement: give up, the operator picks by hand


def thumb(rgb):
    """48x27 grayscale, zero mean and unit spread (so exposure and white balance do not matter)."""
    import cv2
    g = np.asarray(rgb)[::4, ::4].astype(np.float32).mean(axis=2)
    g = cv2.resize(g, (W, H), interpolation=cv2.INTER_AREA)
    return (g - g.mean()) / (g.std() + 1e-6)


def _d(a, b):
    return float(np.abs(a - b).mean())


class Vote:
    """Decides whether ``arm`` (the camera that should ride the arm) really is the one on the arm."""

    def __init__(self, arm, other):
        self.arm, self.other = arm, other
        self.prev = None; self.t = float("-inf"); self.n = 0; self.sum = {arm: 0.0, other: 0.0}
        self.last, self.streak, self.rounds = None, 0, 0

    def feed(self, pose, frames, now=None):
        """``pose``: arm joint angles (deg); ``frames``: {name: rgb}. Returns None (not yet), 'ok', 'swap' or 'unsure'."""
        now = time.time() if now is None else now
        if now - self.t < SAMPLE_S: return None
        self.t = now
        cur = (np.asarray(pose, dtype=float), {n: thumb(frames[n]) for n in (self.arm, self.other)})
        prev, self.prev = self.prev, cur
        if prev is None or np.abs(cur[0] - prev[0]).max() < MOVING_DEG: return None
        for n in self.sum: self.sum[n] += _d(cur[1][n], prev[1][n])
        self.n += 1
        if self.n < SAMPLES: return None
        a, o = self.sum[self.arm], self.sum[self.other]
        self.n = 0; self.sum = dict.fromkeys(self.sum, 0.0)          # next round starts fresh
        r = "ok" if a > CLEAR * o else "swap" if o > CLEAR * a else None
        self.streak = self.streak + 1 if r and r == self.last else (1 if r else 0)
        self.last = r; self.rounds += 1
        if r and self.streak >= AGREE: return r
        return "unsure" if self.rounds >= MAX_ROUNDS else None
