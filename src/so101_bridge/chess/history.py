"""What actually happened, so the next game goes better than the last one.

Every grasp, place, move and verification appends one line to ``var/chess_history.jsonl``. That file is
never read by the routine while it plays — it is the record, not the plan — but between games it is the
only evidence about things nobody can measure directly: whether the taught grasp height for a knight is
really right, which squares the arm keeps getting wrong, how much the jaws actually close on a pawn.

:func:`stats` aggregates it and :func:`suggestions` turns the aggregate into concrete, reversible edits
to ``config/chess.json`` — each one naming the field, the current value, the proposed value and the
evidence behind it. **Nothing is applied automatically.** The operator presses Apply on the /chess page
(or sends ``chess_apply``), which is the only way a suggestion ever changes how the arm behaves.

A suggestion with no ``path`` is advice with no automatic form: re-teach this grasp, re-probe that
corner, look at whether the board has moved. Those are shown and never applied.
"""

import json
import time

from ..paths import VAR_DIR
from ..shared.store import get_path, set_path
from ..util import log
from . import workspace as ws_mod

HISTORY_FILE = VAR_DIR / "chess_history.jsonl"
MAX_ROWS = 20000                  # keep the file bounded; the aggregate is what matters, not the tail
RECENT = 40                       # how many attempts per piece/square a suggestion is allowed to weigh

KINDS = ("grasp", "place", "move", "verify", "reading")

# thresholds a suggestion has to clear before it is worth showing an operator
MIN_ATTEMPTS = 4                  # never draw a conclusion from one bad grasp
MISS_RATE_WARN = 0.25             # a quarter of grasps missing is a real problem, not bad luck
GRASP_STEP_CM = 0.3               # how far a suggestion moves a grasp height at a time
STALL_MARGIN = 2.0                # keep the miss threshold this far below the openings that really held


def append(row):
    """Record one attempt. Never raises: losing a history line must not stop a game."""
    try:
        row = {"t": round(time.time(), 1), "time": time.strftime("%Y-%m-%d %H:%M:%S"), **row}
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as e:
        log(f"chess: could not append to the history ({e})")
    return row


def read(limit=None):
    """The history, oldest first. Unparseable lines are skipped rather than killing the read."""
    if not HISTORY_FILE.is_file(): return []
    rows = []
    try:
        with open(HISTORY_FILE) as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try: rows.append(json.loads(line))
                except Exception: continue
    except OSError as e:
        log(f"chess: history unreadable ({e})"); return []
    if len(rows) > MAX_ROWS: rows = rows[-MAX_ROWS:]
    return rows[-limit:] if limit else rows


def trim():
    """Rewrite the file with only the rows we keep — called when it has grown past MAX_ROWS."""
    rows = read()
    HISTORY_FILE.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return len(rows)


# ---------------------------------------------------------------- aggregation
def stats(rows=None, d=None):
    """Aggregate the history into what the page draws and what the suggestions reason over.

    ``pieces[p]``  grasps, misses, miss_rate, mean settled opening when it held, the taught height
    ``squares[s]`` attempts, verify failures, mean lean used, last verdict
    ``games``      one row per game seen, moves and how many needed a human
    ``totals``     the headline numbers
    """
    rows = read() if rows is None else rows
    d = d or ws_mod.load()
    pieces, squares, games = {}, {}, {}

    def piece(p):
        return pieces.setdefault(p, {"grasps": 0, "misses": 0, "held": [], "z_cm": [], "lean_deg": []})

    def square(s):
        return squares.setdefault(s, {"attempts": 0, "verify_fail": 0, "misses": 0, "lean_deg": [], "last": None})

    for r in rows:
        kind, sq, p = r.get("kind"), r.get("square"), r.get("piece")
        if r.get("game"):
            g = games.setdefault(r["game"], {"moves": 0, "by_human": 0, "verify_fail": 0, "misses": 0})
            if kind == "move":
                g["moves"] += 1
                if r.get("executed_by") == "human": g["by_human"] += 1
            if kind == "verify" and not r.get("ok"): g["verify_fail"] += 1
            if kind == "grasp" and not r.get("ok"): g["misses"] += 1
        if kind == "grasp" and p:
            pc = piece(p); pc["grasps"] += 1
            if r.get("ok"):
                if r.get("settled") is not None: pc["held"].append(float(r["settled"]))
            else:
                pc["misses"] += 1
            if r.get("z_cm") is not None: pc["z_cm"].append(float(r["z_cm"]))
            if r.get("lean_deg") is not None: pc["lean_deg"].append(float(r["lean_deg"]))
        if sq:
            sc = square(sq)
            if kind in ("grasp", "place"): sc["attempts"] += 1
            if kind == "grasp" and not r.get("ok"): sc["misses"] += 1
            if kind == "verify" and not r.get("ok"): sc["verify_fail"] += 1
            if r.get("lean_deg") is not None: sc["lean_deg"].append(float(r["lean_deg"]))
            sc["last"] = r.get("detail") or kind

    for p, pc in pieces.items():
        pc["held"] = pc["held"][-RECENT:]; pc["z_cm"] = pc["z_cm"][-RECENT:]; pc["lean_deg"] = pc["lean_deg"][-RECENT:]
        pc["miss_rate"] = round(pc["misses"] / pc["grasps"], 3) if pc["grasps"] else 0.0
        pc["mean_held"] = round(sum(pc["held"]) / len(pc["held"]), 1) if pc["held"] else None
        pc["min_held"] = round(min(pc["held"]), 1) if pc["held"] else None
        pc["taught_z_cm"] = d["pieces"].get(p, {}).get("grasp_z_cm")
        pc["stall_min"] = d["pieces"].get(p, {}).get("stall_min")
        pc["name"] = ws_mod.PIECE_NAMES.get(p, p)
    for s, sc in squares.items():
        sc["lean_deg"] = sc["lean_deg"][-RECENT:]
        sc["mean_lean"] = round(sum(sc["lean_deg"]) / len(sc["lean_deg"]), 1) if sc["lean_deg"] else None

    totals = {"rows": len(rows), "games": len(games),
              "moves": sum(g["moves"] for g in games.values()),
              "by_human": sum(g["by_human"] for g in games.values()),
              "grasps": sum(p["grasps"] for p in pieces.values()),
              "misses": sum(p["misses"] for p in pieces.values()),
              "verify_fail": sum(s["verify_fail"] for s in squares.values())}
    totals["grasp_success_pct"] = (round(100 * (totals["grasps"] - totals["misses"]) / totals["grasps"], 1)
                                   if totals["grasps"] else None)
    return {"pieces": pieces, "squares": squares, "games": games, "totals": totals}


# ---------------------------------------------------------------- suggestions
def suggestions(st=None, d=None):
    """Concrete, reversible edits the evidence supports. Each is `{id, what, why, path, value, current}`.

    ``path`` is a dotted path into ``config/chess.json`` that :func:`apply` can write; ``path: None``
    means there is no automatic form and a person has to go and do something.
    """
    d = d or ws_mod.load()
    st = st or stats(d=d)
    out = []

    for p, pc in sorted(st["pieces"].items()):
        name = pc["name"]
        if pc["grasps"] < MIN_ATTEMPTS: continue

        # the jaws keep closing on nothing: the taught grasp height is probably too high
        if pc["miss_rate"] >= MISS_RATE_WARN:
            cur = pc["taught_z_cm"]
            if cur is not None:
                out.append({
                    "id": f"grasp_z_{p}", "path": f"pieces.{p}.grasp_z_cm",
                    "current": cur, "value": round(max(0.2, float(cur) - GRASP_STEP_CM), 2),
                    "what": f"lower the {name}'s grasp height by {GRASP_STEP_CM} cm",
                    "why": f"{pc['misses']} of {pc['grasps']} grasps missed ({pc['miss_rate'] * 100:.0f}%) — "
                           f"the jaws are closing above the piece",
                })
            else:
                out.append({
                    "id": f"grasp_teach_{p}", "path": None, "current": None, "value": None,
                    "what": f"teach the {name}'s grasp (teach_fix grasp_{p})",
                    "why": f"{pc['misses']} of {pc['grasps']} grasps missed and no grasp has ever been taught "
                           f"for this piece — it is using the default height",
                })

        # a miss is detected by the settled opening; if every real hold sits well above the threshold,
        # raising it catches near-misses that currently pass as a grasp
        if pc["min_held"] is not None and pc["stall_min"] is not None and len(pc["held"]) >= MIN_ATTEMPTS:
            want = round(pc["min_held"] - STALL_MARGIN, 1)
            if want - float(pc["stall_min"]) >= 1.0:
                out.append({
                    "id": f"stall_{p}", "path": f"pieces.{p}.stall_min",
                    "current": pc["stall_min"], "value": want,
                    "what": f"raise the {name}'s miss threshold to {want}",
                    "why": f"{len(pc['held'])} successful grasps all settled at {pc['min_held']} or above; "
                           f"the current threshold {pc['stall_min']} would let a near-miss through",
                })

    # squares the arm keeps getting wrong are a geometry problem, not a tuning one
    for s, sc in sorted(st["squares"].items()):
        bad = sc["verify_fail"] + sc["misses"]
        if bad >= 2 and sc["attempts"] >= MIN_ATTEMPTS and bad / max(sc["attempts"], 1) >= MISS_RATE_WARN:
            out.append({
                "id": f"square_{s}", "path": None, "current": None, "value": None,
                "what": f"re-probe or re-teach around {s}",
                "why": f"{bad} of {sc['attempts']} attempts on {s} went wrong"
                       + (f" (mean lean {sc['mean_lean']} deg)" if sc["mean_lean"] is not None else "")
                       + " — check the board has not moved and the corner marks still fit",
            })

    return out


def apply(suggestion_id, st=None, d=None):
    """Apply one suggestion by id. Returns what changed, or raises ValueError saying why it cannot."""
    d = d or ws_mod.load()
    match = next((s for s in suggestions(st, d) if s["id"] == suggestion_id), None)
    if match is None:
        raise ValueError(f"no current suggestion {suggestion_id!r} — the evidence may have changed since it was shown")
    if not match["path"]:
        raise ValueError(f"{suggestion_id!r} is advice, not an automatic change: {match['what']}")
    before = get_path(d, match["path"])
    set_path(d, match["path"], match["value"])
    ws_mod.save(d)
    append({"kind": "reading", "detail": f"applied {suggestion_id}: {match['path']} {before} -> {match['value']}"})
    log(f"chess: applied {suggestion_id} — {match['path']} {before} -> {match['value']} ({match['why']})")
    return {"id": suggestion_id, "path": match["path"], "from": before, "to": match["value"], "what": match["what"]}


# ---------------------------------------------------------------- what the routine calls
def record_grasp(game, ply, piece, square, z_cm, lean_deg, settled, expect_min, ok, detail=""):
    return append({"kind": "grasp", "game": game, "ply": ply, "piece": piece, "square": square,
                   "z_cm": z_cm, "lean_deg": lean_deg, "settled": settled, "expect_min": expect_min,
                   "ok": bool(ok), "detail": detail})


def record_move(game, ply, uci, san, executed_by, seconds=None, steps=None, detail=""):
    return append({"kind": "move", "game": game, "ply": ply, "uci": uci, "san": san,
                   "executed_by": executed_by, "seconds": seconds, "steps": steps, "ok": True, "detail": detail})


def record_verify(game, ply, ok, squares=None, detail=""):
    return append({"kind": "verify", "game": game, "ply": ply, "ok": bool(ok),
                   "square": (squares or [None])[0], "squares": squares, "detail": detail})
