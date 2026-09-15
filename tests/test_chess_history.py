"""The learning store: what gets recorded, what it adds up to, and what it is allowed to suggest.

The point of these tests is the *restraint*: a suggestion must not appear from one bad grasp, must name
the field it would change and the evidence behind it, and must never be applied without being asked for.
"""

import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from chess_synth import taught  # noqa: E402
from so101_bridge.chess import history as H  # noqa: E402
from so101_bridge.chess import workspace as ws  # noqa: E402


@pytest.fixture
def cfg():
    d = taught(grasps=("p", "k", "n"))
    ws.save(d)
    return ws.load()


def grasps(n, ok, piece="n", square="b1", settled=19.0, expect=12, game="g1"):
    for i in range(n):
        H.record_grasp(game, i + 1, piece, square, 2.0, 30.0, settled, expect, ok)


def test_nothing_is_recorded_until_something_happens(cfg):
    assert H.read() == []
    st = H.stats(d=cfg)
    assert st["totals"]["rows"] == 0 and st["totals"]["grasp_success_pct"] is None
    assert H.suggestions(st, cfg) == []


def test_a_recorded_attempt_survives_a_round_trip(cfg):
    H.record_grasp("g1", 3, "p", "e2", 1.4, 18.0, 20.5, 12, True, detail="grasp p on e2")
    row = H.read()[-1]
    assert row["kind"] == "grasp" and row["piece"] == "p" and row["square"] == "e2"
    assert row["settled"] == 20.5 and row["ok"] is True and row["t"] > 0 and row["time"]


def test_an_unwritable_history_does_not_stop_a_game(cfg, monkeypatch, tmp_path):
    (tmp_path / "nope").write_text("a file where a directory should be")
    monkeypatch.setattr(H, "HISTORY_FILE", tmp_path / "nope" / "deep" / "x.jsonl")
    H.record_grasp("g1", 1, "p", "e2", 1.4, 18.0, 20.0, 12, True)      # must not raise
    assert H.read() == []


def test_a_corrupt_line_is_skipped_rather_than_fatal(cfg):
    H.record_grasp("g1", 1, "p", "e2", 1.4, 18.0, 20.0, 12, True)
    with open(H.HISTORY_FILE, "a") as f:
        f.write("{not json\n\n")
    H.record_grasp("g1", 2, "p", "e2", 1.4, 18.0, 20.0, 12, True)
    assert len(H.read()) == 2


def test_stats_separate_the_piece_from_the_square(cfg):
    grasps(3, False, piece="n", square="b1")
    grasps(5, True, piece="n", square="b1")
    grasps(4, True, piece="p", square="e2")
    H.record_verify("g1", 2, False, squares=["h8"])
    st = H.stats(d=cfg)
    assert st["pieces"]["n"]["grasps"] == 8 and st["pieces"]["n"]["misses"] == 3
    assert st["pieces"]["n"]["miss_rate"] == pytest.approx(0.375)
    assert st["pieces"]["n"]["name"] == "knight"
    assert st["squares"]["b1"]["attempts"] == 8 and st["squares"]["b1"]["misses"] == 3
    assert st["squares"]["e2"]["attempts"] == 4 and st["squares"]["e2"]["misses"] == 0
    assert st["squares"]["h8"]["verify_fail"] == 1
    assert st["totals"]["grasp_success_pct"] == pytest.approx(75.0)


def test_one_bad_grasp_suggests_nothing(cfg):
    grasps(1, False)
    assert H.suggestions(d=cfg) == []


def test_repeated_misses_suggest_lowering_that_piece_grasp_height(cfg):
    grasps(6, False, piece="k")
    s = next(x for x in H.suggestions(d=cfg) if x["id"] == "grasp_z_k")
    assert s["path"] == "pieces.k.grasp_z_cm"
    assert s["value"] == pytest.approx(float(cfg["pieces"]["k"]["grasp_z_cm"]) - H.GRASP_STEP_CM)
    assert "6 of 6" in s["why"] and "missed" in s["why"]


def test_a_piece_that_was_never_taught_is_told_to_be_taught_not_nudged(cfg):
    d = ws.load(); d["pieces"]["q"]["grasp_z_cm"] = None; ws.save(d)
    grasps(5, False, piece="q")
    s = next(x for x in H.suggestions(d=ws.load()) if x["id"] == "grasp_teach_q")
    assert s["path"] is None and "teach" in s["what"]


def test_consistently_firm_grasps_suggest_a_tighter_miss_threshold(cfg):
    grasps(6, True, piece="p", settled=21.0)
    s = next(x for x in H.suggestions(d=cfg) if x["id"] == "stall_p")
    assert s["path"] == "pieces.p.stall_min"
    assert s["value"] == pytest.approx(21.0 - H.STALL_MARGIN)
    assert s["current"] == cfg["pieces"]["p"]["stall_min"]


def test_a_threshold_already_about_right_is_left_alone(cfg):
    d = ws.load(); d["pieces"]["p"]["stall_min"] = 19.5; ws.save(d)
    grasps(6, True, piece="p", settled=21.0)
    assert not [x for x in H.suggestions(d=ws.load()) if x["id"] == "stall_p"]


def test_a_square_that_keeps_failing_is_advice_with_no_automatic_fix(cfg):
    grasps(2, False, piece="p", square="h8")
    grasps(4, True, piece="p", square="h8")
    H.record_verify("g1", 1, False, squares=["h8"])
    s = next(x for x in H.suggestions(d=cfg) if x["id"] == "square_h8")
    assert s["path"] is None and "h8" in s["what"]
    with pytest.raises(ValueError, match="advice"):
        H.apply("square_h8", d=cfg)


def test_applying_a_suggestion_writes_it_and_logs_the_change(cfg):
    grasps(6, True, piece="p", settled=21.0)
    before = ws.load()["pieces"]["p"]["stall_min"]
    out = H.apply("stall_p")
    assert out["path"] == "pieces.p.stall_min" and out["from"] == before
    assert ws.load()["pieces"]["p"]["stall_min"] == out["to"] != before
    assert any("applied stall_p" in (r.get("detail") or "") for r in H.read())
    assert not [x for x in H.suggestions(d=ws.load()) if x["id"] == "stall_p"]     # and it stops suggesting it


def test_applying_something_that_is_no_longer_suggested_is_refused(cfg):
    with pytest.raises(ValueError, match="no current suggestion"):
        H.apply("stall_p")


def test_the_history_is_bounded(cfg, monkeypatch):
    monkeypatch.setattr(H, "MAX_ROWS", 10)
    grasps(25, True, piece="p")
    assert len(H.read()) == 10
    assert H.trim() == 10
    assert len(H.HISTORY_FILE.read_text().strip().splitlines()) == 10
