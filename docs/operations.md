# SO-101 follower control bridge — operator & agent manual

Version: 3.3 (2026-09-12). Owner: Abderrehmen Gharsallah. Package: `src/so101_bridge/`.
Companion files: `learnings.md` (why things are the way they are), `metadata.json` (all numbers, machine-readable).

This manual is written so that a small language model (or a person) can operate the arm safely by following
the steps literally. Sections 1–4 are for everyone; sections 5–9 are the exact interfaces.

---

## 1. What this is

A Python daemon (`so101-bridge`) that owns the SO-101 follower arm and its two cameras. Everything else
(a human at the dashboard, an AI agent writing files, the built-in autonomous routine) sends *goals* to it.
The daemon enforces all safety rules itself, so a bad command becomes a slow, limited, or refused move —
never a jerk and never a push into the table.

Hardware: SO-101 follower arm (6 Feetech STS3215 motors) on `/dev/cu.usbmodem5A460836731`, calibration id
`my_follower`; two Innomaker U20CAM cameras: OpenCV index 0 = **top** (overhead view of the table),
index 1 = **wrist** (mounted on the gripper's fixed jaw, looks along the jaws). Indices can swap after
re-plugging — always verify on the dashboard which image is which.

## 2. Start / stop

Start (macOS Terminal, in the repository root):
```
uv run so101-bridge
```
Expected Terminal lines within ~10 s: `rest pose: {...}`, `EXTENDED shoulder_lift ...`, `EXTENDED elbow_flex ...`,
`floor model fitted on N points ... rms X mm`, `dashboard: http://localhost:8765`, `ready. present={...}`.
If it prints `ESTOP -> frozen`, the emergency stop from a previous session is still set: press **RESUME**.

Dashboard: open http://localhost:8765 in a browser on the Mac.

Stop the arm (any of these, in order of speed):
1. Dashboard **STOP** button (creates the `ESTOP` file; arm freezes where it is; all commands ignored until RESUME).
2. Double-click `scripts/STOP.command` in Finder (same effect).
3. `touch ESTOP` in the repository root (same effect; `make stop` does it too).
4. Ctrl-C in the bridge Terminal: bridge exits, arm keeps holding its pose with torque on.

Torque off (arm goes limp — only when it rests on the table): dashboard **RELEASE**/**FREE-DRIVE**,
`scripts/RELEASE.command`, or `{"action":"release"}`. Torque back on: **ENGAGE** or `{"action":"engage"}`.

Restarting the bridge never drops torque: the arm holds its pose through a restart.

## 3. Safety rules the daemon enforces (you do not have to implement them, but do not fight them)

| Rule | Value | Effect |
|---|---|---|
| Soft joint limits | calibration sweep ± 3° margin, widened by `config/limits.json` | goals clamped |
| Step cap | 12° per joint per command | larger goals shortened, logged `STEP CAP` |
| Speed cap | requested speed clamped to 0.5–30 °/s; default 5 °/s | interpolation rate |
| Motor-side caps | Goal_Velocity 300 steps/s, Acceleration 15 | gentle ramps even if a big error appears |
| Stall guard | tracking lag > 4° for 0.7 s while moving | freeze (`STALL on <joint>`) |
| Load guard | Present_Load > per-joint limit (300–450 of 1000) while moving | freeze (`LOAD GUARD`) |
| Floor guard | predicted fingertip height < 8 mm for a goal | step shortened (`FLOOR GUARD`); < 0 while moving → freeze (`FLOOR FREEZE`) |
| ESTOP file | exists | everything ignored, arm frozen |
| Gripper | torque 50 %, protection current 50 % | can't crush |

A frozen arm ("stalled" mode) stays where it is under torque. Send a new goal *away* from the obstacle,
or **HOLD**, to continue.

## 3b. Reading the dashboard

| Panel | What it tells you |
| --- | --- |
| Status chips (top right) | `mode` · `torque` · `auto` (routine status) · `tip z` (predicted fingertip height) · `loop` (measured control-loop rate; ~30 Hz is healthy, a sustained drop means the bus or the cameras are struggling) · `floor` (contacts and fit rms) · `state` (clock and sequence number of the last state — if it stops advancing the daemon is stuck) |
| Cameras | the live streams with the detector's box drawn in; the strip under each image repeats the detection as numbers (centre x/y, long side in px, area). "no brick detected" means the routine would refuse to start |
| Side view | the arm drawn to scale from the **fitted floor model** — the same chain the floor guard reasons about. The dashed lines are the guard margin (0.8 cm) and the grasp height (1.0 cm); the white dot is the fingertips and turns red at or below the margin |
| Joints | per joint: present value, goal (`→`), a track showing the soft-limit span with present (coloured), goal (blue) and interpolated command (grey) markers, the motor load against its guard limit, jog buttons and a slider |
| Tip height | the last 60 s of predicted fingertip height with the guard and grasp lines — use it while jogging near the table |
| Tracking error | per joint `|goal − present|`; a line that stays high is the arm not keeping up (a stall freeze usually follows) |
| Log | the tail of `var/bridge.log`, colour-coded (red: guards, failures, refusals; amber: clamps and warnings; blue: routine and teaching). Hovering pauses the auto-scroll |

The page polls `/state` 3×/s, the waypoints every 2 s and the log every 1.5 s. Losing the daemon shows as a red
`state` chip and an outlined page; nothing is cached, so what you see is at most ~300 ms old.

## 4. Standard procedures

### 4.1 Autonomous pick-and-place (brick → tin)
Preconditions: bridge running; dashboard shows an orange "brick" box on the yellow brick in the **top** image;
floor model fitted (Terminal line or dashboard "Floor model: N contact points, fit rms …"); tin at its usual
place (the drop pose is replayed, not vision-guided); no ESTOP banner; a human within reach of STOP.

1. Press **AUTO PICK** (or write `{"action":"auto"}` to `cmd/`).
2. Watch `bridge.log`. Normal sequence:
   `AUTO: start` → (`not at rest -> moving to READY` or unfold) → `AUTO servo: brick x=… -> pan …` (1–3 lines)
   → `AUTO servo i: brick x= y= size= tip z=…cm -> {…}` (6–12 lines; x→≈540, y≈250±60, size rising, z falling)
   → `AUTO: at grasp height (tip z ≈1.3 cm), brick size ≥ 280px` → `AUTO: holding (gripper stalled at ≈20)`
   → `AUTO: released` → `AUTO: done`. Total ≈ 2 minutes.
3. Any `AUTO FAILED: <reason>` line means the routine stopped and the arm is holding. See §9.
4. Between runs a human must put the brick back on the table (anywhere in the camera-visible area in front of the arm).

### 4.2 Return to rest
Press **GO TO REST** or write `{"action":"rest"}`. Path: up to a mid pose (gripper closed) → fold down → the pose in
`config/rest.json`. Do not RELEASE torque unless the arm is resting on the table.

### 4.3 Manual jogging
Dashboard *Joint control*: −5/−1/+1/+5 buttons (relative), sliders (absolute on release), speed 2/4/8 °/s.
Or files: `{"goal": {"shoulder_pan": 3}, "relative": true, "speed": 3}`. Near the table use ≤ 3 °/s and steps ≤ 3°,
and watch `tip_z_cm` in the state: stop when it is ≤ 1.0.

### 4.4 Teaching
- **REC**: logs joint positions at 10 Hz (plus every goal) to `recordings/rec_<timestamp>.jsonl`; press again to stop.
- **Save current pose** (name): stores a waypoint in `config/waypoints.json`; **Go** returns to it in capped steps.
- **Save as REST**: overwrites `config/rest.json` with the current pose.
- **Mark contact** (tips height in cm): appends the current pose to `config/floor_points.json` and refits the floor
  model. Use `0` when the fingertips touch the table, `1.9` when they rest on top of the Duplo brick.
  Take contacts spread over the workspace (near/far, gripper vertical and tilted). To make the model exact,
  measure the height of the shoulder-lift motor shaft above the table and put it in `floor_config.json`
  as `{"z0": <metres>}`, then restart.

## 5. Command interface (files and HTTP)

Write a JSON file into `var/cmd/` (write to `x.tmp`, then rename to `NNN.json`; the daemon reads on the
next 30 Hz tick and moves the file to `var/done/`). Equivalent HTTP: `GET http://localhost:8765/cmd?a=<action>&…`.

| JSON | HTTP | Meaning |
|---|---|---|
| `{"goal": {"<joint>": deg, …}, "relative": false, "speed": 4}` | `a=goal&j=<joint>&v=<deg>&rel=0&speed=4` | move (absolute) |
| `{"goal": {"<joint>": delta}, "relative": true, "speed": 3}` | `a=goal&j=<joint>&v=<delta>&rel=1&speed=3` | move (relative) |
| `{"action": "hold"}` | `a=hold` | stop and hold present pose |
| `{"action": "release"}` / `{"action": "engage"}` | `a=release` / `a=engage` | torque off / on |
| `{"action": "auto"}` | `a=auto` | start autonomous pick-and-place |
| — | `a=abort` | abort routine, hold |
| `{"action": "rest"}` | `a=rest` | go to rest pose |
| — | `a=stop` / `a=resume` | create / remove ESTOP |
| — | `a=rec_start` / `a=rec_stop` | recording |
| — | `a=wp_save&name=X` / `a=wp_goto&name=X` / `a=wp_delete&name=X` | waypoints |
| — | `a=rest_save` | current pose → config/rest.json |
| — | `a=floor_mark&h=<cm>` | add floor contact |
| `{"action": "diag", "regs": ["Present_Load", …]}` | — | dump motor registers to the log |
| `{"action": "write", "reg": "P_Coefficient", "motor": "elbow_flex", "value": 48}` | — | guarded register write |

Joints: `shoulder_pan`, `shoulder_lift`, `elbow_flex`, `wrist_flex`, `wrist_roll`, `gripper` (gripper 0–100, others degrees).
Speed: number (all joints) or `{"joint": speed}`.

## 6. State interface

`var/state.json` (rewritten 5×/s, atomically) and `GET /state`:
```
{"seq", "time", "mode", "torque_on", "present": {joint: value}, "goal": {...}, "cmd": {...},
 "speed": {...}, "limits": {joint: [lo, hi]}, "last_cmd", "estop": bool, "tip_z_cm": float|null,
 "load": {joint: raw}, "load_limit": {joint: raw}, "loop_hz": float}
```
`/state` additionally has `"blob": {"top": {...}, "wrist": {cx, cy, w, h, long, area}}`, `"auto": <routine status>`,
`"rec": <file or null>`, `"floor": {points, fitted, rms_mm, tip_z_cm, margin_cm, grasp_cm, arm}`, where `arm` is the
arm as the floor model sees it: `[[r, z], …]` in metres for shoulder axis, elbow, wrist and fingertips (the last
`z` is `tip_z_cm / 100`), or `null` when the model is not fitted.

`GET /log?n=<lines>` returns the last `n` lines of `var/bridge.log` as a JSON array (`n` ≤ 400, default 120).

`mode` values: `hold` (holding, idle) · `moving` (interpolating to goal) · `reached` (at goal, holding) ·
`stalled` (a guard froze it) · `estop` · `released` (torque off).

Images: `var/top.jpg`, `var/wrist.jpg` (960×540 JPEG, ~every 1.5 s, with detection overlay) and MJPEG at `/top.mjpg`, `/wrist.mjpg`.

## 7. Log patterns (`var/bridge.log`)

| Pattern | Meaning |
|---|---|
| `ready. present=` | bridge up |
| `goal[src]: {...}` / `reached {...}` | move accepted / finished |
| `STEP CAP`, `CLAMP`, `FLOOR GUARD` | goal was shortened |
| `STALL on`, `LOAD GUARD`, `FLOOR FREEZE` | arm frozen; routine aborted |
| `ESTOP -> frozen` / `ESTOP cleared` / `ignored <action> (estop)` | emergency stop state |
| `AUTO: …`, `AUTO servo …`, `AUTO FAILED: …`, `AUTO: done` | routine progress |
| `bus hiccup` | Feetech packet loss on connect; retried automatically |
| `floor model fitted on N points … rms X mm` | model status (want X < 3) |

## 8. Geometry cheat-sheet (calibrated degrees, this arm)

- `shoulder_lift` **+** = upper arm forward = gripper **down** (and a bit back). Rest ≈ −45. Allowed −47…98.
- `elbow_flex` **−** = unfold = gripper **forward** (and a bit up). Rest ≈ 83 (folded). Allowed −80…84.5.
- `wrist_flex` **−** = tips/camera pitch forward. Camera looks straight down at wrist ≈ 37.5 when lift = 0;
  keep wrist ≈ 37.5 − 0.7·lift while descending to keep looking down.
- `shoulder_pan` **+** = gripper moves to the right in the wrist image (scene shifts left). ≈ 50–100 px per 3°.
- `gripper` 60 = open (wide enough for the brick); commanding 10 while holding the brick stalls at ≈ 20.
- Wrist image: fixed jaw is the left dark shape (tip ≈ x 380), moving jaw at right (≈ x 850 when open).
  A brick centred at x ≈ 520–550, y ≈ 250–300 with long side ≥ 350 px is graspable.
- Fingertip height (floor model) at READY (0, 70.5, 37.5) ≈ 3 cm by the current fit; grasp at ≈ 1.3 cm.

## 9. Failure → remedy

| Symptom | Cause | Do this |
|---|---|---|
| `ignored … (estop)` for everything | ESTOP file present | RESUME (dashboard) or delete `ESTOP` |
| `AUTO FAILED: no yellow brick visible` | brick not in view / lighting | move brick into the top camera view; check overlay box |
| `AUTO FAILED: brick lost …` | brick left the wrist view | GO TO REST, re-place the brick more centrally, retry |
| `AUTO FAILED: detector unreliable` | colour washed out | more light / brick less glossy; check `var/wrist.jpg` |
| `AUTO FAILED: missed the brick` | jaws closed on nothing | brick was not between jaws; retry (routine re-aligns) |
| `AUTO FAILED: shoulder at its forward limit …` | brick too far from the base | move brick ≥ 5 cm closer to the arm |
| `AUTO FAILED: floor model not taught yet` | < 3–4 contact points | §4.4 Mark contact |
| `STALL on <joint>` | joint blocked or too heavy | check for obstacle; move away; if elbow: P gain (write P_Coefficient 48) |
| `LOAD GUARD: {…}` | load spike | as above; thresholds in `LOAD_LIMIT` |
| `FLOOR FREEZE` | predicted below table | lift with `shoulder_lift` −5 (relative), then continue |
| `bus hiccup` repeated 5× | cable/power | check the arm's power supply and USB cable |
| joint moves but stops short by 2–10° | P gain too low | `{"action":"write","reg":"P_Coefficient","motor":"<j>","value":48}` |
| camera images swapped | index change after re-plug | edit `CAMS` in `src/so101_bridge/settings.py`, restart |

## 10. Files

| Path | What |
| --- | --- |
| `src/so101_bridge/` | the daemon (`app.py` control loop, `controller.py`, `autopick.py`, `floor.py`, `vision.py`, `dashboard.py`, `settings.py`) |
| `config/` | `limits.json` · `rest.json` · `floor_points.json` · `floor_config.json` (optional z0/L1/L2/L3) · `waypoints.json` |
| `var/` | `cmd/` (inbox) · `done/` (processed) · `state.json` · `bridge.log` · `top.jpg` `wrist.jpg` · `recordings/*.jsonl` |
| `scripts/` | `STOP.command` · `RESUME.command` · `RELEASE.command` |
| `docs/` | `operations.md` (this) · `learnings.md` · `metadata.json` · `decision-log.json` · `motion-log-pick-place.md` |
| `ESTOP` | at the repository root: present = arm frozen |


## Added in 3.4

- `config/settings.json` overrides any constant in `settings.py` (port, camera indices, gains, guard limits,
  detector colour gates, servo tuning); `config/poses.json` overrides the taught paths. Examples in `config/*.example.json`.
- Routines are registered by name (`routines.py`); start with `/cmd?a=routine&name=<name>` or `{"action":"routine","name":...}`.
  `/state.routines` lists them with their phase sequence.
- `/state.progress` = current routine phase, detail, per-phase timings and the live approach servo values;
  `/state.events` = the last 60 guard events; `/state.preflight` = the start checklist (the AUTO button dims when red).
- Dashboard: Routine panel (phases, preflight, approach convergence), Guard events panel, Esc = STOP.
- Tests run without lerobot installed (`hardware.py` imports it lazily); `tests/test_autopick.py` drives the routine
  against a simulated arm.


## Added in 3.5 — painting

- `/paint` page and API: `/paint/state`, `/paint/cmd?a=paper_size|mark_corner|mark_extra|clear_paper|station_set|
  station_mark|station_delete|brush|run|delete_program|trace_border`, `POST /paint/plan` (picture body; query
  `name, margin_cm, detail, white_threshold, max_stroke_cm, dry`), `/paint/program/<name>`.
- Control loop `path` command: `{"action":"path","points":[pose,...],"speed":deg_s}` follows dense waypoints
  continuously; each point is step-capped / clamped / floor-guarded relative to the previous one; HOLD, ESTOP and the
  guards drop the queue. `/state.path` = {remaining, total} while a path runs.
- Routine `paint` (select with `ctrl.paint_request = {"name": ...}`; the page does this). Phases: preflight, start,
  painting (detail = colour), finish, done. `/state.progress.paint` = step/stroke counters and painted stroke indices.
- Files: `config/painting.json` (paper, corners, stations, brush), `paintings/<name>.json` (compiled programs).
- Safety notes: the floor guard protects the *fingertips*; the brush tip is planned by the tool model
  (press depth `brush.press_cm`, default 1.5 mm). Validate with the border dry run and a DRY program before painting.
  Stations are visited via an "up" pose (shoulder −10°) so the brush does not sweep the paper.


## Added in 3.6 — video recording

- Both processed camera feeds (with overlays) are tiled side by side into `var/videos/<time>_<name>.mp4` at ~10 fps.
- Start/stop from the **● REC VIDEO** button on either page or `/cmd?a=video_start&name=…` / `a=video_stop`;
  every routine (pick, paint, dry run) records automatically while it runs (`AUTO_RECORD_ROUTINES`, settings.json).
- `/state.video` = current recording {file, seconds, frames, auto}; `/state.videos` = catalogue; clips play/download at `/videos/<file>`.


## 11. Painting from files — no browser, no agent (added 3.7, 2026-09-14)

Everything the `/paint` page does can be driven by JSON files in `var/cmd/`; `var/state.json` now carries
`auto` (routine status), `progress` (`routine`, `phase`, `detail`, `paint` = step / stroke counters and label)
and `video`, so a shell loop can follow a run.

| JSON | Meaning |
|---|---|
| `{"action":"paint_compile","plan":"paintings/<n>.plan.json","name":"<n>","dry":false}` | compile a hand-written plan into `paintings/<n>.json` (logs the skipped/unreachable strokes) |
| `{"action":"paint","program":"<n>"}` | run a saved program (records video automatically) |
| `{"action":"paint","program":"<n>","from_stroke":12}` | resume at stroke 12 — starts with the rinse/dip that precedes it |
| `{"action":"paint_goto","u":6.7,"v":4.7,"z":3.0}` | brush tip to paper (u, v) cm at z cm above the paper, height correction applied (`"raw":1` = model height, no correction) |
| `{"action":"paint_probe","u":..,"v":..,"z":1.0}` | record "the tip touched the paper here when commanded z" → height correction map |
| `{"action":"paint_mark_extra","u":..,"v":..}` | present pose = tip on the paper at (u, v) (refits the tool model) |
| `{"action":"paint_brush","hover_cm":3,"dip_every_cm":6}` | brush parameters (any key of `brush` in `config/painting.json`) |
| `{"action":"abort"}` | abort the running routine and hold (a plain `hold` only interrupts one move) |
| `{"action":"video_start","name":"x"}` / `{"action":"video_stop"}` | manual recording |

**Plan file** (`paintings/<n>.plan.json`): `{"strokes": [{"color": "<station>", "points": [[u, v], [u, v], ...]}]}` in
paper cm, u along the top edge A→B, v down A→D. A stroke is a polyline: the brush goes down at the first point,
follows every vertex and lifts at the last — so a hill is one stroke and a circle is one closed polygon. Colours are
painted in plan order; a colour change inserts a rinse (2 water dips) and a dip; the brush re-dips after
`brush.dip_every_cm` of painted line. `paintings/landscape.plan.json` is the reference (14 strokes, 4 colours).

**Height correction** (`config/painting.json["probes"]`). The tool model is geometric and its corners were taught
in free-drive; under torque the arm sags and the brush lands 1–2 cm lower than planned. Probes record where the
tip *really* touched at a *commanded* height; the compiler adds the inverse-distance-weighted probe value to every
height it asks the model for. Probes depend on the motor gains: after changing `P_GAIN` re-probe (P 32→48 on
shoulder_lift moved the touch height from 1.5 to 1.0 cm).

**Recipe**: `scripts/paint.sh landscape` (or `DRY=1 scripts/paint.sh landscape` first). A human stays within reach
of STOP; between runs put the brush back in the gripper if it was removed and check the pans have not moved.

Guard settings that painting needs (`config/settings.json`): `LOAD_LIMIT.shoulder_lift 600` (the stretched arm
alone reads 400–490), `STALL_DEG 6` (shoulder sag at full reach is 3.5–4.5° at P=32), `P_GAIN.shoulder_lift 48`.


## 12. Chess and guided teaching (added 3.8, 2026-09-15)

Chess is a discrete pick-and-place on a taught plane, built out of the same parts as painting: the
plane model, the guarded `path` replay, the probe-based height correction and the routine registry now
live in `src/so101_bridge/shared/` and belong to neither task. What is new is guided teaching, board
vision, the rules engine (`python-chess`) and three routines.

**Nothing here has run on the arm yet.** The board model, the move logic, the physical planning and the
turn loop are tested against a simulated arm; the geometry below is what the code assumes, not what has
been measured. P0 (§12.6) is the first session on real hardware.

### 12.1 The board's coordinate frame

`u` runs left to right as the robot sees the board, `v` from the far edge towards the robot. The plane
the model is fitted to spans **corner centre to corner centre** — 7 pitches, 25.375 cm on a 29 cm board —
because the four taught contacts are the closed fingertips on the centres of a1, h1, h8 and a8.

`config/chess.json["board"]["robot_side"]` names the colour sitting in front of the robot; its home rank
is the near one and its queenside is on the robot's left. Turning the board round is that one word, not
a re-teach: the same taught corners are reinterpreted. Nothing else in the package knows the orientation.

### 12.2 Guided teaching (any workspace, not just chess)

    scripts/chess.sh teach          # or the Start button on /chess
    scripts/chess.sh free           # torque off — the daemon tells you where to put the arm
    scripts/chess.sh done           # record this point, move to the next
    scripts/chess.sh skip | back    # leave one out / go back one

The session is a file: `config/teach/chess_board.json`, 18 steps — four corners, two check squares, six
grasps, four slots, two notes. `/state.teach` carries the current prompt, the step number and what has
been recorded so far; every Done writes straight into `config/chess.json`, so an aborted session keeps
what it already learned. `teach_fix <id>` re-records one step without redoing the rest.

What each step records: a **contact** stores the arm pose; a **grasp** stores the pose *and* the gripper
opening, and the model turns it into a height above the board and the lean used, which is what gets
reproduced on all 64 squares; a **probe** stores where the tip really touched at a commanded height.

### 12.3 Board model and the reachability report

    scripts/chess.sh compile        # fit, print the report, record the unreachable squares
    scripts/chess.sh verify         # ARM MOVES: visit every taught point at hover, descend to 1 cm
    scripts/chess.sh goto e4 6      # ARM MOVES: jaws over e4, 6 cm above the board

The gripper's reach (wrist axis → jaw centre) is **given**, not fitted: `gripper.tool_len_cm`, seeded
with the floor model's L3 = 11 cm. Measure it in P0 and correct it — a wrong value shows up as a board
residual rather than being quietly absorbed, which is the point of fixing it.

The report prints the lean, in degrees, that each of the 64 squares needs at grasp, hover and carry
height, and `--` where no pose inside the joint limits reaches. Squares with no grasp pose at all are
written to `chess.json["unreachable"]`; the routine plays those moves by asking a human.

### 12.4 Playing

    scripts/chess.sh new human llm  # who plays which colour: human | llm | engine
    scripts/chess.sh play
    scripts/chess.sh wait           # blocks until it is the LLM's turn or the game ends
    scripts/chess.sh game           # the game document, ~300 bytes of FEN + move list
    scripts/chess.sh move e7e5      # the arm plays it (or asks a human if it cannot reach)
    scripts/chess.sh confirm        # "yes, that is what I played" / "I have fixed the board"

The LLM never sees a frame and never applies a rule. It reads `var/games/<id>.json` and writes one move;
`python-chess` decides legality and the daemon decides how to move the arm.

The human's move is read from the overhead camera when the board's image corners have been taught, and
typed with `chess.sh move` otherwise — the game is fully playable with no camera at all. A camera reading
is believed only when it has been **stable** for `CHESS_STABLE_N` frames and the change is **sane** (at
most four squares, consistent with some legal move). With `CHESS_AUTO_TRIGGER=false`, the default, a
reading the camera is sure about still waits for `chess_confirm`.

The board is only ever read with the arm at rest — while it is moving it covers the board.

### 12.5 What the /chess page shows

Six panels beyond the teaching and board controls, all fed by `/chess/state` so an agent sees exactly
what the page does:

| Panel | What it draws | Needs |
|---|---|---|
| **Position** | the position from the FEN, pieces on a chequered 8x8, last move outlined, in the robot's own view (its back rank at the bottom) | nothing — it draws the start position before any game |
| **Camera reading** | the same 8x8, green where the camera sees a piece and red where it disagrees with the position; plus the warped board image with the square grid on it | image corners taught **and** an empty-board reference captured — the panel says which of the two is missing |
| **Arm and board, side view** | the arm to scale as the floor model sees it, with the taught board plane and the grasp / hover / carry heights drawn on it | the floor model fitted |
| **Present pose** | the live joint pose, selectable, with *Copy JSON*, *Copy as a goal command* and *Save as waypoint* | nothing |
| **What the history has learned** | grasp success per piece as bars, and an 8x8 of the squares that went wrong | some history |
| **Suggestions** | what the evidence supports changing, each with an Apply button | at least four attempts at the same thing |

The camera panel needs one extra step the rest do not: **Capture empty board** (`chess.sh reference`,
`{"action": "chess_reference"}`) with the board clear. Every reading is a difference from that reference,
so it has to be taken when there really is nothing on the board. The reader is then shared by the page
and the routine, because its reference and fitted thresholds are what make two readings comparable.

The reachability grid switches between grasp, hover and carry height, and clicking a square sends the
jaws there — that is a real move, at the height in the box next to the grid.

### 12.6 Learning over time

Every grasp, place and verification appends one line to `var/chess_history.jsonl` — which piece, which
square, the commanded height, the lean the IK really used, the opening the jaws settled at, and whether
that counted as a hold. The routine never reads it back mid-game: it is the record, not the plan.

    scripts/chess.sh learned          # grasp success per piece, squares that went wrong, suggestions
    scripts/chess.sh apply <id>       # write one suggestion into config/chess.json

`learned` turns the aggregate into suggestions, each naming the field, the current value, the proposed
value and the evidence. **Nothing is applied on its own.** Two kinds:

| Suggestion | Trigger | Applies to |
|---|---|---|
| lower a piece's grasp height by 0.3 cm | a quarter or more of that piece's grasps missed | `pieces.<p>.grasp_z_cm` |
| raise a piece's miss threshold | every successful grasp settled well above the current threshold, so a near-miss would pass as a hold | `pieces.<p>.stall_min` |
| teach this piece's grasp | it keeps missing and no grasp was ever taught for it | advice — no automatic form |
| re-probe or re-teach around a square | that square keeps failing its grasp or its verification | advice — no automatic form |

A suggestion needs at least four attempts at the same thing before it appears, so one bad grasp never
moves anything. Applying one is logged with the before and after values, and recorded in the history
itself, so a change that made things worse is visible and reversible.

The vision thresholds learn separately and continuously: they are refitted from any position whose truth
is known (the start of a game, and after every verified robot move) and written back to
`config/chess.json["vision"]`.

### 12.7 State, needs and log patterns

`/state.chess` = `{id, mode, robot, players, fen, turn, waiting_for, phase, ply, last_move, needs, result}`.
`needs` is the only channel for asking a person for something, and the routine holds until it is answered:

| needs.kind | What happened | Remedy |
|---|---|---|
| `unreachable` | the arm cannot serve some action of the robot's move | play the move for it, then `chess.sh confirm` |
| `feed` | setup wants the next piece | put it on the feed slot, then `confirm` |
| `ambiguous` | the camera saw a change several legal moves would explain | `chess.sh move <uci>` |
| `illegal_move` | a move that is not legal here, or a reading no legal move explains | fix the board or send a legal move |
| `verify_failed` | after the arm moved, the camera does not see the expected position | fix the board, then `confirm` |
| `grasp_failed` | the jaws closed on nothing (settled below the piece's `stall_min`) | routine stops; nothing is recorded that did not happen |
| `off_book` | a puzzle line ran out or diverged | start a game instead |

Log lines: `chess: <id> ply N <who> <san> (<uci>)` per move, `chess: <id> NEEDS <kind>: {...}` when it
asks for help, `chess: reachability …` for the report, `teach: <session>/<step> recorded …` per taught
point, `CHESS FAILED: …` when a routine stops.

### 12.8 P0 — the first session on the arm

1. Place the board, robot centred on the near edge. Set `robot_side`, size and camera on `/chess`.
2. `scripts/chess.sh teach`, then work through the prompts. Measure the jaw-centre offset while you are
   there and correct `gripper.tool_len_cm`; measure z0 and write `config/floor_config.json`.
3. `scripts/chess.sh compile` — read the reachability report before anything moves.
4. `scripts/chess.sh verify` — the arm visits every taught point. Watch where the tip actually lands.
5. `DRY=1 scripts/chess.sh setup`, then a real setup, then `DRY=1 scripts/chess.sh play`.
6. Photograph the board for `tests/fixtures/chess/` (empty, start, after e4, a capture, castling, a hand
   in frame) — the vision tests in `tests/test_chess_vision.py` skip until those exist.

Settings that chess adds (`config/settings.json`): `CHESS_CAM`, `CHESS_ENGINE`, `CHESS_ENGINE_LIMIT`,
`CHESS_POLL_S`, `CHESS_STABLE_N`, `CHESS_STABLE_DIFF`, `CHESS_AUTO_TRIGGER`, `CHESS_CARRY_CM`,
`CHESS_MAX_LEAN_DEG`, `CHESS_MOVE_TIMEOUT_S`.

### 12.9 Command interface

| JSON (`var/cmd/*.json`) | Meaning |
|---|---|
| `{"action":"teach_start","session":"chess_board"}` | begin a guided teaching session |
| `{"action":"teach_done"}` / `teach_skip` / `teach_back` / `teach_abort` | drive it |
| `{"action":"teach_fix","step":"a1"}` | jump back to one step and re-record it |
| `{"action":"teach_free"}` / `teach_engage` | torque off / on, through the normal guards |
| `{"action":"chess_board","size_cm":29,"robot_side":"white","cam":"top"}` | board placement |
| `{"action":"chess_mark","square":"a1"}` | present pose = fingertips on that square's centre |
| `{"action":"chess_slot","name":"handover"}` | present pose = that slot |
| `{"action":"chess_image_corners","corners":{"a1":[x,y],…}}` | where the corners are in the camera frame |
| `{"action":"chess_compile"}` | fit, report, record the unreachable squares |
| `{"action":"chess_goto","square":"e4","z":6}` | jaws over a square (arm moves) |
| `{"action":"chess_check_view"}` | can the camera see the board at all? |
| `{"action":"chess_reference"}` | capture the EMPTY board — every camera reading is a difference from it |
| `{"action":"chess_apply","id":"stall_p"}` | apply one suggestion from the history to `config/chess.json` |
| `{"action":"chess_new","players":{"white":"human","black":"llm"}}` | start a game document |
| `{"action":"chess_play"}` / `chess_setup` / `chess_verify` | run a routine (`"dry":true` for a dry run) |
| `{"action":"chess_puzzle","puzzle":"mate_in_2_01"}` | set a FEN up and play the scripted line |
| `{"action":"chess_move","move":"e7e5"}` | supply a move (the LLM's, or the one a human just played) |
| `{"action":"chess_confirm"}` | answer whatever the game is waiting for |

HTTP: `/chess` the page, `/chess/state` (everything the page draws), `POST /chess/cmd` with the same
JSON, `/chess/<cam>.mjpg` the overhead view with the taught corners drawn on it, `/chess/warp.jpg` the
warped board as a still, `/chess/game/<id>` the full game document, `/chess/history?n=200` the raw
history rows plus the aggregate.

Log lines the history adds: `chess: applied <id> — <path> <before> -> <after> (<why>)` when a suggestion
is applied, and `chess: empty-board reference captured` when the camera gets its baseline.

## 13. Initialisation page `/init` (added 2026-11-11)

`make init` (`uv run so101-bridge --init`) starts the bridge in **init mode**: dashboard only, no control loop,
no motor command, torque left as it is. The daemon also falls into init mode by itself when the lerobot
calibration file (`~/.cache/huggingface/lerobot/calibration/robots/so_follower/my_follower.json`) is missing or
incomplete, which is what happens when the Hugging Face cache is cleared. Every other URL redirects to `/init`.

- **Health checks**: serial port, all six motors answer, motor supply voltage (>= 6 V; ~5 V means only USB power),
  calibration file, file = motor registers, backup copy in `config/calibration/`, ESTOP, config files, lerobot
  version, and (button) each camera grabs a frame.
- **Lost calibration file**: *rebuild file from motors* writes only the file, from the motor registers (homing
  offset, min, max). For joints under `extend` in `limits.json` the registers may hold the bridge's widened
  limits (RAM only, gone after a power cycle), so the range logged as `cal was a..b` in `var/bridge.log` wins.
  *restore from config/ backup* copies the mirror back. A normal start creates the mirror when it is missing.
- **Guided recalibration** (rebuilt arm / new motor only; it moves 0 deg of every joint, so all taught poses must
  be re-taught): lerobot's `record_calibration` split into confirmed buttons: release torque -> homing pose +
  half-turn homing -> sweep -> write + save (old file to `var/calibration_backups/`) -> engage.
- The page shows lerobot's own homing diagram, the `so101.mdx` Calibrate section and the CLI equivalent, read
  from the installed lerobot checkout. In normal mode `/init` shows the files, cameras and docs only.
- Endpoints: `GET /init/state`, `POST /init/cmd {"a": <command>, "confirm": "yes"}`.

## 14. Lab template `/lab` (added 2026-11-11)

Start every new experiment from the lab. It is generic (no paint or chess logic) and safe by construction:
every move is a request to the control loop, so the step cap, soft limits, table guard, load/stall guards and
ESTOP all apply. Nothing in a lab sends `src="floor_ok"`.

- **Page**: cameras (raw, no overlays), joints & jog (range track: motor sweep / soft limit / lab envelope /
  taught points / goal), side view to scale with the table guard, grasp line, reach circle, goal ghost, points
  and rest, plus a schematic top view (pan). Panels: pending changes, guards (+ guard events), and tabs for
  points & rest, reach envelope, video, raw state (tree, copy as JSON / path / goal command / Python), API.
  STOP is the big red button or `Esc`. Free-drive always asks first, and its dialog opens on Cancel.
- **Staged changes**: points, lab rest, envelope and settings go to `var/lab/lab.pending.json`. They are
  visible with a diff and can be edited, applied one by one or discarded. Only *Apply* writes `config/lab.json`.
  Staged points can be visited before they are applied.
- **Points**: the present pose under a name with a role (reference, calibration, approach, waypoint, contact)
  and a note. *Go* shows a move preview first (per-joint change, rough time, joints that need the envelope).
- **Lab rest**: `rest` in `config/lab.json`. *Go to rest* goes up to the high folded pose, then down onto it.
  When it is unset, the global REST path is used. `config/rest.json` is never written from a lab.
- **Reach envelope** (points the calibration margin excludes): `envelope.{joint: [lo, hi]}` widens the soft
  limit for this lab's moves only, through the loop's per-command `allow`. The loop caps it at the motor
  sweep. No motor register and no `config/limits.json` is written. A taught point always carries its own
  allowance (±0.5°).
- **Tool in the gripper** (pen, brush, probe), optional, in the *Tool* tab.
  - **Definition**: `tool` in `config/lab.json` = name, `length_cm` (whole tool), `out_cm` (the part sticking out past
    the fingertips), `margin_cm` (the tool tip's own table margin, ≥ −0.5 cm, so ≤ 0 lets a pen touch the paper) and
    `grip` (the gripper value that holds it; *Use present gripper* records it).
  - **Mount** (asks first; the definition must be applied): it is saved in `var/tool.json` and survives a restart.
    From then on the control loop, on **every** page:
    - watches three points: the fingertips (usual 0.8 cm), the tool tip (its own margin) and the tool's back end
      (0.8 cm). The tool lies along the gripper axis.
    - freezes the arm when any of them is predicted 0.8 cm below its margin.
    - locks the gripper: a goal that would move it is dropped with a `TOOL LOCK` event; only the mount itself closes it.
  - **Unmount** (asks first): back to the fingertip guard. The jaws stay closed until you open them.
  - Points recorded while a tool is mounted remember it, and their tip in the side view is the tool tip.
  - Do not mount a tool for the paint or chess routines: the gripper lock and the tip margin apply to them too.
- **Cameras**: `/lab/cam/<cam>.mjpg?w=&q=&fps=` streams the raw frames. Each new frame is encoded once and
  shared by every viewer, only new frames are pushed, and quality is chosen per browser.
  `/lab/cam/<cam>.jpg` returns one frame. Defaults are in `cameras` in `config/lab.json`.
- **Video**: `video.dir` (relative to the repo root, default `var/videos/lab`) and `video.raw` (no overlays)
  in `config/lab.json`. The page shows the exact file path while recording.
- **API** (the same one the page uses, so scripts and agents get the same guards and staging):

  | endpoint | what |
  |---|---|
  | `GET /lab/state` | everything the page draws |
  | `GET /lab/observe[?images=1]` | compact observation: pose, fingertip, table clearance, points, cameras (+ base64 JPEGs) |
  | `GET /lab/actions` | every action with params, `moves`, `confirm` (build LLM tool definitions from this) |
  | `POST /lab/cmd` | `{"action": ..., ...}` -> `{ok, error?, message?, needs_confirm?}`; a `confirm` action must be repeated with `"confirm": "yes"` |
  | `GET /lab/journal?n=50` | the recent actions |
  | `GET /labs` | every registered lab (name, title, description, ui module) |

- **Journal**: every action that ran or was refused goes to `var/lab/<lab>.journal.jsonl`. It shows in the
  *Journal* tab and at `GET /<lab>/journal?n=`, and the last 10 entries are in `/observe`.
- **More labs**: `make lab NAME=<name> TITLE="..." DESC="..."` scaffolds `labs/<name>.py` and
  `web/<name>/panels.js`. The lab is discovered at start-up, served at `/<name>` on the shared page with its
  own tabs, and listed at `GET /labs` (and in the nav).

**The full guide** (anatomy of a lab, moving the arm, staging, panels, cameras, model/LLM hooks, tests, and
what may go into the template) is in [docs/lab.md](lab.md).
