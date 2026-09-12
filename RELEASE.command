#!/bin/bash
# Double-click ONLY when the arm is resting on the table: switches motor torque off so it goes limp.
d="$(dirname "$0")"; echo '{"action":"release"}' > "$d/cmd/999_release.json"
echo "Torque release requested."
