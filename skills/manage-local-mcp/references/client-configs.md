# MCP Client Config Reference

Use this reference when updating or reviewing MCP client config. Keep edits scoped to one named server.

## Codex

Default path:

```text
~/.codex/config.toml
```

URL-based HTTP MCP entry:

```toml
[mcp_servers.playwright]
url = "http://localhost:8931/mcp"
```

Notes:

- Prefer URL entries for long-running local HTTP MCP services.
- Restart the Codex session if the current session does not pick up the change.

## Zed

Default path:

```text
~/.config/zed/settings.json
```

URL-based remote MCP entry:

```jsonc
{
  "context_servers": {
    "playwright": {
      "enabled": true,
      "url": "http://localhost:8931/mcp"
    }
  }
}
```

Notes:

- Zed uses `context_servers` for custom MCP servers.
- Preserve JSONC comments and unrelated settings when editing.

## Hermes Agent

Common path:

```text
~/.hermes/config.yaml
```

URL-based MCP entry:

```yaml
mcp_servers:
  playwright:
    url: "http://localhost:8931/mcp"
    enabled: true
```

Notes:

- Hermes supports both command-based and URL-based MCP servers under `mcp_servers`.
- Use `/reload-mcp` or restart Hermes after config changes.
- To keep config but stop loading a server, set `enabled: false`.
- OAuth tokens, when present, live outside the main config under `~/.hermes/mcp-tokens/`; do not print them.

## LaunchAgent Defaults

New macOS user services should use:

```text
Label: top.qiyuey.<name>-mcp
Path: ~/Library/LaunchAgents/top.qiyuey.<name>-mcp.plist
Logs: /tmp/<name>-mcp-<port>.log and /tmp/<name>-mcp-<port>.err.log
```

For current Playwright MCP compatibility:

```bash
npx @playwright/mcp@latest --port 8931 --host 127.0.0.1 --extension --browser=msedge
```
