"""Hardware identity and tuning constants — the only file to edit when the setup changes."""

# ------------------------------------------------------------------------------- hardware
PORT = "/dev/cu.usbmodem5A460836731"
ROBOT_ID = "my_follower"
CAMS = {"top": 0, "wrist": 1}
HTTP_PORT = 8765
LOOP_HZ = 30
VISION_EVERY = 3            # process/encode frames every N loops (~10 fps)
STATE_EVERY = 0.2
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
ARM = JOINTS[:-1]
DEFAULT_SPEED = {j: 5.0 for j in JOINTS}; DEFAULT_SPEED["gripper"] = 20.0
MAX_SPEED = 30.0
CAL_MARGIN_DEG = 3.0
MAX_STEP_DEG = 12.0
LOAD_LIMIT = {"shoulder_pan": 300, "shoulder_lift": 400, "elbow_flex": 450, "wrist_flex": 300, "wrist_roll": 300}
STALL_DEG, STALL_SEC = 4.0, 0.7
REACH_TOL = 3.0             # deg; elbow sags ~2 deg under gravity even at P=48
MOTOR_GOAL_VELOCITY, MOTOR_ACCEL = 300, 15
P_GAIN = {"elbow_flex": 48}; P_GAIN_DEFAULT = 32          # lerobot's 16 is too weak for small steps vs gravity

# ------------------------------------------------------- wrist-camera servo tuning (AUTO PICK)
# Targets are in the 960x540 frame the detector runs on: brick centre x between the jaws,
# from READY (top) to GRASP.
SERVO_X_TOP, SERVO_X_BOTTOM, SERVO_TOL = 470, 540, 30
SERVO_GAIN_DEG_PER_PX = 0.04            # pan+  -> scene shifts left in the wrist image
GRASP_SIZE_PX = 360                     # brick long side in px when the jaws are at brick height
CY_MIN, CY_MAX = 170, 340               # brick centre y band along the approach direction
CY_READY = 280                          # brick centre y at READY in the manual run (reach reference)
REACH_GAIN_DEG_PER_PX = 0.07            # elbow-  (unfold) -> brick moves DOWN in the wrist image
SERVO_MAX_ITERS = 30
CY_TARGET = 300                         # brick centre y we aim for during the approach (jaw tips ~ y 350-400)
LIFT_STEP, LIFT_MAX = 5.0, 95.0         # shoulder step per iteration (down) and forward cap (limits.json allows 98)
WRIST_PER_LIFT = 0.7                    # wrist- per lift+ to keep the camera looking down (READY->GRASP data)

