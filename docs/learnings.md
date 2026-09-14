# SO-101 follower — control bridge: what we learned (2026-09-12)

Setup: MacBook Pro (Apple Silicon), lerobot 0.6.2 (uv venv, Python 3.12), SO-101 follower on
`/dev/cu.usbmodem5A460836731`, calibration id `my_follower`, cameras: OpenCV index 0 = overhead ("top"),
index 1 = gripper ("wrist"), both Innomaker U20CAM 1080p (indices can swap after re-plugging: check the
dashboard). Bridge: `uv run so101-bridge` from the repository root, dashboard http://localhost:8765.

## Result
First fully autonomous pick-and-place succeeded at 18:27 (AUTO PICK): visual servo to the brick, descent by
the floor model to a 1.3 cm predicted fingertip height, grasp verified by gripper stall (~20.4), carry to the
confirmed drop pose over the tin, release, retreat to the configured rest pose.

## Hardware / firmware lessons
- lerobot writes P_Coefficient=16 to the STS3215 motors. That is too weak for small smooth position steps
  against gravity: the elbow silently stalled (load ~18%) 3-11 deg short of its goal. We run P=32 on the arm
  joints, P=48 on the elbow (still ~1.5-2 deg sag); gripper stays at 16 (soft grip).
- Feetech bus drops status packets occasionally ("There is no status packet!"): retry connect, use num_retry
  on setup writes.
- lerobot's `configure()` on connect briefly disables torque -> the arm sags / can drop a held object.
  The bridge subclasses the robot and skips configure(); gains and gripper protections are written explicitly.
- Calibration sweeps define the soft limits. This arm's shoulder_lift sweep was only +/-43.5 deg although the
  joint physically goes to ~101 deg forward. `limits.json {"extend": ...}` widens the soft limit AND rewrites
  the motor's Min/Max_Position_Limit registers (they clamp goals too). Current: shoulder_lift [-47, 98],
  elbow_flex [-80.4, 84.5] (the rest pose sits against the mechanical stops, slightly past the sweep).
- Pushing DOWN into the table shows almost no motor load (gravity helps), so load/stall guards do NOT catch
  table contact. A geometric floor model is required (below).

## Joint conventions on this arm (calibrated degrees)
- shoulder_lift +  : upper arm forward -> gripper DOWN (and slightly back). Rest = -45.
- elbow_flex -     : unfold -> gripper FORWARD and slightly UP. Rest = 83 (folded).
- wrist_flex -     : camera/tips pitch FORWARD; wrist ~ 37.5 at lift 0 looks straight down;
  keep ~ -0.7 deg wrist per +1 deg lift to keep looking down while descending.
- shoulder_pan +   : scene shifts LEFT in the wrist image (gripper moves image-right).
- gripper 0..100: 60 = open wide enough for the brick; closing to goal 10 stalls at ~20 when holding it.

## Floor model (fingertip height)
Planar 3-link chain (L1 .1159, L2 .1350, L3 .110 m), fitted to taught table-contact poses
(`floor_points.json`, plus 2 observed contacts). Height is independent of pan. Contact-only data leaves the
model under-determined away from the table; anchoring works best with the measured shoulder-axis height
(`floor_config.json {"z0": metres}`) -> in simulation the fit becomes exact. Current fit (z0 free):
rms 1.4 mm on 7 points; good near the objects, unreliable in the folded rest region (predicts 11 cm).
Guards: goals shortened so the tips stay >= 8 mm; motion freezes if predicted height < 0. AUTO refuses to
run without a fitted model.

## Vision (wrist camera, 960x540)
- Yellow brick: HSV hue 14-40, sat >= 40, val >= 80, open(5) + close(21) to merge stud columns, solidity
  >= 0.7, fill >= 0.5. Sat drops to ~45-55 when the brick fills the view (washed out) -> earlier gate of 70
  fragmented it into single studs and the size cue collapsed. Table sat <= 33, tin label is orange (hue ~15)
  and ring-shaped: rejected by hue+shape.
- Never trust a sudden size collapse (>40%) as "brick far away": treat as a bad detection.
- Servo targets: brick centre x 470 (ready) -> 540 (grasp), y ~300, gains 0.04 deg/px (pan), 0.07 deg/px
  (elbow reach). Brick long side ~350-380 px when the jaws are at brick height.

## Poses that work (deg: pan, lift, elbow, wrist, gripper)
- READY (looking down): -2, 0, 70.5, 37.5, 60
- Grasp reached today: -19, 39-42, 60-69, 8-15  (depends on brick position; found by servo)
- Carry: lift -10 twice from grasp (shoulder back = up), then DROP over the tin: 6.8, 26.5, 47.7, 5
- Rest: configurable in rest.json (currently -11.8, -45.7, 83.3, 54.1, grip 2.7)

## Operating procedure
1. Start bridge, open dashboard, check both cameras show the brick box, ESTOP banner off.
2. Hand near STOP (dashboard) / STOP.command / Ctrl-C (arm holds, torque stays on).
3. AUTO PICK. Aborts (and freezes) on: lost brick, stall, load, floor freeze, ESTOP, missed grasp.
4. GO TO REST when done; RELEASE only with the arm resting on the table.
Teach tools: joint buttons/sliders, REC (10 Hz jsonl in recordings/), Save waypoint, Mark contact (floor).
Files: src/so101_bridge/ (v3.3), config/{limits,rest,floor_points,waypoints}.json, var/bridge.log.

## Painting a picture with 14 strokes (2026-09-14)

Result: the landscape (sun, roof, three flowers, two hills, stems, house, window) painted from
`paintings/landscape.plan.json` — hand-written polylines instead of the hatcher, so 14 strokes and 3 colour
changes instead of hundreds of hatch lines. Run and resumed entirely through `var/cmd/` files.

What went wrong first, and the fix each time:
- **Start of a program**: the first travel was one far point → step-capped to 12°, arm began from the wrong place.
  The routine now bridges from the present pose to each step's first point in ≤8° increments (also makes
  `from_stroke` resume possible).
- **"reached" never came** although the arm was still: a joint sat between REACH_TOL (3°) and STALL_DEG (4°).
  `SETTLE_SEC`: a finished move that is stationary for 0.8 s is reached, with the residual logged.
- **Brush 1.5–2 cm too low** at "hover": geometric model + free-drive-taught corners vs. a sagging arm under torque.
  Fixed by probing (operator says "touch" while the commanded height steps down) and a probe-based correction map
  in the compiler — not by re-teaching corners. Corner residuals after refit were ±0.6 cm; sag was the rest.
- **LOAD GUARD on shoulder_lift** (416, then 488) at the far edge of the paper / far pan: that is gravity, not a
  collision. Limit 400→600 for this joint only; STALL_DEG 4→6; shoulder P 32→48 (less sag). Changing P moved the
  touch height by 0.5 cm — gains and probes belong together.
- **Wet brush swept sideways** leaving a station: compile now goes straight up (hover, then shoulder −10°), turns
  the pan alone, then descends to the stroke. Hover raised 1.5→3 cm so inter-stroke travel is clearly off the paper.
- **Fading colour**: re-dip after ~2–3 short strokes → `dip_every_cm` 12→6.
- Camera: the overhead view is mostly the arm while it paints; the operator's eyes were the reliable sensor for
  height. A camera that sees the paper past the arm (or the wrist camera looking at the tip) would let the agent
  verify contact itself.
- Do not use GO TO REST with the brush in the gripper: `go_rest` opens the gripper to 10 on its way. Fold with a
  `path` that leaves the gripper alone (present 2.4).

## Filling areas (same session, 17:52–18:11)

Second pass: fill the sky (blue) and the ground (green) so no paper stays white. `paintings/sky_ground.plan.json`
(27 blue radial strokes + the first ground attempt) and `paintings/ground_h.plan.json` (8 horizontal rows).
- **Stroke direction matters for this arm.** A stroke along v (towards/away from the base) is a coordinated
  lift+elbow move at the limit of reach — heavy, slow and it needed one dip per stroke. A stroke along u is a pan
  sweep at constant lift: light, smooth, 18 cm in one go. The ground took 8 pan strokes / 3 min versus 28 radial
  strokes / ~10 min for the sky. Operator's rule: *put the brush down and move the base.* Prefer rows along u for
  fills; keep radial strokes for short details.
- **Fill geometry**: rows 0.8 cm apart with a 6 mm brush close up in watercolour; skip zones as split strokes
  (sun ±0.3 cm, roof, house walls, flower heads) rather than separate programs.
- **Speed**: stroke 8 °/s, travel 9 °/s is the ceiling with the current guards; 12 °/s travel at the far top edge
  read 640 on shoulder_lift (limit now 750 for this joint — it is PWM effort, mostly gravity). Dwell 1 s at a dip.
- **Colour contamination**: 2 water dips × 0.5 s did not clean a blue-loaded brush; the green pan turned teal and
  the ground came out blue-green until the operator refreshed the pan. Now `rinse_dips 3`, `dip_dwell_s 1.0`; a
  towel station would help more than more water dips (the compiler already supports `kind: "towel"`).
- **Re-dip interval**: fills want a fresh brush per row (`dip_every_cm 8`); outlines were fine at 6.
- **Cameras swapped** again (`wrist.jpg` was the overhead view). It sees the whole sheet when the arm is folded or
  to the side — check it between colour blocks; the other camera is useless while painting (all arm).
- **Pausing a routine**: `hold` during a dwell does *not* stop the routine (it only fails a move). Use
  `{"action":"abort"}` (added) — it aborts the routine and holds. Then `paint … from_stroke N` to continue.
- **Left margin stayed pale**: the first sky strokes (u 1.4–3) ran on a brush that had travelled far from the pan;
  start fills near the pan side or dip before the first stroke of each block.
- **Clearance around finished shapes**: ±0.3 cm around the sun was not enough — wet blue spread into the yellow.
  Wet watercolour bleeds about one brush width (0.6 cm); keep ≥0.8 cm from anything already painted, and paint
  the light shapes *after* the surrounding fill if they must stay clean (or mask them with a dry stroke gap).
- **Blanks in a fill**: 0.8 cm row pitch left gaps where the brush ran dry mid-row. Use 0.6 cm pitch (overlap),
  dip before every row, and cross-hatch (a second pass at a different v offset) for a solid area.

## Touch-up pass and signature (18:14–18:23)

`paintings/touchup.plan.json`: 10 green rows offset half a pitch from the first fill (cross-hatch), 5 yellow chords
inside the sun (pan sweeps), and "Claude" in red as 6 block-letter polylines (0.8 × 1.2 cm per letter, u 12.9→19.5,
v 12.2). 21 strokes, 2 colour changes, ~8 min including one interruption.
- **Cross-hatch works**: the second green pass, offset 0.4 cm, closed every blank in the ground.
- **Re-painting a light colour over a dried dark bleed** recovers the shape: 5 yellow chords brought the sun back.
- **Bogus load packet**: `LOAD GUARD {'shoulder_pan': 776, 'shoulder_lift': 976, 'wrist_roll': -998}` during a plain
  travel, with normal loads one tick later — three joints at once near ±1000 is a corrupt Feetech read, not a
  collision. TODO: make the load guard require two consecutive over-limit samples (or reject |load| > 900 on
  more than one joint at once) before freezing. Resume was one `paint … from_stroke` away.
- **Lettering**: with a 6 mm brush, letters need ≥1.2 cm height and ≥0.3 cm gaps to stay legible; a 5-point
  polyline per letter is enough. Sign last, after the background has dried.

## Second iteration — `paintings/landscape2.plan.json` (18:43–19:15)

Same paper position, same stations, one plan, 84 strokes (41 blue rows, 24 green rows, 6 yellow chords, 12 red
incl. signature, 1 blue window), 4 colour changes, ~31 min incl. one restart. Videos `var/videos/20260914_1843*`,
`_1846*`, `_1904*`. Result (camera): even sky and ground with no white gaps, sun on clean paper, roof on a white
house silhouette, flowers and signature crisp. Compared with the first sheet the difference came from *order and
geometry*, not from new hardware:
1. **Fills first, details last.** Paint the two big light areas, leaving white *reserves* (sun circle + 0.8 cm,
   house polygon + 0.6 cm), then put the details onto paper that has dried while the other fill ran (~10 min).
   Painting details first and filling around them is what smeared the first sheet.
2. **Rows, not columns.** Every fill row is a pan sweep at constant lift (u direction), pitch 0.6 cm, split so no
   run exceeds 9 cm, dip before every run (`dip_every_cm 6`). ~25 s per row.
3. **Clean colour**: `rinse_dips 3 × 1 s` before each change; still a slight green cast in the yellow after the
   green fill — a towel station (`kind: "towel"`) is the next step.
4. **Bogus load packets** (`wrist_roll -995` on an idle joint) stopped the run once more → load guard now needs
   the same joint hot on two consecutive ticks; single spikes are logged as `load spike ignored`. No false stops
   after that, at stroke 10 / travel 11 deg/s.
5. **Speed**: 10 / 11 deg/s ran clean with limits 750 / STALL 6; that is the practical ceiling for this arm with
   a 16 cm brush (peak shoulder load 544 at the far edge).
6. **Camera check cadence**: after the sky, after the ground, at the end — enough to catch a wrong colour or a
   missed reserve; the overhead view shows the whole sheet only when the arm is at a station or folded.
Things still to improve: hill line as a soft two-tone (the light and dark greens of the reference are one green
here); a towel dab; `abort` at a stroke boundary is manual — a `pause_at_stroke` flag would be cleaner.
