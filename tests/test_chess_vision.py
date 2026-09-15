"""Board reading.

The image half of this module cannot be tested honestly without pictures of the real board under the
real light, and there is no camera here yet — so those tests skip until ``tests/fixtures/chess/``
exists (shoot it in P0: empty, start position, after e4, a capture, castling, a hand in frame).
What *can* be tested now is everything between the pixels and the rules: the warp geometry, the
threshold fit, the colour split, and the two gates that decide whether a reading is believed at all.
"""

import sys

import numpy as np
import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from so101_bridge.chess import game as rules  # noqa: E402
from so101_bridge.chess import vision as cv  # noqa: E402
from so101_bridge.chess import workspace as ws  # noqa: E402

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures" / "chess"
needs_photos = pytest.mark.skipif(not FIXTURES.is_dir(),
                                  reason="no board photographs yet — shoot tests/fixtures/chess/ in P0")


def cfg(side="white"):
    d = ws.load(); d["board"]["size_cm"] = 29.0; d["board"]["robot_side"] = side
    return d


# ---------------------------------------------------------------- geometry, no picture needed
@pytest.mark.parametrize("side", ["white", "black"])
def test_the_warped_board_is_eight_cells_in_the_robots_orientation(side):
    d = cfg(side)
    t = cv.corner_targets(d, px=512)
    cell = 512 / 8
    assert set(t) == set(ws.CORNERS)
    for name, (x, y) in t.items():
        assert x % cell == pytest.approx(cell / 2, abs=1e-6)     # every corner lands on a cell centre
        assert y % cell == pytest.approx(cell / 2, abs=1e-6)
    near = max(t.values(), key=lambda p: p[1])
    assert near == t["a1" if side == "white" else "h8"]          # the robot's own back rank is at the bottom


def test_cells_and_square_names_line_up_with_the_board_frame():
    d = cfg()
    names = cv.square_names(d, px=512)
    assert names[(0, 0)] == "a8" and names[(7, 7)] == "h1"       # row 0 is the far rank
    boxes = cv.cell_boxes(px=512, centre_frac=0.6)
    y0, y1, x0, x1 = boxes[(0, 0)]
    assert (y1 - y0) == pytest.approx(0.6 * 64, abs=1) and (x1 - x0) == pytest.approx(0.6 * 64, abs=1)
    assert len(set(names.values())) == 64


def test_no_homography_until_the_image_corners_are_taught():
    d = cfg()
    assert cv.homography(d) is None
    r = cv.BoardReader(d)
    assert not r.ok and "image corners" in r.error()


def test_a_homography_maps_the_taught_corners_onto_their_cells():
    import cv2
    d = cfg()
    d["board"]["image_corners"] = {"a1": [100, 400], "h1": [500, 410], "h8": [495, 90], "a8": [105, 95]}
    H = cv.homography(d, px=512)
    assert H is not None
    got = cv2.perspectiveTransform(np.array([[d["board"]["image_corners"][n] for n in ws.CORNERS]], np.float32), H)[0]
    want = [cv.corner_targets(d, 512)[n] for n in ws.CORNERS]
    assert got == pytest.approx(np.array(want, np.float32), abs=0.5)


def test_corners_from_a_detected_grid_are_pushed_out_to_the_outer_square_centres():
    """findChessboardCorners finds the 7x7 inner corners; the board corners are 1.5 cells beyond them."""
    grid = np.array([[[10 + 10 * j, 10 + 10 * i] for j in range(7)] for i in range(7)], float)
    corners = cv.corners_from_grid(grid)
    assert len(corners) == 4
    assert corners[0] == pytest.approx((-5.0, -5.0))             # one and a half 10-px cells out from (10, 10)


# ---------------------------------------------------------------- calibration and the two gates
def feats(diff_by_square, luma=None):
    return {s: {"diff": d, "edges": 0.0, "luma": (luma or {}).get(s, 128.0), "cell": [0, 0]}
            for s, d in diff_by_square.items()}


def test_the_occupancy_threshold_is_refit_from_a_position_we_know():
    board = rules.new_board()
    truth = rules.occupancy(board)
    f = feats({s: (40.0 if v else 3.0) for s, v in truth.items()})
    t = cv.fit_threshold(f, truth)
    assert t == pytest.approx(21.5, abs=0.5)
    assert cv.occupancy(f, t) == truth


def test_overlapping_clusters_leave_the_threshold_alone_rather_than_guessing():
    board = rules.new_board()
    truth = rules.occupancy(board)
    f = feats({s: (12.0 if v else 11.0) for s, v in truth.items()})
    assert cv.fit_threshold(f, truth) is None


def test_colours_split_on_brightness_learned_from_the_start_position():
    board = rules.new_board()
    truth, cols = rules.occupancy(board), rules.colours(board)
    f = feats({s: (40.0 if v else 3.0) for s, v in truth.items()},
              luma={s: (200.0 if c == "white" else 40.0) for s, c in cols.items() if c})
    light_dark = cv.fit_colours(f, cols)
    assert light_dark == (200.0, 40.0)
    assert cv.piece_colours(f, truth, light_dark) == cols


def test_a_still_board_is_stable_and_a_moving_one_is_not():
    quiet = [feats({"e4": 10.0 + 0.1 * k}) for k in range(5)]
    assert cv.is_stable(quiet, n=4, tol=6.0)
    assert not cv.is_stable(quiet[:2], n=4, tol=6.0)
    busy = [feats({"e4": 10.0}), feats({"e4": 90.0}), feats({"e4": 12.0}), feats({"e4": 80.0})]
    assert not cv.is_stable(busy, n=4, tol=6.0)


def test_the_sanity_gate_passes_a_move_and_rejects_a_hand():
    board = rules.new_board()
    before = rules.occupancy(board)
    after = dict(before); after["e2"], after["e4"] = False, True
    ok, why = cv.sanity(before, after)
    assert ok and "e2" in why and "e4" in why
    assert cv.sanity(before, before) == (False, "no change")
    hand = {s: (True if s[1] in "3456" else v) for s, v in before.items()}
    ok, why = cv.sanity(before, hand)
    assert not ok and "hand in frame" in why


def test_castling_changes_four_squares_and_still_passes_the_gate():
    board = rules.new_board("rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    before = rules.occupancy(board)
    after = rules.new_board(board.fen()); after.push(__import__("chess").Move.from_uci("e1g1"))
    ok, _ = cv.sanity(before, rules.occupancy(after))
    assert ok


# ---------------------------------------------------------------- the pixel half, once there are photographs
@needs_photos
def test_every_fixture_is_read_correctly():
    import cv2
    d = ws.load()
    empty = cv2.imread(str(FIXTURES / "empty.jpg"))
    reader = cv.BoardReader(d)
    assert reader.ok, reader.error()
    assert reader.set_reference(empty)
    for name, fen in [("start.jpg", None), ("after_e4.jpg", None), ("capture.jpg", None), ("castling.jpg", None)]:
        board = rules.new_board(fen)
        read = reader.read(cv2.imread(str(FIXTURES / name)))
        reader.calibrate(read["features"], board)
        assert read["occupancy"] == rules.occupancy(board), name


@needs_photos
def test_a_hand_in_the_frame_is_rejected_by_the_gates():
    import cv2
    d = ws.load()
    reader = cv.BoardReader(d)
    reader.set_reference(cv2.imread(str(FIXTURES / "empty.jpg")))
    board = rules.new_board()
    read = reader.read(cv2.imread(str(FIXTURES / "hand.jpg")))
    ok, why = cv.sanity(rules.occupancy(board), read["occupancy"])
    assert not ok, why
