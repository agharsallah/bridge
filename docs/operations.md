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
