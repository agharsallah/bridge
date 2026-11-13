# SO-101 follower — control bridge: what we learned (2026-09-12)

Setup: MacBook Pro (Apple Silicon), lerobot 0.6.2 (uv venv, Python 3.12), SO-101 follower on
`/dev/cu.usbmodem5A460836731`, calibration id `my_follower`, cameras: overhead ("top") and gripper ("wrist"),
both Innomaker U20CAM 1080p with the same USB serial. They are picked by model and position, not by a fixed
index (see "Cameras: two identical Innomakers" at the end). Bridge: `uv run so101-bridge` from the repository
root, dashboard http://localhost:8765.

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


## Chess prep, 3.8 (2026-09-15) — software only, nothing run on the arm yet

The board, pieces and camera have not been touched; this is what building the chess side taught us about
the code, and what the first hardware session has to settle.

1. **Painting and chess share a plane, not a tool.** Everything generic moved to `shared/`: the plane fit,
   the homography, the lean window, the IK, `plane_travel`, the probe-based height correction, the JSON
   store. `paint/kinematics.py` is now forty lines that say "the surface is paper, the tool is a brush, and
   nobody measured the brush". The acceptance test for the move was not the unit tests but recompiling
   every `paintings/*.plan.json` before and after and diffing the output: byte-identical, including the
   dry-run variants. Refactors of geometry code need that kind of check, not a green suite.
2. **Fit what you do not know; fix what you do.** Painting *fits* the brush length because the brush is
   glued in and nobody measured it. Chess *gives* the gripper's reach, because wrist-axis-to-jaw-centre is
   a dimension of the arm. Making it a parameter (`tool_len=None` fits, a number fixes) was one line in
   the plane model, and it changes what the four taught corners are for: with the length fixed they locate
   and orient the board and nothing else, so a mis-measured jaw offset shows up as a board residual
   instead of being quietly absorbed into a wrong length. There is a test for exactly that.
3. **The board's frame is the robot's frame, not chess's.** `u` left-to-right as the robot sees it, `v`
   from the far edge towards it, and one config word (`robot_side`) says which colour sits in front of the
   arm. Everything downstream — the reachability report, the warped camera image, the square/cell mapping —
   is written in that frame, so turning the board round is a config change rather than a re-teach, and no
   module below the workspace has an opinion about orientation.
4. **Occupancy is almost always enough to name a move.** A binary "something is there" reading per square,
   diffed against the position python-chess already knows, identifies the move uniquely in every real game
   we replayed — because the *origin* square emptying is what disambiguates two knights going to the same
   square. The only genuine ambiguity is a promotion (queen or knight look identical from above), which is
   a question about the piece, not the move. That is why the resolver returns a candidate *list* and the
   routine asks instead of guessing, and why colour is only ever used to *reject* a candidate.
5. **A pose that is not reachable is not an error, it is a lower height.** The far squares run out of arm
   before they run out of board: they can be served low but not at carry height. `physical.steps` looks for
   the highest height each end of a travel can be served at and ramps between them, which also solves the
   near-rank problem from the other direction (a piece lifted off rank 1 would otherwise be dragged across
   its neighbours). Only a square with no grasp pose *at all* becomes a human-assisted move.
6. **Guided teaching turned out to be the interesting part.** The old way — know which dashboard button to
   press in which order — kept the procedure in someone's head. A session is now a file the daemon reads
   out (`config/teach/chess_board.json`, 18 steps), each Done writes straight to `config/chess.json`, and
   a grasp step is post-processed into the two things that generalise: the height above the board and the
   lean the operator used. The mechanism is not chess-specific and should replace the paint page's corner
   buttons next.
7. **Write the game document atomically.** The first headless game stalled because the test's move feeder
   read `var/games/<id>.json` while the routine was rewriting it and got half a document. The LLM's
   `chess.sh wait` loop would have hit the same thing on a real game; both files now go through
   `util.atomic_write`.
8. **What P0 has to measure**: the jaw-centre offset (`gripper.tool_len_cm`, seeded at L3 = 11 cm), z0 for
   `config/floor_config.json`, which squares the real joint limits actually reach at grasp/hover/carry, the
   lean each needs, whether tilted grasps topple neighbours at 3.6 cm pitch, and whether the overhead
   camera sees the whole board with the arm at rest. The reach table in `drafts/chess-plan.md` is a
   prediction from a free-fit z0 and is expected to be pessimistic.

### Observability and learning, added the same day

9. **The page must not cost the control loop anything.** The obvious way to show the warped board is an
   MJPEG stream, which means warping every frame at 30 Hz inside the loop that also talks to the motor
   bus — more work than the loop itself does. It is served as an on-demand still instead (`/chess/warp.jpg`),
   and the whole page is one `/chess/state` call. The camera comparison is computed there, at the 1 Hz
   the page polls, not in the loop.
10. **The empty-board reference is a separate, deliberate step.** Occupancy is a difference from a
   picture of the empty board, so that picture has to be taken when the board really is empty — which a
   routine starting mid-game cannot do for itself. Hence `chess_reference` / the **Capture empty board**
   button, and a reader shared between the page and the routine so both compare against the same
   baseline with the same fitted thresholds.
11. **Copy the pose before you move the arm.** Free-driving loses the pose you were at, and the taught
   config only holds the points the session asked for. The pose panel exists because the cheapest
   recovery from "that was nearly right" is a clipboard: it offers the raw JSON and a ready
   `{"action": "goal", ...}` command, and can save it as a named waypoint.
12. **A self-tuning rig you cannot audit is worse than one you tune by hand.** The history records every
   grasp with the evidence (commanded height, lean the IK actually used, settled jaw opening, whether it
   held) and the aggregate proposes changes — but applying one is a person pressing Apply, the change is
   logged with its before and after, and the fact that it was applied goes into the history too. Two of
   the four suggestion kinds have no automatic form at all: *teach this piece* and *re-probe this square*
   are things only a human can do, and pretending otherwise would hide a geometry problem behind a
   tuning knob. Four attempts at the same thing is the floor for any conclusion, so one bad grasp moves
   nothing.
13. **Keep the game document small and put the telemetry elsewhere.** The LLM reads
   `var/games/<id>.json` every turn, so it stays a FEN and a move list; each move carries only a compact
   `exec` block (seconds, steps, points, grips). Everything a human would want for debugging lives in
   `var/chess_history.jsonl`, which nothing in the loop ever reads back.

## First real chess grasp attempt — Nf3 from g1 (2026-09-15, 19:59–20:53)

Board next to the arm base (h1 centre ~9 cm from the shoulder axis). Three attempts, what each one taught:

1. **19:59, "grasp held", nothing picked.** `gripper_to` counted 0.8 s of standstill *before* the jaws had
   started moving as "settled" (returned 59.8 = still open), and `wait_reached` accepted the stale `reached`
   of the previous move, so the descent was cut short by the next command. Both fixed in `controller.py`
   (wait for the loop to take the request; ignore stillness until the jaws have moved). A load guard trip
   on a garbage packet (pan −1020, wrist_roll −1019 on two ticks) is now rejected outright (≥2 joints
   near ±1000 = corrupt read).
2. **Why the arm could not hover over rank 1.** Squares 8–9 cm from the shoulder axis need wrist_flex
   90–100° to hold the jaws 6 cm up; the calibration sweep stopped at 83.8°. The planner's fallback
   "hover as high as you can" gave a 1.5 cm hover over g1 — jaws opening at piece-base height, then a
   carry at 1.5–3 cm across f2/g2. Two changes: a pick/place is now refused unless the square can be
   approached at `hover_cm` (no silent low hovers), and the operator proved the pose by hand
   (wrist 95.4° over g1) so `limits.json` extends wrist_flex to 100°. Hover became 100 % reachable, carry
   at 14 cm 94 % (e1–h1 excluded).
3. **20:53, first real attempt with the new limits — a partial win.** The arm went to g1 at 14 cm, opened,
   descended straight, closed, detected the miss (jaws settled at 5.5 < 12), and lifted straight up before
   anything else — no piece knocked over, guards silent. Two misses to fix:
   - jaws opened to 35 and brushed the piece on h1 — 3.6 cm pitch leaves ~1 cm per side; the opening must
     be ≤ 3 cm across the jaws (`pieces.<p>.open`, measured, not guessed);
   - the knight was not between the jaws at the taught 2.57 cm: the height model is uncertain by ~2 cm
     (`z0` free-fitted, tool length unmeasured) and the knight's neck is narrow — grasp lower, at the base.

Rules that now live in config: piece heights in `config/pieces.json`; `heights.clearance_cm` 8 and
`carry_cm` = tallest piece + clearance (14 cm) — jaws 8 cm above the pieces before any sideways travel.
Open question for the next attempt: confirm the hold with a camera before carrying (the wrist camera was
not starting this session; the top camera needs the image corners taught to read a cell).

## Second calibration, first complete Nf3 (2026-09-17, 14:20–14:40)

Board moved out to ~17 cm (h1 centre from the shoulder axis), re-measured: **24 × 24 cm, 3 cm squares** (the
29 cm in the config was wrong — `board.size_cm` and `config/pieces.json` now say 24/3). Corners and e4/d5
taught with a vertical gripper, every piece grasp taught at its base.

- **Result:** g1→f3 executed end to end on the first run after the refit: grasp held (jaws 7.7 on a knight
  that closes to 7.2), carry at 14 cm, upright release. The knight landed on **f2**, one square short toward
  the arm. `heights.place_offset_cm = [0, -1.0]` shifts every place point 1 cm away from the robot; to be
  re-checked with the step-by-step mode before it is trusted.
- **The gripper is 15.8 cm, not 11.** Fitting the tool length instead of fixing it at L3 took the corner
  residual from 1.16 to 0.71 cm rms and the board-size disagreement from 2.7 to 1.1 cm. Rank-8 corners were
  taught at 62° lean, e4/d5 at 10°, and a wrong tool length shows up exactly as a lean-dependent shift.
  `gripper.tool_len_cm` is 15.8 pending a ruler measurement (wrist_flex axis → closed fingertips).
- **Encoder wrap at rank 8.** Hand-driving to h8/a8 pushed shoulder_lift to 141°; the register range ends at
  ~133° (4095 ticks) and the reading came back as −218.8°. The fit survived (trig is periodic) but the IK
  rejected every rank-8 pose. Values unwrapped in chess.json; `limits.json` extends shoulder_lift to 133° and
  elbow_flex to −86°, which gives hover on all 64 squares and grasp on 60 (a8 b8 g8 h8 stay out).
- **Pick tilted, place upright.** The arm's model is accurate near the lean the corners were taught at, and
  a piece released tilted falls over. `heights.place_lean_deg = 0` makes the place column as vertical as the
  arm allows while the pick keeps the taught lean.
- **Jaw opening ≤ 2.5 cm (gripper 22)** on 3 cm squares; 35 brushed the neighbour.
- **Step mode** (`STEP=true scripts/chess.sh play`): the routine waits for `chess_confirm` before each
  primitive and drops a top-camera still in `var/steps/`, so the operator can judge open / descend /
  grasp / lift / carry / place one at a time and the miss is attributed to the right step.

### Step-by-step Nf3, third run (14:46–14:54) — the knight stood on f3

Offsets found one primitive at a time with the operator judging each pause:
- pick: jaws 0.5 cm to the arm's right and 0.3 cm short of the g1 centre → `heights.pick_offset_cm = [-0.5, -0.3]`;
- place: 1.2 cm short and 0.3 cm left at f3 → `heights.place_offset_cm = [0.3, -1.2]`, set by nudging the held
  knight with a one-point `path` while the routine waited, then releasing.
- At the f3 place pose (shoulder_lift ~92°, elbow −40°) the arm sags ~1 cm: commanded 3.3 cm, model reads 2.3.
  Releasing there still set the knight down upright — the `place_drop_cm` 0.5 absorbed it.
- Grasp signature for this knight: jaws taught closed at 7.2, hold settles at 7.7–7.9, an empty close reads ~1.
- Restarting the daemon kills the routine and drops its `needs` — with a piece in the jaws. Finish by hand:
  gripper goal to open, a straight `path` column up, then `rest`. `FROM=n scripts/chess.sh move …` now resumes a
  move at step n instead of replaying everything (the arm must already be where step n−1 left it).

**Method that worked:** step mode as the default way to debug (and to run) a move on hardware. One primitive
per confirm, the operator states the offset they see ("0.3 cm forward, 0.5 cm left"), the offset goes into
config, and the run resumes from the last good step. Two passes were enough to centre both the pick and the
place; a full autonomous run comes after, never before.

## Camera-driven game, first robot move verified by the camera (2026-09-17, 15:20–16:03)

Setup that works, two commands on an EMPTY board, both persisted (`board.image_corners` in chess.json,
`var/chess_reference.png`): `scripts/chess.sh grid` (cv2.findChessboardCorners → the four corner-square
centres) then `scripts/chess.sh reference`. Mid-game, `FROM_POSITION=true scripts/chess.sh reference`
synthesizes the empty board from the known position (occupied cells painted with the median empty cell of
the same colour). Never capture a plain reference with pieces on: every occupied square then reads empty
for the rest of the game.

What broke and the fix:
- **Image corners are in raw-frame pixels (1920×1080)**, the preview is 960×540 — a 2× scale error put the
  grid on the laptop keyboard.
- **Auto-exposure**: the frame brightens when pieces arrive; every empty square then differs 30–58 from a
  reference taken minutes earlier. `_match_exposure` maps the frame onto the reference using the medians of
  the board's dark and light tones (pieces are outliers). Empties dropped to 0.6–6.
- **One threshold cannot serve both square colours**: black on dark ≈ 6–18, empty light ≈ 5–6. Thresholds are
  now fitted per square colour (`{"dark": 7.5, "light": 9.9}` today).
- **The routine recaptured the reference from the live (populated) frame** whenever it built a new reader.
  It now keeps a persisted reference. The dashboard rebuilds the reader after a restart the same way.
- **Perspective lean**: at this camera angle a piece's head reaches into the cell above (knight on c3 read
  as d4 occupied). `vision.centre_frac` 0.6 → 0.4 helps; the real fix is a camera straight above the board.
- **Grasp signature must be judged against the close target**: with close=7.2 an empty close settles at
  7.3 and a hold at 7.7 — `stall_min` below the close target detects nothing. Now close 5.0, hold ≥ 5.8.
- **Paths across the board**: a joint-space bridge from rest, or "rest" sent from a low pose, sweeps pieces.
  `physical.approach` lifts straight up if low and enters over the corner nearest the arm (h1) at carry
  height; `physical.retreat` goes back over h1 at carry before folding to rest.

Result: 2.Nc3 (b1→c3) played by the arm from rest and back, verified by the camera at rest (b1 empty,
c3 occupied). Pick offset ended at [-0.7, -0.3] cm after the operator's step-mode corrections.

## Cameras: two identical Innomakers (2026-11-12/13)

Symptoms over two days: the laptop camera shown as "wrist", top and wrist swapped between page loads, the
wrist failing to start about 15 times in a row, OBS shown as "top", and finally both tiles showing the tower.
Each fix taught something:

- **OpenCV numbers cameras after sorting them by AVFoundation `uniqueID`** (`cap_avfoundation_mac.mm`:
  `sortedArrayUsingComparator … uniqueID compare`). The raw `devicesWithMediaType` order differs, and even flips
  after a camera is opened in the same process. Any listing used to choose an index must be sorted the same way
  (`camera_ids.devices`). Not sorting looked like "the IDs are random", and it was the root of the earlier
  confusion.
- **A plain index is fragile.** When one camera drops off the bus, or the OBS virtual camera sorts in between,
  another device slides into its slot. So `CAMS` uses `"model#n"`, which counts only devices of that model:
  the laptop and OBS can never be picked.
- **Identical cameras share one serial (`SN0001`).** The `uniqueID` (`0x21200000c456366`, …) encodes only the
  USB location (`0x212` = hub port 2). Pinning by uniqueID works until a cable moves, and then the views swap
  silently. Nothing in USB or AVFoundation tells the two apart.
- **What does tell them apart: motion.** While the arm moves, the wrist view changes everywhere; the tower
  only sees the arm cross part of its view. `camera_identity.Vote` sums thumbnail changes (48×27 grayscale,
  5 Hz, only while a joint moves ≥ 1.5°). On recorded takes, single rounds were sometimes fooled by hands or
  falling bricks in the tower view. Rounds of 20 samples, a 1.5× margin and **two agreeing rounds** gave no
  wrong verdict. After 6 rounds without agreement it gives up and asks the operator; it never guesses.
  Comparing against stored reference views did not work: a day later the scene had changed (balloons gone,
  paper down) and both orders looked equally unlike.
- **Two cameras must never share a device.** With one twin unplugged, positions shift: "wrist" took the only
  Innomaker, the other flickered back for a moment, and "top" landed on the same device. The bridge now passes
  the uids other cameras hold as `exclude` to the camera's child process. The child reports the uid it opened,
  and attaching is serialised. A camera back from a drop prefers its previous port.
- **Verify by looking, not by listing.** Grab a frame per index and look at it (`cv2.VideoCapture(i)` in a
  fresh process): the jaws close-up is the wrist. Every wrong conclusion above came from trusting a listing.
- **The hand choice wins.** **Assign…** (any device, any camera) and **⇄ Swap** are saved to
  `config/settings.json`. The identity check then stands down for that reconnect, and `CAM_AUTO_ID` switches
  it off for good.
- **A flaky camera is a cable or port problem.** The `0x212` camera dropped repeatedly. In the drawing take
  the wrist view is black for about a third of the time. Reseat it or move it off the hub before blaming
  software.

## Sharing a take (2026-11-13)

- Lab takes are one wide clip (tower | wrist, 960×540 each, 10 fps). Side by side they become tiny on a phone.
  **Stacked** (`vstack`) each view keeps its full width, and 1080×1216 (8:9) is within X/Twitter's accepted
  ratios (1:2.39 … 2.39:1).
- Speed with `setpts=PTS*target/duration`, then `fps=30`. The 10 fps source is then shown faster, with no
  invented frames. H.264 high profile, `yuv420p` and `+faststart` are what X accepts without re-encoding
  problems. The recipe is in the README ("Sharing a take").
- Lab recordings are mpeg4 at roughly 1 MB/10 s. For the README a 720-wide CRF 26 copy is about 2 MB a minute.

