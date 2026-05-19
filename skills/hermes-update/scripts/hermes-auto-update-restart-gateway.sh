#!/usr/bin/env bash
# Delayed gateway restart helper invoked by hermes-auto-update-external.sh.
#
# Sleeps long enough for cron's update-result message to deliver, then
# restarts hermes-gateway.  Keeps the restart out of the cron-driven update
# task so that "interpreter shutdown" race conditions can't kill the
# Telegram/Weixin delivery.

set -u

: "${HOME:?HOME must be set to run hermes-auto-update-restart-gateway.sh}"

_extra_path="$HOME/.local/bin:/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/opt/homebrew/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
if [ -n "${PATH:-}" ]; then
  export PATH="$_extra_path:$PATH"
else
  export PATH="$_extra_path"
fi
unset _extra_path

HERMES_BIN="${HERMES_BIN:-$(command -v hermes 2>/dev/null || true)}"
if [ -z "$HERMES_BIN" ] || [ ! -x "$HERMES_BIN" ]; then
  for _candidate in "$HOME/.local/bin/hermes" "/usr/local/bin/hermes" "/opt/homebrew/bin/hermes"; do
    if [ -x "$_candidate" ]; then
      HERMES_BIN="$_candidate"
      break
    fi
  done
fi

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DELAY_S="${HERMES_RESTART_DELAY_SECONDS:-180}"

LOG="$HERMES_HOME/logs/auto-update-restart.log"
mkdir -p "$(dirname "$LOG")"
{
  echo "=== auto-update-restart $(date -Is) ==="
  if [ -z "$HERMES_BIN" ] || [ ! -x "$HERMES_BIN" ]; then
    echo "hermes binary not found on PATH; skipping restart" >&2
    exit 1
  fi
  echo "sleeping ${DELAY_S}s before restart"
  sleep "$DELAY_S"
  echo "restarting hermes-gateway $(date -Is)"
  "$HERMES_BIN" gateway restart
  echo "exit=$?"
} >> "$LOG" 2>&1
