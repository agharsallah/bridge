"""Proven joint poses (degrees; gripper 0..100) and the configurable rest pose.

The waypoint lists are the paths that were validated on the real arm — see docs/learnings.md.
"""

import json

from .paths import REST_FILE
from .settings import JOINTS
from .util import log


def P(pan=None, lift=None, elbow=None, wrist=None, roll=None, grip=None):
    d = {"shoulder_pan": pan, "shoulder_lift": lift, "elbow_flex": elbow, "wrist_flex": wrist,
         "wrist_roll": roll, "gripper": grip}
    return {k: v for k, v in d.items() if v is not None}


REST_DEFAULT = P(pan=-2, lift=-42.5, elbow=80.7, wrist=37.1, roll=-7.5, grip=10)


def load_rest():
    """The rest pose from config/rest.json, falling back to the built-in default."""
    try:
        r = json.loads(REST_FILE.read_text()) if REST_FILE.is_file() else {}
        return {**REST_DEFAULT, **{k: float(v) for k, v in r.items() if k in JOINTS}}
    except Exception as e:
        log(f"config/rest.json unreadable ({e}) -> default"); return dict(REST_DEFAULT)


UNFOLD = [P(lift=-32, elbow=80.7, wrist=37), P(lift=-22, elbow=78, wrist=36), P(lift=-12, elbow=74, wrist=35),
          P(lift=-3, elbow=70.5, wrist=36), P(lift=0, elbow=70.5, wrist=37.5, grip=60)]                       # READY: looking straight down
DESCENT = [P(lift=10, elbow=66, wrist=37.5), P(lift=18, elbow=61, wrist=37.5), P(lift=26, elbow=57, wrist=36),
           P(lift=34, elbow=54, wrist=32), P(lift=40.4, elbow=53.6, wrist=28), P(lift=41.2, elbow=52, wrist=24),
           P(lift=41.5, elbow=58, wrist=18), P(lift=42, elbow=64, wrist=12), P(lift=42.1, elbow=69, wrist=8)]
LIFT = [P(lift=33, elbow=65, wrist=8), P(lift=24, elbow=65, wrist=8), P(lift=13.5, elbow=69.4, wrist=8)]
TRANSIT = [P(pan=5), P(lift=13.8, elbow=62.3, wrist=5), P(lift=18.2, elbow=54.1, wrist=5),
           P(pan=6.8, lift=26.5, elbow=47.7, wrist=5)]                     # DROP pose (confirmed today)
RETREAT = [P(lift=20, elbow=56, wrist=5), P(pan=-2, grip=10), P(lift=10, elbow=66, wrist=15),
           P(lift=0, elbow=68.5, wrist=22), P(lift=-10, elbow=72, wrist=30), P(lift=-20, elbow=77, wrist=35),
           P(lift=-30, elbow=80, wrist=37), P(lift=-42, elbow=80.7, wrist=37)]
