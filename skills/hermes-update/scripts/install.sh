#!/usr/bin/env bash
# Idempotent installer for the hermes-update skill's auto-update plumbing.
#
# Run this once on a fresh Hermes machine after `hermes-skills-sync` has
# linked the skill into `~/.hermes/skills/`.  Safe to re-run: existing
# symlinks/wrappers/units are not overwritten unless --force is passed.
#
# What it sets up:
#   - 3 symlinks in ~/.hermes/scripts/ pointing back into this skill
#   - 1 real-file wrapper in ~/.hermes/scripts/ for the cron reporter
#     (cron's script: field rejects symlinks that escape ~/.hermes/scripts/)
#   - systemd --user unit + timer for the daily external update run
#   - ~/.hermes/local-patches/hermes-agent.yaml seeded with the minimal
#     "HERMES_UPDATE_SKIP_GATEWAY_RESTART" entry if no manifest exists yet
#
# What it intentionally does NOT do (must be done by a human/agent):
#   - Register the cron job that runs the reporter — that requires the
#     `cronjob` tool inside hermes itself; we print the exact invocation
#     to copy/paste at the end.
#   - Apply the upstream `hermes_cli/main.py` HERMES_UPDATE_SKIP_GATEWAY_RESTART
#     patch — the manifest entry is seeded with a placeholder SHA that the
#     user must replace after committing the patch in hermes-agent.

set -euo pipefail

: "${HOME:?HOME must be set}"

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS_SRC="$SKILL_DIR/scripts"
TEMPLATES="$SCRIPTS_SRC/templates"
PATCHES_DIR="$SKILL_DIR/patches"

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SCRIPTS_DEST="$HERMES_HOME/scripts"
LOCAL_PATCHES_DIR="$HERMES_HOME/local-patches"
LOCAL_PATCHES_FILE="$LOCAL_PATCHES_DIR/hermes-agent.yaml"
HERMES_AGENT_REPO="$HERMES_HOME/hermes-agent"
AUTO_UPDATE_LOG_DIR="$HERMES_HOME/logs/auto-update"

# Per-OS scheduler paths.  systemd --user is preferred on Linux; macOS uses
# a per-user LaunchAgent.  Other Unixes (FreeBSD/OpenBSD) fall through to a
# no-op and the installer just prints a manual-registration hint.
OS_KIND="$(uname -s)"
case "$OS_KIND" in
  Linux)
    SCHEDULER="systemd"
    SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
    ;;
  Darwin)
    SCHEDULER="launchd"
    LAUNCHD_AGENTS_DIR="$HOME/Library/LaunchAgents"
    LAUNCHD_PLIST_LABEL="com.hermes.auto-update"
    LAUNCHD_PLIST_PATH="$LAUNCHD_AGENTS_DIR/$LAUNCHD_PLIST_LABEL.plist"
    ;;
  *)
    SCHEDULER="manual"
    ;;
esac

FORCE=0
APPLY_PATCHES=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --apply-patches) APPLY_PATCHES=1 ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//;/^set -euo/d'
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 64
      ;;
  esac
done

log() { printf '[install] %s\n' "$*"; }
warn() { printf '[install] WARN: %s\n' "$*" >&2; }

ensure_dir() {
  local d="$1"
  mkdir -p "$d"
}

install_symlink() {
  local target="$1" link="$2"
  if [ -L "$link" ]; then
    local current
    current="$(readlink "$link")"
    if [ "$current" = "$target" ]; then
      log "symlink ok: $link"
      return
    fi
    if [ "$FORCE" -eq 1 ]; then
      log "symlink replace (force): $link -> $target (was $current)"
      ln -sfn "$target" "$link"
    else
      warn "symlink points elsewhere, leaving alone (pass --force to replace): $link -> $current"
    fi
    return
  fi
  if [ -e "$link" ]; then
    if [ "$FORCE" -eq 1 ]; then
      log "replacing real file with symlink (force): $link"
      mv "$link" "${link}.bak-$(date +%Y%m%d-%H%M%S)"
      ln -s "$target" "$link"
    else
      warn "$link exists as a real file; refusing to clobber (pass --force or remove it)"
    fi
    return
  fi
  log "creating symlink: $link -> $target"
  ln -s "$target" "$link"
}

install_wrapper() {
  local src="$1" dest="$2"
  if [ -e "$dest" ] && [ "$FORCE" -eq 0 ]; then
    log "wrapper present, leaving alone: $dest"
    return
  fi
  log "installing wrapper: $dest"
  install -m 0755 "$src" "$dest"
}

install_unit() {
  local src="$1" dest="$2"
  if [ -e "$dest" ] && [ "$FORCE" -eq 0 ]; then
    log "systemd unit present, leaving alone: $dest"
    return
  fi
  log "installing systemd unit: $dest"
  install -m 0644 "$src" "$dest"
}

install_launchd_plist() {
  local src="$1" dest="$2"
  if [ -e "$dest" ] && [ "$FORCE" -eq 0 ]; then
    log "LaunchAgent plist present, leaving alone: $dest"
    return
  fi
  log "installing LaunchAgent plist: $dest"
  # Render the template's __SCRIPT__ / __LOG_DIR__ placeholders into the
  # destination plist.  We avoid `sed -i` because BSD sed and GNU sed
  # disagree about its argument syntax; a python rewrite is portable.
  python3 - "$src" "$dest" "$SCRIPTS_DEST/hermes-auto-update-external.sh" "$AUTO_UPDATE_LOG_DIR" <<'PY'
import sys, pathlib
src = pathlib.Path(sys.argv[1])
dest = pathlib.Path(sys.argv[2])
script = sys.argv[3]
logdir = sys.argv[4]
text = src.read_text()
text = text.replace("__SCRIPT__", script)
text = text.replace("__LOG_DIR__", logdir)
dest.write_text(text)
PY
  chmod 0644 "$dest"
}

seed_local_patches() {
  if [ -e "$LOCAL_PATCHES_FILE" ]; then
    log "local-patches manifest present, leaving alone: $LOCAL_PATCHES_FILE"
    return
  fi
  log "seeding minimal local-patches manifest: $LOCAL_PATCHES_FILE"
  install -m 0644 "$TEMPLATES/local-patches.minimal.yaml" "$LOCAL_PATCHES_FILE"
}

# Status values printed by check_or_apply_skip_restart_patch (used by
# print_cron_registration_hint to tailor the post-install message):
#   ok-applied    the marker was already present in HEAD (existing install)
#   am-applied    install applied the .patch and produced a new commit
#   am-needed     patch missing and --apply-patches NOT passed; user must run
#   no-repo       ~/.hermes/hermes-agent missing; skip silently
#   am-failed     git am attempted but failed (caller already saw the error)
SKIP_RESTART_PATCH_STATUS=""

check_or_apply_skip_restart_patch() {
  local patch_file="$PATCHES_DIR/0001-fix-update-allow-external-schedulers-to-skip-gateway-restart.patch"
  if [ ! -d "$HERMES_AGENT_REPO/.git" ]; then
    SKIP_RESTART_PATCH_STATUS="no-repo"
    warn "hermes-agent repo not found at $HERMES_AGENT_REPO; skip-restart patch step deferred"
    return
  fi
  if [ ! -f "$patch_file" ]; then
    SKIP_RESTART_PATCH_STATUS="no-repo"
    warn "patch file missing: $patch_file"
    return
  fi
  # Marker check uses git show so we look at HEAD's tree, not the worktree —
  # matches what `hermes-local-patches.py status` does.  Use process
  # substitution to avoid `set -o pipefail` treating grep -q's early close
  # (SIGPIPE on git show) as a failure when the marker is found in line 1.
  if grep -q HERMES_UPDATE_SKIP_GATEWAY_RESTART \
      <(git -C "$HERMES_AGENT_REPO" show HEAD:hermes_cli/main.py 2>/dev/null); then
    SKIP_RESTART_PATCH_STATUS="ok-applied"
    log "skip-restart patch already in HEAD of $HERMES_AGENT_REPO"
    return
  fi
  if [ "$APPLY_PATCHES" -ne 1 ]; then
    SKIP_RESTART_PATCH_STATUS="am-needed"
    warn "skip-restart patch NOT in HEAD; pass --apply-patches to git-am it now"
    return
  fi
  # Refuse to clobber an in-progress am/cherry-pick/rebase.
  if [ -d "$HERMES_AGENT_REPO/.git/rebase-apply" ] \
      || [ -d "$HERMES_AGENT_REPO/.git/rebase-merge" ] \
      || [ -f "$HERMES_AGENT_REPO/.git/CHERRY_PICK_HEAD" ]; then
    SKIP_RESTART_PATCH_STATUS="am-failed"
    warn "skip-restart patch: $HERMES_AGENT_REPO has an in-progress operation; resolve it first"
    return
  fi
  log "applying skip-restart patch via git am in $HERMES_AGENT_REPO"
  if git -C "$HERMES_AGENT_REPO" am --keep-cr "$patch_file"; then
    local new_sha
    new_sha="$(git -C "$HERMES_AGENT_REPO" rev-parse --short HEAD)"
    log "skip-restart patch applied as commit $new_sha"
    SKIP_RESTART_PATCH_STATUS="am-applied"
    # If the freshly-seeded manifest still has the placeholder SHA, swap in
    # the real one we just produced.  We don't touch an existing user-edited
    # manifest beyond this string replacement; if the placeholder isn't
    # present we leave the file alone.
    if [ -f "$LOCAL_PATCHES_FILE" ] \
        && grep -q REPLACE_WITH_YOUR_COMMIT_SHORT_SHA "$LOCAL_PATCHES_FILE"; then
      log "rewriting manifest placeholder SHA -> $new_sha"
      python3 - "$LOCAL_PATCHES_FILE" "$new_sha" <<'PY'
import sys, pathlib
path = pathlib.Path(sys.argv[1])
sha = sys.argv[2]
text = path.read_text()
text = text.replace("REPLACE_WITH_YOUR_COMMIT_SHORT_SHA", sha)
path.write_text(text)
PY
    fi
  else
    SKIP_RESTART_PATCH_STATUS="am-failed"
    warn "git am failed; run \`git -C $HERMES_AGENT_REPO am --abort\` and apply manually"
  fi
}

enable_systemd_timer() {
  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not available; skipping timer enablement"
    return
  fi
  if ! systemctl --user >/dev/null 2>&1; then
    warn "systemd --user not active in this session; run manually later:"
    warn "  systemctl --user daemon-reload && systemctl --user enable --now hermes-auto-update.timer"
    return
  fi
  log "reloading systemd --user and enabling hermes-auto-update.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now hermes-auto-update.timer
  systemctl --user list-timers hermes-auto-update.timer --no-pager 2>/dev/null || true
}

enable_launchd_agent() {
  if ! command -v launchctl >/dev/null 2>&1; then
    warn "launchctl not available; skipping LaunchAgent activation"
    return
  fi
  local uid
  uid="$(id -u)"
  local domain="gui/$uid"
  # `bootstrap` registers the plist with launchd; if it's already loaded we
  # bootout-then-bootstrap to pick up any edits.  `bootout` returns non-zero
  # when the service isn't loaded, which we tolerate.
  if launchctl print "$domain/$LAUNCHD_PLIST_LABEL" >/dev/null 2>&1; then
    log "reloading LaunchAgent (bootout + bootstrap): $LAUNCHD_PLIST_LABEL"
    launchctl bootout "$domain/$LAUNCHD_PLIST_LABEL" 2>/dev/null || true
  else
    log "loading LaunchAgent: $LAUNCHD_PLIST_LABEL"
  fi
  if ! launchctl bootstrap "$domain" "$LAUNCHD_PLIST_PATH"; then
    warn "launchctl bootstrap failed; load manually with:"
    warn "  launchctl bootstrap gui/$uid $LAUNCHD_PLIST_PATH"
    return
  fi
  launchctl enable "$domain/$LAUNCHD_PLIST_LABEL" 2>/dev/null || true
  # Show next scheduled fire time for visibility; print is verbose, grep just
  # the lines that humans usually care about.
  launchctl print "$domain/$LAUNCHD_PLIST_LABEL" 2>/dev/null \
    | awk '/^	(state|next run|last exit code)/' || true
}

enable_scheduler() {
  case "$SCHEDULER" in
    systemd) enable_systemd_timer ;;
    launchd) enable_launchd_agent ;;
    manual)  warn "unknown OS ($OS_KIND); start hermes-auto-update-external.sh from your own scheduler" ;;
  esac
}

print_cron_registration_hint() {
  cat <<'EOF'

============================================================
NEXT STEPS (must be done by hand inside Hermes)
============================================================

1. Register the reporter cron job.  From a Hermes agent session
   (or via the `cronjob` MCP tool) run:

     cronjob(
       action="create",
       name="hermes-auto-update-report",
       schedule="15 17 * * *",
       no_agent=False,
       deliver="origin",
       enabled_toolsets=["terminal"],
       script="hermes-auto-update-report.py",
       prompt="""
你是 Hermes Agent 自动更新结果汇报助手。上方 script_output 来自
~/.hermes/scripts/hermes-auto-update-report.py。

规则：
1. 如果 script_output 严格等于 `[SILENT]`，最终只输出 `[SILENT]`，
   不要附加任何文字。
2. 不要执行更新、不要调用 send_message、不要创建/修改 cron job；
   这里只负责汇报外部 systemd timer 的结果。
3. script_output 的第一行是确定性权威锚点，格式为
   `AUTHORITATIVE_STATUS=*** LABEL=<中文状态词> NEEDS_ATTENTION=<yes|no>`。
   这是脚本钉死的真理来源，汇报必须满足（QC 校验项，违反即视为汇报错误）：
   a. 最终汇报的最后一行原样附上该 AUTHORITATIVE_STATUS 锚点行，一字不改。
   b. 正文描述的状态必须与锚点 LABEL 一致，不得淡化或歪曲。
   c. NEEDS_ATTENTION=yes 时正文必须含明确的“需要人工查看日志/处理”提示。
4. 如果 script_output 是 JSON（锚点行之后的部分）：
   - status=updated：用中文简洁汇报“自动更新已完成”，包含
     version/head/origin、patch 是否已验证/恢复、git_status、log 路径。
     可选：运行 gh release 读取 changelog 附在“## 更新内容”下，失败则注明。
   - status=update_failed / patch_failed / skipped_locked / missing：用中文
     报告状态、关键摘要、log 路径、head/origin/git_status，提醒人工查看日志。
5. 只使用 script_output 和 gh 读取的 changelog 信息，不要臆测。
"""
     )

   Adjust the schedule if your timezone or 17:00 trigger needs to shift;
   keep it ~15min after the systemd timer's OnCalendar value.

EOF

  case "$SKIP_RESTART_PATCH_STATUS" in
    ok-applied)
      cat <<EOF
2. ✓ HERMES_UPDATE_SKIP_GATEWAY_RESTART patch is already in HEAD of
   $HERMES_AGENT_REPO (no action needed).

EOF
      ;;
    am-applied)
      cat <<EOF
2. ✓ HERMES_UPDATE_SKIP_GATEWAY_RESTART patch was just applied via git am
   and the manifest placeholder SHA was rewritten.  Verify:

     ~/.hermes/scripts/hermes-local-patches.py status

   Expect: \`PATCH_COMMITTED hermes-update-skip-gateway-restart\`.

EOF
      ;;
    am-needed)
      cat <<EOF
2. Apply the HERMES_UPDATE_SKIP_GATEWAY_RESTART patch.  Re-run install.sh
   with --apply-patches to git-am it automatically, or do it by hand:

     cd $HERMES_AGENT_REPO
     git am $PATCHES_DIR/0001-fix-update-allow-external-schedulers-to-skip-gateway-restart.patch
     git rev-parse --short HEAD

   Then put the new short SHA into the commit_candidates list of
   ~/.hermes/local-patches/hermes-agent.yaml's
   \`hermes-update-skip-gateway-restart\` entry (replacing the
   REPLACE_WITH_YOUR_COMMIT_SHORT_SHA placeholder if that's still there).
   Verify:

     ~/.hermes/scripts/hermes-local-patches.py status

   Expect: \`PATCH_COMMITTED hermes-update-skip-gateway-restart\`.

EOF
      ;;
    am-failed)
      cat <<EOF
2. ✗ HERMES_UPDATE_SKIP_GATEWAY_RESTART patch apply FAILED.  Resolve the
   conflict in $HERMES_AGENT_REPO, run \`git am --abort\` if needed, then
   apply manually and update the manifest commit_candidates SHA.

EOF
      ;;
    no-repo|*)
      cat <<EOF
2. Apply the HERMES_UPDATE_SKIP_GATEWAY_RESTART patch once
   $HERMES_AGENT_REPO exists; install.sh ships the patch at
   $PATCHES_DIR/0001-fix-update-allow-external-schedulers-to-skip-gateway-restart.patch
   and \`--apply-patches\` will git-am it for you.

EOF
      ;;
  esac

  case "$SCHEDULER" in
    systemd)
      cat <<'EOF'
3. Manually trigger one external run to verify end-to-end:

     systemctl --user start hermes-auto-update.service
     journalctl --user -u hermes-auto-update.service -e --no-pager | tail
     cat ~/.hermes/state/auto-update/latest.json

============================================================
EOF
      ;;
    launchd)
      cat <<EOF
3. Manually trigger one external run to verify end-to-end:

     launchctl kickstart -k gui/\$(id -u)/$LAUNCHD_PLIST_LABEL
     tail -n 100 $AUTO_UPDATE_LOG_DIR/launchd.stderr.log
     cat $HERMES_HOME/state/auto-update/latest.json

   Inspect the agent state any time:

     launchctl print gui/\$(id -u)/$LAUNCHD_PLIST_LABEL | head -50

============================================================
EOF
      ;;
    *)
      cat <<EOF
3. This OS ($OS_KIND) has no scheduler integration in install.sh; wire
   $SCRIPTS_DEST/hermes-auto-update-external.sh into your preferred timer
   (cron/at/...) and verify it writes $HERMES_HOME/state/auto-update/latest.json.

============================================================
EOF
      ;;
  esac
}

main() {
  log "skill dir:       $SKILL_DIR"
  log "scripts source:  $SCRIPTS_SRC"
  log "hermes home:     $HERMES_HOME"
  log "os kind:         $OS_KIND"
  log "scheduler:       $SCHEDULER"
  log "force mode:      $FORCE"

  ensure_dir "$SCRIPTS_DEST"
  ensure_dir "$LOCAL_PATCHES_DIR"
  ensure_dir "$AUTO_UPDATE_LOG_DIR"
  ensure_dir "$HERMES_HOME/state/auto-update"

  install_symlink "$SCRIPTS_SRC/hermes-local-patches.py"            "$SCRIPTS_DEST/hermes-local-patches.py"
  install_symlink "$SCRIPTS_SRC/hermes-auto-update-external.sh"      "$SCRIPTS_DEST/hermes-auto-update-external.sh"
  install_symlink "$SCRIPTS_SRC/hermes-auto-update-restart-gateway.sh" "$SCRIPTS_DEST/hermes-auto-update-restart-gateway.sh"

  install_wrapper "$TEMPLATES/hermes-auto-update-report.wrapper.py" "$SCRIPTS_DEST/hermes-auto-update-report.py"

  case "$SCHEDULER" in
    systemd)
      ensure_dir "$SYSTEMD_USER_DIR"
      install_unit "$TEMPLATES/hermes-auto-update.service" "$SYSTEMD_USER_DIR/hermes-auto-update.service"
      install_unit "$TEMPLATES/hermes-auto-update.timer"   "$SYSTEMD_USER_DIR/hermes-auto-update.timer"
      ;;
    launchd)
      ensure_dir "$LAUNCHD_AGENTS_DIR"
      install_launchd_plist "$TEMPLATES/com.hermes.auto-update.plist" "$LAUNCHD_PLIST_PATH"
      ;;
    manual)
      warn "scheduler unit installation skipped on this OS ($OS_KIND)"
      ;;
  esac

  seed_local_patches

  check_or_apply_skip_restart_patch

  enable_scheduler

  print_cron_registration_hint
}

main "$@"
