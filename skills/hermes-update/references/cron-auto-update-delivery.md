# Cron auto-update delivery notes

Use this when a recurring `hermes-auto-update` job runs and produces output but the user does not receive a Telegram/Weixin report.

## Symptom

`hermes cron list --all` shows the update job as `ok`, but `last_delivery_error` contains something like:

```text
delivery error: Telegram send failed: RuntimeError('cannot schedule new futures after interpreter shutdown')
```

Local output exists under:

```bash
~/.hermes/cron/output/<job_id>/*.md
```

If those files have a non-`[SILENT]` response, the update report should have been delivered; the failure is post-run delivery.

## Root cause

`hermes update` restarts the gateway at the end. The cron scheduler runs inside the gateway process. If update restarts gateway before the cron scheduler calls `_deliver_result()`, the Python interpreter is already shutting down and platform adapters can no longer schedule futures.

In the observed May 2026 incident:

- `hermes-auto-update` completed successfully and saved `~/.hermes/cron/output/e4cb6a472ab7/2026-05-15_02-03-27.md`.
- `hermes update` printed `✓ Service restart requested` and `✓ Restarted ai.hermes.gateway`.
- `gateway.error.log` then recorded `cron.scheduler: Job 'e4cb6a472ab7': delivery error ... cannot schedule new futures after interpreter shutdown`.

## Durable fix pattern

1. Ensure `hermes update` supports skipping immediate gateway restart. In `hermes_cli/main.py`, immediately before the "Auto-restart ALL gateways after update" block, add/check:

```python
if os.getenv("HERMES_UPDATE_SKIP_GATEWAY_RESTART", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}:
    print(
        "  ℹ Skipping gateway restart because "
        "HERMES_UPDATE_SKIP_GATEWAY_RESTART is set"
    )
    print("    Restart later with: hermes gateway restart")
    print()
    print("Tip: You can now select a provider and model:")
    print("  hermes model              # Select provider and model")
    return
```

2. In the cron prompt, run:

```bash
HERMES_UPDATE_SKIP_GATEWAY_RESTART=1 hermes update --yes 2>&1
```

3. After verification, schedule a delayed restart without waiting for it:

```bash
nohup "$HOME/.hermes/scripts/hermes-auto-update-restart-gateway.sh" >/tmp/hermes-auto-update-delayed-restart.nohup 2>&1 &
```

4. The helper script should sleep before restart (default used locally: 180s):

```bash
#!/usr/bin/env bash
set -euo pipefail
sleep "${HERMES_AUTO_UPDATE_RESTART_DELAY:-180}"
{
  echo "[$(date '+%F %T')] restarting Hermes gateway after cron auto-update"
  hermes gateway restart
  echo "[$(date '+%F %T')] done"
} >> "$HOME/.hermes/logs/hermes-auto-update-delayed-restart.log" 2>&1
```

5. Preserve this local fix across upstream updates by adding it to `~/.hermes/local-patches/hermes-agent.yaml` with marker `HERMES_UPDATE_SKIP_GATEWAY_RESTART` in `hermes_cli/main.py`. If using the local commit from the May 2026 incident, the commit candidate was `946f7dd8e` (`fix(update): allow cron to skip gateway restart`).

## Updating the recurring job

The recurring `hermes-auto-update` prompt should explicitly say:

- never call `send_message`; cron delivers the final response;
- no-update path must output exactly `[SILENT]`;
- update path must use `HERMES_UPDATE_SKIP_GATEWAY_RESTART=1 hermes update --yes`;
- after successful update + local patch restoration, start the delayed restart script with `nohup` and do not wait for it;
- final report should include whether delayed restart was scheduled.

## Quick triage commands

```bash
hermes cron list --all
python - <<'PY'
import json, pathlib
p=pathlib.Path.home()/'.hermes/cron/jobs.json'
job=next(j for j in json.loads(p.read_text())['jobs'] if j.get('name')=='hermes-auto-update')
print(job.get('deliver'), job.get('origin'), job.get('last_delivery_error'))
PY
ls -lt ~/.hermes/cron/output/<job_id> | head
```

If delivery target is `origin`, inspect `origin.platform` and `origin.chat_id`; for Telegram DM delivery they should point at the user's Telegram chat.

## Pitfalls

- Do not fix this by making the cron agent call `send_message`; it creates duplicate/incorrect delivery behavior and still races gateway restart.
- Do not immediately run `hermes gateway restart` inside the cron job after `hermes update`; delay it in a detached helper so `_deliver_result()` can finish first.
- If upstream later adds an official skip-restart flag, prefer that and retire the local patch, but keep the delayed-restart ordering for gateway-hosted cron jobs.
