#!/bin/sh
# Drive a chess game from a terminal — only the running bridge and this shell, no agent, no browser.
#
#   scripts/chess.sh teach                 # start the guided teaching session for the board
#   scripts/chess.sh done                  # record the current teaching step and move on
#   scripts/chess.sh reach                 # teach by hand the squares the model says it cannot reach
#   scripts/chess.sh free | engage         # torque off / on while teaching
#   scripts/chess.sh compile               # fit the board, print the 64-square reachability report
#   scripts/chess.sh verify                # drive the arm to every taught point (arm moves!)
#   scripts/chess.sh goto e4 [z_cm]        # put the jaws over a square (arm moves!)
#   scripts/chess.sh setup                 # place the start position from pieces fed to the feed slot
#   scripts/chess.sh new [white] [black]   # new game; players: human | llm | engine  (default human llm)
#   scripts/chess.sh play                  # start/resume the game loop
#   scripts/chess.sh wait                  # block until it is the LLM's turn (or the game ends)
#   scripts/chess.sh state                 # the game document the LLM reads (~300 bytes)
#   scripts/chess.sh move e7e5             # play that move (robot executes it if it is the robot's side)
#   scripts/chess.sh confirm               # "yes, that is what I played" / "I fixed the board"
#   scripts/chess.sh abort                 # stop the routine, hold position
#   scripts/chess.sh reference             # capture the EMPTY board - every camera reading is a diff from it
#   scripts/chess.sh learned               # what the history says: grasp success, bad squares, suggestions
#   scripts/chess.sh apply <id>            # apply one suggestion to config/chess.json
#
# STOP at any moment: the dashboard button, scripts/STOP.command, or `touch ESTOP`.
set -e
cd "$(dirname "$0")/.."
send() { n=$(date +%s%N | cut -c1-13); printf '%s' "$1" > var/cmd/x.tmp && mv var/cmd/x.tmp "var/cmd/$n.json"; sleep 0.4; }
state() { python3 -c "import json,sys;print(json.dumps(json.load(open('var/state.json')).get('chess') or {},indent=1))"; }
gamefile() {
  python3 - <<'PY'
import json, pathlib
games = sorted(pathlib.Path("var/games").glob("*.json"), key=lambda p: p.stat().st_mtime) if pathlib.Path("var/games").is_dir() else []
print(games[-1] if games else "", end="")
PY
}

cmd=${1:-state}
case "$cmd" in
  teach)   send '{"action":"teach_start","session":"chess_board"}'; tail -2 var/bridge.log ;;
  reach)   send '{"action":"teach_reach"}'; sleep 0.8; tail -3 var/bridge.log ;;
  done)    send '{"action":"teach_done"}'; sleep 0.3; tail -2 var/bridge.log ;;
  skip)    send '{"action":"teach_skip"}' ;;
  back)    send '{"action":"teach_back"}' ;;
  free)    send '{"action":"teach_free"}' ;;
  engage)  send '{"action":"teach_engage"}' ;;
  compile) send '{"action":"chess_compile"}'; sleep 1.5; tail -25 var/bridge.log ;;
  verify)  send '{"action":"chess_verify"}' ;;
  goto)    send "{\"action\":\"chess_goto\",\"square\":\"${2:?usage: chess.sh goto <square> [z_cm]}\",\"z\":${3:-6}}" ;;
  setup)   send "{\"action\":\"chess_setup\",\"dry\":${DRY:-false}}" ;;
  new)     send "{\"action\":\"chess_new\",\"players\":{\"white\":\"${2:-human}\",\"black\":\"${3:-llm}\"}}"; sleep 0.5; tail -1 var/bridge.log ;;
  play)    send "{\"action\":\"chess_play\",\"dry\":${DRY:-false}}" ;;
  puzzle)  send "{\"action\":\"chess_puzzle\",\"puzzle\":\"${2:?usage: chess.sh puzzle <name>}\"}" ;;
  move)    send "{\"action\":\"chess_move\",\"move\":\"${2:?usage: chess.sh move <uci>, e.g. e2e4}\"}" ;;
  confirm) send '{"action":"chess_confirm"}' ;;
  abort)   send '{"action":"abort"}' ;;
  reference) send '{"action":"chess_reference"}'; sleep 0.6; tail -1 var/bridge.log ;;
  learned) exec python3 scripts/chess_learned.py ;;
  apply)   send "{\"action\":\"chess_apply\",\"id\":\"${2:?usage: chess.sh apply <suggestion-id>}\"}"; sleep 0.6; tail -2 var/bridge.log ;;
  state)   state ;;
  game)    f=$(gamefile); [ -n "$f" ] && cat "$f" || echo "no game yet" ;;
  wait)
    # Block until the game wants the LLM (or an engine/script hands back), or the game is over.
    while :; do
      python3 - <<'PY' && exit 0
import json, sys
try: s = json.load(open("var/state.json")).get("chess") or {}
except Exception: sys.exit(1)
if s.get("result"): print("game over:", s["result"]); raise SystemExit(0)
if s.get("waiting_for") == "llm" or (s.get("needs") or {}).get("kind"):
    print(json.dumps({k: s.get(k) for k in ("id", "fen", "turn", "waiting_for", "needs")}, indent=1)); raise SystemExit(0)
sys.exit(1)
PY
      sleep 1
    done ;;
  *) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 1 ;;
esac
