#!/usr/bin/env bash
set -euo pipefail

MUSIC_URL="${KOT_MUSIC_URL:-https://music.yandex.ru/}"
KOT_URL="${KOT_EDGE_URL:-http://127.0.0.1:8765/}"
KOT_EDGE_WAIT_SECONDS="${KOT_EDGE_WAIT_SECONDS:-60}"

if command -v chromium >/dev/null 2>&1; then
  BROWSER="chromium"
elif command -v chromium-browser >/dev/null 2>&1; then
  BROWSER="chromium-browser"
elif command -v google-chrome >/dev/null 2>&1; then
  BROWSER="google-chrome"
else
  echo "Chromium not found" >&2
  exit 1
fi

# XDG Autostart can run before the local API is ready. Wait for its TCP port so
# the Kot tab does not remain on Chromium's connection-error page after boot.
for ((attempt = 0; attempt < KOT_EDGE_WAIT_SECONDS; attempt++)); do
  if (exec 3<>/dev/tcp/127.0.0.1/8765) 2>/dev/null; then
    exec 3>&-
    break
  fi
  sleep 1
done

exec "$BROWSER" \
  --new-window \
  --start-maximized \
  --no-first-run \
  --disable-session-crashed-bubble \
  "$MUSIC_URL" \
  "$KOT_URL"
