# Chess on the SO-101 bridge — implementation plan (v2)

Status: proposal, 2026-09-15, revised after review. Builds on bridge 3.7.0. Nothing implemented yet.

Inputs fixed by the owner in this revision: the board is **29 × 29 cm** (pitch 3.625 cm), the arm uses the
**bare gripper** exactly as in the Lego pick-and-place (no brush, no tool fit), the parts chess borrows from
painting move to a **shared package** first, and teaching is a **guided session**: the daemon says "put the
fingertips on point A", the operator free-drives the arm there and presses Done, the daemon records the pose and
moves on.

## 0. Summary

Chess is a discrete pick-and-place on a taught plane. The bridge already has the pieces: a taught-workspace file,
plane kinematics fitted from contact poses, height probes, guarded `path` replay, the routine registry, the
file/HTTP command interface, the simulated-arm test rig and the dry-run pattern. They are currently inside
`paint/`; step R below moves the generic parts to `src/so101_bridge/shared/` so painting and chess both import
them and neither owns them.

New work: guided teaching (generic), a `GripperModel` on the shared plane model, board vision (occupancy diff
against the known position), game logic (python-chess), a pluggable player so an engine, a human **or the LLM**
picks the robot's moves, and three routines `chess_setup`, `chess_play`, `chess_puzzle`.

Token rule: **the LLM never sees images and never applies rules.** It reads a ~300-byte game JSON and, when it is
the player, writes one move. Everything else is deterministic Python at zero token cost.

## 1. Reach on a 29 cm board — the hard constraint

Fitted chain (2026-09-15): L1 0.1159, L2 0.1350, L3 0.110 m, shoulder axis z0 ≈ 9.6 cm above the table (free fit,
rms 1.4 mm, so absolute heights carry 1–2 cm of error until z0 is measured). Minimum gripper lean from vertical
needed to put the fingertips on each rank centre, board near edge at r = 7 cm from the shoulder axis, robot at the
middle of the near edge:

| fingertip height | rank 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2.5 cm (grasp pawn) | 4° | 2° | 6° | 10° | 16° | 22° | 42° | — |
| 4.0 cm (grasp king) | 20° | 10° | 10° | 14° | 18° | 26° | 40° | — |
| 6.0 cm (hover) | — | 20° | 16° | 18° | 22° | 28° | 38° | — |
| 9.0 cm (carry) | — | 34° | 28° | 28° | 30° | 34° | 42° | — |

"—" = no joint solution inside the soft limits. Near edge at 5 cm shifts the problem: rank 1 lost, rank 8 reachable
only at 58° lean. Near edge at 9 cm loses ranks 7–8.

Consequences:

- **Rank 8 (the far rank) is out of reach; rank 1 is reachable only at grasp height, not at carry height.** The
  plan does not fight this. `physical.plan` marks any action whose square is unreachable at the required height,
  and the routine raises `needs: {"kind": "unreachable", "move": ..., "actions": [...]}`: the robot announces its
  move and the human executes it, then confirms. The game continues, the engine or LLM still chooses every move.
- Board placement: near edge 6–7 cm from the shoulder axis, robot centred on a file boundary (d/e). Teaching
  records the real placement; the compile step prints the 64-square reachability report at grasp, hover and carry
  height before any game starts.
- Leans of 20–40° are normal on this board. Pieces must tolerate a tilted grasp: a standard set for a 29 cm board
  has a king ~6–7 cm tall and bases ~2.5 cm; the 3.6 cm square leaves ~1 cm between neighbours. Carry height must
  clear the king: ~9 cm, which needs 28–42° lean on ranks 2–7. Alternative if grasps slip: grip pieces by the head
  with a lower carry height.
- Full autonomy over all 64 squares needs either a smaller board (≤ 22 cm at ≤ 30° lean, measured in v1 of this
  plan) or the arm base raised to a mount that lets the wrist sit at shoulder height at the far rank (P0 checks).
- Camera: a 29 cm board is ~580 px wide on the 1080p overhead frame → ~72 px per square. Comfortable for occupancy
  and colour; the 960×540 processed frame (36 px/square) is enough for the live overlay only.

**The owner expects the arm to reach all 29 cm, and the model is likely pessimistic**: z0 is a free fit and the
model is already known to be wrong by ~9 cm in the folded region. So the table above is a warning, not a verdict.
Proof by demonstration, with today's tooling and zero code, comes first:

1. Torque off (RELEASE). Fingertips on the centre of a8, `wp_save name=a8`; repeat for h8, d8, a1, h1, e1, e4.
2. ENGAGE, rest, then `wp_goto` each waypoint. Reached and held without `STALL` / `LOAD GUARD` = proven at grasp
   height. Note shoulder_lift load and the lean used.
3. Same for carry height: free-drive to ~9 cm above a8/h8 (over a king), save, replay.
4. `config/waypoints.json` then seeds the chess corner contacts and the piece table.

If every square holds, the `unreachable` list is empty and human-assist stays as a rare fallback. If not, P0 has
the exact squares and heights that fail.

## 2. Step R — extract the shared package (before chess, tests stay green)

`src/so101_bridge/shared/` holds what is generic. `paint/` keeps only what is about brushes and pictures.

| Moves to | From | Content |
| --- | --- | --- |
| `shared/plane.py` | `paint/kinematics.py` | `PlaneModel(floor, contacts, size_cm, limits, tool_len=None)`: pitch field, homography, `uv_to_pose`, `pose_to_uv`, `reachability`, lean window/candidates. `tool_len=None` fits it from the contacts (brush); a number fixes it (gripper: L3). `paint.kinematics.ToolModel` becomes a thin wrapper that builds the contact list from `painting.json`. |
| `shared/motion.py` | `paint/program.py`, `paint/routine.py` | `densify_joint`, `up`, `bridge_from_present`, `z_correction(probes, default)`, `plane_travel(model, from_uv, to_uv, z, step_cm)` (straight in plane coordinates at a height). |
| `shared/store.py` | `paint/workspace.py` | `load_json(path, defaults)`, `save_json(path, d)` (adds `updated`), `pose_round(p)`, `catalog(dir)`. Domain files (`painting.json`, `chess.json`) keep their own schema modules. |
| `shared/teach.py` | new | Guided teaching session, §3. |
| `controller.py` | `go_rest` | `rest_path()` synchronous, gripper untouched; `go_rest` calls it in a thread as today. Also `self.frame = {}` (latest raw frames) and `self.teach = None`. |

Acceptance: `make test` green with no change to `tests/test_paint_*`; `paintings/*.json` recompile byte-identical
(modulo `created`) from their plans; one dry-run of `scripts/paint.sh` on the arm.

## 3. Guided teaching (`shared/teach.py`) — the owner's request

A teaching session is a list of steps. Each step names a point, tells the operator where to put the arm, and says
what is recorded when they press Done.

```json
{"name": "chess_board", "steps": [
  {"id": "a1", "kind": "contact", "prompt": "Gripper closed. Fingertips on the CENTRE of square a1 (robot's left-near corner).", "uv": [0, 25.4]},
  {"id": "h1", "kind": "contact", "prompt": "Fingertips on the centre of h1.", "uv": [25.4, 25.4]},
  {"id": "h8", "kind": "contact", "prompt": "Fingertips on the centre of h8.", "uv": [25.4, 0]},
  {"id": "a8", "kind": "contact", "prompt": "Fingertips on the centre of a8.", "uv": [0, 0]},
  {"id": "e4", "kind": "contact", "prompt": "Fingertips on the centre of e4 (checks the model).", "uv": [14.5, 14.5]},
  {"id": "grasp_k", "kind": "grasp", "prompt": "Put a KING on e4. Open the gripper, lower it around the king where you would grip it, close until it holds. Done.", "square": "e4", "piece": "k"},
  {"id": "grasp_p", "kind": "grasp", "prompt": "Same with a PAWN on e4.", "square": "e4", "piece": "p"},
  {"id": "handover", "kind": "contact", "prompt": "Fingertips on the handover spot (off the board, robot's right).", "slot": "handover"},
  {"id": "feed", "kind": "contact", "prompt": "Fingertips on the feed spot where you will hand pieces over.", "slot": "feed"}
]}
```

Flow: `{"action": "teach_start", "session": "chess_board"}` (or the button) → the daemon RELEASES torque only if the
operator asks (`teach_free`), puts the step prompt in `/state.teach` and in the log → operator moves the arm →
`{"action": "teach_done"}` (dashboard **Done** button, or `scripts/chess.sh done`) → the daemon records what the
step asks for and advances. `teach_skip`, `teach_back`, `teach_abort` exist. Steps record:

- `contact`: present arm pose → `board.corners[id]` / `board.extra` / `slots[slot].pose`; the (u, v) comes from the
  step, so square centres are exact by construction.
- `grasp`: present pose + gripper value → `pieces[piece] = {"grasp_pose": pose, "close": gripper, ...}`; the model
  turns the pose into `grasp_z_cm` (height above the plane) and the lean used, so the same grasp is reproduced on
  every square.
- `probe` (used in the verify pass): the daemon drives the arm to a model pose at a commanded height and steps down
  until the operator says Done at touch → `probes[]` (sag correction, same as painting).

**Verify pass** (`teach_verify`): after the contacts are taught, the daemon fits the model, prints residuals per
point (mm) and the 64-square reachability report, then, under torque, moves to each taught point at hover height,
descends to 1 cm and asks "is the tip over the centre?" Done = yes, `teach_fix` = re-record this point. This is the
"make sure the robot reaches these points" check the owner asked for, and it doubles as the probe run.

The same mechanism can later replace the paint page's corner buttons; `paint_corners` is just another session file.
Sessions live in `config/teach/<name>.json` and the results go to the domain config (`config/chess.json`).

## 4. Architecture

```
src/so101_bridge/shared/        plane.py · motion.py · store.py · teach.py            (step R + §3)
src/so101_bridge/chess/
  workspace.py   config/chess.json schema: board corners/extra, pieces, slots, heights, probes, vision params;
                 square(name) -> (u, v); uv from the 29 cm board and 3.625 cm pitch
  kinematics.py  GripperModel = PlaneModel with tool_len fixed to L3 + jaw-centre offset; pose_for(square, z_cm);
                 reachability_report() over 64 squares at grasp/hover/carry
  vision.py      board homography (taught image corners or findChessboardCorners on the empty board), warp to
                 8x8 canonical, per-square features, occupancy(), colour(), stability + sanity gates,
                 threshold self-calibration from a known position
  game.py        python-chess wrapper: resolve(prev_occ, new_occ, colours, board) -> move | candidates; SAN/UCI/PGN
  players.py     Player protocol: EnginePlayer (optional Stockfish), LlmPlayer (waits for chess_move),
                 HumanPlayer (waits for the board), ScriptPlayer (puzzle line)
  physical.py    move -> actions (pick/place/handover/castle/promote); actions -> guarded paths; unreachable marking
  log.py         var/games/<id>.json append-only, PGN mirror, /state.chess summary
  routine.py     @routine chess_setup / chess_play / chess_puzzle
web/chess.html   board overlay (seen vs expected), FEN, moves, players, Teach panel with the Done button
scripts/chess.sh new | status | move <uci> | wait | done | setup | puzzle <file> | abort
```

Touch points in existing code:

| File | Change |
| --- | --- |
| `routines.py::_load_builtins` | `from .chess import routine` |
| `app.py` dispatch | `chess_*` actions (`chess_new`, `chess_move`, `chess_confirm`, `chess_goto`, `chess_compile`, `chess_check_view`) and `teach_*` actions next to the `paint_*` block |
| `app.py` vision block | `with ctrl.lock: ctrl.frame[n] = obs[n]` (raw frame for the routine thread); chess overlay for `/chess/<cam>.mjpg` when a game or teaching is active |
| `controller.py` | `frame`, `teach`, `chess_request`, `rest_path()` |
| `dashboard.py` | `/chess`, `/chess/state`, `/chess/cmd`, `/chess/<cam>.mjpg`, `/chess/game/<id>`; `/state.chess`, `/state.teach` |
| `paths.py` | `CHESS_FILE`, `TEACH_DIR = CONFIG_DIR / "teach"`, `GAMES_DIR = VAR_DIR / "games"`, `PUZZLES_DIR = ROOT / "puzzles"` |
| `settings.py` | `CHESS_CAM`, `CHESS_ENGINE`, `CHESS_ENGINE_LIMIT`, `CHESS_POLL_S 0.5`, `CHESS_STABLE_N 4`, `CHESS_STABLE_DIFF 6.0`, `CHESS_AUTO_TRIGGER False`, `CHESS_CARRY_CM 9.0`, `CHESS_MAX_LEAN_DEG 45` |
| `pyproject.toml` | `"chess>=1.11"` |
| `tests/` | `test_shared_plane.py` (moved from paint kinematics), `test_teach.py`, `test_chess_*.py` |
| `docs/operations.md` | §12 chess + teaching: commands, state, log patterns, failure table |

The guard chain in the control loop does not change. Routines only request goals.

## 5. Data model

`config/chess.json`:

```json
{"board": {"size_cm": 29.0, "pitch_cm": 3.625, "robot_side": "black",
           "corners": {"a1": pose, "h1": pose, "h8": pose, "a8": pose}, "extra": [{"square": "e4", "pose": pose}],
           "image_corners": {"a1": [x, y], "h1": [x, y], "h8": [x, y], "a8": [x, y]}, "cam": "top"},
 "pieces": {"p": {"grasp_z_cm": 1.4, "lean_deg": 12, "open": 60, "close": 8, "stall_min": 12},
            "n": {}, "b": {}, "r": {}, "q": {}, "k": {"grasp_z_cm": 3.0, "lean_deg": 15, "open": 60, "close": 8, "stall_min": 12}},
 "slots": {"feed": {"pose": pose, "uv": [-4, 14.5]}, "handover": {"pose": pose, "uv": [29.4, 14.5]},
           "reserve_queen": {"white": {"uv": []}, "black": {"uv": []}}},
 "heights": {"hover_cm": 6.0, "carry_cm": 9.0, "place_drop_cm": 0.3},
 "probes": [{"u": 14.5, "v": 14.5, "z_touch_cm": 1.0}],
 "vision": {"occ_threshold": null, "warp_px": 512},
 "unreachable": ["a8", "b8", "c8", "d8", "e8", "f8", "g8", "h8"]}
```

`u` runs a→h, `v` runs rank 8→1; square centre (u, v) = (file × pitch, (7 − rank) × pitch); the plane is 7 × 7
pitches = 25.4 cm and its contacts are the fingertips on square **centres**. Slots share the frame.

`var/games/<id>.json` (what the LLM reads) and `puzzles/<name>.json` are unchanged from v1:

```json
{"id": "20260915_1830", "mode": "play", "robot": "black", "players": {"white": "human", "black": "llm"},
 "start_fen": "...", "fen": "<current>", "turn": "black", "waiting_for": "llm",
 "moves": [{"ply": 1, "by": "human", "uci": "e2e4", "san": "e4", "fen_after": "...", "source": "occupancy_diff", "verify": "ok"},
           {"ply": 2, "by": "llm", "uci": "e7e5", "san": "e5", "fen_after": "...",
            "actions": [{"op": "pick", "sq": "e7", "piece": "p"}, {"op": "place", "sq": "e5"}], "executed_by": "robot", "verify": "ok"}],
 "result": null, "pgn": "1. e4 e5"}
```

```json
{"name": "mate_in_2_01", "fen": "...", "robot": "white", "mate_in": 2, "solution": ["d1h5", "g6h5", "f1c4"]}
```

`/state.chess` = `{id, mode, fen, turn, waiting_for, last_move, phase, needs}`. `needs` is the only channel for help:
`ambiguous` (candidates + cropped board image path), `promotion`, `verify_failed`, `unreachable` (human plays the
robot's move), `illegal_move`, `grasp_failed`, `off_book`.

## 6. Players — where the LLM fits

`Player.choose(board, game, wait) -> uci | None`. `HumanPlayer` waits for the board to change; `EnginePlayer`
uses `chess.engine` when `CHESS_ENGINE` is set (`brew install stockfish`; not installed today); `ScriptPlayer`
plays a puzzle line and defers when off-book; `LlmPlayer` sets `waiting_for: "llm"` and blocks until a
`chess_move` command arrives, validated by python-chess. Illegal → logged, `needs.illegal_move`, keeps waiting.

LLM loop, three cheap steps:

```
scripts/chess.sh wait          # blocks in the shell until state.chess.waiting_for == "llm" or the game ends
cat var/games/<id>.json        # fen + move list, ~300 bytes
scripts/chess.sh move e7e5     # the arm plays it (or asks the human if the squares are unreachable)
```

## 7. Vision

Raw overhead frame, captured **only at rest** (the arm covers the board while moving).

1. Homography from 4 taught image corners (click on `/chess`) or `cv2.findChessboardCorners(7×7)` on the empty
   board; warp to 512×512 → 64 px squares.
2. Empty-board reference at setup; per-square features on the central 60 %: mean abs diff to the reference, edge
   density, mean luminance.
3. Occupancy = 2-class split, threshold refit whenever the true position is known (start, after every verified
   robot move). Colour = luminance of the piece pixels against the two clusters learned from the start position.
4. Stability gate (`CHESS_STABLE_N` consecutive samples with board diff < `CHESS_STABLE_DIFF`) then sanity gate
   (≤ 4 squares changed, count consistent with some legal move). Hands fail both.
5. LLM vision only on `needs` events, cropped labelled board, ~350 tokens, expected < 1 per game.

Fixtures in `tests/fixtures/chess/` (empty, start, after e4, capture, castling, hand in frame); all vision tests run
on them.

## 8. Turn state machine (`chess_play`)

```
preflight   floor fitted · chess.json complete · board found in CHESS_CAM · 64 squares read as expected at rest
            · reachability report known · no ESTOP · no routine running
human_turn  poll → stability → occupancy → diff vs expected → resolve with legal_moves
              1 candidate → push → robot_turn; >1 → colour check → needs.ambiguous; 0 → needs.illegal_or_knocked
            CHESS_AUTO_TRIGGER=false: also wait for chess_confirm (button / file) — v1 default
robot_turn  player.choose → validate → physical.plan
              all squares reachable → execute → rest_path → verify at rest → refit thresholds → human_turn
              any unreachable     → needs.unreachable (announce the move) → wait for board == expected → human_turn
            verify fail → needs.verify_failed; hold; wait for fix + chess_confirm
finish      result, PGN, rest
```

## 9. Physical execution

Actions per move type as in v1 (normal, capture → handover first, castling two picks, en passant, promotion from
the reserve queen). Per pick/place: hover at `carry_cm` → descend to the piece's `grasp_z_cm` + probe correction
with the taught lean → close, settled gripper < `stall_min` = missed → lift to `carry_cm` → `plane_travel` in
2 cm steps → descend to `grasp_z_cm + place_drop_cm` → open → lift. Ends with `ctrl.rest_path()`. Dry run keeps
every z at `carry_cm` and never closes (`DRY=1 scripts/chess.sh …`). Speeds 6°/s travel, 4°/s descend.

## 10. Phases and acceptance tests

**R — shared extraction.** §2. Green paint tests, byte-identical recompiles, one paint dry run.

**P0 — feasibility on the arm (half a day).** With `teach.py` in place, run the `chess_board` session on the real
29 cm board and the real pieces, then `teach_verify`. Record: reachable ranks under torque at grasp and carry
height, leans used, gripper opening in mm at 60/100, jaw-centre offset above the fingertips, whether the overhead
camera sees the whole board at rest, which camera index is overhead. Decide: accept rank 8 as human-assisted, or
raise the base / change the board. Measure z0 and write `config/floor_config.json`.

**P1 — board model.** `chess/workspace.py`, `chess/kinematics.py`, `chess_compile` report. Test: synthetic corners
→ 64 centres within 1 mm; unreachable squares listed by name; jaw offset applied.

**P2 — vision on fixtures.** 64/64 occupancy and colour on every fixture; hand fixture rejected.

**P3 — game logic.** 3 PGNs replayed as occupancy matrices → every move resolved, castling/en passant/promotion
covered; `LlmPlayer` accepts a legal `chess_move`, rejects an illegal one.

**P4 — mode 1 setup.** Human feeds pieces on the feed slot; robot places them into the start position, skipping
unreachable squares with `needs.unreachable`. Dry run first. Test: SimArm places the reachable set in order.

**P5 — mode 2 play.** Confirm button first, then auto-trigger. Test: SimArm + fixture vision play a scripted
10-move game headless, including one capture and one unreachable move handled by the human path.

**P6 — mode 3 puzzle.** Setup to the FEN, `ScriptPlayer` plays the line, checkmate asserted headless.

Each phase ends with `make test` green and a paragraph in `docs/learnings.md`.

## 11. Token harness rules

- Deterministic core, LLM at the edges: choose a move, answer a `needs`, comment, drive the CLI. Never rules,
  never frames unprompted, never routine flow.
- The LLM reads `var/games/<id>.json` and `/state.chess`, not `/state`, not images unless `needs.image` is set.
- Waiting is a blocking shell command, not a polling agent.
- Fixtures, SimArm and `DRY=1` before the arm, always.
- Every command is a file; a full game is playable from a terminal with no agent.
- `docs/operations.md §12` written for a small model: commands, state, log patterns, failure → remedy.

## 12. Risks and open decisions

- **Rank 8.** Out of reach on the fitted model. Human-assist keeps the 29 cm board usable; full autonomy needs a
  smaller board or a raised base. P0 decides.
- **Rank 1 at carry height.** Pieces from rank 1 lift at low height and travel over ranks 2–3 before climbing;
  `plane_travel` must ramp z, or the piece is dragged. Handle in `physical.py`.
- **Tilted grasps** on tall pieces may topple neighbours; head grips and a lower carry are the fallback.
- **z0 unmeasured.** One measurement removes most of the probe work. Do it in P0.
- **Camera index swap.** `CHESS_CAM` + a check that the board is found before any game.
- **Human safety.** Hands in the workspace; auto-trigger only after the gates have proven themselves with the
  confirm button. `touch ESTOP` stays one keystroke away.

Reach tables come from the fitted `FloorModel` with a ±lean sweep in 2° steps inside `limits.json`; moves to
`scripts/reach.py` in step R so the report is repeatable after every re-teach.
