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

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SCRIPTS_DEST="$HERMES_HOME/scripts"
LOCAL_PATCHES_DIR="$HERMES_HOME/local-patches"
LOCAL_PATCHES_FILE="$LOCAL_PATCHES_DIR/hermes-agent.yaml"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
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

seed_local_patches() {
  if [ -e "$LOCAL_PATCHES_FILE" ]; then
    log "local-patches manifest present, leaving alone: $LOCAL_PATCHES_FILE"
    return
  fi
  log "seeding minimal local-patches manifest: $LOCAL_PATCHES_FILE"
  install -m 0644 "$TEMPLATES/local-patches.minimal.yaml" "$LOCAL_PATCHES_FILE"
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
3. 如果 script_output 是 JSON：
   - status=updated：用中文简洁汇报“自动更新已完成”，包含
     version/head/origin、patch 是否已验证/恢复、git_status、log 路径。
   - status=update_failed / patch_failed / skipped_locked：用中文报告
     状态、关键摘要、log 路径，提醒人工跟进。
"""
     )

   Adjust the schedule if your timezone or 17:00 trigger needs to shift;
   keep it ~15min after the systemd timer's OnCalendar value.

2. Apply the `HERMES_UPDATE_SKIP_GATEWAY_RESTART` patch to hermes-agent
   (see references/cron-auto-update-delivery.md), commit it, then
   replace `REPLACE_WITH_YOUR_COMMIT_SHORT_SHA` in
   ~/.hermes/local-patches/hermes-agent.yaml with the new commit's
   short SHA.  Run:

     ~/.hermes/scripts/hermes-local-patches.py status

   Expect: `PATCH_COMMITTED hermes-update-skip-gateway-restart`.

3. Manually trigger one external run to verify end-to-end:

     systemctl --user start hermes-auto-update.service
     journalctl --user -u hermes-auto-update.service -e --no-pager | tail
     cat ~/.hermes/state/auto-update/latest.json

============================================================
EOF
}

main() {
  log "skill dir:       $SKILL_DIR"
  log "scripts source:  $SCRIPTS_SRC"
  log "hermes home:     $HERMES_HOME"
  log "force mode:      $FORCE"

  ensure_dir "$SCRIPTS_DEST"
  ensure_dir "$LOCAL_PATCHES_DIR"
  ensure_dir "$SYSTEMD_USER_DIR"
  ensure_dir "$HERMES_HOME/logs/auto-update"
  ensure_dir "$HERMES_HOME/state/auto-update"

  install_symlink "$SCRIPTS_SRC/hermes-local-patches.py"            "$SCRIPTS_DEST/hermes-local-patches.py"
  install_symlink "$SCRIPTS_SRC/hermes-auto-update-external.sh"      "$SCRIPTS_DEST/hermes-auto-update-external.sh"
  install_symlink "$SCRIPTS_SRC/hermes-auto-update-restart-gateway.sh" "$SCRIPTS_DEST/hermes-auto-update-restart-gateway.sh"

  install_wrapper "$TEMPLATES/hermes-auto-update-report.wrapper.py" "$SCRIPTS_DEST/hermes-auto-update-report.py"

  install_unit "$TEMPLATES/hermes-auto-update.service" "$SYSTEMD_USER_DIR/hermes-auto-update.service"
  install_unit "$TEMPLATES/hermes-auto-update.timer"   "$SYSTEMD_USER_DIR/hermes-auto-update.timer"

  seed_local_patches

  enable_systemd_timer

  print_cron_registration_hint
}

main "$@"
