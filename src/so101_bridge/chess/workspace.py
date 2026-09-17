"""The chess workspace: everything the operator teaches, stored in config/chess.json.

    {
      "board": {"size_cm": 29.0, "robot_side": "white", "cam": "top",
                "corners": {"a1": pose, "h1": pose, "h8": pose, "a8": pose},   # FINGERTIPS on the square CENTRE
                "extra": [{"square": "e4", "pose": pose}],                     # more contacts = a better fit
                "image_corners": {"a1": [x, y], ...},                          # same four squares in the camera frame
                "reach": {"e1": {"pose": pose,                                 # driven there by hand, see below
                                 "allow": {"wrist_flex": [-83.8, 89.8]}}}},
      "gripper": {"tool_len_cm": 11.0, "open": 60, "close": 8, "max_lean_deg": 45},
      "pieces": {"p": {"grasp_z_cm": 1.4, "lean_deg": 12, "close": 8, "stall_min": 12}, ... , "k": {...}},
      "slots": {"feed": {"pose": pose, "uv": [u, v]}, "handover": {...}, "reserve": {...}},
      "heights": {"hover_cm": 6.0, "carry_cm": 9.0, "place_drop_cm": 0.3, "grasp_default_cm": 1.4},
      "probes": [{"u": 12.7, "v": 12.7, "z_touch_cm": 1.0}],
      "vision": {"occ_threshold": null, "warp_px": 512},
      "unreachable": []
    }

Coordinates. The (u, v) frame is the *robot's* frame, not chess's: u runs left to right as the robot
sees the board, v runs from the far edge towards the robot. The plane it spans is corner centre to
corner centre, 7 pitches = 25.375 cm on a 29 cm board, and that is what the plane model is fitted to.
``robot_side`` says which colour sits in front of the robot — the side whose home rank is nearest it —
and flips the mapping so the near rank is always the robot's own. Nothing else in the package needs to
know the board's orientation.
"""


from ..paths import CHESS_FILE, PIECES_FILE
from ..shared.store import get_path, load_json, pose_round, save_json, set_path
from ..util import log

FILES = "abcdefgh"
CORNERS = ("a1", "h1", "h8", "a8")          # the four board corners, in cycle order round the board
PIECES = ("p", "n", "b", "r", "q", "k")
PIECE_NAMES = {"p": "pawn", "n": "knight", "b": "bishop", "r": "rook", "q": "queen", "k": "king"}
SIDES = ("white", "black")

PIECE_DEFAULTS = {"grasp_z_cm": None,       # None = fall back to heights.grasp_default_cm (nothing taught yet)
                  "lean_deg": None, "open": None, "close": None, "stall_min": 12,
                  "height_cm": None}        # how tall the piece stands; the tallest one sets the carry height
DEFAULTS = {
    "board": {"size_cm": 29.0, "robot_side": "white", "cam": "top", "corners": {}, "extra": [], "image_corners": {},
              "reach": {}},   # squares the operator reached by hand: {"e1": {"pose", "allow", "lean_deg"}}
    "gripper": {"tool_len_cm": 11.0,        # wrist axis -> jaw centre; measure it in P0 and correct this
                "open": 60.0, "close": 8.0, "max_lean_deg": 45.0},
    "pieces": {p: dict(PIECE_DEFAULTS) for p in PIECES},
    "slots": {},
    "heights": {"hover_cm": 6.0, "carry_cm": 9.0, "place_drop_cm": 0.3, "grasp_default_cm": 1.4},
    "probes": [],
    "vision": {"occ_threshold": None, "warp_px": 512, "centre_frac": 0.6},
    "unreachable": [],
}


def load():
    d = load_json(CHESS_FILE, DEFAULTS)
    d["pieces"] = {p: {**PIECE_DEFAULTS, **d.get("pieces", {}).get(p, {})} for p in PIECES}
    heights = load_json(PIECES_FILE, {}).get("height_cm", {})      # the set's dimensions live in config/pieces.json
    for p in PIECES:
        if d["pieces"][p].get("height_cm") is None and heights.get(p) is not None: d["pieces"][p]["height_cm"] = float(heights[p])
    if d["board"].get("robot_side") not in SIDES: d["board"]["robot_side"] = "white"
    return d


def save(d):
    save_json(CHESS_FILE, d)


# ---------------------------------------------------------------- board geometry
def pitch_cm(d):
    """Centre-to-centre distance between neighbouring squares."""
    return float(d["board"]["size_cm"]) / 8.0


def plane_cm(d):
    """The (u, v) extent the plane model spans: corner centre to corner centre, 7 pitches square."""
    p = pitch_cm(d)
    return (7 * p, 7 * p)


def parse_square(name):
    """'e4' -> (file index 0..7 from a, rank 1..8). Raises on anything else."""
    s = str(name).strip().lower()
    if len(s) != 2 or s[0] not in FILES or not s[1].isdigit() or not 1 <= int(s[1]) <= 8:
        raise ValueError(f"not a square: {name!r}")
    return FILES.index(s[0]), int(s[1])


def square_uv(d, name):
    """Centre of a square in the robot's (u, v) frame, cm.

    ``robot_side`` is the colour sitting in front of the robot, so its home rank is the near rank
    (largest v) and its queenside is on the robot's left (u = 0). Rotating the board 180 degrees is
    therefore a one-word config change, not a re-teach of the geometry."""
    f, r = parse_square(name)
    p = pitch_cm(d)
    if d["board"]["robot_side"] == "white":
        return (f * p, (8 - r) * p)
    return ((7 - f) * p, (r - 1) * p)


def uv_square(d, u, v):
    """Nearest square to a point in the robot's frame, or None if it is off the board."""
    p = pitch_cm(d)
    i, k = round(float(u) / p), round(float(v) / p)
    if not (0 <= i <= 7 and 0 <= k <= 7): return None
    if d["board"]["robot_side"] == "white": f, r = i, 8 - k
    else: f, r = 7 - i, k + 1
    return f"{FILES[f]}{r}"


def squares(d=None):
    """All 64 square names, far rank first as the robot sees them (the order the reports print in)."""
    d = d or load()
    order = range(8, 0, -1) if d["board"]["robot_side"] == "white" else range(1, 9)
    files = FILES if d["board"]["robot_side"] == "white" else FILES[::-1]
    return [f"{f}{r}" for r in order for f in files]


def contact_points(d):
    """All taught fingertips-on-a-square poses with their (u, v): [(u, v, pose), ...]."""
    cs = d["board"].get("corners", {})
    pts = [(*square_uv(d, n), cs[n]) for n in CORNERS if n in cs]
    for e in d["board"].get("extra", []):
        if e.get("pose") and e.get("square"):
            pts.append((*square_uv(d, e["square"]), e["pose"]))
    return pts


def corner_quad(d):
    """The four corners in cycle order for the plane model's crossed-corner check, or None."""
    cs = d["board"].get("corners", {})
    if not all(n in cs for n in CORNERS): return None
    return [(n, *square_uv(d, n), cs[n]) for n in CORNERS]


def piece_grasp(d, piece):
    """(grasp height above the board in cm, lean in degrees or None, gripper close value) for a piece."""
    pc = d["pieces"].get(piece.lower(), {})
    z = pc.get("grasp_z_cm")
    if z is None: z = d["heights"].get("grasp_default_cm", 1.4)
    close = pc.get("close")
    if close is None: close = d["gripper"]["close"]
    return float(z), pc.get("lean_deg"), float(close)


def piece_open(d, piece):
    o = d["pieces"].get(piece.lower(), {}).get("open")
    return float(d["gripper"]["open"] if o is None else o)


# ---------------------------------------------------------------- teaching operations
def set_board(size_cm=None, robot_side=None, cam=None):
    d = load()
    if size_cm is not None: d["board"]["size_cm"] = float(size_cm)
    if robot_side is not None:
        if robot_side not in SIDES: raise ValueError(f"robot_side must be one of {SIDES}")
        d["board"]["robot_side"] = robot_side
    if cam is not None: d["board"]["cam"] = str(cam)
    save(d); log(f"chess: board {d['board']['size_cm']} cm, robot plays {d['board']['robot_side']}, cam {d['board']['cam']}")
    return d


def mark_corner(square, pose):
    if square not in CORNERS: raise ValueError(f"corner must be one of {CORNERS}")
    d = load(); d["board"]["corners"][square] = pose_round(pose); save(d)
    log(f"chess: corner {square} = {d['board']['corners'][square]}")


def mark_extra(square, pose):
    parse_square(square)
    d = load()
    d["board"]["extra"] = [e for e in d["board"]["extra"] if e.get("square") != square]
    d["board"]["extra"].append({"square": square, "pose": pose_round(pose)}); save(d)
    log(f"chess: extra contact on {square}")


def clear_board_marks():
    d = load(); d["board"]["corners"], d["board"]["extra"], d["probes"] = {}, [], []; save(d)
    log("chess: board marks cleared")


def set_image_corners(corners):
    d = load(); d["board"]["image_corners"] = {k: [float(x), float(y)] for k, (x, y) in corners.items()}; save(d)
    log(f"chess: image corners {sorted(d['board']['image_corners'])}")


def set_slot(name, pose, uv=None):
    d = load(); slot = d["slots"].setdefault(name, {})
    slot["pose"] = pose_round(pose)
    if uv is not None: slot["uv"] = [round(float(uv[0]), 2), round(float(uv[1]), 2)]
    save(d); log(f"chess: slot {name} taught")


def set_piece(piece, **kw):
    piece = piece.lower()
    if piece not in PIECES: raise ValueError(f"piece must be one of {PIECES}")
    d = load()
    for k, v in kw.items():
        if k in PIECE_DEFAULTS and v is not None: d["pieces"][piece][k] = float(v)
    save(d); log(f"chess: piece {piece} {d['pieces'][piece]}")


def set_heights(**kw):
    d = load()
    for k, v in kw.items():
        if k in DEFAULTS["heights"] and v is not None: d["heights"][k] = float(v)
    save(d); log(f"chess: heights {d['heights']}")


def set_unreachable(names):
    d = load(); d["unreachable"] = sorted(set(names)); save(d)
    log(f"chess: {len(d['unreachable'])} squares marked unreachable")


# ---------------------------------------------------------------- guided teaching hook (shared/teach.py)
def on_teach(cfg, step, record, ctrl):
    """Turn a raw taught pose into what chess actually needs.

    A ``grasp`` step records where the jaws closed on a piece standing on a known square; what the
    routine needs is the *height above the board* and the *lean* the operator used there, because that
    is what has to be reproduced on all 64 squares. Both need the fitted plane model, so this runs
    after the pose is stored and quietly does nothing while the corners are still missing.
    """
    kind = step.get("kind", "contact")
    if kind == "grasp":
        from .kinematics import GripperModel
        st, _ = ctrl.snapshot()
        model = GripperModel(ctrl.floor, cfg, st.get("limits") or {})
        if not model.ok:
            log(f"chess: grasp {step['id']} stored raw — {model.error}; re-run it once the corners are in")
            return
        u, v, z = model.pose_to_uv(record["grasp_pose"])
        target = dict(get_path(cfg, step["to"], {}) or {})
        target["grasp_z_cm"] = round(z * 100, 2)
        target["lean_deg"] = model.lean_of(record["grasp_pose"])
        target["taught_on"] = step.get("square")
        set_path(cfg, step["to"], target)
        log(f"chess: grasp {step['id']} -> z {target['grasp_z_cm']} cm, lean {target['lean_deg']} deg "
            f"(marked at u {u:.1f} v {v:.1f} cm)")
    elif step.get("reach"):
        _reach_note(cfg, step["reach"], (record or {}).get("pose"), ctrl)
    elif kind in ("contact", "pose") and step.get("slot"):
        from .kinematics import GripperModel
        st, _ = ctrl.snapshot()
        model = GripperModel(ctrl.floor, cfg, st.get("limits") or {})
        if model.ok:
            u, v, _ = model.pose_to_uv(record)
            cfg["slots"].setdefault(step["slot"], {})["uv"] = [round(u, 2), round(v, 2)]


def _model(cfg, ctrl):
    from .kinematics import GripperModel
    st, _ = ctrl.snapshot()
    return GripperModel(ctrl.floor, cfg, st.get("limits") or {}), (st.get("limits") or {})


def teach_extra_steps(cfg, ctrl):
    """One more step per square the fitted model says it cannot reach.

    ``--`` in the reachability report is a claim about the *soft limits*, not about the arm: near the
    base the jaws need more wrist_flex than the calibration sweep allows, and the operator can usually
    put the fingertips there by hand. So the session ends by asking them to try. What comes back is
    either a pose that proves the square is serviceable — and says exactly which limit stands in the
    way — or a Skip, which is the honest answer that it really is out of reach.
    """
    model, _ = _model(cfg, ctrl)
    if not model.ok: return []
    z = float(cfg["heights"].get("grasp_default_cm", 1.4))
    out = []
    for sq in squares(cfg):
        if model.pose_for(sq, z) is not None: continue
        out.append({"id": f"reach_{sq}", "kind": "contact", "reach": sq,
                    "append": "board.extra", "match": "square", "with": {"square": sq, "reach": True},
                    "prompt": f"The model cannot put the jaws on {sq.upper()} at grasp height. Free-drive the "
                              f"closed fingertips onto the CENTRE of {sq.upper()} and press Done — or Skip if "
                              f"the arm really will not go there."})
    return out


def _reach_note(cfg, square, pose, ctrl):
    """Record what the operator just proved about one square.

    The soft limits come off the calibration sweep and stay as they are — they are right for the arm in
    general. What a reach step adds is local and evidence-backed: at *this* square the fingertips really
    were here, so the joints this pose needs are allowed at this square and nowhere else. It is written
    to chess.json, not to config/limits.json, and it changes no motor register: the control loop takes
    the allowance with the command and still caps it at the motor's own sweep.
    """
    if not pose: return
    from ..hardware import hard_limits
    model, limits = _model(cfg, ctrl)
    hard = hard_limits()
    allow, clamp = {}, {}
    for j, val in pose.items():
        lo, hi = (limits.get(j) or [None, None])[:2]
        if lo is None or lo <= val <= hi: continue
        allow[j] = [round(val, 1), round(hi, 1)] if val < lo else [round(lo, 1), round(val, 1)]
        hlo, hhi = (hard.get(j) or [val, val])[:2]
        if not hlo <= val <= hhi:
            # free-driving goes there, commanding it does not: the motor's own position registers stop
            # short. The allowance is kept — the move is simply served as far as the motors go.
            clamp[j] = [round(val, 1), round(hlo, 1), round(hhi, 1)]
    entry = {"pose": pose_round(pose), "allow": allow,
             "lean_deg": model.lean_of(pose) if model.ok else None}
    if clamp: entry["clamped_by_motors"] = clamp
    cfg["board"].setdefault("reach", {})[square] = entry
    if clamp:
        what = ", ".join(f"{j} {v[0]} vs motor range {v[1]}..{v[2]}" for j, v in clamp.items())
        log(f"chess: {square} recorded, but WATCH IT: {what} — free-driving reaches that pose, commanding it "
            f"does not, so the motor registers will clamp the move short and the jaws land off {square}. "
            f"Lifting the clamp means editing config/limits.json and restarting.")
    elif allow:
        what = ", ".join(f"{j} to {v[1] if abs(v[1]) > abs(v[0]) else v[0]}" for j, v in allow.items())
        log(f"chess: {square} reached by hand past the soft limit ({what}, lean {entry['lean_deg']} deg) — "
            f"recorded in chess.json; that range is now allowed on {square} alone")
    else:
        log(f"chess: {square} reached by hand inside the soft limits (lean {entry['lean_deg']} deg) — "
            f"the taught pose is stored as the contact for that square")


def reach_allow(d, square):
    """The per-joint range ``square`` may use instead of the soft limit, or None. See :func:`_reach_note`."""
    entry = (d["board"].get("reach") or {}).get(str(square).lower())
    return (entry or {}).get("allow") or None


def reach_pose(d, square):
    """The pose the operator drove to on ``square``, or None."""
    entry = (d["board"].get("reach") or {}).get(str(square).lower())
    return (entry or {}).get("pose")


def on_finish(cfg, session, ctrl):
    """End of the teaching session: refit, reprint the report and rewrite the unreachable list."""
    from .kinematics import report_text
    from .physical import unreachable_squares
    model, _ = _model(cfg, ctrl)
    if not model.ok:
        log(f"chess: board model not fitted — {model.error}"); return
    log(f"chess: board model {model.summary()}")
    log(report_text(model.reachability_report(), cfg))
    set_unreachable(unreachable_squares(model, cfg))


# ---------------------------------------------------------------- status
def status(d=None):
    d = d or load()
    corners = [n for n in CORNERS if n in d["board"].get("corners", {})]
    taught = [p for p in PIECES if d["pieces"][p].get("grasp_z_cm") is not None]
    slots = [n for n, s in d["slots"].items() if s.get("pose")]
    return {"board": {"size_cm": d["board"]["size_cm"], "pitch_cm": round(pitch_cm(d), 4),
                      "robot_side": d["board"]["robot_side"], "cam": d["board"]["cam"],
                      "corners": corners, "extra": len(d["board"].get("extra", [])),
                      "image_corners": sorted(d["board"].get("image_corners", {}))},
            "pieces_taught": taught, "slots": slots, "probes": len(d.get("probes", [])),
            "unreachable": d.get("unreachable", []),
            "ready": len(corners) == 4 and "handover" in slots and bool(taught)}
