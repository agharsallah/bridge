"""Move -> actions -> guarded path/grip steps, on the synthetic board."""

import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from chess_synth import WIDE_LIMITS, floor_model, taught  # noqa: E402
from so101_bridge.chess import game as rules  # noqa: E402
from so101_bridge.chess import physical  # noqa: E402
from so101_bridge.chess import workspace as ws  # noqa: E402
from so101_bridge.chess.kinematics import GripperModel  # noqa: E402


@pytest.fixture
def board_model():
    d = taught()
    return d, GripperModel(floor_model(), d, WIDE_LIMITS)


def ops(planned):
    return [(a["op"], a.get("sq") or a.get("slot")) for a in planned["actions"]]


def test_a_quiet_move_is_one_pick_and_one_place(board_model):
    d, _ = board_model
    p = physical.plan(rules.new_board(), "e2e4", d)
    assert ops(p) == [("pick", "e2"), ("place", "e4")]
    assert p["san"] == "e4"


def test_a_capture_clears_the_target_before_the_capturer_arrives(board_model):
    d, _ = board_model
    b = rules.new_board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    assert ops(physical.plan(b, "e4d5", d)) == [("pick", "d5"), ("place", "handover"),
                                                ("pick", "e4"), ("place", "d5")]


def test_en_passant_removes_the_pawn_that_is_not_on_the_target_square(board_model):
    d, _ = board_model
    b = rules.new_board("rnbqkbnr/pppp1ppp/8/4pP2/8/8/PPPPP1PP/RNBQKBNR w KQkq e6 0 3")
    assert ops(physical.plan(b, "f5e6", d)) == [("pick", "e5"), ("place", "handover"),
                                                ("pick", "f5"), ("place", "e6")]


def test_castling_is_two_ordinary_moves(board_model):
    d, _ = board_model
    b = rules.new_board("rnbqk2r/pppp1ppp/5n2/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    assert ops(physical.plan(b, "e1g1", d)) == [("pick", "e1"), ("place", "g1"),
                                                ("pick", "h1"), ("place", "f1")]


def test_promotion_swaps_the_pawn_for_a_reserve_queen(board_model):
    d, _ = board_model
    b = rules.new_board("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    assert ops(physical.plan(b, "a7a8q", d)) == [("pick", "a7"), ("place", "handover"),
                                                 ("pick", "reserve_queen_white"), ("place", "a8")]


def test_check_passes_a_served_move_and_names_what_it_cannot_do(board_model):
    d, model = board_model
    p = physical.plan(rules.new_board(), "e2e4", d)
    assert physical.check(model, p["actions"], d) == []
    d2 = taught(); d2["slots"].pop("handover")
    b = rules.new_board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    bad = physical.check(model, physical.plan(b, "e4d5", d2)["actions"], d2)
    assert len(bad) == 1 and "handover" in bad[0]["why"]


def test_every_square_is_servable_on_a_board_the_arm_can_reach(board_model):
    d, model = board_model
    assert physical.unreachable_squares(model, d) == []


def test_steps_lift_travel_and_descend_in_that_order(board_model):
    d, model = board_model
    p = physical.plan(rules.new_board(), "b1c3", d)
    steps = physical.steps(model, p["actions"], d, start_uv=ws.square_uv(d, "e4"))
    assert steps and all(s["op"] in ("path", "grip", "dwell") for s in steps)
    labels = [s.get("label", "") for s in steps]
    assert any("carry to b1" in x for x in labels) and any("down on b1" in x for x in labels)
    assert any("lift from b1" in x for x in labels) and any("release" in x for x in labels)
    # the jaws open before descending on the piece and close only once they are down
    open_i = next(i for i, s in enumerate(steps) if s["op"] == "grip" and "open over b1" in s["label"])
    grasp_i = next(i for i, s in enumerate(steps) if s["op"] == "grip" and s["label"].startswith("grasp"))
    down_i = next(i for i, s in enumerate(steps) if "down on b1" in s.get("label", ""))
    assert open_i < down_i < grasp_i


def test_a_grasp_step_carries_the_threshold_that_detects_a_miss(board_model):
    d, model = board_model
    steps = physical.steps(model, physical.plan(rules.new_board(), "e2e4", d)["actions"], d)
    grasps = [s for s in steps if s["op"] == "grip" and "expect_min" in s]
    assert grasps and all(s["expect_min"] == d["pieces"]["p"]["stall_min"] for s in grasps)


def test_travel_is_straight_on_the_board_not_through_it(board_model):
    """A joint-space interpolation between two squares bows across the board and sweeps pieces off it."""
    d, model = board_model
    steps = physical.steps(model, [{"op": "pick", "sq": "a1", "piece": "p"},
                                   {"op": "place", "sq": "h8", "piece": "p"}], d,
                           start_uv=ws.square_uv(d, "a1"))
    travel = next(s for s in steps if "carry to h8" in s.get("label", ""))
    a, h = ws.square_uv(d, "a1"), ws.square_uv(d, "h8")
    for pose in travel["points"]:
        u, v, _ = model.pose_to_uv(pose)
        t = max(0.0, min(1.0, ((u - a[0]) * (h[0] - a[0]) + (v - a[1]) * (h[1] - a[1])) /
                         ((h[0] - a[0]) ** 2 + (h[1] - a[1]) ** 2)))
        off = abs((u - (a[0] + t * (h[0] - a[0]))) + 0) ** 2 + abs(v - (a[1] + t * (h[1] - a[1]))) ** 2
        assert off ** 0.5 < 0.2, (u, v)                      # within 2 mm of the straight line a1-h8


def test_dry_run_never_closes_the_jaws_and_never_goes_low(board_model):
    d, model = board_model
    steps = physical.steps(model, physical.plan(rules.new_board(), "e2e4", d)["actions"], d, dry_run=True)
    assert not [s for s in steps if s["op"] == "grip"]
    carry = d["heights"]["carry_cm"]
    for s in steps:
        for pose in s.get("points", []):
            assert model.pose_to_uv(pose)[2] * 100 >= carry - 0.2


def test_describe_reads_as_one_line(board_model):
    d, _ = board_model
    b = rules.new_board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    text = physical.describe(physical.plan(b, "e4d5", d))
    assert text.startswith("exd5 (e4d5):") and "handover" in text
