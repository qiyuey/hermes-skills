# 在 OpenClaw 容器里接入 Claude Code + Companion 的设计方案

> 起草时间:2026-04-14
> 作者:song + Claude(设计对话产出,详见对话归档)
> 状态:设计完成,待实施
>
> 相关文档:
> - [`companion-product-analysis.md`](../0329-claudecode安卓套壳/companion-product-analysis.md) —— Companion 产品调研
> - [`claude-session-architecture-analysis.md`](../0329-claudecode安卓套壳/claude-session-architecture-analysis.md) —— Claude Code `--sdk-url` 调研与 PoC 失败复盘
> - [`claude-code-via-android.md`](../0329-claudecode安卓套壳/claude-code-via-android.md) —— 现有 tmux + FIFO + tail 路线的完整实现文档
> - `infra-foreman/infra-foreman-server/OPENCLAW_CHAT_PROTOCOL.md` —— foreman ↔ OpenClaw 协议

---

## 1. 背景 & 目标

### 1.1 现状

企微 → infra-foreman → OpenClaw 容器里的 OpenClaw 服务,是一条已经跑起来的链路。用户在企微里聊天,foreman 作为代理把消息转给 OpenClaw,收流式回复、按 streamId 缓存,然后被企微端轮询或 Emon hook 回写。

**问题是 OpenClaw 不太靠谱**。它是一个用 LLM 驱动的个人助理 agent,跑复杂任务时经常走偏(见 `devdocs/0410-openclaw/openclaw-sessions/` 这 8 次 yuque 适配器尝试,6 次 SKILL.md 迭代后依然只有约 50% 稳定性)。日常体验里表现为:

- 任务执行一半卡住
- 偶发进程崩溃
- 规则遵循度不稳定,对"严格按步骤做"的指令经常绕开
- 出故障后用户只能等或重启容器

### 1.2 目标

在**同一个 OpenClaw 容器里** 并排跑一个 Claude Code,通过 Companion 这个开源 wrapper 暴露出来,让 foreman 能按需把某个会话(sessionKey 粒度)的后端从 OpenClaw 切换到 Claude Code。

**典型用法**:

1. 用户在企微里正常聊天,走 OpenClaw
2. OpenClaw 出故障 / 或者用户想做一个 Claude Code 更擅长的任务(编码、严格流程、容器运维)
3. 用户发 `/claude` → foreman 把这个 sessionKey 的后端切到 Claude Code
4. 后续消息都走 Claude Code,Claude Code 可以直接操作容器(比如重启 OpenClaw)
5. 用户发 `/openclaw` 切回

同时提供一个 **外部 Web UI**:用户从浏览器打开 `http://<宿主机>:3456/` 也能访问同一个 Claude Code 会话,跟企微端 **双向同步**,任一端发的消息另一端都能看到。

### 1.3 非目标

- 不追求 Claude Code 取代 OpenClaw,两者是协作关系
- 不追求工具权限审批(一期全开,见 §11)
- 不追求跨用户的会话共享(sessionKey 粒度隔离)
- 不做 Claude Code 的独立 UI(直接用 Companion 自带的)

---

## 2. 方案选型

### 2.1 候选方案

| 方案 | 说明 |
|---|---|
| **A. tmux + FIFO + tail**(复用安卓套壳) | `infra-foreman-bot/clawdbot` 里既有的 `ClawdBotWebSocketConnectionManager` 路线不够用,因为它连的是 OpenClaw Gateway;要复用的话是借 `my-ai-playground` 项目的 `ClaudeProcessManager.java` / `TmuxProcessManager.java` 那套。容器里起 tmux,FIFO 作为 claude stdin,日志文件 tail 作为 stdout,foreman 侧重写一个 Java 包装,在其上再做 fanout 和 Web UI |
| **B. Companion + `--sdk-url`** | OpenClaw 容器里预装 [the-companion](https://github.com/The-Vibe-Company/companion) + Bun + Claude Code CLI,Companion 作为 HTTP+WebSocket 服务在容器里守常驻;foreman 加一个 WebSocket 客户端连 Companion 的 `/ws/browser/:sessionId` 端点 |

### 2.2 真实对比(含收回错话)

前几轮设计讨论里我说过一句"tmux 方案解析终端文本,脆弱" —— **这是错的,在此收回**。

实测 `my-ai-playground` 项目里的 `claude-code-via-android.md` 文档明确:

```
claude --print --input-format=stream-json --output-format=stream-json --verbose --include-partial-messages
```

**当前 tmux 路线走的就是 stream-json NDJSON**,跟 Companion 走的 `--sdk-url` 协议**吃的是同一份 NDJSON**,消息类型 `system/init` / `stream_event` / `result` 完全相同。两边在"协议脆弱性"这个维度上**等价**。

真实的差异在下面这张表里:

| 维度 | A. tmux + FIFO + tail | B. Companion + `--sdk-url` |
|---|---|---|
| 消息协议 | stream-json NDJSON | stream-json NDJSON |
| 协议版本脆弱性 | 同(都取决于 CC 版本) | 同 |
| 版本固化效果 | ✅ 同样有效 | ✅ 同样有效 |
| stdin 传输 | FIFO `<>` 读写模式 | WebSocket frame |
| stdout 传输 | 日志文件 + 50ms RandomAccessFile 轮询 | WebSocket 流式 push |
| 进程生命周期 | tmux session 解耦(我们自己写) | Companion CliLauncher(现成) |
| 会话续接 | `--resume <sid>` | `--resume <sid>`(完全一样) |
| 权限审批 | 无(tmux 方案没法拦截) | control_request 结构化,Companion 有完整实现 |
| **多 client fanout** | ❌ **单端,自己写 hub** | ✅ **原生支持**(多浏览器连 `/ws/browser/:id`) |
| **外部 Web UI** | ❌ 自己写 React + 后端 API | ✅ **自带**(`http://localhost:3456/`) |
| 已踩过的坑 | `claude-code-via-android.md` 列了 20+ 条(session_conflict 五版演进、resume 失败、tail 竞态、silenceOutput、tmux session 上限、warmup 输出泄露…) | 相对少,主要是 CC 协议版本漂移 |
| 团队沉没成本 | 已写好的安卓套壳 Java 代码 | 需要新增 foreman 集成层 |

### 2.3 为什么选 B(Companion)

**主要理由有三条,"协议稳不稳"不在其中**:

1. **把传输层脏活外包了** —— `claude-code-via-android.md` 2300+ 行,大部分都在讲 FIFO/tmux/tail/processMap 分支/session_conflict 修复演进/warmup 屏蔽竞态等等,**这些全是 tmux+日志+FIFO 路线专属的坑**。Companion 走 WebSocket 没有这层,坑也就不存在。
2. **双端 fanout 天然就有** —— 用户明确需要"企微端 + 外部 Web UI 同步可见"。Companion 的 `/ws/browser/:id` 端点本质就是一个多消费者的 hub(多个浏览器/多个 foreman 连接都能收到同一个 CC session 的流式回复),这在 tmux 方案下需要从零做。
3. **外部 Web UI 零成本** —— Companion 自带 React 前端 + WebSocket 协议 + 会话列表 + 工具调用可视化 + 权限审批对话框,全套开箱可用。tmux 方案下这部分要从零写。

**代价要诚实承认**:

- tmux 方案是已经被真实流量打磨过的代码,Companion 在我们这个场景下是全新引入,上线第一周肯定会踩它特有的陌生坑(协议漂移、某些 edge case 行为等)
- 依赖一个相对年轻的第三方项目(截至 2026-04,Companion 2.3k stars,v0.95.x),版本锁死后不能跟进的话,未来某天可能被 Anthropic 协议变更卡住
- Companion 把 sessionId 用 `randomUUID()` 自己生成(`cli-launcher.ts:290`),不接受外部 ID,需要 foreman 侧维护一层 sessionKey ↔ companionSessionId 的映射(见 §6.3)

`devdocs/0329-claudecode安卓套壳/claude-session-architecture-analysis.md` 里做过一次 `--sdk-url` 的 PoC 并失败了 —— 当时卡在 system/init 握手后 claude 不响应用户消息。失败原因在那份文档里复盘过:**缺少完整的握手协议,server 侧需要主动发的初始化消息序列没找全**。Companion 专门逆向了这个协议,写了 `WEBSOCKET_PROTOCOL_REVERSED.md`,**这块是我们当时缺的那块**。所以选 B 等于"站在别人解过的坑上"。

---

## 3. 目标架构

### 3.1 拓扑

```
  ┌─────────────┐      ┌─────────────┐
  │  企业微信 A  │      │  企业微信 B  │
  └──────┬──────┘      └──────┬──────┘
         │                    │
         └─────────┬──────────┘
                   │ (HTTP 业务协议)
                   ▼
         ┌──────────────────────────────┐
         │     infra-foreman            │
         │                              │
         │  BotStreamFacade.triggerStream
         │         │                    │
         │         ▼                    │
         │  BackendModeStore (MySQL)    │
         │    sessionKey → openclaw/companion
         │         │                    │
         │  ┌──────┴────────┐           │
         │  ▼               ▼           │
         │ Clawd…(现有)  Companion…(新) │
         │ WSManager     WSManager      │
         └──────┬────────────┬──────────┘
                │            │
                │            │ WebSocket (双向)
                │            │ HTTP REST (幂等管理)
                │            │
                ▼            ▼
    ┌───────────────────────────────────────┐
    │         OpenClaw 容器(Linux)         │
    │                                       │
    │  OpenClaw 服务(已存在)              │
    │    端口:内部 x 对外 80              │
    │                                       │
    │  Companion 服务(新增,常驻)         │
    │    端口:3456                        │
    │    ├── HTTP /api/*                    │
    │    ├── WS /ws/browser/:id  ←─ foreman │
    │    ├── WS /ws/cli/:id    ←─ claude   │
    │    └── 静态文件 /            ←─ 浏览器│
    │                                       │
    │  claude 进程(每 session 一个)       │
    │    被 Companion 按需拉起,持久化到   │
    │    ~/.claude/projects/<...>.jsonl     │
    │                                       │
    │  volumes:                             │
    │    ~/.openclaw    ← 现有              │
    │    ~/.companion   ← 新增              │
    │    ~/.claude      ← 新增              │
    └───────────────────────────────────────┘
                       ▲
                       │ 宿主机端口映射
                       │ -p 3456:3456
                       │
              ┌────────┴─────────┐
              │  外部 Web UI     │
              │  浏览器直接访问   │
              │  http://host:3456│
              └──────────────────┘
```

同一个 Claude Code session **可以同时被**:
- foreman(代企微 A)
- foreman(代企微 B,如果 A 和 B 属于同一 sessionKey)
- 外部浏览器 1
- 外部浏览器 2

**连上**,任何一端发消息所有人都看得到,任何一端发 `/stop` 所有人都收到 abort 事件。这是 Companion `/ws/browser/:id` 端点的天然语义(见 §14.3 fanout 机制)。

### 3.2 数据流:普通消息

```
[企微 A] → 发消息 "帮我看一下 openclaw 日志"
  ↓ HTTP (foreman 业务协议)
[foreman] BotStreamFacade.triggerStream(request)
  ↓ 查 BackendModeStore
mode = "companion"
  ↓ 查 companion_session_id 从 DB
已存在 UUID = "abc-123"
  ↓ 查 Companion HTTP: GET /api/sessions/abc-123
state = "connected"(存活)
  ↓ 若尚未建立,现场连 WS:ws://container:3456/ws/browser/abc-123
  ↓ WS 发: { type: "user_message", content: "...", client_msg_id: uuid }
[Companion] WsBridge 收到,转给对应 claude 进程(/ws/cli/abc-123)
  ↓ NDJSON: {"type":"user","message":{...}}
[claude] 开始处理 → 流式产出 stream_event / assistant / result
  ↓ NDJSON over /ws/cli/abc-123
[Companion] 收到 → 广播给所有连在 /ws/browser/abc-123 的 client
  ↓
[foreman] 收到 stream_event → 追加到 StreamMessageStorage(streamId 下的 buffer)
  ↓
[企微 A] 下次轮询 GET /stream/<id> → 看到流式更新
```

### 3.3 数据流:切换指令

```
[企微] → 发 "/claude"
  ↓
[foreman] 消息入口拦截(在 dispatchInboundMessage 之前)
  ↓ 判断是 /claude
  ↓ 写 DB: UPDATE session_backend_mode
           SET current_backend='companion', switched_at=NOW()
           WHERE session_key=?
  ↓ 查 DB: companion_session_id 是否已存在
  ↓ 不存在 → 走 Companion HTTP: POST /api/sessions/create
            body: { backend: "claude", env: { ANTHROPIC_API_KEY: ... },
                    permissionMode: "bypassPermissions" }
            ← 返回 { sessionId: "abc-123", state: "starting" }
  ↓ 写 DB: 存 companion_session_id = "abc-123"
  ↓ 建立 WS 连接 ws://container:3456/ws/browser/abc-123
  ↓ 回复企微: "已切换到 Claude Code(session: abc-123)"
```

切回:

```
[企微] → 发 "/openclaw"
  ↓ foreman 拦截
  ↓ 写 DB: UPDATE session_backend_mode SET current_backend='openclaw' WHERE ...
  ↓ 注意:不杀 Companion 的 CC session,只停止路由
  ↓ 回复企微: "已切换到 OpenClaw"
```

### 3.4 数据流:`/stop` 中断

```
[企微] → 发 "/stop"
  ↓ foreman 拦截
  ↓ 当前 backend = companion
  ↓ 已有 /ws/browser/<id> 连接
  ↓ WS 发: { type: "interrupt", client_msg_id: uuid }
[Companion] 收到 interrupt
  ↓ 经过 browser-ingest 去重(见 ws-bridge-browser-ingest.ts:17)
  ↓ 转发到 /ws/cli/<id>
  ↓ control_request: { subtype: "interrupt" }
[claude] 接收 interrupt
  ↓ 打断当前 turn
  ↓ 发 result (subtype: "aborted")
[Companion] 广播 aborted 事件给所有 /ws/browser/<id> client
  ↓
[foreman] 看到 aborted → markFinished 对应 streamId,状态透传给企微
[浏览器外部 Web UI] 同时也看到 aborted,UI 停止旋转圈圈
```

### 3.5 数据流:重启 / relaunch / 自愈

```
场景:容器重启后,claude 进程全死,companion-data volume 保留了 launcher.json

[Companion] 启动时 CliLauncher.restoreFromDisk() 扫 ~/.companion/
  ↓ 读 launcher.json,拿到所有 sessionId + pid
  ↓ process.kill(pid, 0) 探活,全部失败 → 状态改 exited
  ↓ 等待 browser 重连触发 relaunch

[企微] 发消息 "继续"
  ↓ foreman 查 DB 拿到 companion_session_id="abc-123"
  ↓ HTTP GET /api/sessions/abc-123 → state="exited"
  ↓ HTTP POST /api/sessions/abc-123/relaunch
[Companion] 用存下来的 cliSessionId 启 claude --resume <cliSessionId>
  ↓ 若 5s 内退出(resume 失败),Companion 自动清 cliSessionId(cli-launcher.ts:600)
  ↓ 下一次 create 就是全新 session
  ↓ foreman 探测到 exited → 调 POST /api/sessions/create 新建
  ↓ 更新 DB 里的 companion_session_id
  ↓ 回消息给用户: "历史已重置,新会话开始"
```

---

## 4. 关键决策清单

所有设计决策一处列全,便于快速回顾。

| # | 决策 | 结论 | 理由 / 依据 |
|---|---|---|---|
| D1 | 候选方案 | Companion + `--sdk-url` | §2.3 |
| D2 | 凭证方式 | Anthropic API key(与 OpenClaw 同款) | 无 OAuth 流程、无 credentials 文件冲突、无状态 |
| D3 | API key 注入方式 | 容器启动时通过 env `ANTHROPIC_API_KEY=...` 注入 Companion 进程 | Companion spawn CC 时会继承 env (cli-launcher.ts:spawnEnv) |
| D4 | 工具权限 | 全 allow,`permissionMode: "bypassPermissions"` | 企微没法做审批 UI;需要执行自由度 |
| D5 | Companion 运行身份 | 容器内 **node** 非 root 用户 | 避免 Companion 源码对 root 的自动降级(cli-launcher.ts:540) |
| D6 | 容器部署方式 | 阶段一:`docker exec` 临时装跑通;阶段二:打新镜像 `openclaw-cc:YYYY.MM.DD-pinned` | 迭代友好 + 最终固化 |
| D7 | 版本固化 | Bun + Claude Code + Companion + 镜像 tag 四件套全锁 | 把 CC 升级风险从被动变主动 |
| D8 | 切换指令 | `/claude` 切入 Claude Code,`/openclaw` 切回 OpenClaw | 直白易记 |
| D9 | 切换粒度 | 按 sessionKey(企微用户 × 群聊维度) | 最小惊吓原则;参考 foreman `resolveSessionKeyForRouting` |
| D10 | BackendMode 持久化 | MySQL 表 `session_backend_mode`,foreman 重启不丢 | 见 §7 DDL |
| D11 | sessionKey ↔ Companion UUID 映射 | foreman DB 维护 `companion_session_id` 字段,**不改 Companion 源码** | Companion `launch()` 在 cli-launcher.ts:290 硬编码 `randomUUID()`,改源码会破坏版本固化 |
| D12 | `/claude` 再次进入时的会话续接策略 | 优先 `POST /api/sessions/:id/relaunch`(带 `--resume` 恢复历史),失败 fallback 新建 | 语义对齐 OpenClaw 持久会话 |
| D13 | 系统提示词 / 人设注入 | `~/.claude/CLAUDE.md` 挂 volume,CC 启动时自动加载 | Companion 对 Claude 没接通 systemPrompt 字段(只用于 Codex),CLAUDE.md 是 CC 原生支持的机制 |
| D14 | 外部 Web UI 入口 | 容器直接暴露 3456 端口(和 OpenClaw 80 端口平级),无 foreman 反代 | 简化架构;依赖 Companion 自带 token 鉴权 |
| D15 | Companion auth token | 从 env `COMPANION_AUTH_TOKEN` 外部注入固定值,不用自动生成 | 可审计、可轮换、外部可调用 |
| D16 | Docker volumes | `~/.openclaw`(现有)+ `~/.companion`(新)+ `~/.claude`(新) | 三个缺一不可:Companion session 元信息、CC 对话 transcript、CC 的 CLAUDE.md |
| D17 | `/stop` 实现 | WS 发 `{"type":"interrupt","client_msg_id":uuid}`,不走 HTTP | 证据见 §14.5 / ws-bridge-browser-ingest.ts:17 |
| D18 | `/clear` 实现 | CC 源码(clear/index.ts:14)把 `/clear` 与 `/new` 合并;foreman 拦截后走与 `/new` 相同路径,并回提示 | 语义差异要告知用户 |
| D19 | `/compact` 实现 | 原样透传给 CC(CC 原生 `supportsNonInteractive: true`) | 见 §11 |
| D20 | OpenClaw 专属命令在 CC 路径下行为 | 30+ 条专属命令(/skill /mcp /subagents /plugins …) 一律报错并提示切回 OpenClaw | 见 §11 |
| D21 | 回归测试套件位置 | 新建 `infra-foreman-cc-e2e` 子模块,6 条黄金路径脚本 | 版本升级前强制跑 |
| D22 | Claude Code / Companion / Bun 具体版本号 | **延后到部署时定**,在 `deployment-phase1.md` 记录实际选用版本 | 部署时查 latest |

---

## 5. foreman 改动清单(文件级,不含代码)

**设计原则**:新增与 Companion 相关的代码尽量集中在一个新子包下,与现有 `clawdbot/` 并列平行;尽量不改 `StreamMessageService` / `StreamMessageStorage` / 企微 server 模块 / Emon 模块(它们是后端无关的基础设施)。

### 5.1 新增文件

位于 `infra-foreman-bot/src/main/java/com/yuanfudao/infra/foreman/bot/`:

| 文件 | 职责 |
|---|---|
| `companion/CompanionHttpClient.java` | 包装 Companion HTTP REST API 调用:`createSession` / `getSession` / `killSession` / `relaunchSession`。基于 `RestTemplate` + `COMPANION_AUTH_TOKEN` Bearer |
| `companion/CompanionWebSocketConnectionManager.java` | 平行于现有 `ClawdBotWebSocketConnectionManager`,管理 `/ws/browser/:id` 长连接池,含重连 + 心跳 + client_msg_id 去重 |
| `companion/CompanionWebSocketClient.java` | 单条 WS 连接的封装,处理 NDJSON 帧解析与 outbound 发送 |
| `companion/CompanionMessageTranslator.java` | Companion browser WS 消息 ↔ foreman 内部事件的翻译层。Companion 发来的 `stream_event` / `assistant` / `result` / `aborted` → 对接现有的 `appendContent` / `markFinished` / `markInterrupted` / `markError` |
| `companion/CompanionLaunchOptions.java` | DTO,封装创建 session 时的参数(permissionMode、env、cwd 等) |
| `backend/BackendMode.java` | enum: `OPENCLAW` / `COMPANION` |
| `backend/BackendModeStore.java` | 基于 MySQL 的持久化存储,接口 `getMode(sessionKey)` / `setMode(sessionKey, mode, companionSessionId)` / `getCompanionSessionId(sessionKey)` |
| `backend/BackendSwitchHandler.java` | 识别 `/claude` / `/openclaw` 指令,执行切换动作,返回给用户的确认消息 |
| `backend/BackendRouter.java` | 门面,在 `BotStreamFacade.triggerStream` 内部被调用,根据当前 sessionKey 的 BackendMode 决定把消息路由给 `ClawdBotWebSocketConnectionManager` 还是 `CompanionWebSocketConnectionManager` |

位于 `infra-foreman-bot/src/main/resources/mapper/`:

| 文件 | 职责 |
|---|---|
| `SessionBackendModeMapper.xml` | MyBatis mapper for `session_backend_mode` 表 |

位于 `infra-foreman-bot/src/main/resources/db/`:

| 文件 | 职责 |
|---|---|
| `session_backend_mode.sql` | 建表 DDL(见 §7) |

### 5.2 改动文件

| 文件 | 改什么 |
|---|---|
| `BotStreamFacade` 实现类(在 `infra-foreman-server` 里) | `triggerStream` 入口处调用 `BackendRouter.route()`,按 mode 转发到对应后端 |
| `BotSlashCommandHandler` 或等价拦截器 | 在指令分发前先判断是否是 `/claude` / `/openclaw` / `/new` / `/clear` / `/stop` / `/compact`,根据当前 BackendMode 决定本地处理还是透传;必要时拒绝 OpenClaw 专属命令并提示 |
| `BotStreamConfig`(或同目录配置类) | 新增 Companion 相关配置项:`companion.base-url` / `companion.auth-token` / `companion.enabled` |
| `infra-foreman-bot/pom.xml` | 若需要新增 WebSocket 客户端依赖,检查 `java-websocket` 或类似库是否已在(应该已在,现有 clawdbot 也在用) |

### 5.3 不改动的文件

明确记录不动,避免后续 refactor 误伤:

- `StreamMessageService` / `StreamMessageStorage` —— 后端无关,两条后端路径共用同一个 stream 缓存
- `infra-foreman-server` 的企微 API controller —— 企微端完全无感知后端切换
- `infra-foreman-emon` —— Emon 的 finishHook 也是后端无关
- `ClawdBotWebSocketConnectionManager` —— 只跑 OpenClaw 路径,Companion 路径走平行的新类

---

## 6. foreman 核心流程(描述,不是代码)

### 6.1 消息入口路由

```
triggerStream(request):
  sessionKey = resolveSessionKeyForRouting(channelId, userLdap, chatid)
  mode       = BackendModeStore.getMode(sessionKey)   // 默认 OPENCLAW

  slashCmd = parseSlashCommand(request.content)
  if slashCmd in [/claude, /openclaw]:
    return BackendSwitchHandler.handle(sessionKey, slashCmd)

  if mode == COMPANION and slashCmd is OpenClaw-exclusive:
    return errorResponse("此命令仅 OpenClaw 模式可用,请先发 /openclaw 切回")

  if mode == COMPANION and slashCmd in [/new, /clear]:
    return CompanionRoutes.resetSession(sessionKey)

  if mode == COMPANION and slashCmd == /stop:
    return CompanionRoutes.interrupt(sessionKey)

  if mode == COMPANION:
    return CompanionRoutes.sendMessage(sessionKey, request)
  else:
    return ClawdBotRoutes.sendMessage(sessionKey, request)   // 现状不变
```

### 6.2 BackendSwitchHandler 逻辑

```
handle(sessionKey, /claude):
  BackendModeStore.setMode(sessionKey, COMPANION)
  companionSessionId = BackendModeStore.getCompanionSessionId(sessionKey)
  if companionSessionId is null:
    companionSessionId = CompanionHttpClient.createSession(
      backend="claude",
      env={ANTHROPIC_API_KEY: config.apiKey},
      permissionMode="bypassPermissions",
      cwd="/workspace"
    )
    BackendModeStore.setCompanionSessionId(sessionKey, companionSessionId)
  return reply("已切换到 Claude Code(session: <last 8 chars of UUID>)")

handle(sessionKey, /openclaw):
  BackendModeStore.setMode(sessionKey, OPENCLAW)
  // 不杀 Companion session,下次 /claude 可以续接
  return reply("已切换到 OpenClaw")
```

### 6.3 CompanionRoutes.sendMessage 核心

```
sendMessage(sessionKey, request):
  companionSessionId = BackendModeStore.getCompanionSessionId(sessionKey)

  // 1. 探活
  sessionInfo = CompanionHttpClient.getSession(companionSessionId)
  if sessionInfo is null:
    // 可能被 Companion 清掉了(极少)
    companionSessionId = createFreshSession(sessionKey)
  else if sessionInfo.state == "exited":
    ok = CompanionHttpClient.relaunchSession(companionSessionId)
    if not ok:
      CompanionHttpClient.killSession(companionSessionId)
      companionSessionId = createFreshSession(sessionKey)
      appendSystemMessage("[历史已重置,新会话开始]")

  // 2. 拿到 WS 连接
  wsClient = CompanionWebSocketConnectionManager.getOrConnect(
    companionSessionId,
    onMessage: msg => CompanionMessageTranslator.translate(msg, streamId)
  )

  // 3. 发用户消息
  wsClient.send({
    type: "user_message",
    client_msg_id: UUID.random(),
    content: request.content,
    attachments: request.attachments
  })

  // 4. 返回 streamId,后续流式更新走 CompanionMessageTranslator 写 StreamMessageStorage
  return TriggerResult.success(streamId)
```

### 6.4 CompanionMessageTranslator 的翻译规则

| Companion 的 NDJSON 事件 | 对应 foreman 内部动作 |
|---|---|
| `{type:"stream_event", event:{type:"content_block_delta", delta:{type:"text_delta", text:"..."}}}` | `appendContent(streamId, text)` |
| `{type:"stream_event", event:{type:"content_block_start", content_block:{type:"tool_use", name:"Bash"}}}` | `appendContent(streamId, "\n[工具: Bash]\n")` |
| `{type:"stream_event", event:{type:"content_block_delta", delta:{type:"input_json_delta", text:"..."}}}` | `appendContent(streamId, text, color=orange)` or 折叠不展示 |
| `{type:"assistant", message:{content:[...]}}` | 必要时用完整 content 兜底修正 chatStream(防止 delta 漏收) |
| `{type:"result", subtype:"success"}` | `markFinished(streamId)` |
| `{type:"result", subtype:"aborted"}` | `markInterrupted(streamId, "[内容已中断]")` |
| `{type:"result", subtype:"error"}` | `markError(streamId, error.message)` |
| `{type:"session_update"}` | 忽略或刷新 UI 层会话元信息 |
| `{type:"permission_request"}` | 理论上 bypassPermissions 下不会收到;收到当成 error 处理 + 日志 |

> 注:具体字段名以实际实现时抓包核对为准。Companion 内部使用的消息类型定义在 `web/server/session-types.ts`。

---

## 7. 数据库 Schema

遵守项目 CLAUDE.md 里规定的建表模板:id、dbctime、dbutime 三个标准字段必须有。

```sql
CREATE TABLE `session_backend_mode` (
    `id` bigint unsigned NOT NULL AUTO_INCREMENT COMMENT '自增id',
    `session_key` varchar(128) NOT NULL COMMENT 'foreman 的 sessionKey(channelId + userLdap + chatid 衍生)',
    `current_backend` varchar(16) NOT NULL DEFAULT 'openclaw' COMMENT '当前后端:openclaw | companion',
    `companion_session_id` varchar(64) DEFAULT NULL COMMENT 'Companion 自己生成的 UUID(mode=companion 时非空,切回 openclaw 后保留)',
    `companion_session_state` varchar(16) DEFAULT NULL COMMENT '最近一次观察到的 Companion 侧状态:starting / connected / running / exited',
    `companion_session_state_at` datetime(3) DEFAULT NULL COMMENT '上次状态更新时间',
    `switched_at` datetime(3) NOT NULL COMMENT '最近一次切换时间',
    `switched_by_ldap` varchar(64) DEFAULT NULL COMMENT '触发切换的企微用户 ldap',
    `dbctime` datetime(3) DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
    `dbutime` datetime(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_session_key` (`session_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT '企微会话后端路由模式';
```

**查询典型用例**:

- 某用户当前用哪个后端:`SELECT current_backend FROM session_backend_mode WHERE session_key = ?`
- 哪些用户在用 Companion:`SELECT session_key, switched_at FROM session_backend_mode WHERE current_backend = 'companion'`
- 最近被切过 Companion 的会话:`SELECT * FROM session_backend_mode WHERE current_backend = 'companion' ORDER BY switched_at DESC LIMIT 20`

---

## 8. CLAUDE.md 系统提示词(落到 `~/.claude/CLAUDE.md`)

```markdown
# 你是谁 & 你在哪

你是一个**个人助理 AI**,不是传统意义上的编程助手。你的工作是帮用户处理日常事务、
回答问题、执行命令、运维这台服务器等等。

## 运行环境

你现在跑在一个 Linux 容器里,这个容器叫 `openclaw`,是用户的个人工作容器。
你跟另一个 agent 叫 **OpenClaw** 共享同一个容器环境、同一个文件系统、同一把
Anthropic API key。

- OpenClaw 是用户常驻的个人助理,大多数时候由它接待用户
- 你(Claude Code)是用户在企微里发 `/claude` 临时把你召唤出来的
- 用户发 `/openclaw` 就会切回 OpenClaw,你本次会话结束(但历史保留)

## 你和 OpenClaw 的关系

**你们是协作关系**,不是竞争。用户切到你通常是因为下面几种情况之一:

1. **OpenClaw 卡死 / 出 bug / 报错不能用了** → 用户切到你来救场。常见操作:
   - `ps aux | grep openclaw` 看进程
   - 看 `/home/node/.openclaw/logs/` 或 systemctl 日志
   - 按需重启 OpenClaw 进程
2. **OpenClaw 做不到的复杂编码任务** → 用户切到你来做(你是 Claude Code,
   强项是编码和工具调用)
3. **用户想让你做一些需要严格遵守步骤的任务**,因为用户反馈 OpenClaw 在这类
   任务上不太守规矩

## 工作风格

- **全开权限**(用户已经把你设成 `bypassPermissions`),不用等审批
- 默认按 root 级信任执行用户请求,但涉及**不可逆操作**(rm -rf、killall、
  重启整机)时口头确认一次
- 工具产出简洁,不要长篇大论 —— 用户是从企微看回复的,长文体验差
- 回答用中文
- 你看不到企微端的 UI 限制,但假设回复会被转码成纯文本 + markdown,别发
  依赖终端渲染的东西

## 你不该做什么

- 不要主动给 OpenClaw "建议"或者 "教它怎么做",除非用户明确问
- 不要假装自己是 OpenClaw(你们俩是不同的 agent,用户很清楚这一点)
- 不要在系统里留下你自己的 "标记"(比如改 /home/node/.profile 之类)
```

这个文件放在挂载进容器的 `~/.claude/CLAUDE.md`(通过 `claude-data` volume 持久化),对所有 Claude Code session 生效。用户以后可以随时编辑这个文件,改完后**下一个** session 自动加载,已在跑的 session 不受影响。

---

## 9. 容器部署

### 9.1 阶段一:临时 `docker exec` 跑通

目标:**不打新镜像**,在运行中的 OpenClaw 容器里临时装好全套,验证企微切换流程能跑通,然后再考虑镜像固化。

前置:知道 OpenClaw 容器的 ID 或 name(假设为 `openclaw`)。

```bash
# 1. 进容器
ssh windows        # 或 docker 宿主机
docker exec -it -u root openclaw bash

# 2. 装 Bun
curl -fsSL https://bun.sh/install | bash
export PATH="$HOME/.bun/bin:$PATH"
bun --version

# 3. 装 Claude Code
npm install -g @anthropic-ai/claude-code
claude --version

# 4. 装 the-companion
bun install -g the-companion
the-companion --version

# 5. 配环境变量(持久化到 node 用户的 .bashrc)
su - node
cat >> ~/.bashrc << 'EOF'
export ANTHROPIC_API_KEY="<跟 OpenClaw 同款 API key>"
export COMPANION_AUTH_TOKEN="<固定的长随机字符串>"
export COMPANION_HOME="/home/node/.companion"
export PATH="$HOME/.bun/bin:$PATH"
EOF

# 6. 写 CLAUDE.md(§8 的内容)
mkdir -p ~/.claude
cat > ~/.claude/CLAUDE.md << 'EOF'
# 你是谁 & 你在哪
...(§8 完整内容)
EOF

# 7. 启动 Companion(前台跑,先观察日志)
source ~/.bashrc
the-companion serve --port 3456
```

**验证步骤**:
- 宿主机:`curl -H "Authorization: Bearer <token>" http://localhost:3456/api/sessions` → 应该返回空数组
- 宿主机端口没暴露?临时 `docker run -p` 不行,需要容器启动时带 `-p 3456:3456`。如果现有容器没带,暂时先在宿主机上 `ssh -L 3456:localhost:3456` 隧道到自己 Mac 测

**问题定位**:
- `the-companion serve` 启动失败 → 看 Bun 版本(需要 ≥ 1.0)和 Node 版本
- Companion 启动但 `claude` 起不来 → 检查 Companion 日志中 spawn 命令,在容器里手动跑一次那行 claude 看报什么错
- claude 起来但连不上 WebSocket → 检查 `COMPANION_AUTH_TOKEN` 是否在 env 里;检查 sessionId 是否 URL-encode 正确

### 9.2 阶段二:打新镜像固化

跑通后,把所有步骤沉淀进 Dockerfile:

```dockerfile
# 基于现有 OpenClaw 镜像
FROM <openclaw-current-tag>

# 安装 Bun(指定具体版本)
USER root
RUN curl -fsSL https://bun.sh/install | bash -s "bun-v1.1.38"   # TODO 部署时定

# 安装 Claude Code 和 the-companion(全局,pin 具体版本)
USER node
RUN ~/.bun/bin/bun install -g the-companion@0.95.X                # TODO 部署时定
RUN npm install -g @anthropic-ai/claude-code@2.1.XXX              # TODO 部署时定

# 拷贝 CLAUDE.md 模板到镜像(真实内容由 volume 覆盖)
COPY --chown=node:node CLAUDE.md /home/node/.claude/CLAUDE.md

# 环境变量(敏感值在 docker-compose.yml 注入,不写进镜像)
ENV COMPANION_HOME=/home/node/.companion
ENV PATH=/home/node/.bun/bin:$PATH

# Supervisor 或者直接 entrypoint 启两个进程(OpenClaw + Companion)
COPY supervisor-companion.conf /etc/supervisor/conf.d/companion.conf

# 暴露 Companion 端口
EXPOSE 3456
```

**镜像 tag 命名**:`openclaw-cc:2026.04.XX-cc<ccver>-companion<cver>-bun<bver>`

**docker-compose.yml 改动**:

```yaml
services:
  openclaw:
    image: openclaw-cc:2026.04.XX-cc2.1.XXX-companion0.95.X-bun1.1.38
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}          # 从宿主机 env 注入
      COMPANION_AUTH_TOKEN: ${COMPANION_AUTH_TOKEN}
    ports:
      - "80:80"         # 现有 OpenClaw Web UI
      - "3456:3456"     # 新增 Companion Web UI + API
    volumes:
      - openclaw-data:/home/node/.openclaw
      - companion-data:/home/node/.companion     # 新增
      - claude-data:/home/node/.claude            # 新增
    user: node                                    # 确保非 root,避免权限降级
```

### 9.3 启动顺序与健康检查

Supervisor 配置让两个服务独立起:

```ini
[program:openclaw]
command=/usr/local/bin/openclaw-entrypoint.sh
user=node
autorestart=true

[program:companion]
command=/home/node/.bun/bin/the-companion serve --port 3456
user=node
autorestart=true
stdout_logfile=/var/log/companion.stdout.log
stderr_logfile=/var/log/companion.stderr.log
environment=HOME="/home/node",PATH="/home/node/.bun/bin:/usr/bin:/bin"
```

**健康检查**:

- `curl -s http://localhost:80/...` → OpenClaw 就绪
- `curl -s -H "Authorization: Bearer $TOKEN" http://localhost:3456/api/sessions` → Companion 就绪

foreman 在启动 / 重连时调 Companion 的 `/api/sessions` 做一次 ping,失败就标 `companion.enabled=false`,所有 `/claude` 请求直接返回错误消息"Claude Code 后端不可用,请联系运维"。

---

## 10. 指令兼容性(A / B / C 三档)

### A 档 —— 会话控制类,foreman 必须拦截并翻译

| 指令 | OpenClaw 路径(现状) | Companion 路径(新) |
|---|---|---|
| `/new` / `/reset` | OpenClaw 原生处理 | foreman 拦截:`POST /api/sessions/<id>/kill` → 清 DB `companion_session_id` → 下条消息触发新建。**回复**: "已开始新的 Claude Code 会话,历史已清空" |
| `/clear` | OpenClaw 原生处理 | 同 `/new`。**CC 源码 clear/index.ts:14 明确 `aliases: ['reset', 'new']`**,语义上合并。**回复**: "Claude Code 不区分 /clear 和 /new,已开始新会话" |
| `/compact` | OpenClaw 原生处理 | 原样透传给 CC(`{type:"user_message", content:"/compact"}`)。CC 源码 `compact/index.ts` 标注 `supportsNonInteractive: true` |
| `/stop` | OpenClaw stopCommand 分支(见 `OPENCLAW_CHAT_PROTOCOL.md` §1.2) | **WS 发**`{type:"interrupt", client_msg_id:uuid}` 到 `/ws/browser/<id>` |
| `/restart` | OpenClaw 原生处理(重启 OpenClaw 自身) | **报错**: "此命令仅 OpenClaw 模式可用,请先发 /openclaw 切回" |

### B 档 —— OpenClaw 专属,CC 路径下一律报错

从 `openclaw/src/auto-reply/commands-registry.shared.ts` 里捞出来的 30 条专属命令:

```
/skill /subagents /agents /plugins /mcp /allowlist /approve
/tools /tasks /acp /kill /focus /unfocus /activation /config
/debug /context /think /verbose /fast /reasoning /elevated
/exec /queue /send /steer /session /export-session /tts /btw /bash
```

**Companion 路径下收到这些**:

```
⚠️ 此命令 `/skill` 是 OpenClaw 专属,Claude Code 不支持。
如需使用,请先发 /openclaw 切回 OpenClaw 模式。
```

`BackendSwitchHandler` 里维护这个黑名单(或 foreman 拿 OpenClaw 命令注册表做参照),两边都能加新命令。

### C 档 —— 通用命令,原样透传

| 指令 | 行为 |
|---|---|
| `/help` / `/commands` | 透传。CC 侧有 `/help`,会返回 CC 的帮助(跟 OpenClaw 的不同,但用户会意识到) |
| `/model` / `/models` | 透传。两边都有,CC 会在当前 session 里切 |
| `/status` | 透传。CC 有 `/status`,返回 session 状态 |
| `/usage` | 透传。CC 返回 token 计费视图 |
| `/whoami` | 透传。CC 没有这个,会报 unknown command。可忍受 |

---

## 11. 安全风险与应对

| 风险 | 程度 | 应对 |
|---|---|---|
| Companion `COMPANION_AUTH_TOKEN` 泄露 → 任意用户可开 CC session 执行任意命令(bypassPermissions + API key 计费) | **高** | 1. Token 从外部 env 注入,使用 ≥ 32 字节高熵值;2. 暴露的 3456 端口绑内网 IP 或放 Tailscale 内,不暴露公网;3. 定期轮换 token |
| `ANTHROPIC_API_KEY` 在容器 env 里 | 中 | 用 docker secret / 绑定 env file(不入镜像),与 OpenClaw 共享同一 key 的计费监控 |
| 企微 `/claude` 指令被盗用(其他人能冒充用户切换) | 中 | 依赖 foreman 既有的企微用户身份校验;切换时 `switched_by_ldap` 字段记谁干的 |
| Claude Code 在容器里用 root 跑工具 | 中 | 容器内进程以 node 用户跑,不能 sudo。"真需要 root"的场景走 OpenClaw(它本来就是) |
| CC 误执行破坏性命令(`rm -rf /`) | 中 | `~/.claude/CLAUDE.md` 里的"不可逆操作先确认"约束 + 工具实际发生的操作都会出现在 stream 里,企微用户能看到并口头叫停 |
| 多 client 同时连一个 session 导致消息冲突 | 低 | Companion 原生支持多 browser client + event fanout + replay 机制,见 §14 |
| Claude Code / Companion 版本漂移 | 中 | 版本全锁,回归测试套件门禁升级 |
| OpenClaw 和 Claude Code 共用 API key 互相消耗额度 | 低 | 监控侧分 provider;如果需要隔离,各自一把 key(但 D2 约定了共享) |

---

## 12. 回归测试清单(`infra-foreman-cc-e2e` 模块)

6 条黄金路径,每次 CC / Companion / Bun 版本升级前必须全绿才能发布。放在独立子模块 `infra-foreman-cc-e2e`,用 Java 或 Python 实现都行(建议 Python,脚本短,直接调 Companion HTTP + WS API):

| # | 场景 | 成功标准 |
|---|---|---|
| 1 | 基本对话 | 发 "hello" → 5s 内收到 assistant 消息含 "Hello" 或类似问候 |
| 2 | 工具调用 Bash | 发 "运行 `echo ok`" → 看到 `tool_use name=Bash` + 输出含 "ok" |
| 3 | 工具调用 Write | 发 "写一个文件 /tmp/x 内容是 y" → 看到 `tool_use name=Write` + 文件真实存在 |
| 4 | 流式长输出 | 发 "用 100 字介绍 Linux" → 连续收到 ≥ 5 条 `stream_event` + 最终 result,无截断 |
| 5 | 会话续接 | 发消息 A → 重启 Companion 进程 → 发消息 B,验证 CC 能引用 A 的内容(说明 --resume 生效) |
| 6 | 双端 fanout | client1 和 client2 同时连 `/ws/browser/<id>` → client1 发消息 → client2 也收到 user_message 广播 + 后续的 assistant 流式;client1 发 interrupt → client2 收到 aborted |

每条测试的预期时间:1–2 分钟。全套 ~15 分钟跑完。

失败的话产出一份结构化报告(哪条失败、expected vs actual、响应里的异常消息类型),便于判断是 CC 升级问题还是 Companion 升级问题还是 foreman 的路由问题。

---

## 13. TODO(部署时 / 实施时决定)

- [ ] Bun 具体版本号
- [ ] Claude Code 具体版本号
- [ ] the-companion 具体版本号
- [ ] 镜像 tag 命名
- [ ] `COMPANION_AUTH_TOKEN` 生成策略(机器生成 + 存 secret 库?)
- [ ] Companion 3456 端口是绑内网 IP 还是 Tailscale-only(D14 说直接暴露,但具体绑哪个 IP 还没定)
- [ ] Companion message-type 翻译层的具体字段名(需抓一次真实消息确认,设计时参考 `session-types.ts` 已经够)
- [ ] `/stop` 的 WS 发送是否需要等 aborted 事件回来才返回(同步 vs 异步)
- [ ] 回归测试套件的具体 Java 测试类 or Python 脚本模板
- [ ] BackendMode 切换时是否要 abort 当前 OpenClaw / Companion 上正在跑的 run(如果有 in-flight 的话)
- [ ] 外部 Web UI 的用户(浏览器访问者)如何知道自己应该填哪个 sessionKey —— 方案 1:企微里发一条提示 URL,带 sessionKey query 参数;方案 2:Companion 自带的 session list UI,用户自己选

---

## 14. Companion 架构剖析(供参考实现细节)

> 本章的目的不是教你用 Companion,而是回答 "Companion 是怎么把这些能力做到的"。
> 对实施这个方案的工程师来说,读完这章就能大致脑补出 Companion 内部,出 bug 时能定位。
>
> 本章内容是 `companion-product-analysis.md` 的精炼+补充,侧重对本次迁移最相关的维度。

### 14.1 整体三层模型

```
┌──────────────────────────────────────────┐
│   Browser (React) 或外部 WS client       │
│   - 会话列表 / 时间线展示                  │
│   - 权限审批对话框                         │
│   - 断线重连 + 事件 replay 协议            │
└─────────────┬────────────────────────────┘
              │ NDJSON over WebSocket
              │ /ws/browser/:sessionId
              ▼
┌──────────────────────────────────────────┐
│  Companion Server (Bun + Hono)           │
│                                           │
│  ┌────────────────────────────────┐     │
│  │  WsBridge(消息路由核心)        │     │
│  │    ├─ SessionStateMachine(8 态)│    │
│  │    ├─ BackendAdapter 翻译层     │     │
│  │    │    ├─ ClaudeAdapter       │     │
│  │    │    └─ CodexAdapter        │     │
│  │    ├─ SessionStore(文件系统)   │     │
│  │    └─ CliLauncher(进程管理)    │     │
│  └────────────────────────────────┘     │
│                                           │
│  ┌────────────────────────────────┐     │
│  │  Hono HTTP API                  │     │
│  │    /api/sessions/*              │     │
│  │    /api/git/* /api/fs/* ...     │     │
│  └────────────────────────────────┘     │
│                                           │
│  EventBus(组件间解耦 pub-sub)            │
└─────────────┬────────────────────────────┘
              │ NDJSON over WebSocket
              │ /ws/cli/:sessionId
              │ (claude 作为 client 主动连上来)
              ▼
┌──────────────────────────────────────────┐
│  Claude Code CLI                          │
│  claude --sdk-url ws://localhost:3456/... │
│  --print --output-format stream-json     │
│  --input-format stream-json              │
│  --permission-mode bypassPermissions     │
└──────────────────────────────────────────┘
```

**核心决策:CLI 是无状态的,Server 维护所有状态**。CLI 可以随时被杀,Server 靠文件系统持久化跨重启恢复。多个 browser client 可以同时连同一个 session,Server 做 fanout。

### 14.2 模块依赖

```
SessionOrchestrator(总入口,routes.ts 调用)
    ├─ CliLauncher(cli-launcher.ts,进程启动/终止)
    │    └─ ContainerManager(container-manager.ts,docker exec 模式)
    ├─ WsBridge(ws-bridge.ts,消息路由)
    │    ├─ SessionStateMachine(session-state-machine.ts)
    │    ├─ SessionStore(session-store.ts)
    │    ├─ ClaudeAdapter(claude-adapter.ts)
    │    └─ CodexAdapter(codex-adapter.ts)
    ├─ WorktreeTracker(git worktree 生命周期)
    ├─ AgentExecutor(子 agent 任务管理)
    └─ EventBus(event-bus.ts,companionBus 解耦各组件)
```

### 14.3 多 session 生命周期

#### 数据结构(cli-launcher.ts:220 附近)

```typescript
class CliLauncher {
  private sessions = new Map<string, SdkSessionInfo>();   // sessionId → 元信息
  private processes = new Map<string, Subprocess>();      // sessionId → Bun 子进程句柄
  private sessionEnvs = new Map<string, Record<string, string>>();  // 每 session 独立 env
  private store: SessionStore | null = null;              // 持久化接口
}

interface SdkSessionInfo {
  sessionId: string;
  state: "starting" | "connected" | "running" | "exited";
  pid?: number;
  cliSessionId?: string;    // CC 自己的 internal session ID,--resume 用
  model?: string;
  permissionMode?: string;
  cwd: string;
  createdAt: number;
  backendType?: "claude" | "codex";
  // ... git 信息 / 容器信息 等
}
```

**一个 sessionId 对应一个 claude 子进程**。并发 10 个 session 就是 10 个 `Bun.spawn` 出来的独立 claude 进程。

#### spawn 命令构造(cli-launcher.ts:520 附近)

```typescript
const args = [
  "--sdk-url", sdkUrl,                     // ws://localhost:3456/ws/cli/<sessionId>
  "--print",
  "--output-format", "stream-json",
  "--input-format", "stream-json",
  "--include-partial-messages",            // 需要这个才能拿到流式 token
  "--verbose",
];

if (options.model)            args.push("--model", options.model);
if (effectivePermissionMode)  args.push("--permission-mode", effectivePermissionMode);
if (options.allowedTools)     args.push("--allowedTools", ...);
if (options.resumeSessionId)  args.push("--resume", options.resumeSessionId);

args.push("-p", "");                       // headless 模式占位
```

重要:`--sdk-url` 让 CC 自己作为 client **反向连接**到 Companion server。Companion 不需要开 stdin/stdout pipe,全部走 WebSocket。

#### 权限降级逻辑(cli-launcher.ts:530-555)

```typescript
const shouldDowngradeContainerBypass =
  isContainerized
  && options.permissionMode === "bypassPermissions"
  && process.env.COMPANION_FORCE_BYPASS_IN_CONTAINER !== "1";

const shouldDowngradeRootBypass =
  !isContainerized
  && isRootProcess                          // process.getuid() === 0
  && options.permissionMode === "bypassPermissions"
  && process.env.COMPANION_FORCE_BYPASS_AS_ROOT !== "1";

if (shouldDowngradeContainerBypass || shouldDowngradeRootBypass) {
  effectivePermissionMode = "acceptEdits";  // 被动降级
}
```

这就是为什么 D5 决策写"容器内 Companion 必须以 node 用户跑(非 root)" —— 如果以 root 跑,这段代码会把 `bypassPermissions` 悄悄降成 `acceptEdits`,所有写操作要审批,而企微没法审批 → 卡死。

#### 存活检测(cli-launcher.ts:226 `restoreFromDisk`)

```typescript
restoreFromDisk(): number {
  const data = this.store.loadLauncher<SdkSessionInfo[]>();
  for (const info of data) {
    if (info.pid) {
      try {
        process.kill(info.pid, 0);   // signal 0 = 只探活不杀
        info.state = "starting";     // 等 CC 自己用 --sdk-url 重连
      } catch {
        info.state = "exited";
      }
    }
  }
}
```

**关键好处**:Companion server 重启时**不杀 CC 进程**。CC 正在跑的长任务(编译、下载)不会被打断,Companion 重启后探活 + 等 CC 自己重连上来,无缝续接。

#### 退出处理(cli-launcher.ts:600 附近)

```typescript
proc.exited.then((exitCode) => {
  session.state = "exited";
  session.exitCode = exitCode;

  // 如果 --resume 启动后 5 秒内就退出,大概率是 resume 失败
  const uptime = Date.now() - spawnedAt;
  if (uptime < 5000 && options.resumeSessionId) {
    session.cliSessionId = undefined;  // 清掉坏的 cliSessionId,下次 relaunch 走新建
  }

  this.processes.delete(sessionId);
  this.persistState();
  companionBus.emit("session:exited", { sessionId, exitCode });
});
```

**自愈**:`cliSessionId` 失效后自动清空,避免无限 resume 失败循环。foreman 侧不用判断 --resume 成功还是失败,Companion 替你做了。

### 14.4 会话状态机(8 态)

参考 `companion-product-analysis.md` §5,这里只说对我们最相关的部分。

```
starting            CC 进程已启动,WS 未连接
initializing        CC WS 已连接,等待 system.init
ready               空闲,等待用户消息(唯一能接受新消息的状态)
streaming           LLM 生成中 / 工具执行中
awaiting_permission 权限请求已发出,等待用户决策
compacting          上下文压缩进行中
reconnecting        CC socket 断开,在恢复窗口内(15s)
terminated          进程已退出或被杀死
```

**我们要注意的**:

- `awaiting_permission` 状态在 bypassPermissions 模式下**不应该出现**。一旦观察到这个状态 → 说明权限降级了,立即告警 + 检查 D5 的用户是不是对。
- `reconnecting` 状态(15s 窗口)是优雅处理短暂网络抖动的设计。Companion 不会把短断线当崩溃处理,foreman 也不应该急着 relaunch。foreman 侧看到 state 还不是 `exited` 就老实等着。
- `compacting` 是 `/compact` 正在跑,这时不能发新用户消息。foreman 透传 `/compact` 后要等 state 回到 `ready` 才能继续。

### 14.5 WebSocket 协议消息类型(对我们最有用的几类)

来源:`ws-bridge-browser-ingest.ts` 头部 + `session-types.ts`(未读完但从调用处推断出)。

**Browser → Server**(foreman 会用这些):

```typescript
IDEMPOTENT_BROWSER_MESSAGE_TYPES = [
  "user_message",          // 发用户消息
  "permission_response",   // 响应权限请求(bypass 模式下不用)
  "interrupt",             // 中断当前 turn ← /stop 靠这个
  "set_model",             // 切模型
  "set_permission_mode",   // 切权限模式(运行时改)
  "mcp_get_status",        // MCP 相关
  "mcp_toggle",
  "mcp_reconnect",
  "mcp_set_servers",
  "set_ai_validation",
]
```

所有这些消息都支持 `client_msg_id` 幂等去重(foreman 重试不会被当成两条新消息)。

**Server → Browser**(foreman 会收这些,要翻译成内部事件):

```
session_init              session 初始化完成
session_update            session 状态变化(phase 转换等)
stream_event              流式 token(文本/思考/tool input json)
assistant                 完整的 assistant 消息
result                    一轮结束,含 usage 和 subtype(success/error/aborted)
permission_request        权限请求(bypass 模式下不应出现)
system_status             压缩开始/结束等
tool_progress             长时间工具心跳
keep_alive                心跳
```

### 14.6 存储布局(纯文件系统)

`COMPANION_HOME`(默认 `~/.companion/`)里的布局:

```
~/.companion/
├── auth.json              Companion 自身的 auth token(如果 env 没注入)
├── sessions/              session 元信息
│   ├── launcher.json      CliLauncher 的快照(所有 session 的元信息数组)
│   ├── <uuid-1>.json      每个 session 一个 PersistedSession JSON
│   │                      字段:id, state, messageHistory,
│   │                      pendingMessages, pendingPermissions,
│   │                      eventBuffer, nextEventSeq, archived, ...
│   ├── <uuid-2>.json
│   └── ...
└── ...                    其他管理类文件(skills 缓存 等)
```

**写入策略**(`session-store.ts`):

```typescript
save(session: PersistedSession): void {
  // 防抖 150ms,同一 session 快速更新合并成一次写
  clearTimeout(this.debounceTimers.get(session.id));
  const timer = setTimeout(() => this.saveSync(session), 150);
  this.debounceTimers.set(session.id, timer);
}

saveSync(session: PersistedSession): void {
  writeFileSync(this.filePath(session.id), JSON.stringify(session), "utf-8");
}
```

流式输出每个 token 都走 `save()` 不会每次都落盘,150ms 内合并为一次 `saveSync`。关键状态变化(比如权限请求)用 `saveSync` 立即写。

**存储依赖**:**零**。没有 SQLite,没有 Postgres,没有 Redis。只要 `~/.companion` 能写,Companion 就能跑。volume 挂对了就行。

### 14.7 Claude Code 那边发生了什么(transcript 存哪)

Companion 不管 CC 自己的对话历史 —— CC 自己把完整的 NDJSON 消息流写到:

```
~/.claude/projects/<cwd-hash>/<cliSessionId>.jsonl
```

一行一个 JSON,包含系统 init、assistant、user、tool_use、tool_result、result 全部。

`claude --resume <cliSessionId>` 就是读这个文件恢复上下文。所以:

- **D16 决策里必须挂 `~/.claude` volume**,否则容器重启 → 这些 jsonl 丢 → 所有 `/claude` 会话历史没了 → `--resume` 失败 → 每次都是新会话
- 用 API key(非 OAuth)也改变不了这个:CC 存 transcript 跟登录方式无关

### 14.8 Companion 里没做好 / 要注意的

从 `companion-product-analysis.md` §7 里摘关键的:

| 问题 | 对我们影响 |
|---|---|
| **协议版本维护成本高**(CC 每次小版本升级可能变协议) | D7 版本固化策略是对策。用户升级 CC 前必须跑 `infra-foreman-cc-e2e` |
| **权限请求超时缺失**(CLI 侧无限等) | bypassPermissions 下不会出现这个问题,但要监控 `awaiting_permission` 状态出现的频率 |
| **多浏览器同时审批的一致性**(时序窗口) | bypassPermissions 下 moot |
| **容器资源泄漏**(Idle Kill 保留容器) | 我们不用 Companion 的容器模式(CC 跟 Companion 在同一容器里跑),不受影响 |
| **事件缓冲内存占用**(每会话 ~5MB) | 按企微用户数估算,100 个活跃用户 = 500MB,可接受 |

### 14.9 关键源码文件索引

| 文件 | 路径 | 作用 |
|---|---|---|
| CliLauncher | `web/server/cli-launcher.ts` | 进程管理,最关键 |
| WsBridge | `web/server/ws-bridge.ts` | 消息路由 |
| WsBridge browser ingest | `web/server/ws-bridge-browser-ingest.ts` | 解析 browser 发来的消息,含幂等类型定义 |
| WsBridge cli ingest | `web/server/ws-bridge-cli-ingest.ts` | 解析 CC 发来的消息 |
| ClaudeAdapter | `web/server/claude-adapter.ts` | stream-json NDJSON ↔ 内部格式翻译 |
| SessionStateMachine | `web/server/session-state-machine.ts` | 8 态状态机 |
| SessionStore | `web/server/session-store.ts` | 文件系统持久化,含 150ms 防抖 |
| SessionCreationService | `web/server/session-creation-service.ts` | POST /api/sessions/create 的实现 |
| routes.ts | `web/server/routes.ts` | 所有 HTTP endpoints 定义(Hono) |
| paths.ts | `web/server/paths.ts` | `COMPANION_HOME` 解析 |

---

## 15. 对我们现有 Android 套壳方案的借鉴

继续保留 `infra-foreman` 以外的场景(Android app 通过 HTTP 轮询连本地后端跑 CC)时,可以从 Companion 设计里借鉴的:

| Companion 的设计 | 我们安卓套壳现状 | 移植价值 |
|---|---|---|
| 形式化状态机(8 态) | 有,但是散落的 volatile 标志位 | **高**,能减少状态 bug |
| reconnecting 中间状态 | 无 | **高**,能优雅处理网络抖动 |
| intentional kill 区分 | 有(delete_conversation 时) | 中,可以完善 |
| 事件序列号 + 重放 | 有(ClaudeOutputBuffer 的 seqId) | 低,已有类似 |
| 消息去重(client_msg_id) | 不确定 | **高**,防重复执行 |
| 防抖持久化 | 无 | 中,异步写 DB 有缓冲 |
| 指数退避重启 | 无 | 中,CC 意外崩溃时有用 |
| 权限超时 | 无 | **高**,现在也有同样问题 |

具体移植方案不在本文档范围,只是 flag 出来备忘。

---

## 附录 A. 决策演进记录

本文档是 9 轮设计对话的最终产物。过程里有几处我最初给错了结论,后来修正,值得记录:

1. **最早判断"tmux 方案解析终端文本脆弱"** → **修正**:实际上 tmux 方案走的是 `stream-json` NDJSON,跟 Companion 是同一份协议,脆弱性等价。Companion 的真实优势是传输层简单、多 client fanout、自带 UI。
2. **最早判断"用 API key 就可以不挂 `~/.claude` volume"** → **修正**:CC 的对话 transcript 是存在 `~/.claude/projects/*.jsonl` 的,跟登录方式无关。必须挂这个 volume,否则 `--resume` 永远失败。
3. **最早没考虑到 Companion 自己生成 UUID 不接受外部 sessionId** → **修正**:要么 patch Companion 源码(破坏版本固化),要么 foreman 侧维护映射表(D11 选的方案)。

---

## 附录 B. 相关外部资源

- Companion GitHub: https://github.com/The-Vibe-Company/companion
- Companion npm 包: https://www.npmjs.com/package/the-companion
- Bun 官网: https://bun.sh/
- Claude Code 官方文档: https://docs.claude.com/en/docs/claude-code
- `WEBSOCKET_PROTOCOL_REVERSED.md`: Companion repo 里的协议逆向文档,是这次 `--sdk-url` 能落地的关键

---

**文档结束**。下一步:进入阶段一部署(§9.1),在运行中的 OpenClaw 容器里 `docker exec` 装全套并验证 `/claude` 切换能跑通,跑通后回来起草 `deployment-phase2.md`(Dockerfile + 镜像固化)。
