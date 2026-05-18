# Bedrock adaptive thinking for Claude Code

## When this applies

Use this note when Claude Code is configured with Amazon Bedrock and a newer Claude model, especially Opus 4.7 via an Application Inference Profile ARN, and a smoke test fails with an error like:

```text
API Error: 400 "thinking.type.enabled" is not supported for this model.
Use "thinking.type.adaptive" and "output_config.effort" to control thinking behavior.
```

## Current model behavior

- Opus 4.7 only supports adaptive thinking: `thinking: {"type": "adaptive"}` plus `output_config.effort`.
- Opus 4.6 and Sonnet 4.6 support adaptive thinking and should prefer it; legacy `thinking.type: "enabled"` with `budget_tokens` is deprecated.
- Older Claude 4.x models may still require legacy thinking with `budget_tokens`.
- Claude Code versions before the Bedrock adaptive-thinking fix may send the legacy thinking format to Bedrock when an inference profile ARN resolves to Opus 4.7.

## Preferred remediation

1. Upgrade Claude Code first:

```bash
claude update
claude --version
```

2. Re-test with a one-turn print-mode smoke test:

```bash
claude -p 'Reply exactly: CLAUDE_CODE_OK' --model opus --max-turns 1 --output-format json
```

Success criteria:

- process exits `0`
- JSON result has `is_error: false`
- `result` contains the requested sentinel
- `modelUsage` points at the expected Bedrock profile/model

3. Avoid long-term `MAX_THINKING_TOKENS=0` as the fix. It can be useful only as a temporary diagnostic because it disables thinking rather than adopting the new adaptive-thinking API.

4. Do not globally force `CLAUDE_CODE_EFFORT_LEVEL=max` unless the user explicitly wants that cost/latency tradeoff. Recent Claude Code versions have model-aware defaults (for example xhigh on Opus 4.7 and high on Opus/Sonnet 4.6), which is usually the best-practice baseline.

5. If upgraded Claude Code still fails only for Opus over Bedrock, temporarily set the default model to `sonnet` while keeping the Opus alias/profile available for manual testing. Treat that as a compatibility workaround, not the desired final state.

## Verification matrix

Run all three to prove both the default and aliases work:

```bash
claude -p 'Reply exactly: CLAUDE_CODE_DEFAULT_OK' --max-turns 1 --output-format json
claude -p 'Reply exactly: CLAUDE_CODE_OPUS_OK' --model opus --max-turns 1 --output-format json
claude -p 'Reply exactly: CLAUDE_CODE_SONNET_OK' --model sonnet --max-turns 1 --output-format json
```

Also check provider/auth:

```bash
claude auth status --text
```

Expected for Bedrock setups:

```text
API provider: Amazon Bedrock
AWS region: <region>
```
