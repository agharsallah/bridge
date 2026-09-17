"""The chess routines: set the board up, play a game, run a puzzle.

Like every routine in the bridge these only *request* goals — the control loop's step cap, floor guard,
load guard and stall guard are unchanged and still have the last word. What is specific to chess is the
turn loop, and the one rule that keeps it safe around people: the board is only ever read with the arm
parked at rest, and a reading is only believed once it has been still for several frames and the change
it shows is consistent with a legal move.

    chess_setup    place the start position from pieces the human hands to the feed slot
    chess_play     the game loop: human turn (watch the board) / robot turn (choose, execute, verify)
    chess_puzzle   set a FEN up, then play a scripted line
    chess_verify   the teaching verify pass: fit, report reachability, then visit every taught point

Anything the routine cannot do alone goes into ``needs`` in the game document and the routine holds:
an ambiguous reading, a promotion, a failed verification, a square the arm cannot serve. A human
answers with ``chess_confirm`` (or ``chess_move``), and play continues from there.
"""

import time

from ..routines import routine
from ..settings import CHESS_AUTO_TRIGGER, CHESS_CAM, CHESS_POLL_S
from ..util import log
from . import game as rules
from . import history, physical
from . import vision as cvis
from . import workspace as ws_mod
from .kinematics import GripperModel, report_text
from .log import GameLog
from .players import make as make_player

PLAY_PHASES = ("preflight", "human_turn", "robot_turn", "verify", "finish", "done")
SETUP_PHASES = ("preflight", "feeding", "finish", "done")


# ---------------------------------------------------------------- small helpers
def _request(ctrl, key, clear=True):
    """Read (and consume) one field of ``ctrl.chess_request`` — how file and HTTP commands reach a routine."""
    req = getattr(ctrl, "chess_request", None) or {}
    val = req.get(key)
    if val is not None and clear:
        ctrl.chess_request = {k: v for k, v in req.items() if k != key}
    return val


def _model(ctrl, d):
    st, _ = ctrl.snapshot()
    return GripperModel(ctrl.floor, d, st.get("limits") or {})


def _frame(ctrl, cam=None):
    return ctrl.latest_frame(cam or CHESS_CAM)


def _fail(ctrl, msg, glog=None):
    log(f"CHESS FAILED: {msg}")
    ctrl.routine_status = f"failed: {msg}"
    ctrl.phase("failed", msg)
    ctrl.request({"action": "hold", "src": "auto"})
    if glog: glog.phase("failed")
    return False


def _wait_for(ctrl, predicate, timeout=600.0, poll=0.2):
    """Block until ``predicate()`` is truthy. False on abort, ESTOP or timeout."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if ctrl.routine_abort.is_set(): return False
        st, _ = ctrl.snapshot()
        if st.get("estop"): return False
        got = predicate()
        if got: return got
        time.sleep(poll)
    return False


def _snapshot(ctrl, name):
    """Save the newest overhead frame under var/steps/ so a step can be looked at afterwards. Silent if no camera."""
    frame = _frame(ctrl)
    if frame is None: return
    try:
        import cv2
        from ..paths import VAR_DIR
        out = VAR_DIR / "steps"; out.mkdir(exist_ok=True)
        cv2.imwrite(str(out / f"{time.strftime('%H%M%S')}_{name.replace(' ', '_')}.jpg"), frame)
    except Exception as e:
        log(f"chess: step snapshot failed: {e}")


def run_steps(ctrl, steps, on_phase=None, record=None):
    """Execute the primitives from :func:`physical.steps`. ``(ok, reason)``.

    ``record(row)`` is called once per gripper action that carries a ``record`` block, with the settled
    opening and whether it counted as a hold added — that is what feeds ``chess/history.py``."""
    from ..shared.motion import bridge_from_present
    stepwise = bool(_request(ctrl, "step", clear=False))
    resume = int(_request(ctrl, "from_step") or 0)          # 1-based: steps before it already succeeded, skip them
    for i, s in enumerate(steps):
        if ctrl.routine_abort.is_set(): return False, f"aborted at step {i + 1}/{len(steps)}"
        if i + 1 < resume: continue
        if stepwise:
            # the operator watches one primitive at a time: hold here until chess_confirm, keep a still of the board
            _snapshot(ctrl, f"{i:02d}_before_{s.get('label', s['op'])}")
            ctrl.phase("robot_turn", f"step {i + 1}/{len(steps)}: {s.get('label', s['op'])} — chess_confirm to run it")
            if not _wait_for(ctrl, lambda: _request(ctrl, "confirm")): return False, f"stopped before step {i + 1}"
        if on_phase: on_phase(s.get("label", s["op"]))
        if s["op"] == "dwell":
            t0 = time.time()
            while time.time() - t0 < float(s.get("seconds", 0)):
                if ctrl.routine_abort.is_set(): return False, "aborted during dwell"
                time.sleep(0.05)
        elif s["op"] == "grip":
            settled = ctrl.gripper_to(float(s["value"]))
            if settled is None: return False, f"gripper interrupted: {s.get('label')}"
            missed = "expect_min" in s and settled < float(s["expect_min"])
            if record and s.get("record"):
                record({**s["record"], "settled": None if settled is None else round(float(settled), 1),
                        "expect_min": s.get("expect_min"), "ok": not missed, "detail": s.get("label", "")})
            if missed:
                return False, (f"grasp missed: jaws settled at {settled:.1f}, expected at least "
                               f"{s['expect_min']} ({s.get('label')})")
        elif s["op"] == "path":
            pts = bridge_from_present(ctrl, s["points"])
            if not ctrl.path(pts, s.get("speed", physical.TRAVEL_SPEED), allow=s.get("allow")):
                return False, f"move interrupted: {s.get('label')} (guard, ESTOP or timeout)"
        else:
            return False, f"unknown step op {s['op']!r}"
    return True, ""


# ---------------------------------------------------------------- reading the board
def _reader(ctrl, d, board=None):
    """A calibrated :class:`vision.BoardReader`, or None when the camera path is not set up.

    Not having one is not an error: without taught image corners the game still runs, the human just
    types what they played instead of the camera reading it.

    A reader captured earlier (the **Capture empty board** button, ``chess_reference``) is reused: its
    empty-board reference was taken when the board really was empty, which is not something a routine
    starting mid-game can do for itself."""
    existing = getattr(ctrl, "chess_reader", None)
    if existing is not None and existing.ok and existing.reference is not None:
        if board is not None:
            frame = _frame(ctrl, d["board"].get("cam"))
            read = existing.read(frame) if frame is not None else None
            if read: existing.calibrate(read["features"], board)
        return existing
    r = cvis.BoardReader(d)
    if not r.ok:
        log(f"chess: {r.error()} — playing without the camera (moves must be entered with chess_move)")
        return None
    frame = _frame(ctrl, d["board"].get("cam"))
    if frame is None:
        log(f"chess: no frame from camera {d['board'].get('cam')!r} — playing without the camera")
        return None
    if not r.set_reference(frame):
        return None
    if board is not None:
        read = r.read(frame)
        if read: r.calibrate(read["features"], board)
    ctrl.chess_reader = r
    return r


def _watch_for_move(ctrl, reader, board, glog, auto=None):
    """Poll the board until a settled, sane, unambiguous change appears. Returns a UCI move or None.

    A person can always short-circuit the camera by saying what they played (``chess_move``) — which is
    also the whole interface when the board has no taught image corners. With ``CHESS_AUTO_TRIGGER``
    off, a reading the camera is sure about still waits for ``chess_confirm``: the gates have to earn
    their keep with a human watching before the arm starts moving on its own."""
    auto = CHESS_AUTO_TRIGGER if auto is None else auto
    before = rules.occupancy(board)
    pending, announced = None, None
    while True:
        if ctrl.routine_abort.is_set(): return None
        typed = _request(ctrl, "move")                      # a human may always just say what they played
        if typed:
            if rules.is_legal(board, typed):
                glog.clear_need(); return str(typed).strip().lower()
            glog.need("illegal_move", move=typed, fen=board.fen())
        if pending and _request(ctrl, "confirm"):
            glog.clear_need(); return pending
        if reader is not None:
            frame = _frame(ctrl, reader.d["board"].get("cam"))
            read = reader.read(frame) if frame is not None else None
            if read and reader.stable():
                ok, why = cvis.sanity(before, read["occupancy"])
                if not ok:
                    if why != "no change" and announced != why:
                        announced = why
                        glog.need("illegal_move", detail=why, hint="put the position back, or send chess_move")
                else:
                    cands = rules.resolve(board, read["occupancy"], read["colours"])
                    if len(cands) == 1:
                        if auto:
                            glog.clear_need(); return cands[0]
                        if pending != cands[0]:
                            pending = cands[0]; announced = None
                            glog.phase("human_turn", waiting_for="confirm")
                            ctrl.phase("human_turn", f"saw {cands[0]} — send chess_confirm to accept it")
                    elif len(cands) > 1 and announced != why:
                        announced = why
                        glog.need("ambiguous", candidates=cands, detail=why,
                                  hint="send chess_move with the one you played")
                    elif not cands and announced != why:
                        announced = why
                        glog.need("illegal_move", detail=why, hint="put the position back, or send chess_move")
        time.sleep(CHESS_POLL_S)


# ---------------------------------------------------------------- chess_play
@routine("chess_play", label="CHESS — play a game",
         description="Play a game: read the human's move from the board, choose and execute the robot's.",
         phases=PLAY_PHASES)
def play(ctrl):
    req = getattr(ctrl, "chess_request", None) or {}
    ctrl.routine_status = "running"; ctrl.phase("preflight")
    d = ws_mod.load()
    glog = GameLog.load(req.get("game")) if req.get("game") else GameLog.latest()
    if glog is None or glog.doc.get("result"):
        glog = GameLog.new(mode="play", robot=req.get("robot", d["board"]["robot_side"]),
                           players=req.get("players") or {"white": "human", "black": "llm"},
                           start_fen=req.get("fen"))
    model = _model(ctrl, d)
    if not model.ok:
        return _fail(ctrl, f"board model: {model.error}", glog)
    log(report_text(model.reachability_report(), d))
    board = glog.board()
    reader = _reader(ctrl, d, board)
    players = {c: make_player(glog.doc["players"].get(c, "human")) for c in ("white", "black")}
    robot_colour = glog.doc["robot"]
    ctrl.phase("preflight", f"{glog.id}: {glog.doc['players']}, robot plays {robot_colour}")
    try:
        while not ctrl.routine_abort.is_set():
            res = rules.result(board)
            if res:
                glog.finish(*res); break
            turn = rules.side_name(board.turn)
            mine = (turn == robot_colour)
            ctrl.phase("robot_turn" if mine else "human_turn", f"{turn} to move")
            glog.phase("robot_turn" if mine else "human_turn", waiting_for=(players[turn].kind if mine else "board"))
            if not mine:
                uci = _watch_for_move(ctrl, reader, board, glog)
                if not uci: return _fail(ctrl, "no move seen from the human", glog)
                san, fen = rules.push(board, uci)
                glog.move(by=glog.doc["players"].get(turn, "human"), uci=uci, san=san, fen_after=fen,
                          source="board" if reader else "typed", executed_by="human", verify="ok")
                continue
            uci = players[turn].choose(board, glog, ctrl)
            if not uci: return _fail(ctrl, f"{players[turn].kind} did not produce a move", glog)
            ok = _execute(ctrl, d, model, board, glog, uci, players[turn].kind, reader)
            if not ok: return False
        ctrl.phase("finish", str(glog.doc.get("result")))
        ctrl.rest_path(gripper=None)
        ctrl.routine_status = "done"; ctrl.phase("done")
        return True
    finally:
        for p in players.values(): p.close()


def _execute(ctrl, d, model, board, glog, uci, by, reader):
    """Play the robot's move on the real board, or hand it to a human when the arm cannot serve it."""
    planned = physical.plan(board, uci, d)
    log("chess: " + physical.describe(planned))
    bad = physical.check(model, planned["actions"], d)
    if bad:
        glog.need("unreachable", move=uci, san=planned["san"],
                  actions=[b["action"] for b in bad], why=[b["why"] for b in bad],
                  hint=f"play {planned['san']} for the robot, then send chess_confirm")
        ctrl.phase("robot_turn", f"needs a human: {planned['san']} ({'; '.join(b['why'] for b in bad)})")
        if not _wait_for(ctrl, lambda: _request(ctrl, "confirm")):
            return _fail(ctrl, f"nobody played {planned['san']} for the robot", glog)
        san, fen = rules.push(board, uci)
        row = glog.move(by=by, uci=uci, san=san, fen_after=fen, actions=planned["actions"],
                        executed_by="human", verify="assumed")
        history.record_move(glog.id, row["ply"], uci, san, "human",
                            detail="; ".join(b["why"] for b in bad))
        glog.clear_need()
        return True
    steps = physical.steps(model, planned["actions"], d, dry_run=bool(_request(ctrl, "dry", clear=False)))
    if steps is None:
        return _fail(ctrl, f"could not compile a path for {planned['san']}", glog)
    ply = len(glog.doc["moves"]) + 1
    keep = lambda row: history.append({**row, "game": glog.id, "ply": ply})
    t0 = time.time()
    ok, why = run_steps(ctrl, steps, lambda label: ctrl.phase("robot_turn", label), record=keep)
    if not ok:
        glog.need("grasp_failed", move=uci, san=planned["san"], detail=why)
        return _fail(ctrl, why, glog)
    ctrl.rest_path(gripper=None)
    seconds = round(time.time() - t0, 1)
    san, fen = rules.push(board, uci)
    row = glog.move(by=by, uci=uci, san=san, fen_after=fen, actions=planned["actions"], executed_by="robot",
                    exec={"seconds": seconds, "steps": len(steps),
                          "points": sum(len(st.get("points", [])) for st in steps),
                          "grips": len([st for st in steps if st["op"] == "grip"])})
    history.record_move(glog.id, row["ply"], uci, san, "robot", seconds=seconds, steps=len(steps))
    # verify at rest: the camera should now see exactly the position we think we produced
    verdict = "unchecked"
    if reader is not None:
        ctrl.phase("verify", planned["san"])
        frame = _frame(ctrl, d["board"].get("cam"))
        read = reader.read(frame) if frame is not None else None
        if read:
            wrong = [s for s, v in rules.occupancy(board).items() if bool(read["occupancy"].get(s)) != v]
            verdict = "ok" if not wrong else f"differs on {' '.join(wrong)}"
            history.record_verify(glog.id, row["ply"], not wrong, squares=wrong, detail=verdict)
            if wrong:
                glog.need("verify_failed", move=uci, san=planned["san"], squares=wrong,
                          hint="fix the board, then send chess_confirm")
                if not _wait_for(ctrl, lambda: _request(ctrl, "confirm")):
                    return _fail(ctrl, f"board does not match after {planned['san']}", glog)
                glog.clear_need()
            else:
                reader.calibrate(read["features"], board)     # the truth is known here: re-tune the thresholds
    glog.verify(row["ply"], verdict)
    return True


# ---------------------------------------------------------------- chess_setup
@routine("chess_setup", label="CHESS — set the board up",
         description="Place the start position one piece at a time from pieces handed to the feed slot.",
         phases=SETUP_PHASES)
def setup(ctrl):
    req = getattr(ctrl, "chess_request", None) or {}
    ctrl.routine_status = "running"; ctrl.phase("preflight")
    d = ws_mod.load()
    model = _model(ctrl, d)
    if not model.ok: return _fail(ctrl, f"board model: {model.error}")
    if not (d.get("slots", {}).get("feed") or {}).get("pose"):
        return _fail(ctrl, "the feed slot has not been taught (run the chess_board teaching session)")
    board = rules.new_board(req.get("fen"))
    dry = bool(req.get("dry"))
    todo = _start_squares(board)
    glog = GameLog.new(mode="setup", robot=d["board"]["robot_side"], start_fen=board.fen())
    ctrl.phase("feeding", f"{len(todo)} pieces")
    done, skipped = 0, []
    for square, piece in todo:
        if ctrl.routine_abort.is_set(): return _fail(ctrl, "aborted", glog)
        acts = [{"op": "pick", "slot": "feed", "piece": piece["symbol"]},
                {"op": "place", "sq": square, "piece": piece["symbol"]}]
        bad = physical.check(model, acts, d)
        if bad:
            skipped.append(square)
            glog.need("unreachable", square=square, piece=piece["name"], why=[b["why"] for b in bad],
                      hint=f"put the {piece['colour']} {piece['name']} on {square} yourself, then chess_confirm")
            ctrl.phase("feeding", f"{square}: needs a human ({piece['colour']} {piece['name']})")
            if not _wait_for(ctrl, lambda: _request(ctrl, "confirm")): return _fail(ctrl, "nobody placed it", glog)
            glog.clear_need(); continue
        glog.need("feed", square=square, piece=piece["name"], colour=piece["colour"],
                  hint=f"put a {piece['colour']} {piece['name']} on the feed spot, then chess_confirm")
        ctrl.phase("feeding", f"hand over a {piece['colour']} {piece['name']} for {square}")
        if not _wait_for(ctrl, lambda: _request(ctrl, "confirm")): return _fail(ctrl, "no piece handed over", glog)
        glog.clear_need()
        steps = physical.steps(model, acts, d, dry_run=dry)
        if steps is None: return _fail(ctrl, f"no path for {square}", glog)
        keep = lambda row: history.append({**row, "game": glog.id, "ply": done + 1})
        ok, why = run_steps(ctrl, steps, lambda label: ctrl.phase("feeding", f"{square}: {label}"), record=keep)
        if not ok: return _fail(ctrl, why, glog)
        done += 1
    ctrl.rest_path(gripper=None)
    ctrl.phase("finish", f"{done} placed, {len(skipped)} left to the human ({' '.join(skipped) or '-'})")
    glog.finish("setup complete", None)
    ctrl.routine_status = "done"; ctrl.phase("done")
    return True


def _start_squares(board):
    """Every occupied square of a position, far rank first so the arm never reaches over a piece it placed."""
    import chess
    order = sorted(chess.SQUARES, key=lambda s: -chess.square_rank(s))
    out = []
    for s in order:
        p = board.piece_at(s)
        if p is None: continue
        out.append((chess.square_name(s),
                    {"symbol": p.symbol().lower(), "colour": rules.side_name(p.color),
                     "name": ws_mod.PIECE_NAMES[p.symbol().lower()]}))
    return out


# ---------------------------------------------------------------- chess_puzzle
@routine("chess_puzzle", label="CHESS — run a puzzle",
         description="Set a position up from a puzzle file and play the scripted solution.",
         phases=PLAY_PHASES)
def puzzle(ctrl):
    import json

    from ..paths import PUZZLES_DIR
    req = getattr(ctrl, "chess_request", None) or {}
    name = req.get("puzzle")
    path = PUZZLES_DIR / f"{name}.json" if name else None
    if not path or not path.is_file(): return _fail(ctrl, f"no puzzle {name!r} in puzzles/")
    spec = json.loads(path.read_text())
    d = ws_mod.load()
    ctrl.routine_status = "running"; ctrl.phase("preflight", f"{name}: {spec.get('fen')}")
    model = _model(ctrl, d)
    if not model.ok: return _fail(ctrl, f"board model: {model.error}")
    robot = spec.get("robot", d["board"]["robot_side"])
    glog = GameLog.new(mode="puzzle", robot=robot, start_fen=spec["fen"],
                       players={robot: "script", ("black" if robot == "white" else "white"): "human"},
                       name=f"{time.strftime('%Y%m%d_%H%M%S')}_{name}")
    board = glog.board()
    reader = _reader(ctrl, d, board)
    script = make_player("script", line=spec.get("solution", []))
    while not ctrl.routine_abort.is_set():
        res = rules.result(board)
        if res:
            glog.finish(*res); break
        turn = rules.side_name(board.turn)
        if turn == robot:
            ctrl.phase("robot_turn", "scripted line")
            uci = script.choose(board, glog, ctrl)
            if not uci: return _fail(ctrl, "the puzzle line ran out", glog)
            if not _execute(ctrl, d, model, board, glog, uci, "script", reader): return False
        else:
            ctrl.phase("human_turn", "waiting for the reply")
            uci = _watch_for_move(ctrl, reader, board, glog)
            if not uci: return _fail(ctrl, "no reply seen", glog)
            san, fen = rules.push(board, uci)
            glog.move(by="human", uci=uci, san=san, fen_after=fen, executed_by="human", verify="ok")
    ctrl.phase("finish", str(glog.doc.get("result")))
    ctrl.rest_path(gripper=None)
    ctrl.routine_status = "done"; ctrl.phase("done")
    return True


# ---------------------------------------------------------------- chess_verify (the teaching verify pass)
@routine("chess_verify", label="CHESS — verify the taught board",
         description="Fit the board model, print residuals and the 64-square reachability, then visit "
                     "every taught point under torque so the operator can see where the tip lands.",
         phases=("preflight", "report", "visiting", "done"))
def verify(ctrl):
    d = ws_mod.load()
    ctrl.routine_status = "running"; ctrl.phase("preflight")
    model = _model(ctrl, d)
    if not model.ok: return _fail(ctrl, f"board model: {model.error}")
    ctrl.phase("report")
    log(f"chess: board model {model.summary()}")
    report = model.reachability_report()
    log(report_text(report, d))
    ws_mod.set_unreachable(physical.unreachable_squares(model, d))
    visit = [n for n in ws_mod.CORNERS if n in d["board"].get("corners", {})]
    visit += [e["square"] for e in d["board"].get("extra", []) if e.get("square")]
    hover, probe_z = float(d["heights"]["hover_cm"]), 1.0
    ctrl.phase("visiting", f"{len(visit)} taught points")
    for square in visit:
        if ctrl.routine_abort.is_set(): return _fail(ctrl, "aborted")
        above = model.pose_for(square, hover)
        down = model.pose_for(square, probe_z)
        if above is None or down is None:
            log(f"chess: {square} cannot be visited (hover {above is not None}, 1 cm {down is not None})"); continue
        ctrl.phase("visiting", f"{square} at {hover} cm")
        if not ctrl.goto_far(above, 4.0): return _fail(ctrl, f"could not reach above {square}")
        ctrl.phase("visiting", f"{square} at {probe_z} cm — is the tip over the centre?")
        if not ctrl.goto_far(down, 2.0): return _fail(ctrl, f"could not descend on {square}")
        time.sleep(1.0)
        if not ctrl.goto_far(above, 3.0): return _fail(ctrl, f"could not lift from {square}")
    ctrl.rest_path(gripper=None)
    ctrl.routine_status = "done"; ctrl.phase("done", f"visited {len(visit)} points")
    return True
