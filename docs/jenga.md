# Jenga lab: push one brick out with a stick

`/jenga` — built on the lab template (`docs/lab.md`). The arm holds a stick ("pencil") as a mounted tool
and pushes the **middle brick of one row** out of the back of the tower. This page is what the sessions of
2026-11-12 taught, and how to pick up from there.

## Start here next time

1. Tower: 15 rows, front face about **24.5 cm** from the arm's base axis, brick ends of the odd rows facing
   the arm. Stick mounted (Tool tab → `stick`, 13 cm long, 10 cm out, grip 8.6).
2. `make run`, open `http://localhost:8765/jenga`.
3. If the tower was rebuilt, press **Tower rebuilt**. Otherwise correct any brick in the 3-D view (click it,
   *mark as* in / pushed / out).
4. Press **▶ Play a turn** (or pick a middle brick first). It asks once, then does everything: approach,
   line up by camera, push, pull the stick out. The take is filmed (`var/videos/lab/`) and logged
   (`var/lab/jenga_runs/`).

From a script or an agent: `POST /jenga/cmd {"action": "turn", "dry": true}` shows the plan; the real one
needs `"confirm": "yes"`, which an agent only sends after the operator said go.

## How it is judged

- **Exactly one brick moves.** Shifting a block of rows, or a neighbour, is a lost turn even if the tower stands.
- The brick should come **out the back**: 3.3 cm of tip travel past first contact left it in the tower,
  4.5 cm dropped it out. `jenga.turn_depth_cm` is 4.5.
- Pull the stick out **carefully**: it must not drag the rows around the slot.
- End to end, few stops, recorded.

## What a turn does, and why

| Step | What | Why (what went wrong without it) |
|---|---|---|
| stage | From rest: wrist up 24° first, then a level pose in open space, then pan to the tower | At rest the table guard believes the stick tip is at the table; a move that dips first causes `FLOOR FREEZE`, after which every move is refused |
| place | Straight line to 1.3 cm before the face, at the row's height, stick **level** | A stick angled 17° down hit the seam below its row and brought the top half down |
| align | Height by the **wrist camera**, repeated until within 0.15 cm of the row's middle | The model's tip height is ~3.3 cm lower than the real tip and drifts ±0.5 cm between takes; by model alone the stick hit the row below and shoved six rows back |
| close in | 0.8 cm forward, camera check again | Reaching out lowers the tip a little |
| push | One slow stroke (2 deg/s), no stop | Moves chained without waiting came up short and off line |
| retract | Tip down 0.4 cm, then straight back out | The tip rises 0.3–0.7 cm when the direction reverses and would rub the row above |

If the camera cannot read the rows, the turn stops **without pushing**.

## Numbers (config `jenga.*`, all measured on 2026-11-12)

| Key | Value | Meaning |
|---|---|---|
| `pan_deg` | −6.2 | centres the tip on a middle brick (−5.3 was 0.5 cm right and rubbed the right neighbour) |
| `face_r_cm` | 24.5 | tool-tip reach at first contact |
| `tip_z_offset_cm` | −3.35 | reported tip height minus real height: row 11 reads ≈ 12.4, row 13 ≈ 15.4 |
| `sag_cm` | 0.9 | ask for this much more height than the reading you want |
| `slack_cm` | 0.45 | lost when a small vertical move reverses direction; small reversals may do nothing, then arrive all at once |
| `min_row` | 11 | lower rows need the arm too folded at this distance; move the tower to 30–36 cm to play them |
| `turn_depth_cm` | 4.5 | tip travel past the face |
| `depth_max_cm` | 3.5 | limit of the manual `push` action (half a brick) |

Reported heights come from the *present* pose (`/jenga/state` → `side.tip`), never from the goal.

## Pieces

- `labs/jenga.py` — the lab, `nudge`, `place`, `push` (manual tools; they refuse while the arm is still moving).
- `labs/jenga_turn.py` — `turn`, `see` (camera check, no move), `tower_reset`, `tower_mark`.
- `labs/jenga_vision.py` — tip against the rows from one wrist frame. Checked against nine frames of the
  session to ~0.1 cm; three of them are test data (`tests/data/jenga/`). Only valid before contact.
- `labs/jenga_tower.py` — which brick is in / pushed / out, which rows are playable (`var/lab/jenga_tower.json`).
- `web/jenga/tower3d.js` — the 3-D tower (drag to turn, click to pick), with the stick where the arm reports it.

## Not done yet

- **`turn` has not run on the arm.** Every step in it was done by hand on 2026-11-12, and the planning and
  the camera check are tested offline, but the routine itself is new. Run the first one with a hand on STOP.
- No check *during* the push that only one brick is moving; watch the first take.
- The stick is modelled on the gripper axis. A tilt/offset field in `tool.py` would remove
  `tip_z_offset_cm` and the freeze at rest (see `docs/lab.md` §6).
- The tip's place in the wrist image (`jenga.vision.tip_px`) moves if the stick is re-mounted: check with
  **Check the height** before the first turn.
