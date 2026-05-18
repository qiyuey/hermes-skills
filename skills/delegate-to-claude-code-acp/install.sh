#!/bin/bash
# delegate-to-claude-code-acp: first-use bootstrap
# Idempotent; safe to re-run. Call from the skill directory or with SKILL_DIR env.

set -euo pipefail

ACP_PKG_VERSION="0.27.0"
CLAUDE_PKG_VERSION="2.1.109"
AWS_REGION="${AWS_REGION:-us-west-2}"
SKILLS_ROOT="${SKILLS_ROOT:-/root/.openclaw/skills/yuanbot/yuanli-skill-hub/skills}"
SKILL_DIR="${SKILL_DIR:-$SKILLS_ROOT/delegate-to-claude-code-acp}"
WRAPPER_SRC="$SKILL_DIR/bin/claude-as-acp"
WRAPPER_LINK="/usr/local/bin/claude-as-acp"
CLAUDE_SKILLS_DIR="/root/.claude/skills"

echo "=== delegate-to-claude-code-acp install ==="

# 1. Install @agentclientprotocol/claude-agent-acp globally (pinned version)
if command -v claude-agent-acp >/dev/null 2>&1; then
  echo "[1/6] claude-agent-acp already installed"
else
  echo "[1/6] installing @agentclientprotocol/claude-agent-acp@${ACP_PKG_VERSION}..."
  npm install -g "@agentclientprotocol/claude-agent-acp@${ACP_PKG_VERSION}" 2>&1 | tail -5
  command -v claude-agent-acp >/dev/null || { echo "claude-agent-acp install failed"; exit 1; }
fi

# 2. Install or upgrade claude CLI to pinned version.
#
#    Production bots often ship with an older claude CLI (observed 2.0.42 on
#    one test bot, latest at time of writing is ${CLAUDE_PKG_VERSION}). Older
#    versions have protocol / stream-json deltas that can break the ACP path.
#    We pin a known-good recent version.
CURRENT_CLAUDE_VER="$(claude --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo '')"
if [ "$CURRENT_CLAUDE_VER" = "$CLAUDE_PKG_VERSION" ]; then
  echo "[2/6] claude CLI already at pinned version $CLAUDE_PKG_VERSION"
else
  if [ -n "$CURRENT_CLAUDE_VER" ]; then
    echo "[2/6] upgrading claude CLI: $CURRENT_CLAUDE_VER -> $CLAUDE_PKG_VERSION"
  else
    echo "[2/6] installing claude CLI $CLAUDE_PKG_VERSION (not currently installed)"
  fi
  npm install -g "@anthropic-ai/claude-code@${CLAUDE_PKG_VERSION}" 2>&1 | tail -5
  NEW_VER="$(claude --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo '')"
  if [ "$NEW_VER" != "$CLAUDE_PKG_VERSION" ]; then
    # Common failure: a stale static ELF binary at /usr/local/bin/claude
    # shadows the npm-installed version at /usr/bin/claude. Detect and rewire.
    NPM_CLAUDE="$(npm root -g 2>/dev/null)/@anthropic-ai/claude-code/cli.js"
    if [ -f "$NPM_CLAUDE" ] && [ -f /usr/local/bin/claude ] && [ ! -L /usr/local/bin/claude ]; then
      NPM_VER="$("$NPM_CLAUDE" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo '')"
      OLD_VER="$(/usr/local/bin/claude --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo '')"
      if [ "$NPM_VER" = "$CLAUDE_PKG_VERSION" ] && [ "$OLD_VER" != "$CLAUDE_PKG_VERSION" ]; then
        echo "       /usr/local/bin/claude is a stale static binary ($OLD_VER); redirecting to npm version ($NPM_VER)"
        mv /usr/local/bin/claude /usr/local/bin/claude.legacy-$OLD_VER
        ln -sfn /usr/bin/claude /usr/local/bin/claude
        hash -r
        NEW_VER="$(claude --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo '')"
      fi
    fi
    if [ "$NEW_VER" = "$CLAUDE_PKG_VERSION" ]; then
      echo "       claude CLI now at $NEW_VER"
    else
      echo "       WARN: expected $CLAUDE_PKG_VERSION but \`claude --version\` still reports $NEW_VER"
      echo "       wrapper will fall back to CLAUDE_CODE_EXECUTABLE env detection"
    fi
  else
    echo "       claude CLI now at $NEW_VER"
  fi
fi

# 3. Bootstrap ~/.aws/credentials with claude-profile section.
#
#    Why this step exists:
#    On OpenClaw bots the gateway env has AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
#    and CLAUDE_CODE_AWS_PROFILE=claude-profile. The AWS SDK bundled inside
#    claude CLI honors CLAUDE_CODE_AWS_PROFILE as a profile name hint and will
#    refuse to fall back to env vars if the profile is not found in
#    ~/.aws/credentials. Symptom: "Could not load credentials from any providers".
#    Fix: write a [claude-profile] section populated from the env vars.
if [ -z "${AWS_ACCESS_KEY_ID:-}" ] || [ -z "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  echo "[3/6] WARN: AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY not in env; skipping credentials bootstrap"
  echo "       Bootstrap will happen lazily on first wrapper invocation if env vars are present then."
else
  mkdir -p /root/.aws
  if ! grep -q "^\[claude-profile\]" /root/.aws/credentials 2>/dev/null; then
    cat > /root/.aws/credentials <<EOF
[claude-profile]
aws_access_key_id = $AWS_ACCESS_KEY_ID
aws_secret_access_key = $AWS_SECRET_ACCESS_KEY
EOF
    cat > /root/.aws/config <<EOF
[profile claude-profile]
region = $AWS_REGION
EOF
    chmod 600 /root/.aws/credentials /root/.aws/config
    echo "[3/6] wrote /root/.aws/credentials with [claude-profile]"
  else
    echo "[3/6] /root/.aws/credentials already has [claude-profile]"
  fi
fi

# 4. Symlink wrapper into /usr/local/bin
if [ ! -x "$WRAPPER_SRC" ]; then
  echo "[4/6] ERROR: wrapper not found or not executable at $WRAPPER_SRC"
  exit 1
fi
ln -sfn "$WRAPPER_SRC" "$WRAPPER_LINK"
echo "[4/6] symlinked $WRAPPER_LINK -> $WRAPPER_SRC"

# 5. Expose OpenClaw skills to Claude Code.
#
#    Claude Code CLI reads user skills from ~/.claude/skills/<skill-name>/SKILL.md.
#    We symlink every OpenClaw skill dir (except this one) into there so Claude
#    Code can discover and use them as tools when it sees fit. This mirrors the
#    phase2 behavior (yuque-cli / opencli-for-openclaw / etc. all become visible
#    inside Claude Code).
mkdir -p "$CLAUDE_SKILLS_DIR"
linked=0
skipped=0
if [ -d "$SKILLS_ROOT" ]; then
  for sk in "$SKILLS_ROOT"/*/; do
    [ -d "$sk" ] || continue
    name="$(basename "$sk")"
    # skip self to avoid recursive loop / self-delegation
    [ "$name" = "delegate-to-claude-code-acp" ] && continue
    # only link dirs that actually contain a SKILL.md
    [ -f "$sk/SKILL.md" ] || { skipped=$((skipped+1)); continue; }
    target="$CLAUDE_SKILLS_DIR/$name"
    if [ -L "$target" ] || [ ! -e "$target" ]; then
      ln -sfn "$sk" "$target"
      linked=$((linked+1))
    fi
  done
fi
echo "[5/6] exposed $linked OpenClaw skill(s) to Claude Code (~/.claude/skills/); skipped=$skipped"

# 6. Smoke test (best-effort; non-fatal if env vars are missing)
if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${ANTHROPIC_MODEL:-}" ]; then
  echo "[6/6] running smoke test..."
  tmpout="$(mktemp)"
  if timeout 60 "$WRAPPER_LINK" --cwd /tmp "Reply in exactly one word: READY" > "$tmpout" 2>&1; then
    if grep -q '"type":"session"' "$tmpout" && grep -q 'READY' "$tmpout"; then
      echo "       smoke test OK"
    else
      echo "       smoke test output unexpected; see $tmpout"
    fi
  else
    echo "       smoke test timed out or failed; see $tmpout"
  fi
else
  echo "[6/6] skipping smoke test (AWS_ACCESS_KEY_ID or ANTHROPIC_MODEL not set)"
fi

echo ""
echo "install complete."
