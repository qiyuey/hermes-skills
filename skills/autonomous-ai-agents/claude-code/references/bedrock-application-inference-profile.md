# Claude Code on AWS Bedrock — Application Inference Profile setup

Companies often hand devs a set of **application-inference-profile** ARNs (one per Claude family, region-pinned), not raw model IDs. These ARNs are what the IAM policy is bound to. Configuring Claude Code with the wrong ARN class is the most common failure mode and superficially looks like an IAM permission problem.

## The two ARN classes — don't confuse them

| Kind | Example | Who creates | Authorization |
|------|---------|-------------|---------------|
| **System-defined cross-region inference profile** | `us.anthropic.claude-opus-4-7` | AWS-managed | Resource ARN `inference-profile/us.anthropic.claude-*`. Default IAM almost never allows this for corp accounts. |
| **Application inference profile** | `arn:aws:bedrock:us-west-2:<acct>:application-inference-profile/<id>` | Your org's admin | Resource ARN `application-inference-profile/<id>`. The corp's IAM policy is usually scoped to *this*. |

If your env points `ANTHROPIC_DEFAULT_*_MODEL` at the system profile (`us.anthropic.claude-*`) but your account is only authorized for the application profile ARNs, every call returns:

```
AccessDeniedException ... is not authorized to perform: bedrock:InvokeModelWithResponseStream
on resource: arn:aws:bedrock:us-east-1:<acct>:inference-profile/us.anthropic.claude-opus-4-7
```

The error text mentions `inference-profile/us.anthropic.claude-*` — that's the giveaway it's the system profile, not your application profile. The fix is a config change, not an IAM change.

## Diagnostic flow — isolate ARN-correctness from credentials

Run these in order. The pattern: confirm credentials work, confirm read perms, then test each ARN class directly. This separates "creds dead" from "wrong ARN class" from "actually no perm".

```bash
# 1. Credentials valid + identity recognized?
aws sts get-caller-identity
# Expected: returns {"UserId":..., "Arn":"arn:aws:iam::...:user/..."}
# If this fails, it's a credentials problem (not IAM/ARN).

# 2. Read perms on Bedrock?
aws bedrock list-foundation-models --region us-west-2 \
  --query 'modelSummaries[?contains(modelId, `claude`)].modelId' | head
# If this returns the list, account is connected to Bedrock.

# 3. Direct invoke against the SUSPECT ARN — bypasses Claude Code entirely.
aws bedrock-runtime invoke-model \
  --model-id "arn:aws:bedrock:us-west-2:<acct>:application-inference-profile/<id>" \
  --body '{"anthropic_version":"bedrock-2023-05-31","max_tokens":10,"messages":[{"role":"user","content":"hi"}]}' \
  --cli-binary-format raw-in-base64-out --region us-west-2 /tmp/out.json
# Success here ⇒ the ARN works, your config (env/settings) is the problem.
# AccessDenied here ⇒ real IAM problem, escalate to admin.

# 4. (Sanity) test the SYSTEM profile too. Usually fails for corp accounts.
aws bedrock-runtime invoke-model \
  --model-id "us.anthropic.claude-opus-4-7" \
  --body '...' --region us-east-1 /tmp/out.json
# AccessDenied is normal here for application-profile-only accounts. Confirms what's authorized.
```

If step 3 succeeds and your Claude Code config used the system-profile ID — root cause confirmed: wrong ARN class in env/settings. Proceed to fix config (next section).

## Recommended config: `~/.claude/settings.json` (Anthropic-official pattern)

Per https://code.claude.com/docs/en/amazon-bedrock — pin model versions and define `/model` overrides via the user settings file rather than shell rc. Keeps Bedrock-specific env from leaking into sibling tools (hermes-agent, etc.).

```json
{
  "autoUpdatesChannel": "latest",
  "theme": "auto",
  "model": "opus",
  "env": {
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "AWS_REGION": "us-west-2",
    "AWS_PROFILE": "claude-code",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "arn:aws:bedrock:us-west-2:<acct>:application-inference-profile/<opus-id>",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "arn:aws:bedrock:us-west-2:<acct>:application-inference-profile/<sonnet-id>",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "arn:aws:bedrock:us-west-2:<acct>:application-inference-profile/<haiku-id>"
  },
  "modelOverrides": {
    "claude-opus-4-7":   "arn:aws:bedrock:us-west-2:<acct>:application-inference-profile/<opus-id>",
    "claude-sonnet-4-6": "arn:aws:bedrock:us-west-2:<acct>:application-inference-profile/<sonnet-id>",
    "claude-haiku-4-5":  "arn:aws:bedrock:us-west-2:<acct>:application-inference-profile/<haiku-id>"
  }
}
```

Key points:
- **`AWS_REGION` must match the ARN's region** — Bedrock rejects cross-region invokes against an application profile.
- **`env` block** keeps `CLAUDE_CODE_USE_BEDROCK` and the model ARNs scoped to Claude Code only — not exported into other processes started from the same shell.
- **`ANTHROPIC_DEFAULT_*_MODEL` covers the `opus`/`sonnet`/`haiku` aliases** (the defaults `/model opus` etc. resolve through).
- **`modelOverrides` covers the explicit-version aliases** (`/model claude-opus-4-7`, etc.) so `/model` picker entries route to the right ARN.
- **`.zshrc` should only carry `AWS_PROFILE`** if you need it shell-wide; everything else lives in settings.json.

## IAM policy for the application-profile case (official, minimal)

If you control the account and want to author the policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "AllowApplicationProfileInvoke",
    "Effect": "Allow",
    "Action": [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:ListInferenceProfiles",
      "bedrock:GetInferenceProfile"
    ],
    "Resource": [
      "arn:aws:bedrock:*:*:application-inference-profile/*",
      "arn:aws:bedrock:*:*:foundation-model/*"
    ]
  }]
}
```

`bedrock:GetInferenceProfile` lets Claude Code resolve the application profile ARN to its backing foundation model and pick the right request shape; without it, Claude Code retries with the alternate shape (one extra round-trip per new model — works but slower).

## Verification recipe (after writing settings.json)

Requires Claude Code **v2.1.94+** (supports startup model checks with application inference profile ARNs). Upgrade first if needed:

```bash
claude update
claude --version
```

Then verify all three model families:

```bash
# Force-clear any stale shell exports first, so the test honours settings.json only
unset ANTHROPIC_DEFAULT_OPUS_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL CLAUDE_CODE_USE_BEDROCK

echo 'reply with exactly: BEDROCK-OK' | claude -p --model sonnet
echo 'reply with exactly: BEDROCK-OK' | claude -p --model opus
echo 'reply with exactly: BEDROCK-OK' | claude -p --model haiku
```

All three should return `BEDROCK-OK`. If only some pass, check which ARN was missing/typoed in settings.json — each model resolves through its own env var.

## Common misreads of the AccessDenied error

- ❌ "Credentials are stale" — `sts get-caller-identity` succeeds, so creds are fine. Don't rotate keys.
- ❌ "IAM perms got revoked" — possible but rare; verify by step 3 above before escalating.
- ❌ "Need to switch region" — only true if ARN region and `AWS_REGION` mismatch; switching region while keeping system-profile ID changes nothing.
- ✅ Almost always: env var points to wrong ARN class.

The cheapest test that distinguishes these: `aws bedrock-runtime invoke-model --model-id "<application-profile-ARN>"` directly. Two minutes saves an escalation thread to IT.

## 1M context window (`[1m]` alias)

Claude Code supports a 1M-token context window on select models via the `[1m]` suffix alias. On Bedrock, this requires both the **Claude Code client** and the **Bedrock inference profile** to support 1M.

### Supported models (1M)

| Model | 1M Alias | Bedrock Foundation Model |
|-------|----------|--------------------------|
| Claude Opus 4.7 | `opus[1m]` | `anthropic.claude-opus-4-7` ✅ |
| Claude Opus 4.6 | `opus[1m]` | `anthropic.claude-opus-4-6` ✅ |
| Claude Sonnet 4.6 | `sonnet[1m]` | `anthropic.claude-sonnet-4-6` ✅ |
| Claude Sonnet 4.5 | `sonnet[1m]` | `anthropic.claude-sonnet-4-5-20250929-v1:0` ✅ |
| Claude Haiku 4.5 | — | ❌ Not supported |

Models **not** supporting 1M: `claude-3-*`, `claude-opus-4-0/4-1/4-5`, `claude-haiku-4-5`.

### How to enable

**Two approaches, both valid:**

**① Simpler: Append `[1m]` to the ARN in env vars (recommended)**

Put `[1m]` directly on the ARN in `ANTHROPIC_DEFAULT_*_MODEL`. Claude Code strips the suffix before calling Bedrock. This makes 1M the always-on window for Opus/Sonnet without touching `modelOverrides` or `model` key.

```json
{
  "env": {
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "AWS_REGION": "us-west-2",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "arn:aws:bedrock:us-west-2:<acct>:application-inference-profile/<opus-id>[1m]",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "arn:aws:bedrock:us-west-2:<acct>:application-inference-profile/<sonnet-id>[1m]",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "arn:aws:bedrock:us-west-2:<acct>:application-inference-profile/<haiku-id>"
  }
}
```

You can drop `modelOverrides` entirely — `/model opus` resolves through the env var which already carries `[1m]`. Haiku stays bare (1M not supported).

**② Alternative: Leave ARN bare, set `model` in settings.json**

If you want the default model selection (not env var) to control 1M:

```json
{
  "model": "opus[1m]"
}
```

**Interactive (any approach):**
```
/model opus[1m]
```

**Disable 1M globally:**
```bash
export CLAUDE_CODE_DISABLE_1M_CONTEXT=1
```

### Important constraints

1. **Bedrock profile must support 1M** — verify in AWS Console → Bedrock → Inference profiles that your application profile lists the foundation model with 1M support. If the profile was created before 1M was available, you may need to recreate it.

2. **Cost is ~5×** — tokens beyond 200K are billed at long-context rates. Monitor AWS Bedrock billing.

3. **`modelOverrides` do not need `[1m]` suffix** — the alias is handled by Claude Code's model resolution, not the ARN. Keep your `modelOverrides` pointing at the same application-inference-profile ARN regardless of whether you use `opus` or `opus[1m]`.

4. **Switching back** — use `/model opus` (without `[1m]`) to return to the 200K window.

### Verification

```bash
# Confirm 1M is active
claude -p 'What is your context window size?' --model opus[1m] --max-turns 1

# Confirm disabled
CLAUDE_CODE_DISABLE_1M_CONTEXT=1 claude -p 'What is your context window size?' --model opus[1m] --max-turns 1
```
