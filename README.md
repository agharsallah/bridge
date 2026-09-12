# SO-101 follower control bridge — operator & agent manual

Version: bridge.py v3.3 (2026-09-12). Owner: Abderrehmen Gharsallah. Repo root: this directory (`bridge/`).
Companion files: `LEARNINGS.md` (why things are the way they are), `metadata.json` (all numbers, machine-readable).

This manual is written so that a small language model (or a person) can operate the arm safely by following
the steps literally. Sections 1–4 are for everyone; sections 5–9 are the exact interfaces.

---

## 1. What this is

A Python daemon (`bridge.py`) that owns the SO-101 follower arm and its two cameras. Everything else
(a human at the dashboard, an AI agent writing files, the built-in autonomous routine) sends *goals* to it.
The daemon enforces all safety rules itself, so a bad command becomes a slow, limited, or refused move —
never a jerk and never a push into the table.

Hardware: SO-101 follower arm (6 Feetech STS3215 motors) on `/dev/cu.usbmodem5A460836731`, calibration id
`my_follower`; two Innomaker U20CAM cameras: OpenCV index 0 = **top** (overhead view of the table),
index 1 = **wrist** (mounted on the gripper's fixed jaw, looks along the jaws). Indices can swap after
re-plugging — always verify on the dashboard which image is which.

## 2. Start / stop

Start (macOS Terminal, in this directory):
```
uv run python bridge.py
```
Expected Terminal lines within ~10 s: `rest pose: {...}`, `EXTENDED shoulder_lift ...`, `EXTENDED elbow_flex ...`,
`floor model fitted on N points ... rms X mm`, `dashboard: http://localhost:8765`, `ready. present={...}`.
If it prints `ESTOP -> frozen`, the emergency stop from a previous session is still set: press **RESUME**.

Dashboard: open http://localhost:8765 in a browser on the Mac.

Stop the arm (any of these, in order of speed):
1. Dashboard **STOP** button (creates the `ESTOP` file; arm freezes where it is; all commands ignored until RESUME).
2. Double-click `STOP.command` in Finder (same effect).
3. `touch ./ESTOP` in any Terminal (same effect).
4. Ctrl-C in the bridge Terminal: bridge exits, arm keeps holding its pose with torque on.

Torque off (arm goes limp — only when it rests on the table): dashboard **RELEASE**/**FREE-DRIVE**,
`RELEASE.command`, or `{"action":"release"}`. Torque back on: **ENGAGE** or `{"action":"engage"}`.

Restarting the bridge never drops torque: the arm holds its pose through a restart.

## 3. Safety rules the daemon enforces (you do not have to implement them, but do not fight them)

| Rule | Value | Effect |
|---|---|---|
| Soft joint limits | calibration sweep ± 3° margin, widened by `limits.json` | goals clamped |
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
`rest.json`. Do not RELEASE torque unless the arm is resting on the table.

### 4.3 Manual jogging
Dashboard *Joint control*: −5/−1/+1/+5 buttons (relative), sliders (absolute on release), speed 2/4/8 °/s.
Or files: `{"goal": {"shoulder_pan": 3}, "relative": true, "speed": 3}`. Near the table use ≤ 3 °/s and steps ≤ 3°,
and watch `tip_z_cm` in the state: stop when it is ≤ 1.0.

### 4.4 Teaching
- **REC**: logs joint positions at 10 Hz (plus every goal) to `recordings/rec_<timestamp>.jsonl`; press again to stop.
- **Save current pose** (name): stores a waypoint in `waypoints.json`; **Go** returns to it in capped steps.
- **Save as REST**: overwrites `rest.json` with the current pose.
- **Mark contact** (tips height in cm): appends the current pose to `floor_points.json` and refits the floor
  model. Use `0` when the fingertips touch the table, `1.9` when they rest on top of the Duplo brick.
  Take contacts spread over the workspace (near/far, gripper vertical and tilted). To make the model exact,
  measure the height of the shoulder-lift motor shaft above the table and put it in `floor_config.json`
  as `{"z0": <metres>}`, then restart.

## 5. Command interface (files and HTTP)

Write a JSON file into `cmd/` (write to `x.tmp`, then rename to `NNN.json`; the daemon reads on the
next 30 Hz tick and moves the file to `done/`). Equivalent HTTP: `GET http://localhost:8765/cmd?a=<action>&…`.

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
| — | `a=rest_save` | current pose → rest.json |
| — | `a=floor_mark&h=<cm>` | add floor contact |
| `{"action": "diag", "regs": ["Present_Load", …]}` | — | dump motor registers to the log |
| `{"action": "write", "reg": "P_Coefficient", "motor": "elbow_flex", "value": 48}` | — | guarded register write |

Joints: `shoulder_pan`, `shoulder_lift`, `elbow_flex`, `wrist_flex`, `wrist_roll`, `gripper` (gripper 0–100, others degrees).
Speed: number (all joints) or `{"joint": speed}`.

## 6. State interface

`state.json` (rewritten 5×/s, atomically) and `GET /state`:
```
{"seq", "time", "mode", "torque_on", "present": {joint: value}, "goal": {...}, "cmd": {...},
 "speed": {...}, "limits": {joint: [lo, hi]}, "last_cmd", "estop": bool, "tip_z_cm": float|null}
```
`/state` additionally has `"blob": {"top": {...}, "wrist": {cx, cy, w, h, long, area}}`, `"auto": <routine status>`,
`"rec": <file or null>`, `"floor": {points, fitted, rms_mm, tip_z_cm}`.

`mode` values: `hold` (holding, idle) · `moving` (interpolating to goal) · `reached` (at goal, holding) ·
`stalled` (a guard froze it) · `estop` · `released` (torque off).

Images: `top.jpg`, `wrist.jpg` (960×540 JPEG, ~every 1.5 s, with detection overlay) and MJPEG at `/top.mjpg`, `/wrist.mjpg`.

## 7. Log patterns (`bridge.log`)

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
| `AUTO FAILED: detector unreliable` | colour washed out | more light / brick less glossy; check `wrist.jpg` |
| `AUTO FAILED: missed the brick` | jaws closed on nothing | brick was not between jaws; retry (routine re-aligns) |
| `AUTO FAILED: shoulder at its forward limit …` | brick too far from the base | move brick ≥ 5 cm closer to the arm |
| `AUTO FAILED: floor model not taught yet` | < 3–4 contact points | §4.4 Mark contact |
| `STALL on <joint>` | joint blocked or too heavy | check for obstacle; move away; if elbow: P gain (write P_Coefficient 48) |
| `LOAD GUARD: {…}` | load spike | as above; thresholds in `LOAD_LIMIT` |
| `FLOOR FREEZE` | predicted below table | lift with `shoulder_lift` −5 (relative), then continue |
| `bus hiccup` repeated 5× | cable/power | check the arm's power supply and USB cable |
| joint moves but stops short by 2–10° | P gain too low | `{"action":"write","reg":"P_Coefficient","motor":"<j>","value":48}` |
| camera images swapped | index change after re-plug | edit `CAMS` in bridge.py, restart |

## 10. Files

`bridge.py` daemon · `LEARNINGS.md` · `README.md` (this) · `metadata.json` · `limits.json` · `rest.json` ·
`floor_points.json` · `floor_config.json` (optional z0/L1/L2/L3) · `waypoints.json` · `recordings/*.jsonl` ·
`cmd/` (inbox) · `done/` (processed) · `state.json` · `top.jpg` `wrist.jpg` · `bridge.log` ·
`STOP.command` `RESUME.command` `RELEASE.command` · `bridge_v1_backup.py`.
