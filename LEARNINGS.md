# SO-101 follower — control bridge: what we learned (2026-09-12)

Setup: MacBook Pro (Apple Silicon), lerobot 0.6.2 (uv venv, Python 3.12), SO-101 follower on
`/dev/cu.usbmodem5A460836731`, calibration id `my_follower`, cameras: OpenCV index 0 = overhead ("top"),
index 1 = gripper ("wrist"), both Innomaker U20CAM 1080p (indices can swap after re-plugging: check the
dashboard). Bridge: `uv run python bridge.py` from this directory, dashboard http://localhost:8765.

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
Files: bridge.py (v3.3), limits.json, rest.json, floor_points.json, waypoints.json, bridge.log.
