# Claude Code native install and auto-updates

Use this when a user asks how Claude Code was installed, whether the channel is best practice, or why it did not auto-update.

## Current best practice

- Prefer the native installer (`curl -fsSL https://claude.ai/install.sh | bash`) for user machines.
- Official docs mark npm installation as deprecated; npm should be migrated with `claude install` and then removed from global npm if it shadows the native binary.
- Homebrew/WinGet/Linux package-manager installs are valid, but updates normally arrive through the package manager rather than Claude Code's own auto-updater.

## Inspection recipe

```bash
which -a claude
realpath "$(command -v claude)"
ls -l "$(command -v claude)"
claude --version

python3 - <<'PY'
import json
from pathlib import Path
p = Path.home()/'.claude.json'
data = json.loads(p.read_text())
for k in ['installMethod','autoUpdates','autoUpdatesProtectedForNative']:
    print(f'{k}={data.get(k)!r}')
PY
```

Native install normally looks like:

```text
~/.local/bin/claude -> ~/.local/share/claude/versions/<version>
installMethod='native'
```

## Why native install may not auto-update

The release channel in `~/.claude/settings.json` (for example `autoUpdatesChannel: latest`) selects the channel, but does not necessarily mean auto-updates are enabled. Check `~/.claude.json` for:

```text
autoUpdates=False
```

If native auto-updates should be enabled, set it to true. Prefer using Claude Code's own config UI/command if available; otherwise edit the JSON carefully:

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = Path.home()/'.claude.json'
data = json.loads(p.read_text())
data['autoUpdates'] = True
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
PY
python3 -m json.tool ~/.claude.json >/dev/null
```

Then verify:

```bash
claude --version
claude -p 'Reply exactly: AUTO_UPDATE_CHECK_OK' --max-turns 1 --output-format json
```

## Pitfalls

- Do not conclude an install is npm just because `npm` exists. Inspect the actual `claude` symlink/realpath and `~/.claude.json installMethod`.
- `claude doctor` can hang even when Claude Code itself works; pair it with a direct print-mode smoke test.
- If multiple installers are present, `which -a claude` reveals path shadowing. Clean up old npm/Homebrew installs only after confirming the native binary works.