#!/bin/bash
# Double-click in Finder: clears the ESTOP so the arm accepts commands again (it stays still until commanded).
rm -f "$(dirname "$0")/../ESTOP"
echo "ESTOP cleared. Arm holds position until a new command arrives."
