"""The board's coordinate frame: squares to (u, v) and back, either way round the board."""

import pytest

from so101_bridge.chess import workspace as ws


def cfg(side="white"):
    d = ws.load(); d["board"]["size_cm"] = 29.0; d["board"]["robot_side"] = side
    return d


def test_pitch_and_plane():
    d = cfg()
    assert ws.pitch_cm(d) == pytest.approx(3.625)
    assert ws.plane_cm(d) == pytest.approx((25.375, 25.375))       # corner centre to corner centre


def test_uv_is_the_robots_frame_not_the_boards():
    """u runs left to right as the robot sees it, v from the far edge towards it — whichever side it plays."""
    white, black = cfg("white"), cfg("black")
    p = ws.pitch_cm(white)
    # robot plays white: its own home rank (1) is nearest, so it has the largest v; a-file on its left
    assert ws.square_uv(white, "a1") == pytest.approx((0.0, 7 * p))
    assert ws.square_uv(white, "h8") == pytest.approx((7 * p, 0.0))
    # robot plays black: the board is turned round, so rank 8 is nearest and the h-file is on its left
    assert ws.square_uv(black, "h8") == pytest.approx((0.0, 7 * p))
    assert ws.square_uv(black, "a1") == pytest.approx((7 * p, 0.0))
    # ...and in both cases the robot's own back rank is the near one
    assert ws.square_uv(white, "e1")[1] > ws.square_uv(white, "e8")[1]
    assert ws.square_uv(black, "e8")[1] > ws.square_uv(black, "e1")[1]


@pytest.mark.parametrize("side", ["white", "black"])
def test_square_uv_round_trips_for_all_64(side):
    d = cfg(side)
    names = ws.squares(d)
    assert len(names) == 64 and len(set(names)) == 64
    for n in names:
        u, v = ws.square_uv(d, n)
        assert ws.uv_square(d, u, v) == n
        assert ws.uv_square(d, u + 1.2, v - 1.2) == n            # within half a square still resolves


def test_off_board_points_have_no_square():
    d = cfg()
    assert ws.uv_square(d, -6.0, 10.0) is None
    assert ws.uv_square(d, 10.0, 40.0) is None


def test_corner_cycle_goes_round_the_board():
    """a1 -> h1 -> h8 -> a8 must be a simple quadrilateral, or the plane fit maps the board to a bow tie."""
    for side in ("white", "black"):
        d = cfg(side)
        pts = [ws.square_uv(d, n) for n in ws.CORNERS]
        area = sum(pts[i][0] * pts[(i + 1) % 4][1] - pts[(i + 1) % 4][0] * pts[i][1] for i in range(4)) / 2
        assert abs(area) == pytest.approx(25.375 ** 2)           # the full square, not a crossed shape


def test_bad_squares_are_rejected():
    for bad in ("i1", "a9", "", "e", "44", None):
        with pytest.raises(ValueError):
            ws.parse_square(bad)


def test_piece_grasp_falls_back_until_it_is_taught():
    d = cfg()
    z, lean, close = ws.piece_grasp(d, "q")
    assert z == d["heights"]["grasp_default_cm"] and lean is None and close == d["gripper"]["close"]
    d["pieces"]["q"].update(grasp_z_cm=3.2, lean_deg=18.0, close=6.0)
    assert ws.piece_grasp(d, "q") == (3.2, 18.0, 6.0)


def test_saved_config_survives_a_round_trip():
    ws.set_board(size_cm=29.0, robot_side="black", cam="top")
    ws.mark_corner("a1", {"shoulder_pan": 1.234, "shoulder_lift": 2.0, "elbow_flex": 3.0,
                          "wrist_flex": 4.0, "wrist_roll": 5.0})
    ws.mark_extra("e4", {"shoulder_pan": 9.0, "shoulder_lift": 2.0, "elbow_flex": 3.0, "wrist_flex": 4.0})
    d = ws.load()
    assert d["board"]["robot_side"] == "black"
    assert d["board"]["corners"]["a1"]["shoulder_pan"] == 1.23         # stored rounded, arm joints only
    assert "gripper" not in d["board"]["corners"]["a1"]
    assert [e["square"] for e in d["board"]["extra"]] == ["e4"]
    assert ws.status(d)["board"]["corners"] == ["a1"]
    assert ws.status(d)["ready"] is False
