#!/usr/bin/env bash
# Delayed gateway restart helper invoked by hermes-auto-update-external.sh.
#
# Sleeps long enough for cron's update-result message to deliver, then
# restarts hermes-gateway.  Keeps the restart out of the cron-driven update
# task so that "interpreter shutdown" race conditions can't kill the
# Telegram/Weixin delivery.

set -u
export PATH="/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/home/yuchuan/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/yuchuan"

DELAY_S="${HERMES_RESTART_DELAY_SECONDS:-180}"

LOG="$HOME/.hermes/logs/auto-update-restart.log"
mkdir -p "$(dirname "$LOG")"
{
  echo "=== auto-update-restart $(date -Is) ==="
  echo "sleeping ${DELAY_S}s before restart"
  sleep "$DELAY_S"
  echo "restarting hermes-gateway $(date -Is)"
  /home/yuchuan/.local/bin/hermes gateway restart
  echo "exit=$?"
} >> "$LOG" 2>&1
