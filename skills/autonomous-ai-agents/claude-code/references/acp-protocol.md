# Hermes ↔ Claude Code：ACP 协议通信

## 什么是 ACP

ACP（Agent Client Protocol）是 2025 年 9 月由 Zed Industries 发起的开放 JSON-RPC 标准，定位是**编辑器/客户端与 AI 编码 Agent 之间的协议**（类比 LSP）。

- **传输层：** JSON-RPC over stdio（本地进程间，NDJSON 事件流）
- **架构：** 客户端（Hermes）spawn 子进程（Claude Code ACP server），双方走结构化事件流
- **不是 MCP：** MCP 是"AI 应用调工具"，ACP 是"客户端调 Agent"，方向相反

```
Hermes (ACP client)
    ↕ JSON-RPC / stdio (NDJSON 事件流)
claude-agent-acp (ACP server, wraps Claude Code)
    ↕
Claude Code (实际执行编码任务)
```

Hermes 自身也支持 ACP server 模式：`hermes acp`（可被 Zed、VS Code ACP Client 等调用）。

## 两种调用方式对比

| 方式 | 协议 | 优点 | 缺点 |
|---|---|---|---|
| `terminal` + `claude -p` | 无协议，直接 shell subprocess | 零配置，即开即用 | 无 session 记忆，无结构化事件 |
| `terminal(pty:true)` + `claude-as-acp` | **ACP / NDJSON** | 多轮有状态续接，结构化输出，可观测 | 需要 `claude-as-acp` wrapper |

**推荐：多轮/复杂任务用 ACP；一次性简单任务用 `-p`。**

## claude-as-acp wrapper 用法

本机已安装 `claude-as-acp`（来自 `delegate-to-claude-code-acp` skill）。

```bash
# 基本调用（pty:true 是必须的）
claude-as-acp --task <task-name> '<完整任务描述>'

# 同名 task = 自动续接 session（有记忆）
claude-as-acp --task refactor-auth "重构 auth.py 用 JWT"
claude-as-acp --task refactor-auth "再加单元测试"   # Claude 记得上一轮

# 新名 task = 全新 session（从零）
claude-as-acp --task new-feature "写个新功能"
```

### task 命名规则

- 格式：`^[a-z0-9][a-z0-9-]{0,63}$`（小写字母、数字、`-`，不能以 `-` 开头）
- 好：`refactor-auth`、`hello-py`、`todo-list-node`
- 坏：`Refactor Auth`（大写+空格）、`my_task`（下划线）

### 核心规则

1. **同名 = 有记忆续接，不同名 = 从零开始**，不确定时保守选旧名
2. **必须 `pty:true`**，ACP server 需要真终端
3. 每次调用结束后在 reply 末尾写 `📎 CC task: <名字>`，下一轮从中读取

## NDJSON 事件类型

| event type | 含义 |
|---|---|
| `task` | 第一行，含 `{name, cwd, resuming}`，`resuming:true` 表示续接 |
| `text` | Claude 的回复文本（流式分片，拼起来是主内容） |
| `tool_call` | Claude 开始调工具 |
| `tool_update` | 工具调用中/完成，`status:completed/failed` |
| `done` | 本轮结束，含 `stop_reason`、`error`、`next_call_hint` |
| `wrapper_timeout` | 超时，进度已保存，可用同名 task 继续 |

## ACP 生态（截至 2025-2026）

支持 ACP 的 Agent：Claude Code（via `claude-agent-acp`）、Gemini CLI、Codex CLI、GitHub Copilot、OpenCode、OpenHands、Kiro CLI、Cursor、Qwen Code、**Hermes Agent**（`hermes acp`）等。

参考：[agentclientprotocol.com](https://agentclientprotocol.com)
