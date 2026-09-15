#!/usr/bin/env bash
# Manual smoke test for a built Interlock3D executable: launches it under
# a virtual display, drives the real "Open STL(s)..." dialog with actual
# X11 input events (not a test hook), and screenshots the result so you
# can confirm the mesh actually rendered rather than just that the
# process didn't crash.
#
# Linux-only (uses Xvfb + xdotool + ImageMagick's `import`) -- this is
# for sanity-checking a Linux build specifically. On Windows/Mac, just
# run the built app and try it directly; there's a real display to look
# at.
#
# Usage: packaging/smoke_test.sh [path/to/Interlock3D/executable]
set -euo pipefail

EXE="${1:-dist/Interlock3D/Interlock3D}"
TEST_STL="${2:-../test_data/known/plate_with_matched_hole.stl}"
DISPLAY_NUM=99
SCREEN="1300x900x24"

if [ ! -x "$EXE" ]; then
    echo "Executable not found or not executable: $EXE" >&2
    echo "Usage: $0 [path/to/Interlock3D/executable] [path/to/test.stl]" >&2
    exit 1
fi

rm -f "/tmp/.X${DISPLAY_NUM}-lock"
Xvfb ":${DISPLAY_NUM}" -screen 0 "$SCREEN" &
XVFB_PID=$!
sleep 2
export DISPLAY=":${DISPLAY_NUM}"

cleanup() {
    kill "$APP_PID" 2>/dev/null || true
    kill "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT

"$EXE" &
APP_PID=$!
sleep 5
if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "FAIL: app exited immediately after launch (check for a startup crash)" >&2
    exit 1
fi
echo "OK: app launched and is still running"

import -window root /tmp/interlock3d_smoke_launched.png
echo "Screenshot: /tmp/interlock3d_smoke_launched.png"

# Click "Open STL(s)..." (top-left toolbar button) and type a file path
# into the resulting dialog.
xdotool mousemove 45 14 click 1
sleep 2
xdotool key --clearmodifiers ctrl+a
xdotool type --delay 20 "$(realpath "$TEST_STL")"
sleep 1
xdotool key Return
sleep 3

if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "FAIL: app crashed while loading an STL" >&2
    exit 1
fi
echo "OK: app still running after loading a test STL"

import -window root /tmp/interlock3d_smoke_loaded.png
echo "Screenshot: /tmp/interlock3d_smoke_loaded.png"
echo ""
echo "Check both screenshots: the second one should show a rendered mesh"
echo "with colored feature overlays, not a blank/black viewport."
