"""Jenga tower: which brick is where, and which one to play next. Pure bookkeeping, no arm, no camera.

Rows are counted from the table (row 1 is the bottom one); every row has three bricks, column 0 on the
left, 1 in the middle, 2 on the right, seen from the arm. In an *end row* the bricks point at the arm, so
the stick can push one out; in the rows between, the arm sees the long side of a single brick.
The file only lists bricks that are not simply in place: ``{"rows": 15, "bricks": {"11-1": {"state": "out"}}}``.
"""

import json

STATES = ("in", "pushed", "out")


def new(rows=15):
    return {"rows": int(rows), "bricks": {}}


def load(path):
    try:
        t = json.loads(path.read_text())
        return {"rows": int(t["rows"]), "bricks": dict(t.get("bricks") or {})}
    except (OSError, ValueError, KeyError, TypeError):
        return new()


def save(path, tower):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(tower, indent=1))
    return tower


def brick(tower, row, col):
    return tower["bricks"].get(f"{int(row)}-{int(col)}") or {"state": "in"}


def mark(tower, row, col, state, depth_cm=0.0):
    """Record what happened to one brick; "in" forgets it."""
    row, col = int(row), int(col)
    if state not in STATES: raise ValueError(f"state must be one of {', '.join(STATES)}")
    if not (1 <= row <= tower["rows"] and 0 <= col <= 2): raise ValueError(f"no brick at row {row}, column {col}")
    key = f"{row}-{col}"
    if state == "in": tower["bricks"].pop(key, None)
    else: tower["bricks"][key] = {"state": state, **({"depth_cm": round(float(depth_cm), 1)} if state == "pushed" else {})}
    return tower


def end_row(c, row):
    return (int(row) % 2 == 1) == (c.get("end_rows", "odd") == "odd")


def playable(tower, c, row):
    """Why this row's middle brick cannot be played, or "" when it can."""
    row = int(row)
    if not 1 <= row <= tower["rows"]: return f"the tower has {tower['rows']} rows"
    if not end_row(c, row): return f"row {row} shows the long side of a brick to the arm: only the rows between can be pushed"
    if row > tower["rows"] - 2: return "the top two rows are left alone: nothing holds them down"
    if row < c.get("min_row", 1): return f"row {row} is below the lowest row the stick has been tried on ({c['min_row']})"
    if any(brick(tower, row, k)["state"] != "in" for k in range(3)): return f"row {row} has already lost or moved a brick"
    return ""


def candidates(tower, c):
    """Rows whose middle brick can be played, highest first: the higher the row, the less weight rests on the brick."""
    return [r for r in range(tower["rows"], 0, -1) if not playable(tower, c, r)]


def row_reading(c, row):
    """The tip height the arm *reports* when the stick is level with the middle of this row (the model reads low)."""
    return (int(row) - 0.5) * c["row_cm"] + c["tip_z_offset_cm"]
