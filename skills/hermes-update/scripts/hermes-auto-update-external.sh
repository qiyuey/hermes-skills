#!/usr/bin/env bash
# Hermes external auto-update.
#
# Runs daily via the hermes-auto-update.timer systemd user unit.  The flow is:
#
#   1. Snapshot working-tree state and current vs origin/main
#   2. Recover any unstaged changes that match a registered local patch
#      (so they become real commits before `hermes update` autostashes them)
#   3. Run `hermes update` with HERMES_UPDATE_SKIP_GATEWAY_RESTART=1
#   4. Verify all registered patches via the patch engine; cherry-pick any
#      that are missing.  IMPORTANT: never bail out on a single conflict —
#      report and move on so unrelated patches still get applied.
#   5. Schedule a delayed gateway restart (so cron's report can deliver
#      before the gateway is taken down)
#   6. Write a JSON status file consumed by `hermes-auto-update-report.py`
#
# Failure modes reported via `~/.hermes/state/auto-update/latest.json`:
#   no_update          - already up-to-date
#   skipped_locked     - another instance held the lock
#   update_failed      - `hermes update` exited non-zero
#   patch_failed       - some local patch could not be re-applied
#   updated            - update + patch verification both succeeded

set -u

export PATH="/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/home/yuchuan/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/home/yuchuan"

HERMES_BIN="/home/yuchuan/.local/bin/hermes"
REPO="$HOME/.hermes/hermes-agent"
PATCH_ENGINE="$HOME/.hermes/scripts/hermes-local-patches.py"
RESTART_HELPER="$HOME/.hermes/scripts/hermes-auto-update-restart-gateway.sh"
LOG_DIR="$HOME/.hermes/logs/auto-update"
STATUS_DIR="$HOME/.hermes/state/auto-update"
LOCK="$STATUS_DIR/update.lock"
mkdir -p "$LOG_DIR" "$STATUS_DIR"

RUN_ID="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/$RUN_ID.log"
STATUS_TMP="$STATUS_DIR/latest.json.tmp"
STATUS="$STATUS_DIR/latest.json"

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "Another update is already running" > "$LOG"
  python3 - <<PY
import json, time, pathlib
path = pathlib.Path('$STATUS')
tmp = pathlib.Path('$STATUS_TMP')
data = {'run_id':'$RUN_ID','status':'skipped_locked','log':'$LOG','started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),'ended_at':time.strftime('%Y-%m-%dT%H:%M:%S%z')}
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2)); tmp.replace(path)
PY
  exit 0
fi

# Capture the current rendered patch report into a file under the run log
# directory, so the human-readable log + status JSON both reference the same
# source of truth.
PATCH_REPORT="$LOG_DIR/$RUN_ID.patches.txt"

json_status() {
  local status="$1"; shift
  local summary="$1"; shift || true
  python3 - <<PY
import json, time, pathlib, subprocess, os
repo = pathlib.Path('$REPO')
def run(cmd):
    try:
        return subprocess.check_output(cmd, cwd=repo, stderr=subprocess.STDOUT, text=True).strip()
    except Exception as e:
        return str(e)
patch_report = pathlib.Path('$PATCH_REPORT')
data = {
  'run_id': '$RUN_ID',
  'status': '$status',
  'summary': '''$summary''',
  'log': '$LOG',
  'started_at': os.environ.get('STARTED_AT',''),
  'ended_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
  'version': run(['$HERMES_BIN','--version']),
  'head': run(['git','rev-parse','--short','HEAD']),
  'origin': run(['git','rev-parse','--short','origin/main']),
  'git_status': run(['git','status','--short']),
  'patch_report': patch_report.read_text(errors='ignore') if patch_report.exists() else '',
}
tmp = pathlib.Path('$STATUS_TMP'); path = pathlib.Path('$STATUS')
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2)); tmp.replace(path)
PY
}

# Defensive: enable rerere so we don't have to re-resolve the same upstream
# refactor conflicts forever.  Stored per-repo in .git/rr-cache.
git -C "$REPO" config rerere.enabled true 2>/dev/null || true
git -C "$REPO" config rerere.autoupdate true 2>/dev/null || true

export STARTED_AT="$(date +%Y-%m-%dT%H:%M:%S%z)"
{
  echo "=== Hermes external auto-update $RUN_ID ==="
  date -Is
  echo "=== before ==="
  "$HERMES_BIN" --version || true
  cd "$REPO"
  git fetch origin main
  PRE_HEAD="$(git rev-parse --short HEAD)"
  PRE_ORIGIN="$(git rev-parse --short origin/main)"
  echo "PRE_HEAD $PRE_HEAD"
  echo "PRE_ORIGIN $PRE_ORIGIN"
  echo "PRE_STATUS_START"; git status --short; echo "PRE_STATUS_END"

  # If origin/main is already in HEAD and `hermes --version` agrees, we're
  # ahead via local-patch commits — nothing to do.  Up-to-date check needs
  # both signals because version-string lag (cache) sometimes keeps the
  # "Up to date" line stale by a minute after a successful update.
  if git merge-base --is-ancestor origin/main HEAD && "$HERMES_BIN" --version 2>&1 | grep -q 'Up to date'; then
    echo "NO_UPDATE"
    : > "$PATCH_REPORT"
    json_status "no_update" "Hermes already up to date; local patch commits may be ahead of origin/main"
    exit 0
  fi

  # Recover unstaged changes that already correspond to a registered patch.
  # Otherwise hermes-update's autostash will round-trip them through `git
  # stash apply`, leaving them un-committed and at risk of being clobbered
  # by a future cherry-pick.
  echo "=== pre-update patch recovery ==="
  if [ -x "$PATCH_ENGINE" ]; then
    "$PATCH_ENGINE" recover || true
  else
    echo "PATCH_ENGINE_MISSING $PATCH_ENGINE"
  fi

  echo "=== update ==="
  set +e
  HERMES_UPDATE_SKIP_GATEWAY_RESTART=1 timeout 45m "$HERMES_BIN" update --yes
  UPDATE_RC=$?
  set -e
  echo "UPDATE_RC $UPDATE_RC"
  if [ "$UPDATE_RC" -ne 0 ]; then
    : > "$PATCH_REPORT"
    json_status "update_failed" "hermes update exited $UPDATE_RC"
    exit 0
  fi

  echo "=== after update ==="
  "$HERMES_BIN" --version || true
  git fetch origin main || true
  echo "HEAD $(git rev-parse --short HEAD)"
  echo "ORIGIN $(git rev-parse --short origin/main)"
  echo "STATUS_START"; git status --short; echo "STATUS_END"

  echo "=== restore/check local patches ==="
  if [ -x "$PATCH_ENGINE" ]; then
    set +e
    "$PATCH_ENGINE" apply 2>&1 | tee "$PATCH_REPORT"
    PATCH_RC=${PIPESTATUS[0]}
    set -e
  else
    echo "PATCH_ENGINE_MISSING $PATCH_ENGINE" | tee "$PATCH_REPORT"
    PATCH_RC=2
  fi
  echo "PATCH_RC $PATCH_RC"

  echo "=== final ==="
  "$HERMES_BIN" --version || true
  echo "HEAD $(git rev-parse --short HEAD)"
  echo "ORIGIN $(git rev-parse --short origin/main)"
  echo "STATUS_START"; git status --short; echo "STATUS_END"
  ps -ef | grep -i '[h]ermes.*gateway' | head || true

  if [ "$PATCH_RC" -ne 0 ]; then
    json_status "patch_failed" "hermes updated, but local patch restore had unresolved entries (see patch_report)"
  else
    json_status "updated" "Hermes updated and local patches verified/restored"
  fi

  # Schedule the gateway restart asynchronously so cron's report has time
  # to deliver before the gateway dies.  The helper sleeps then restarts.
  if [ -x "$RESTART_HELPER" ]; then
    nohup "$RESTART_HELPER" >/tmp/hermes-auto-update-delayed-restart.nohup 2>&1 &
  fi
} >> "$LOG" 2>&1
