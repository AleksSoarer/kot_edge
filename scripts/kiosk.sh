#!/usr/bin/env bash
set -euo pipefail

MUSIC_URL="${KOT_MUSIC_URL:-https://music.yandex.com/}"
KOT_URL="${KOT_EDGE_URL:-http://127.0.0.1:8765/}"
KOT_EDGE_WAIT_SECONDS="${KOT_EDGE_WAIT_SECONDS:-60}"
MUSIC_WARMUP_SECONDS="${KOT_MUSIC_WARMUP_SECONDS:-20}"
MUSIC_STABILIZE_SECONDS="${KOT_MUSIC_STABILIZE_SECONDS:-3}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MUSIC_EXTENSION_DIR="$SCRIPT_DIR/yandex-music-bootstrap"

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

# Start Yandex Music as the foreground tab first. When it is opened immediately
# in the background, Chromium can defer page/MPRIS initialization indefinitely.
"$BROWSER" \
  --new-window \
  --start-maximized \
  --no-first-run \
  --disable-session-crashed-bubble \
  --disable-background-timer-throttling \
  --disable-renderer-backgrounding \
  --autoplay-policy=no-user-gesture-required \
  --load-extension="$MUSIC_EXTENSION_DIR" \
  "$MUSIC_URL" &

browser_pid=$!

# The extension clicks Play once so that Chromium creates its MPRIS object.
# Pause it as soon as playerctl can see it; Kot can then start it by voice.
player_ready=0
for ((attempt = 0; attempt < MUSIC_WARMUP_SECONDS * 4; attempt++)); do
  player_name="$(
    playerctl -l 2>/dev/null \
      | grep -E '^(chromium|google-chrome)(\.|$)' \
      | head -n 1 \
      || true
  )"
  if [[ -n "$player_name" ]]; then
    player_status="$(playerctl --player="$player_name" status 2>/dev/null || true)"
    player_title="$(
      playerctl --player="$player_name" metadata --format '{{title}}' 2>/dev/null \
        || true
    )"
    if [[ "$player_status" == "Paused" && -n "$player_title" ]]; then
      player_ready=1
      break
    fi
    if [[ "$player_status" == "Playing" && -n "$player_title" ]]; then
      # Pausing immediately after MPRIS appears can make Yandex discard the
      # not-yet-stable queue and return to Stopped. Let the first track settle.
      sleep "$MUSIC_STABILIZE_SECONDS"
      playerctl --player="$player_name" pause >/dev/null 2>&1 || true
      sleep 0.5
      player_status="$(playerctl --player="$player_name" status 2>/dev/null || true)"
      player_title="$(
        playerctl --player="$player_name" metadata --format '{{title}}' 2>/dev/null \
          || true
      )"
      if [[ "$player_status" == "Paused" && -n "$player_title" ]]; then
        player_ready=1
        break
      fi
    fi
  fi
  sleep 0.25
done

if ((player_ready == 0)); then
  echo "Yandex Music did not prepare a playable MPRIS queue in ${MUSIC_WARMUP_SECONDS}s" >&2
else
  echo "Yandex Music ready: $player_title ($player_status)"
fi

# A second invocation uses the existing Chromium profile/window and opens Kot
# as the active tab. Yandex Music remains initialized in the background.
"$BROWSER" "$KOT_URL"

# Some Chromium packages keep the first launcher attached, others fork and
# return immediately. Both behaviours are valid for XDG Autostart.
wait "$browser_pid" || true
