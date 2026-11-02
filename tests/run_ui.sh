#!/bin/bash
# Runs the UI tests on a private Broadway display, so nothing appears on the desktop or takes focus.
# Usage: tests/run_ui.sh [screenshot-dir]
cd "$(dirname "$0")/.." || exit 1
display=$((RANDOM % 400 + 100))
gtk4-broadwayd ":$display" >/dev/null 2>&1 &
daemon=$!
trap 'kill "$daemon" 2>/dev/null' EXIT
sleep 0.4
GDK_BACKEND=broadway BROADWAY_DISPLAY=":$display" GSK_RENDERER=cairo \
  timeout 90 /usr/bin/python3 tests/ui_driver.py "$@"
