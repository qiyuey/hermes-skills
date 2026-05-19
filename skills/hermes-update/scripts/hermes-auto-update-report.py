#!/usr/bin/env python3
import json, pathlib, datetime, sys
from zoneinfo import ZoneInfo

status_path = pathlib.Path.home() / '.hermes/state/auto-update/latest.json'
if not status_path.exists():
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
else:
    print(json.dumps(data, ensure_ascii=False, indent=2))
