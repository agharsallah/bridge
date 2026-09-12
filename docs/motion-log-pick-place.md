# Engineering decision log — SO-101 pick-and-place (brick → tin)

Reference run: AUTO PICK, 2026-09-12 18:27:22–18:28:55 (bridge.py v3.3), second run 18:30–18:32 identical in structure.
Joint order used below: `pan / lift / elbow / wrist / gripper` (calibrated degrees; gripper 0–100).
Global limits in force for every movement: joint soft limits (pan ±91.8, lift −47…98, elbow −80.4…84.5, wrist ±83.8),
12° max per command, 4–5 °/s interpolation, motor caps (Goal_Velocity 300, Acceleration 15), stall guard (lag > 4° / 0.7 s),
per-joint load guard (300–450 ‰), floor guard (fingertip ≥ 8 mm for goals; freeze below 0), ESTOP file/button, gripper torque 50 %.

---

## M0 — Pre-flight (no motion)
- **Goal:** Confirm the system is allowed to move.
- **Sensor inputs:** Floor model status (fitted, rms 1.4 mm on 7 contact points); top-camera yellow-brick detection (bbox present within 3 s); ESTOP file absent; joint state readable.
- **Constraints:** Routine must refuse to start without a fitted floor model or a visible brick.
- **Reason:** The floor model is the only protection against pushing into the table; the top view proves the target exists.
- **Commands:** `{"action":"auto"}`; internal checks only.
- **Expected result:** `AUTO: start` logged, no motion.
- **Errors/corrections:** Earlier attempts failed here on a single dropped detection frame → check now waits up to 3 s for a detection.

## M1 — Move to READY (look-down pose above the workspace)
- **Goal:** Reach `pan −2 / lift 0 / elbow 70.5 / wrist 37.5 / gripper 60` from any start pose.
- **Sensor inputs:** Present joint angles; predicted fingertip height (≈3–5 cm at READY by the model).
- **Constraints:** ≤ 10° per sub-step (step cap 12°), 4 °/s, "reached" tolerance 2.5–3° (elbow gravity sag ≈ 1.5°).
- **Reason:** READY places the wrist camera looking straight down with the jaws open, giving a consistent view for alignment; if the arm starts at rest, a proven unfold path (lift −32 → −22 → −12 → −3 → 0 with elbow 80.7 → 70.5) avoids sweeping the gripper along the table.
- **Commands:** From rest: five absolute waypoints at 5 °/s. From elsewhere: capped-step convergence (`_goto_far`) to READY at 4 °/s. Gripper → 60.
- **Expected result:** Brick visible in the wrist image, joints within 3° of READY.
- **Errors/corrections:** A 1° tolerance previously caused an endless retry loop on elbow sag → tolerance raised to 2.5°.

## M2 — Coarse lateral alignment (pan servo)
- **Goal:** Put the brick's centre at x ≈ 470 px in the wrist image (between the fixed jaw at x≈380 and the moving jaw at x≈850).
- **Sensor inputs:** Wrist detection centre x (run 1: 411 → 445).
- **Constraints:** Pan step clamped to ±4°, 3 °/s, tolerance ±30 px, max 3 iterations; detection waited up to 1.5 s.
- **Reason:** pan+ shifts the scene left ≈ 0.04 °/px at this height; correcting laterally first keeps the brick centred while descending.
- **Commands:** `pan = present + 0.04 × (x − 470)` → e.g. pan −2.4 (relative).
- **Expected result:** |x − 470| ≤ 30 px.
- **Errors/corrections:** None in the reference run.

## M3 — Servo descent to grasp height (9 iterations)
- **Goal:** Lower the jaw tips to a predicted 1.0 cm above the table while keeping the brick centred (x → 540, y ≈ 300) between the jaws.
- **Sensor inputs:** Wrist detection (x, y, long side in px); floor-model fingertip height z from (lift, elbow, wrist); present joints.
- **Constraints:** Per iteration: pan ±4° (gain 0.04 °/px, ×0.6 below z = 8 cm), elbow ±6° (gain 0.07 °/px on y error, dead-band 60 px), lift step = min(5°, (z − 1 cm)/0.25 cm per °), wrist −0.7° per +1° lift; 4 °/s; abort if brick lost > 2 s or size collapses > 40 % four times; stop descending if lift would exceed 95°.
- **Reason:** Three decoupled effects: pan moves the brick laterally in the image; elbow unfolding moves it forward (down in the image); shoulder forward lowers the tips while the wrist compensation keeps the camera looking down. Height comes from the kinematic model, not from the image, because image size fails when the close-up brick washes out.
- **Commands (run 1, logged):**
  | it | brick x,y,size | tip z | goal issued |
  |---|---|---|---|
  | 0 | 445, 251, 253 px | 3.2 cm | pan −16.8, lift 4.4, wrist 34.1 |
  | 1 | 472, 231, 259 | 2.7 | pan −18.2, elbow 67.0, lift 9.3, wrist 31.2 |
  | 2 | 501, 273, 254 | 3.3 | pan −19.0, lift 14.1, wrist 28.3 |
  | 3 | 511, 252, 269 | 2.7 | lift 19.1, wrist 25.4 |
  | 4 | 510, 229, 288 | 2.1 | elbow 63.5, lift 23.8, wrist 23.0 |
  | 5 | 516, 277, 289 | 2.6 | lift 29.0, wrist 20.0 |
  | 6 | 514, 252, 312 | 1.9 | lift 32.9, wrist 18.3 |
  | 7 | 513, 232, 330 | 1.4 | elbow 59.9, lift 34.8, wrist 17.8 |
  | 8 | 520, 291, 322 | 2.0 | lift 39.1, wrist 15.4 |
  | end | —, —, 351 | 1.3 | stop: z ≤ 1.4 cm, |x−540| ≤ 30, |y−300| ≤ 90 |
- **Expected result:** z ≤ 1.4 cm with the brick centred and ≥ 280 px long (sanity check that it is really between the jaws).
- **Errors/corrections:** Elbow unfolding raises the tips slightly, so z oscillates by a few mm between iterations — accepted, the loop converges. Earlier versions without the floor model descended until table contact; the model-based stop removed that failure.

## M4 — Grasp
- **Goal:** Close the jaws on the brick and verify the hold.
- **Sensor inputs:** Gripper present position over time (settles when it stalls); gripper load.
- **Constraints:** Goal 10 at 15 units/s; gripper torque 50 %, protection current 50 %; hold must settle for 0.8 s; stalled value must be ≥ 14 or the grasp is declared missed and the jaws reopen.
- **Reason:** The Duplo brick is 32 mm wide; the jaws stall at ≈ 20 on it and close to < 14 on nothing, so the stall position is a reliable grasp sensor without force feedback.
- **Commands:** `gripper → 10`; wait for stability.
- **Expected result:** `holding (gripper stalled at ≈ 20)`.
- **Errors/corrections:** Run 1 and 2 both stalled at 20.4. Missed-grasp path (open, abort) never triggered.

## M5 — Lift
- **Goal:** Raise the brick clear of the table and the tin rim (~5.5 cm).
- **Sensor inputs:** Present joints; floor-model z (rises to ≈ 6–10 cm).
- **Constraints:** Two relative steps of lift −10° with wrist +5° each, 4 °/s; step cap respected.
- **Reason:** Shoulder back is the purest "up" motion in this pose; the wrist compensation keeps the brick from tilting toward the table.
- **Commands:** `lift −10, wrist +5` ×2 (relative).
- **Expected result:** Brick still in the jaws (gripper reading unchanged), z > 6 cm.
- **Errors/corrections:** None. (In the manual run the brick pivoted to hang vertically in the jaws during a forward move — harmless for the drop.)

## M6 — Transit to the drop pose
- **Goal:** Reach the confirmed drop pose `pan 6.8 / lift 26.5 / elbow 47.7 / wrist 5` above the tin opening.
- **Sensor inputs:** Present joints only (tin position assumed unchanged; drop pose was confirmed by the operator in the manual run).
- **Constraints:** ≤ 10° per sub-step, 4 °/s, all guards active; predicted z ≈ 10–11 cm along the way.
- **Reason:** Joint-space replay of a validated pose is the most predictable path over the rim; the wrist camera is offset from the jaws and gave misleading XY for the tin, so vision is not used here.
- **Commands:** capped-step convergence to the drop pose.
- **Expected result:** Tin visible below the jaws in the wrist image; gripper still ≈ 20.
- **Errors/corrections:** None. Limitation: if the tin is moved, this pose must be re-taught (Save waypoint).

## M7 — Release
- **Goal:** Drop the brick into the tin.
- **Sensor inputs:** Abort flag / ESTOP (checked during a 1 s pause); gripper position.
- **Constraints:** 1 s hold before opening so a human can STOP; open at 25 units/s to 60.
- **Reason:** A deliberate pause is the last human veto before an irreversible action.
- **Commands:** wait 1 s → `gripper → 60` → wait 0.8 s.
- **Expected result:** Brick visible inside the tin in the wrist image; `AUTO: released`.
- **Errors/corrections:** None (both runs).

## M8 — Retreat
- **Goal:** Withdraw from above the tin without sweeping the rim, close the gripper, fold toward rest.
- **Sensor inputs:** Present joints; floor-model z.
- **Constraints:** Proven waypoint chain at 5 °/s: `lift 20/elbow 56/wrist 5` → `pan −2, gripper 10` → `lift 10/elbow 66/wrist 15` → `lift 0/elbow 68.5/wrist 22` → `lift −10/elbow 72/wrist 30` → `lift −20/elbow 77/wrist 35` → `lift −30/elbow 80/wrist 37`; each step ≤ 12°.
- **Reason:** First rise and pull back (elbow refold), then pan to the rest heading, then fold down in the same sequence used successfully by hand — no new geometry.
- **Commands:** the seven absolute waypoints above.
- **Expected result:** Arm folded above its rest position, gripper closed.
- **Errors/corrections:** None.

## M9 — Rest
- **Goal:** Settle on the operator-defined rest pose (`rest.json`: −11.8 / −45.7 / 83.3 / 54.1 / gripper 2.7), which sits against the mechanical stops.
- **Sensor inputs:** Present joints; rest.json.
- **Constraints:** Capped-step convergence at 4 °/s; soft limits widened to −47 (lift) and 84.5 (elbow) so the stops can be reached; torque stays on.
- **Reason:** A configurable rest pose lets the operator choose a stable parked configuration; reaching the stops requires the limits to include them.
- **Commands:** `_goto_far(rest)`.
- **Expected result:** `AUTO: done`; joints within 3° of rest.json.
- **Errors/corrections:** None. Torque may be released afterwards only with the arm on the table (RELEASE).

---

## Movement-level lessons
1. Align laterally first (pan), then reach (elbow), then height (shoulder + wrist compensation) — the effects are nearly decoupled in the wrist image.
2. Use the kinematic floor model for height; use the image only for lateral/forward alignment and as a plausibility check.
3. Gripper stall position is a sufficient grasp sensor for a rigid object of known width.
4. Replay validated joint poses for the transport/drop phase; re-teach them if the target container moves.
5. Insert a deliberate pause before irreversible actions (release) and keep all guards active during replayed segments.
