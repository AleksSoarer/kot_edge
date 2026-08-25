#!/usr/bin/env bash
set -euo pipefail

URL="${KOT_EDGE_URL:-http://127.0.0.1:8765/}"

if command -v chromium >/dev/null 2>&1; then
  exec chromium \
    --app="$URL" \
    --start-maximized \
    --no-first-run \
    --disable-session-crashed-bubble
elif command -v chromium-browser >/dev/null 2>&1; then
  exec chromium-browser \
    --app="$URL" \
    --start-maximized \
    --no-first-run \
    --disable-session-crashed-bubble
else
  echo "Chromium not found" >&2
  exit 1
fi
