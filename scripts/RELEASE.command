#!/bin/bash
# Double-click ONLY when the arm is resting on the table: switches motor torque off so it goes limp.
d="$(dirname "$0")/../var/cmd"; mkdir -p "$d"
printf '{"action":"release"}' > "$d/999_release.json.tmp" && mv "$d/999_release.json.tmp" "$d/999_release.json"
echo "Torque release requested."
