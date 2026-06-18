#!/usr/bin/env python3
"""Canonical auto-update reporter.

Emits the latest auto-update status for the cron job `hermes-auto-update-report`
(c6b8487f8eb5) to translate into a human-readable Chinese alert.

harness role: this script is the DETERMINISTIC source of truth. The cron LLM
only narrates. To stop the LLM from misrepresenting the outcome (e.g. calling a
`patch_failed` run "basically fine", or dropping the human-intervention notice),
we emit a machine-checkable AUTHORITATIVE anchor block. The cron prompt requires
the LLM to reproduce the `AUTHORITATIVE_STATUS=` line verbatim, which makes
"did the alert reflect the real status" a deterministic string check rather than
a matter of LLM goodwill.
"""
import json, pathlib, datetime, sys
from zoneinfo import ZoneInfo

status_path = pathlib.Path.home() / '.hermes/state/auto-update/latest.json'

# Canonical status -> standard Chinese status word. The cron LLM must use the
# matching word; QC can diff the alert text against this mapping.
STATUS_LABELS = {
    'updated': '更新成功',
    'no_update': '无更新',
    'update_failed': '更新失败',
    'patch_failed': '补丁恢复失败',
    'skipped_locked': '跳过（已有更新在运行）',
    'missing': '状态文件缺失',
}
# Statuses that REQUIRE a human-intervention notice in the alert.
NEEDS_ATTENTION = {'update_failed', 'patch_failed', 'missing'}


def emit_anchor(status: str) -> str:
    label = STATUS_LABELS.get(status, f'未知状态({status})')
    attn = 'yes' if status in NEEDS_ATTENTION else 'no'
    # Single-line, easy to grep / assert against. The cron LLM must reproduce
    # the AUTHORITATIVE_STATUS line verbatim in its final alert.
    return (
        f"AUTHORITATIVE_STATUS={status} LABEL={label} NEEDS_ATTENTION={attn}"
    )


if not status_path.exists():
    # Missing status file is itself an attention-worthy condition.
    print(emit_anchor('missing'))
    print(json.dumps({'status': 'missing', 'summary': 'auto-update status file missing'}, ensure_ascii=False))
    sys.exit(0)

data = json.loads(status_path.read_text())
# Only report today's result; otherwise stay silent to avoid stale alerts.
tz = ZoneInfo('Asia/Shanghai')
today = datetime.datetime.now(tz).date().isoformat()
ended = (data.get('ended_at') or '')[:10]
if ended != today:
    print('[SILENT]')
    sys.exit(0)

status = data.get('status')
if status == 'no_update':
    print('[SILENT]')
    sys.exit(0)

# Active alert: emit the deterministic anchor first, then the full JSON.
print(emit_anchor(status))
print(json.dumps(data, ensure_ascii=False, indent=2))
