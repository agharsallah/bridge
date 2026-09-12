#!/bin/bash
# Double-click in Finder: freezes the arm immediately (creates the ESTOP file).
touch "$(dirname "$0")/ESTOP"
echo "ARM FROZEN. Close this window. Double-click RESUME.command to allow motion again."
