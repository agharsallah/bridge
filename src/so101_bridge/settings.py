"""Hardware identity and tuning constants.

Defaults live here; a deployment overrides them without touching code by putting the same UPPER_CASE
names into ``config/settings.json`` (dict values are merged key by key, everything else replaced).
``config/settings.example.json`` lists every key. The names that were overridden are kept in
``OVERRIDDEN`` so the dashboard can show them.
"""

import json
import os

# ------------------------------------------------------------------------------- hardware
PORT = "/dev/cu.usbmodem5A460836731"
ROBOT_ID = "my_follower"
CAMS = {"top": 0, "wrist": 1}
CAM_W, CAM_H, CAM_FPS = 1920, 1080, 30
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


# ------------------------------------------------------- brick detector (vision.py) — HSV gates, 960x540 frame
TARGET_HSV = {"h": [14, 40], "s_min": 40, "v_min": 80}   # yellow Duplo; sat drops to ~45 when the brick fills the view
TARGET_MIN_AREA = 600
TARGET_SOLIDITY, TARGET_FILL = 0.7, 0.5
FRAME_W, FRAME_H = 960, 540
JPEG_QUALITY = 75

AUTO_RECORD_ROUTINES = True   # record both cameras to var/videos/ while a routine runs

# ------------------------------------------------------- paint overlay (paint/vision.py) — same 960x540 frame
PAPER_S_MAX, PAPER_V_MIN = 60, 150         # paper: low saturation, bright -> whatever the sheet colour
PAPER_MIN_AREA = 4000
PAPER_SOLIDITY = 0.85                       # paper is a clean rectangle, unlike the brick's studded top
PAPER_RECT_FILL = 0.9                       # area / rotated-bbox area: rejects round trays (~0.78), keeps sheets
WELL_HUE_TOL, WELL_S_MIN, WELL_V_MIN = 12, 60, 50   # +/- hue window around each taught station's colour
WELL_MIN_AREA = 80

# ------------------------------------------------------- overrides from config/settings.json
OVERRIDDEN = []


def _apply_overrides():
    from .paths import SETTINGS_FILE
    path = os.environ.get("SO101_SETTINGS", str(SETTINGS_FILE))
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return
    except Exception as e:                      # malformed file: run on defaults, say so loudly
        print(f"settings: {path} unreadable ({e}) -> defaults", flush=True)
        return
    g = globals()
    for key, value in data.items():
        if key.startswith("_") or key not in g or key in ("OVERRIDDEN",):
            print(f"settings: ignoring unknown key {key!r}", flush=True); continue
        if isinstance(g[key], dict) and isinstance(value, dict):
            g[key] = {**g[key], **value}
        else:
            g[key] = value
        OVERRIDDEN.append(key)
    if "JOINTS" in OVERRIDDEN:
        g["ARM"] = g["JOINTS"][:-1]


_apply_overrides()
