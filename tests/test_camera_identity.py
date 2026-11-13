"""The twin camera whose view moves with the arm is the arm camera; nothing is decided while the arm is still."""

import numpy as np

from so101_bridge.camera_identity import AGREE, MAX_ROUNDS, SAMPLE_S, SAMPLES, Vote

RNG = np.random.default_rng(0)
SCENE = RNG.integers(0, 255, (108, 192, 3)).astype(np.uint8)


def _run(vote, arm_view, steps, move=5.0):
    """Feed `steps` samples; `arm_view` is the camera whose frame changes as the arm moves."""
    out = None
    for k in range(steps):
        moving = RNG.integers(0, 255, (108, 192, 3)).astype(np.uint8)      # a whole new view every sample
        still = np.clip(SCENE.astype(int) + RNG.integers(-3, 4, SCENE.shape), 0, 255).astype(np.uint8)
        frames = {n: (moving if n == arm_view else still) for n in ("wrist", "top")}
        out = vote.feed([k * move, 0, 0, 0, 0], frames, now=k * SAMPLE_S * 1.01) or out
    return out


def test_configured_right_is_confirmed():
    assert _run(Vote("wrist", "top"), "wrist", AGREE * SAMPLES + 3) == "ok"


def test_ports_swapped_is_detected():
    assert _run(Vote("wrist", "top"), "top", AGREE * SAMPLES + 3) == "swap"


def test_no_verdict_while_the_arm_does_not_move():
    assert _run(Vote("wrist", "top"), "top", 3 * AGREE * SAMPLES, move=0.0) is None


def test_views_that_move_alike_end_unsure_so_the_operator_picks():
    vote, out = Vote("wrist", "top"), None
    for k in range(MAX_ROUNDS * SAMPLES + 3):
        view = RNG.integers(0, 255, (108, 192, 3)).astype(np.uint8)             # both views change the same way
        out = vote.feed([k * 5.0, 0, 0, 0, 0], {"wrist": view, "top": view}, now=k * SAMPLE_S * 1.01) or out
    assert out == "unsure"
