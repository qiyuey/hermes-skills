# 01 · 架构与数据流

> **⚠️ 版本提示**:本文档示例命令行使用 **v1.0.0 API**(`--resume <sid>` + `--cwd <path>`)。当前已升级到 **v2.0.0**,主 agent 面只传 `--task <name>` 一个参数,workdir + session 续接由 wrapper 自动决定。**架构本身和数据流未变**,jsonl 单一真源机制、进程树、清理路径全部相同 —— 只是主 agent 对接的参数更简单了。详见 [`CHANGELOG.md`](CHANGELOG.md) 2.0.0 节。


本文讲清楚 4 个问题:

1. 从用户消息到 Claude Code 实际执行,整条链路经过了几个组件
2. 每个组件的职责和进程边界
3. 一次"新建任务"和一次"续接任务"的数据流有什么区别
4. 进程树结构和文件状态落在哪里

---

## 1. 整体拓扑

```
┌────────────────┐
│   用户(企微)    │
└───────┬────────┘
        │ IM 消息
        ▼
┌─────────────────────────────────────────────────────────┐
│  OpenClaw Gateway (bot 上常驻)                           │
│  ┌───────────────────────────────────────────────────┐  │
│  │  主 agent OpenClaw 主 agent(Claude Sonnet via Bedrock)          │  │
│  │                                                    │  │
│  │  loaded skills:                                    │  │
│  │    - delegate-to-claude-code-acp  ← 本 skill       │  │
│  │    - (其他若干 skill)                               │  │
│  │                                                    │  │
│  │  tool: bash(pty:true, background:true)             │  │
│  │  tool: process(poll/submit/kill)                   │  │
│  └─────────────────┬─────────────────────────────────┘  │
│                    │ spawn via bash tool                 │
│                    ▼                                     │
│  ┌───────────────────────────────────────────────────┐  │
│  │  claude-as-acp (wrapper, Node.js, 186 行)          │  │
│  │  /usr/local/bin/claude-as-acp                      │  │
│  │                                                    │  │
│  │  - parse CLI args: --cwd, --resume, prompt         │  │
│  │  - bootstrap /root/.aws/credentials (首次)         │  │
│  │  - spawn claude-agent-acp as child                 │  │
│  │  - translate ACP events → NDJSON on stdout         │  │
│  │  - log 到 /tmp/claude-as-acp.log                   │  │
│  └─────────────────┬─────────────────────────────────┘  │
│                    │ ACP JSON-RPC over stdio             │
│                    ▼                                     │
│  ┌───────────────────────────────────────────────────┐  │
│  │  claude-agent-acp (ACP server, Node.js)            │  │
│  │  @agentclientprotocol/claude-agent-acp@0.27.0      │  │
│  │                                                    │  │
│  │  - 接 ACP protocol 的 initialize/newSession/       │  │
│  │    loadSession/prompt/cancel                        │  │
│  │  - 用 @anthropic-ai/claude-agent-sdk 调 claude CLI │  │
│  └─────────────────┬─────────────────────────────────┘  │
│                    │ stream-json NDJSON over stdio       │
│                    ▼                                     │
│  ┌───────────────────────────────────────────────────┐  │
│  │  claude (Claude Code CLI)                          │  │
│  │  /usr/local/bin/claude (v2.0.42)                   │  │
│  │                                                    │  │
│  │  - 真正跑 LLM tool loop 的进程                      │  │
│  │  - 读写 ~/.claude/projects/<cwd-hash>/<sid>.jsonl  │  │
│  │  - 经 HTTPS_PROXY 连 AWS Bedrock                    │  │
│  └─────────────────┬─────────────────────────────────┘  │
│                    │ HTTPS + AWS SigV4                   │
└────────────────────┼────────────────────────────────────┘
                     ▼
         ┌─────────────────────────┐
         │  AWS Bedrock             │
         │  Claude Sonnet 4.6       │
         │  (inference profile ARN) │
         └─────────────────────────┘
```

---

## 2. 每个组件的职责

| 层 | 进程 | 作用 | 状态存哪 |
|---|---|---|---|
| **L1 用户** | 企微客户端 | 发消息 | 企微服务器 |
| **L2 OpenClaw Gateway** | `openclaw-gateway`(常驻) | 主 agent 对话、工具调度、skill 加载 | `/root/.openclaw/agents/main/sessions/<openclaw-sid>.jsonl` |
| **L3 Wrapper** | `node claude-as-acp`(按需短寿命) | ACP client + NDJSON 翻译 + AWS bootstrap | 无持久状态(日志 `/tmp/claude-as-acp.log`) |
| **L4 ACP Server** | `node claude-agent-acp`(wrapper 子进程) | ACP 协议栈 + session 管理 | 内存 only |
| **L5 Claude CLI** | `claude`(ACP server 的子进程) | LLM tool loop + 写 transcript | `~/.claude/projects/<cwd-hash>/<sid>.jsonl` |
| **L6 Bedrock** | 远端 AWS API | 推理服务 | AWS 侧 |

**关键边界**:

- **L2 和 L3** 是**两个完全独立的操作系统进程**。gateway 通过 `bash` 工具 spawn wrapper,wrapper 退出时 gateway 继续活着。
- **L3、L4、L5** 是一条**父子进程树**。wrapper 是爹,ACP server 是儿子,claude CLI 是孙子。wrapper 退出 → ACP server stdin EOF → ACP server 退出 → claude CLI 被杀 → 整棵树回收。
- **L2 的状态**(OpenClaw 主 agent的对话)和 **L5 的状态**(claude 的 transcript)是**两份独立的持久化状态**,跨不同目录,格式也不一样。

---

## 3. "新建任务"的数据流(Round 1)

```
[用户] 发消息 "用 Claude Code 在 /tmp/foo 下写个 hello.py 打印 hi"
   │
   ▼
[openclaw-gateway]
   ├─ 识别 delegate-to-claude-code-acp skill(触发词 "Claude Code")
   ├─ 读 SKILL.md 学习调用规范
   ├─ 调 bash tool:
   │    command:  claude-as-acp --cwd /tmp/foo 'Create hello.py that prints "hi"'
   │    pty:      true
   │    workdir:  /tmp/foo
   │    background: true  (openclaw 自己决定)
   │    → 返回 bashSessionId (openclaw bash 内部 id)
   │
   ▼
[wrapper pid=N1 启动]
   ├─ 解析 args:  cwd=/tmp/foo, resume=null, prompt="Create..."
   ├─ bootstrap:  if 没 ~/.aws/credentials → 写 [claude-profile]
   ├─ spawn claude-agent-acp pid=N2 (stdio: pipe)
   ├─ emit stdout: (第一行还没发)
   ├─ await conn.initialize({...})
   │    → 跟 ACP server 握手
   ├─ await conn.newSession({cwd: "/tmp/foo"})
   │    → ACP server 内部 spawn claude pid=N3
   │    → claude 生成自己的 sessionId = "cd649582-f194-48fe-8429-03f467730f40"
   │    → 在 /root/.claude/projects/-tmp-foo/cd649582-....jsonl 写入 system/init
   │    → ACP server 返回 {sessionId: "cd649582-..."}
   ├─ emit stdout: {"type":"session","sessionId":"cd649582-...","resumed":false}
   ├─ await conn.prompt({sessionId, prompt})
   │    ┌─────────────────────────────────────────────┐
   │    │ ACP server 往 claude 的 stdin 写入:         │
   │    │   {"type":"user","message":{"role":"user",  │
   │    │    "content":"Create hello.py..."}}         │
   │    │                                              │
   │    │ claude 开始跑:                               │
   │    │   1. 读 prompt,做 planning                   │
   │    │   2. 调 Write tool → 写 /tmp/foo/hello.py   │
   │    │   3. 发 result 给 ACP server                 │
   │    │   4. 每一步都追加到 jsonl 文件                │
   │    └─────────────────────────────────────────────┘
   ├─ 每个 session 事件翻译成 NDJSON 写 stdout:
   │    {"type":"text","text":"I'll create the file..."}
   │    {"type":"tool_call","name":"Write",...}
   │    {"type":"tool_update","status":"completed",...}
   │    {"type":"usage",...}
   │    {"type":"done","stop_reason":"end_turn"}
   ├─ close claude-agent-acp stdin → 触发级联清理
   │    pid=N2 退出 code=0
   │    pid=N3 被 ACP server 的 dispose() kill 掉
   └─ wrapper 进程 exit 0
   │
   ▼
[openclaw-gateway]
   ├─ bash tool 看到 exit,收集全部 stdout
   ├─ 返回给OpenClaw 主 agent agent(tool_result)
   ├─ OpenClaw 主 agent parse NDJSON,提取:
   │    - sessionId="cd649582-..."  ← 关键!记在 assistant 消息里
   │    - text 拼起来当 CC 回复
   │    - tool_call/update 折叠成进度摘要
   └─ 回复用户:
      "完成! hello.py 已创建。CC session id 是 cd649582-..."
   │
   ▼
[用户] 看到OpenClaw 主 agent的回复
```

**持久化状态结束时的样子**:

```
/root/.openclaw/agents/main/sessions/cc-e2e-XXX.jsonl     ← OpenClaw 主 agent的对话
  ├─ [user] 用户的原始消息
  ├─ [assistant:tool] exec claude-as-acp --cwd /tmp/foo ...
  ├─ [toolResult] NDJSON 全文(含 sessionId 那行)
  └─ [assistant] 回复用户的总结(含 "CC session id 是 cd649582-...")

/root/.claude/projects/-tmp-foo/cd649582-....jsonl        ← Claude Code 的 transcript
  ├─ system/init
  ├─ user {"content":"Create hello.py..."}
  ├─ assistant {tool_use: Write, input: {file_path, content}}
  ├─ user(toolResult) {"content":"File created..."}
  └─ assistant {final text}
```

---

## 4. "续接任务"的数据流(Round 2)

这是整个方案最微妙的部分。详细机制另起一篇专讲,这里只画数据流。

```
[用户] 发消息 "让它再加一行打印当前时间"
   │
   ▼
[openclaw-gateway]
   ├─ OpenClaw 主 agent从自己上一条 assistant 消息的文本里看到:
   │    "CC session id 是 cd649582-f194-48fe-8429-03f467730f40"
   │  ← 这个字符串是OpenClaw 主 agent自己在 Round 1 的 reply 里写的,
   │    openclaw session jsonl 里存着。
   │
   ├─ OpenClaw 主 agent判断:这是同一个任务的续作 → 需要 --resume
   ├─ 调 bash tool:
   │    command:  claude-as-acp --cwd /tmp/foo --resume cd649582-... 'Add a second line...'
   │
   ▼
[wrapper pid=N4 启动]  ← 全新的 wrapper 进程!上一轮的 N1/N2/N3 早就清理完了
   ├─ 解析 args:  resume="cd649582-..."
   ├─ spawn claude-agent-acp pid=N5  ← 也是全新的!
   ├─ await conn.initialize({...})
   ├─ await conn.loadSession({sessionId:"cd649582-...", cwd:"/tmp/foo"})
   │    ┌──────────────────────────────────────────────────────────┐
   │    │ ACP server 内部 (@anthropic-ai/claude-agent-sdk):         │
   │    │                                                           │
   │    │ 1. spawn claude pid=N6 with --resume cd649582-...         │
   │    │    claude 自己从 /root/.claude/projects/-tmp-foo/         │
   │    │      cd649582-....jsonl 读取历史                          │
   │    │    → 内存里重建 Round 1 的对话 context                    │
   │    │                                                           │
   │    │ 2. ACP server 调 getSessionMessages(sid) 读同一份 jsonl   │
   │    │    → 把历史消息通过 sessionUpdate 事件回放给 client       │
   │    └──────────────────────────────────────────────────────────┘
   ├─ emit stdout: {"type":"session","sessionId":"cd649582-...","resumed":true}
   │              (注意 resumed:true)
   ├─ await conn.prompt({sessionId, prompt: "Add a second line..."})
   │    ┌──────────────────────────────────────────────────────────┐
   │    │ claude CLI 现在的 context 包含:                           │
   │    │   - Round 1 的 user 消息(Create hello.py)                │
   │    │   - Round 1 的 assistant 消息(Write tool_use)            │
   │    │   - Round 1 的 tool_result                                │
   │    │   - Round 2 的 user 消息(Add second line)  ← 刚追加      │
   │    │                                                           │
   │    │ claude 看到"刚才写的 hello.py"就明白指哪个,               │
   │    │ 直接调 Read + Edit(不是 Write 覆盖)                      │
   │    │                                                           │
   │    │ 新事件持续追加到 jsonl 文件末尾                             │
   │    └──────────────────────────────────────────────────────────┘
   ├─ emit 新的 NDJSON 事件
   ├─ close stdin → 级联清理 N5 + N6
   └─ wrapper exit 0
   │
   ▼
[openclaw-gateway]
   ├─ OpenClaw 主 agent看到 tool_result,发现 sessionId 还是 cd649582-...
   ├─ Edit 成功,file 现在两行
   └─ 回复用户:"已添加第二行 ..."
```

**关键区别**:新进程 N5 跟旧进程 N2 **完全没有关系**,中间连接它们的**只有一个东西** —— 磁盘上的 `cd649582-....jsonl` 文件。

---

## 5. 为什么需要分两段协议

有人会问:既然OpenClaw 主 agent最终都是 parse NDJSON,为什么不让OpenClaw 主 agent直接调 `claude -p --input-format stream-json`?

**三个理由**:

1. **Root 守卫**:`claude -p --dangerously-skip-permissions` 作为 root 启动会硬拒。必须用 ACP 的 `canUseTool` callback 路径,避开 flag。这个 callback 只在 claude-agent-sdk / claude-agent-acp 这套调用约定里才有。

2. **权限协议化**:ACP 的 `requestPermission` 是协议级的结构化请求,可以自动 approve(`allow_always`/`allow_once`/`reject_once`),比 stream-json 的 `permission_prompt_tool` 更干净。wrapper 里一行 `allow_always` 就解决所有 Write 工具的审批。

3. **session/load 的语义**:ACP 的 `loadSession` 不是简单地 `claude --resume`,它**还会通过 sessionUpdate 事件把历史回放给客户端**(`replaySessionHistory` in acp-agent.js:760-778)。这意味着 wrapper 在 Round 2 的 stdout 里能看到完整历史的事件流,一致性更强。

---

## 6. 进程树实测截图

从一次真实跑的 `ps` 输出(lifecycle test,见 `docs/03-lifecycle-and-cleanup.md`):

```
PID   PPID  COMMAND
9422  9417  timeout 120 node /tmp/acp-lifecycle.mjs
9424  9422  node /tmp/acp-lifecycle.mjs                    ← wrapper
9431  9424  node /usr/bin/claude-agent-acp                  ← ACP server
9444  9431  claude                                          ← Claude Code CLI
```

4 层父子,PGID 相同(9422),同一进程组,收 SIGTERM/SIGHUP 同归于尽。

关 wrapper stdin 后:
```
[ACP_EXIT] code=0 sig=null
(no claude processes)   ← ps 再看,什么都没了
```

`claude-agent-acp` 退出触发 `agent.dispose()`,dispose 内部 kill 掉 claude CLI 子进程。见 `docs/03-lifecycle-and-cleanup.md` 的完整分析。

---

## 下一步

理解了架构,接下来看这两篇:

- [`02-multi-turn-mechanism.md`](02-multi-turn-mechanism.md) —— **多轮续接到底是怎么回事**(重点)
- [`03-lifecycle-and-cleanup.md`](03-lifecycle-and-cleanup.md) —— 进程什么时候起、什么时候死、会不会泄漏
