---
name: manage-local-mcp
description: Manage local MCP services and client wiring. Use when asked to create, delete, inspect, repair, or configure a local MCP server for Codex, Hermes, Zed, or another MCP client, especially localhost HTTP services such as Playwright MCP.
---

# Manage Local MCP

## Overview

This skill manages user-level MCP services and the client config that points to them. It is for the whole lifecycle: discover the current source, create or remove a macOS LaunchAgent, wire or unwire Codex/Hermes/Zed, and verify the MCP endpoint.

Default naming for managed services is `top.qiyuey.<name>-mcp`. Treat older personal LaunchAgent labels as legacy and migrate them to `top.qiyuey.*` during cleanup or repair work.

## Workflow

1. Identify the service: capture `name`, `url`, `port`, host, launch command, current process owner, and intended client targets.
2. Audit before changing anything:
   ```bash
   python3 scripts/local_mcp.py doctor --name playwright --port 8931 --url http://localhost:8931/mcp --clients codex,zed,hermes
   ```
3. Create or replace the user service only after confirming the intended command:
   ```bash
   python3 scripts/local_mcp.py install-service --name playwright --port 8931 --playwright-edge --force
   ```
4. Configure clients by URL for HTTP MCP servers:
   ```bash
   python3 scripts/local_mcp.py configure --name playwright --url http://localhost:8931/mcp --clients codex,zed,hermes
   ```
5. Verify again with `doctor`. For Hermes, use `/reload-mcp` or restart Hermes after config edits. Restart or reopen Codex/Zed sessions if they do not hot-reload MCP config.
6. Remove in two separate steps when deleting a service:
   ```bash
   python3 scripts/local_mcp.py remove-config --name playwright --clients codex,zed,hermes
   python3 scripts/local_mcp.py uninstall-service --name playwright
   ```

## Safety Rules

- Never print or copy secrets from launch plists, environment variables, logs, or MCP client config. Redact tokens in summaries.
- Back up config files before editing. The script writes `.bak-local-mcp-<timestamp>` backups automatically.
- Do not kill arbitrary processes on a port. Use `uninstall-service` for managed LaunchAgents; only use `--kill-port` when the user explicitly asks and the process ownership is clear.
- Keep service lifecycle and client config as separate operations. A working MCP endpoint may be intentionally shared by multiple clients.
- Prefer URL-based client entries for local HTTP or Streamable HTTP servers. Use command-based entries only when the MCP server is meant to be spawned by that client.
- If a config file has unusual formatting, inspect it first and patch conservatively instead of running broad rewrites.

## Common Tasks

### Inspect A Local MCP

Use `lsof`, LaunchAgent inspection, and the MCP initialize probe:

```bash
python3 scripts/local_mcp.py doctor --name playwright --port 8931 --url http://localhost:8931/mcp --clients codex,zed,hermes
```

If the service is unmanaged or uses an old label, report the exact process, parent command, plist path, and config references. Do not assume a matching port belongs to this skill.

### Create Playwright MCP

For the standard local browser bridge:

```bash
python3 scripts/local_mcp.py install-service --name playwright --port 8931 --playwright-edge --force
python3 scripts/local_mcp.py configure --name playwright --url http://localhost:8931/mcp --clients codex,zed,hermes
```

Pass extension tokens through `--env KEY=VALUE` only when the user intentionally provides them. Avoid putting secret values in chat.

### Delete A Managed MCP

Remove client entries first, then unload and delete the LaunchAgent:

```bash
python3 scripts/local_mcp.py remove-config --name playwright --clients codex,zed,hermes
python3 scripts/local_mcp.py uninstall-service --name playwright
```

For Hermes, default removal disables the server with `enabled: false`, matching Hermes' supported behavior. Use `--delete` only when the user asks for physical removal from the config file.

## Resources

- `scripts/local_mcp.py`: helper for LaunchAgent lifecycle, MCP probing, and Codex/Zed/Hermes config edits.
- `references/client-configs.md`: client-specific config shapes, paths, and reload notes.
