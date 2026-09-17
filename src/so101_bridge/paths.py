"""Filesystem layout.

Everything the daemon reads or writes is resolved from the repository root, so the package works
the same whether it is started with ``uv run so101-bridge`` or ``python -m so101_bridge``.

    config/   user-editable, version-controlled  (limits, rest pose, floor contacts, taught workspaces)
    var/      runtime state, never version-controlled  (log, state, command queue, recordings, games)
    ESTOP     emergency stop flag, kept at the repository root so it is one short `touch` away
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
VAR_DIR = ROOT / "var"

# --- configuration (tracked)
LIMITS_FILE = CONFIG_DIR / "limits.json"
REST_FILE = CONFIG_DIR / "rest.json"
FLOOR_FILE = CONFIG_DIR / "floor_points.json"
FLOOR_CFG = CONFIG_DIR / "floor_config.json"   # optional: {"z0": shoulder height (m), "L1", "L2", "L3"}
WP_FILE = CONFIG_DIR / "waypoints.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"   # optional overrides of settings.py (see config/settings.example.json)
POSES_FILE = CONFIG_DIR / "poses.json"         # optional overrides of the taught paths in poses.py
CHESS_FILE = CONFIG_DIR / "chess.json"         # taught chess workspace (board corners, pieces, slots, heights)
PIECES_FILE = CONFIG_DIR / "pieces.json"        # the chess set: piece heights in cm
TEACH_DIR = CONFIG_DIR / "teach"               # guided teaching sessions: <name>.json, see shared/teach.py

# --- runtime (ignored)
CMD_DIR = VAR_DIR / "cmd"
DONE_DIR = VAR_DIR / "done"
REC_DIR = VAR_DIR / "recordings"
STATE_FILE = VAR_DIR / "state.json"
LOG_FILE = VAR_DIR / "bridge.log"
GAMES_DIR = VAR_DIR / "games"                  # one JSON per chess game (append-only) + a .pgn mirror
SNAPSHOT = {"top": VAR_DIR / "top.jpg", "wrist": VAR_DIR / "wrist.jpg"}

PUZZLES_DIR = ROOT / "puzzles"                 # chess puzzles: {"fen", "robot", "solution": [uci, ...]}

ESTOP = ROOT / "ESTOP"


def ensure_dirs() -> None:
    """Create the runtime directories; called once at start-up."""
    for d in (CONFIG_DIR, VAR_DIR, CMD_DIR, DONE_DIR, REC_DIR, TEACH_DIR, GAMES_DIR):
        d.mkdir(parents=True, exist_ok=True)
