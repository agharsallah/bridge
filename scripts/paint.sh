#!/bin/sh
# Replay a painting without an agent or the browser — only the running bridge and this shell.
#   scripts/paint.sh landscape              # compile paintings/landscape.plan.json -> landscape.json and run it
#   scripts/paint.sh landscape 12           # resume from stroke 12 (rinse/dip before it included)
#   DRY=1 scripts/paint.sh landscape        # same moves at hover height, no dips
# Preconditions (see docs/operations.md §11): bridge running, brush in the gripper, paper and pans where
# they were taught (config/painting.json), probes calibrated for the current motor gains. The run records
# both cameras to var/videos/ automatically. STOP: dashboard button, scripts/STOP.command or `touch ESTOP`.
set -e
cd "$(dirname "$0")/.."
name=${1:?usage: scripts/paint.sh <plan-name> [from_stroke]}
from=${2:-}
send() { n=$(date +%s%N | cut -c1-13); printf '%s' "$1" > var/cmd/x.tmp && mv var/cmd/x.tmp "var/cmd/$n.json"; sleep 0.5; }
if [ "$DRY" = 1 ]; then dry=true; prog="${name}_dry"; else dry=false; prog="$name"; fi
send "{\"action\":\"paint_compile\",\"plan\":\"paintings/$name.plan.json\",\"name\":\"$prog\",\"dry\":$dry}"
sleep 2; tail -1 var/bridge.log
if [ -n "$from" ]; then send "{\"action\":\"paint\",\"program\":\"$prog\",\"from_stroke\":$from}"
else send "{\"action\":\"paint\",\"program\":\"$prog\"}"; fi
echo "started $prog — follow with: watch -n2 'python3 -c \"import json;s=json.load(open(\\\"var/state.json\\\"));print(s[\\\"auto\\\"],s[\\\"progress\\\"][\\\"paint\\\"])\"'"
