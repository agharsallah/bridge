"""From a chess move to arm motion: actions, then guarded path/grip steps.

Two stages, on purpose. :func:`plan` turns a move into *actions* — pick this, place it there — which is
the level a human reads and the level the ``unreachable`` fallback works at: if the arm cannot serve an
action, the routine announces the move and a person plays it. :func:`steps` turns actions into the
primitives the routine feeds the control loop: dense ``path`` point lists and gripper commands, every
one of them still subject to the loop's step cap, floor guard, load guard and stall guard.

Heights, in the taught board's frame:

    grasp_z_cm      where the jaws close on that kind of piece (taught, per piece)
    + place_drop    a placed piece is released a few mm high so it is set down, not pressed down
    hover_cm        clear of the pieces, used to approach and to verify
    carry_cm        travel height — must clear the tallest piece on the board

A piece is lifted straight up before it travels and lowered straight down at the end, and everything in
between is straight *on the board*, not in joint space: a joint-space interpolation between two squares
bows across the board and sweeps the pieces in between onto the floor.

Squares the operator reached by hand during teaching carry an ``allow`` — the joint range that pose
proved — on every step that works on them, so the control loop lets those steps past the soft limit on
that square and nowhere else. Nothing about the arm's stored limits or its motor registers changes.
"""

import math

import chess

from ..settings import CHESS_CARRY_CM
from ..shared.motion import plane_travel, z_correction
from ..util import log
from . import game as rules
from . import workspace as ws_mod

TRAVEL_SPEED = 6.0          # deg/s while carrying
DESCEND_SPEED = 4.0         # deg/s for the last centimetres onto a piece
Z_SEARCH_STEP_CM = 0.5      # how finely we look for the highest height a square can be served at
PLANE_STEP_CM = 2.0         # spacing of the interpolated points along an in-plane travel


# ---------------------------------------------------------------- move -> actions
def plan(board, uci, d=None):
    """Actions for one move. ``{"uci", "san", "kind", "actions": [...]}``.

    Order matters: a captured piece leaves the board *before* the capturing piece arrives, or two
    pieces end up on one square. Castling is two ordinary moves. A promotion sends the pawn to the
    handover slot and fetches a queen from the reserve, because the arm cannot turn a pawn into a queen.
    """
    d = d or ws_mod.load()
    info = rules.move_kind(board, uci)
    acts = []
    if info["capture"]:
        acts += [{"op": "pick", "sq": info["capture"], "piece": _piece_at(board, info["capture"])},
                 {"op": "place", "slot": "handover", "why": "captured"}]
    if info["promotion"]:
        acts += [{"op": "pick", "sq": info["from"], "piece": info["piece"]},
                 {"op": "place", "slot": "handover", "why": "promoted pawn"},
                 {"op": "pick", "slot": _reserve_slot(d, info["colour"], info["promotion"]),
                  "piece": info["promotion"]},
                 {"op": "place", "sq": info["to"], "piece": info["promotion"]}]
    else:
        acts += [{"op": "pick", "sq": info["from"], "piece": info["piece"]},
                 {"op": "place", "sq": info["to"], "piece": info["piece"]}]
    if info["castle"]:
        rook_from, rook_to = info["castle"]
        acts += [{"op": "pick", "sq": rook_from, "piece": "r"}, {"op": "place", "sq": rook_to, "piece": "r"}]
    san = board.san(chess.Move.from_uci(uci))
    return {"uci": uci, "san": san, "kind": info, "actions": acts}


def _piece_at(board, square):
    p = board.piece_at(chess.parse_square(square))
    return p.symbol().lower() if p else "p"


def _reserve_slot(d, colour, piece):
    """Where the spare queens live. ``reserve_queen_white`` by convention, ``reserve_<piece>_<colour>``
    for the under-promotions nobody has taught yet."""
    name = f"reserve_{ws_mod.PIECE_NAMES.get(piece, piece)}_{colour}"
    return name if name in d.get("slots", {}) else f"reserve_queen_{colour}"


def check(model, actions, d=None):
    """Which actions the arm cannot serve, and why. Empty list = the robot can play the move itself."""
    d = d or ws_mod.load()
    bad = []
    for k, a in enumerate(actions):
        if a.get("slot"):
            slot = d.get("slots", {}).get(a["slot"])
            if not slot or not slot.get("pose"):
                bad.append({"i": k, "action": a, "why": f"slot {a['slot']!r} has not been taught"})
            continue
        z_grasp, lean, _ = ws_mod.piece_grasp(d, a.get("piece", "p"))
        if model.pose_for(a["sq"], z_grasp, lean) is None:
            bad.append({"i": k, "action": a, "why": f"{a['sq']} out of reach at grasp height ({z_grasp} cm)"})
        else:
            z_top = _best_z(model, a["sq"], _carry(d), z_grasp, lean)
            if z_top is None or z_top < _hover(d):
                bad.append({"i": k, "action": a, "why": f"{a['sq']} can only be served up to "
                            f"{z_top if z_top is not None else z_grasp} cm above the board; the approach needs "
                            f"{_hover(d)} cm (hover_cm) so the jaws clear the neighbouring pieces"})
    return bad


def unreachable_squares(model, d=None, heights=None):
    """The squares to write into ``chess.json["unreachable"]`` — no grasp pose anywhere at grasp height."""
    d = d or ws_mod.load()
    z = float(d["heights"].get("grasp_default_cm", 1.4))
    return [s for s in ws_mod.squares(d) if model.pose_for(s, z) is None]


# ---------------------------------------------------------------- actions -> path/grip steps
def _hover(d):
    return float(d["heights"].get("hover_cm", 6.0))


def _carry(d):
    """Travel height: never below the tallest taught piece plus the clearance the operator asked for."""
    carry = float(d["heights"].get("carry_cm", CHESS_CARRY_CM))
    tallest = max([float(p.get("height_cm") or 0) for p in d.get("pieces", {}).values()] or [0.0])
    return max(carry, tallest + float(d["heights"].get("clearance_cm", 0.0)))


def _best_z(model, square, want_cm, floor_cm, lean=None):
    """The highest height at or below ``want_cm`` that ``square`` can be served at, or None.

    The far squares need a lot of lean and run out of arm before they run out of board: they can be
    reached low but not high. Rather than refuse them, a piece there travels lower and the height is
    ramped along the way — which is also what stops a piece lifted off the near rank from being dragged
    across its neighbours."""
    z = float(want_cm)
    while z >= floor_cm - 1e-9:
        if model.pose_for(square, z, lean) is not None: return round(z, 2)
        z -= Z_SEARCH_STEP_CM
    return None


def _slot_uv(d, name):
    slot = d.get("slots", {}).get(name) or {}
    uv = slot.get("uv")
    return (float(uv[0]), float(uv[1])) if uv else None


def steps(model, actions, d=None, dry_run=False, start_uv=None):
    """Primitive steps for a list of actions, or None if any of them cannot be served.

    Step ops:
        ``path``  {"points": [pose, ...], "speed", "label"}   — fed straight to the guarded control loop
        ``grip``  {"value", "label", "expect_min"}            — close/open; ``expect_min`` is the settled
                                                                opening below which the grasp missed
        ``dwell`` {"seconds", "label"}

    ``dry_run`` keeps every height at carry and never closes the jaws, so the whole sequence can be
    watched over the real board with nothing at risk.
    """
    d = d or ws_mod.load()
    zcorr = z_correction(d.get("probes", []), 0.0)
    carry, drop = _carry(d), float(d["heights"].get("place_drop_cm", 0.3))
    out, cur_uv, cur_z = [], start_uv, carry
    for a in actions:
        piece = a.get("piece", "p")
        z_grasp, lean, close = ws_mod.piece_grasp(d, piece)
        open_to = ws_mod.piece_open(d, piece)
        where = a.get("sq") or f"[{a.get('slot')}]"
        allow = ws_mod.reach_allow(d, a["sq"]) if a.get("sq") else None
        if a.get("slot"):
            slot = d.get("slots", {}).get(a["slot"]) or {}
            pose = slot.get("pose")
            if not pose: log(f"chess: slot {a['slot']!r} not taught"); return None
            uv = _slot_uv(d, a["slot"])
            if a["op"] == "pick":
                out.append({"op": "grip", "value": open_to, "label": f"open before {where}"})
            out.append({"op": "path", "points": [dict(pose)], "speed": TRAVEL_SPEED, "label": f"to {where}"})
            out.append({"op": "grip", "value": (close if a["op"] == "pick" else open_to),
                        "label": f"{'take from' if a['op'] == 'pick' else 'release at'} {where}",
                        "record": {"kind": "grasp" if a["op"] == "pick" else "place", "piece": piece,
                                   "slot": a["slot"], "square": None, "z_cm": None, "lean_deg": None},
                        **({"expect_min": _stall_min(d, piece)} if a["op"] == "pick" and not dry_run else {})})
            cur_uv, cur_z = uv, carry
            continue

        u, v = ws_mod.square_uv(d, a["sq"])
        # a piece is picked at the lean it was taught at (where the model is accurate) but set down as upright
        # as the arm allows: released tilted, its base meets the board on one edge and it falls over
        if a["op"] == "place": lean = d["heights"].get("place_lean_deg", lean)
        z_top = _best_z(model, a["sq"], carry, z_grasp, lean)
        z_low = (z_grasp if a["op"] == "pick" else z_grasp + drop)
        if dry_run: z_low = z_top
        if z_top is None or z_top < _hover(d):
            log(f"chess: {a['sq']} cannot be approached from above ({z_top} cm, needs {_hover(d)} cm)"); return None
        # 1) travel to the square at the highest height both ends allow, ramping if they differ
        if cur_uv is not None:
            pts = plane_travel(model, cur_uv, (u, v), cur_z, step_cm=PLANE_STEP_CM, z_to=z_top,
                               zcorr=zcorr, lean=_rad(lean))
            if pts is None: log(f"chess: no clear line from {cur_uv} to {a['sq']} at carry height"); return None
            out.append({"op": "path", "points": pts, "speed": TRAVEL_SPEED, "label": f"carry to {where}",
                        **({"allow": allow} if allow else {})})
        else:
            first = model.pose_for(a["sq"], z_top, lean)
            if first is None: return None
            out.append({"op": "path", "points": [first], "speed": TRAVEL_SPEED, "label": f"to {where}",
                        **({"allow": allow} if allow else {})})
        # 2) open before descending onto a piece, so the jaws never brush a neighbour on the way down
        if a["op"] == "pick" and not dry_run:
            out.append({"op": "grip", "value": open_to, "label": f"open over {where}"})
        # 3) straight down, close or release, straight back up
        down = _column(model, d, a["sq"], z_top, z_low, lean, zcorr)
        if down is None: log(f"chess: cannot descend on {a['sq']}"); return None
        if down: out.append({"op": "path", "points": down, "speed": DESCEND_SPEED, "label": f"down on {where}",
                             **({"allow": allow} if allow else {})})
        if not dry_run:
            out.append({"op": "grip", "value": (close if a["op"] == "pick" else open_to),
                        "label": f"{'grasp' if a['op'] == 'pick' else 'release'} {piece} on {where}",
                        # what the history needs to learn from this attempt: which piece, where, how low
                        # the jaws went and at what lean — the lean the IK really used, not the taught one
                        "record": {"kind": "grasp" if a["op"] == "pick" else "place", "piece": piece,
                                   "square": a["sq"], "slot": None, "z_cm": round(z_low, 2),
                                   "lean_deg": model.lean_of(down[-1]) if down else model.lean_of(
                                       model.pose_for(a["sq"], z_low, lean))},
                        **({"expect_min": _stall_min(d, piece)} if a["op"] == "pick" else {})})
            out.append({"op": "dwell", "seconds": 0.2, "label": "settle"})
        up = _column(model, d, a["sq"], z_low, z_top, lean, zcorr)
        if up: out.append({"op": "path", "points": up, "speed": DESCEND_SPEED, "label": f"lift from {where}",
                           **({"allow": allow} if allow else {})})
        cur_uv, cur_z = (u, v), z_top
    return out


def _column(model, d, square, z_from, z_to, lean, zcorr):
    """Poses straight up or down over one square between two heights, ~0.5 cm apart. [] if there is nothing to do."""
    if abs(z_to - z_from) < 1e-6: return []
    n = max(1, int(round(abs(z_to - z_from) / Z_SEARCH_STEP_CM)))
    u, v = ws_mod.square_uv(d, square)
    out = []
    for k in range(1, n + 1):
        z = z_from + (z_to - z_from) * k / n
        pose = model.pose_for(square, z + zcorr(u, v), lean)
        if pose is None: return None
        out.append(pose)
    return out


def _stall_min(d, piece):
    return float(d["pieces"].get(piece, {}).get("stall_min", 12))


def _rad(lean_deg):
    return None if lean_deg is None else math.pi - math.radians(float(lean_deg))


def describe(planned):
    """One human-readable line per action, for the log, the dashboard and the ``unreachable`` prompt."""
    out = []
    for a in planned["actions"]:
        where = a.get("sq") or a.get("slot")
        out.append(f"{a['op']} {a.get('piece', '')} {where}".replace("  ", " ").strip())
    return f"{planned['san']} ({planned['uci']}): " + " -> ".join(out)
