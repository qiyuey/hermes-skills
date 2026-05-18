# 02 · 多轮对话续接机制(重点)

> **⚠️ 版本提示**:本文档示例命令行使用 **v1.0.0 API**(`--resume <sid>` + `--cwd <path>`)。当前已升级到 **v2.0.0**,主 agent 只传 `--task <name>` 一个参数。**内部续接机制完全没变** —— 仍然是 "jsonl 文件是单一真源" + "loadSession 回放历史",只是 v2 的 workdir 由 task 名确定性派生(`/root/claudecode-workspace/<task>/`),wrapper 自动从对应的 `~/.claude/projects/<hash>/` 目录找最新 jsonl 续接,主 agent 不需要亲手传 sessionId。**机制本身的理解仍然完全适用本文档**,读时把 `--resume abc-123` 换成"自动由 wrapper 做"即可。详见 [`CHANGELOG.md`](CHANGELOG.md) 2.0.0 节。


> 这是整个 skill 最核心的一篇。读完这一篇,你能回答一个问题:
>
> **用户在企微跟 OpenClaw 聊了 3 轮,每轮都调 Claude Code 做事,但每次的 Claude Code wrapper 都是全新启动的子进程 —— Claude Code 凭什么记得上一轮做过什么?**

---

## TL;DR

连续对话的"记忆"**不在任何一个进程里**,它在**磁盘的一个 JSONL 文件里**。

```
/root/.claude/projects/<cwd-hash>/<sessionId>.jsonl
```

这个文件的存在让三层状态能够跨进程重建:

1. **claude CLI 进程内存**:收到 `--resume <sid>` 就读 jsonl → 逐行 replay → 重建内存里的对话历史
2. **claude-agent-acp 的 ACP session**:调 `getSessionMessages(sid)` 读同一个 jsonl → 通过 `sessionUpdate` 事件 replay 给客户端
3. **OpenClaw OpenClaw 主 agent的认知**:根本没读 jsonl,它只记得 sessionId 这个字符串,把它当"外部句柄"看待

三个角色通过一个 UUID(sessionId)+ 一个文件路径约定("cwd slash-to-dash 加前缀")就能协调。没有数据库,没有 IPC 总线,没有 Redis。

---

## 1. 先看证据:一次真实的 3 轮对话

实测数据(详见 [`04-e2e-validation.md`](04-e2e-validation.md)):

```
Round 1: "用 Claude Code 在 /tmp/cc-e2e 下写 hello.py 打印 Hi from Round 1"
Round 2: "让它再加一行 Still here in Round 2"
Round 3: "让它跑一下"
```

Wrapper 日志(`/tmp/claude-as-acp.log`):

```
[14:19:43] [pid=10514] START resume=null                      cwd=/tmp/cc-e2e
[14:19:46] [pid=10514] sessionId=cd649582-f194-48fe-8429-03f467730f40
[14:20:59] [pid=10624] START resume=cd649582-f194-48fe-8429-03f467730f40 cwd=/tmp/cc-e2e
[14:21:02] [pid=10624] sessionId=cd649582-f194-48fe-8429-03f467730f40
[14:21:59] [pid=10721] START resume=cd649582-f194-48fe-8429-03f467730f40 cwd=/tmp/cc-e2e
[14:22:02] [pid=10721] sessionId=cd649582-f194-48fe-8429-03f467730f40
```

观察:

- **3 个不同的 wrapper 进程**(pid 10514, 10624, 10721),每轮一个,短寿命
- 第 1 轮 `resume=null`(首次),后面两轮都传 `resume=cd649582-...`
- 3 轮都是**同一个 sessionId** `cd649582-...`
- Round 2 和 Round 3 的 wrapper 下面各自 spawn 了新的 `claude-agent-acp` 和 `claude` 子进程(都不是 Round 1 那些)

磁盘上的文件:

```bash
$ ls -la /root/.claude/projects/-tmp-cc-e2e/
-rw------- 1 root root 33642 Apr 15 14:22 cd649582-f194-48fe-8429-03f467730f40.jsonl

$ wc -l /root/.claude/projects/-tmp-cc-e2e/cd649582-f194-48fe-8429-03f467730f40.jsonl
42 /root/.claude/projects/-tmp-cc-e2e/cd649582-f194-48fe-8429-03f467730f40.jsonl
```

42 行,33KB。3 轮对话的所有 user / assistant / tool_use / tool_result / queue-operation / last-prompt 全在里面。**这个文件是唯一贯穿 3 轮的东西。**

---

## 2. 三个"session"别混淆

这一节先清思维,不然后面会被搞糊涂。整条链路里有**三个不同层次的"session"**:

| 层 | 名字 | 谁生成的 | 谁用 | 存哪 | 生命周期 |
|---|---|---|---|---|---|
| **A** | OpenClaw session | openclaw CLI(通过 `--session-id`)或 channel router | OpenClaw 主 agent自己,用来组织用户对话 | `/root/.openclaw/agents/main/sessions/<A-id>.jsonl` | 只要用户在聊就活 |
| **B** | ACP session | `claude-agent-acp` 内部 `randomUUID()` (或 resume 复用) | ACP 协议层用来 route `prompt` / `cancel` 等 JSON-RPC 调用 | 只在 claude-agent-acp 进程内存里 | 跟 wrapper 进程同生共死 |
| **C** | Claude CLI session | `claude` CLI 自己生成 或 `--resume` 复用 | claude CLI 自己用来 key 它的 transcript 文件 | `/root/.claude/projects/<cwd-hash>/<C-id>.jsonl` | 磁盘上,只要文件不被删就永远活 |

**核心重要的事实**:从 claude-agent-acp 开始,**ACP session id (B) 和 Claude CLI session id (C) 是同一个 UUID**。

证据:`acp-agent.js:975-987`(installed package dist):

```javascript
async createSession(params, creationOpts = {}) {
  // We want to create a new session id unless it is resume,
  // but not resume + forkSession.
  let sessionId;
  if (creationOpts.forkSession) {
    sessionId = randomUUID();
  }
  else if (creationOpts.resume) {
    sessionId = creationOpts.resume;    // ← 直接用传进来的 resume id 作为 ACP session id
  }
  else {
    sessionId = randomUUID();
  }
  // ...
}
```

也就是说,当我们 `loadSession(sid)` 时,ACP server 内部新建的 session 就直接以 `sid` 作为 id。同时 claude CLI 是用 `--resume <sid>` 启动的(见下面),CLI 侧也认这个 id。所以 B=C,**一个 UUID 两边认**。

**OpenClaw 主 agent那边的 A**(openclaw session)跟 B/C 完全是两回事。OpenClaw 主 agent知道的是 "CC session id 是 cd649582-...",那是 C 的 id;OpenClaw 主 agent自己所在的 openclaw session 叫什么(比如 `cc-e2e-1776233961`),claude 那边一点都不知道。

下文统一把 B/C 这个 UUID 叫 **`ccSid`**(Claude Code session id),把 A 叫 **`openclawSid`**。

---

## 3. 核心机制:一个 UUID + 一个文件路径约定

### 3.1 文件路径的计算规则

`/root/.claude/projects/<cwd-hash>/<ccSid>.jsonl`

- `cwd-hash` 的规则:把 cwd 的绝对路径里的 `/` 替换成 `-`。
- 我们的例子:cwd 是 `/tmp/cc-e2e` → `-tmp-cc-e2e`
- 更多例子:
  - `/root` → `-root`
  - `/tmp/my-project` → `-tmp-my-project`
  - `/home/user/code` → `-home-user-code`

这不是 hash,只是简单的字符替换。claude CLI 和 claude-agent-sdk 内部都用同一个规则,所以**只要 cwd 一致,双方都能定位到同一个文件**。

**重要**:如果下一轮传给 wrapper 的 `--cwd` 跟第一轮不一样,即使 `--resume` 传了同一个 UUID,claude CLI 也找不到 jsonl 文件,resume 会失败。所以 wrapper 的 SKILL.md 里反复强调"永远带 `--cwd <同一个 dir>`"。

### 3.2 JSONL 文件长什么样

一次看过的真实文件样本(`cd649582-....jsonl` 的 42 行,类型分布):

```
16 × user         (user role entries: 用户消息 + toolResult 都是 user 角色)
16 × assistant    (assistant messages including tool_use blocks)
 6 × queue-operation
 3 × last-prompt
 1 × attachment
```

每行是一个 JSON 对象。关键字段(对 multi-turn 续接至关重要):

```json
{
  "type": "user",
  "uuid": "c00cf1ad-daa9-48a9-961b-c98dd37b16f9",
  "parentUuid": null,       // 或某个 uuid,表示消息树的父节点
  "sessionId": "cd649582-f194-48fe-8429-03f467730f40",
  "timestamp": "2026-04-15T14:19:43Z",
  "message": {
    "role": "user",
    "content": [...]
  }
}
```

- `sessionId` 字段:每行都带,写死是 ccSid
- `parentUuid` + `uuid`:消息形成一棵树(不是线性列表!),支持 fork/branch
- `message` 字段:标准的 Anthropic messages API 格式,可以直接 replay 给 LLM

Claude CLI 用 `--resume <ccSid>` 启动时,它做的事情就是:读这个文件 → 按 uuid→parentUuid 拼出消息树 → 找到当前分支的叶子节点 → 把整条链作为 prompt context 发给 LLM。

### 3.3 `getSessionMessages` 和 `replaySessionHistory`

claude-agent-acp 里的 `loadSession` 流程,`acp-agent.js:239-246`:

```javascript
async loadSession(params) {
  const result = await this.getOrCreateSession(params);       // step 1
  await this.replaySessionHistory(params.sessionId);          // step 2
  setTimeout(() => {                                          // step 3
    this.sendAvailableCommandsUpdate(params.sessionId);
  }, 0);
  return result;
}
```

Step 1 — `getOrCreateSession`:

```javascript
async getOrCreateSession(params) {
  const existingSession = this.sessions[params.sessionId];
  if (existingSession) {
    // 同进程内已经存在,复用
    return { sessionId: params.sessionId, modes, ... };
  }
  // 进程内没有 → 新建,但带上 resume 参数
  const response = await this.createSession(
    { cwd: params.cwd, mcpServers: ... },
    { resume: params.sessionId }     // ← 关键
  );
  return { sessionId: response.sessionId, ... };
}
```

`createSession` 又往下调 `query()`(来自 `@anthropic-ai/claude-agent-sdk`),把 `resume: params.sessionId` 传给 SDK。SDK 最终做的事情是 `spawn('claude', ['--resume', sid, '--output-format', 'stream-json', ...])`。证据:`sdk.mjs` 里看到的 spawn 命令构造逻辑:

```javascript
if(_)l.push("--resume",_);   // _ 是 options.resumeSessionId
```

这一步完成后,**内存中有了一个新的 claude CLI 子进程,它已经读完了 jsonl 文件,内存里挂着完整的 Round 1 对话历史**。

Step 2 — `replaySessionHistory`,`acp-agent.js:760-778`:

```javascript
async replaySessionHistory(sessionId) {
  const toolUseCache = {};
  const messages = await getSessionMessages(sessionId);    // ← 读 jsonl
  for (const message of messages) {
    for (const notification of toAcpNotifications(
      message.message.content,
      message.message.role,
      sessionId, toolUseCache, this.client, this.logger,
      { registerHooks: false, clientCapabilities: this.clientCapabilities, cwd: ... }
    )) {
      await this.client.sessionUpdate(notification);      // ← 把历史事件 replay 给客户端
    }
  }
}
```

`getSessionMessages(sessionId)` 是 `@anthropic-ai/claude-agent-sdk` 导出的函数,它**独立地读同一个 jsonl 文件**(不依赖内存中那个新 spawn 的 claude CLI 进程),把每行解析成 message 对象返回。

然后这些 message 被翻译成 ACP 的 `sessionUpdate` 事件(`agent_message_chunk` / `tool_call` / `tool_call_update` ...)推给客户端。

**这意味着**:当 wrapper 做完 `loadSession` 后,它的 stdout 会**先吐出一遍 Round 1 的所有历史事件**,然后才处理 Round 2 的新 prompt。我们实际测试里的 R3 输出就验证了这一点 —— 你在 Round 3 的 stdout 里能看到 Round 1 + Round 2 的所有 Write / Edit / Bash tool 调用事件被回放一遍,然后才是 Round 3 新做的事情。

Step 3 — `sendAvailableCommandsUpdate` 是个锦上添花的步骤(告诉客户端当前 session 可用哪些 slash command),跟 multi-turn 无关。

---

## 4. 完整 Round 2 时序图

把上面三节拼起来,一个完整的 Round 2 长这样:

```
用户: "让它再加一行打印当前时间"
 │
 ▼
╔═════════ OpenClaw Gateway / OpenClaw 主 agent ═══════════╗
║                                              ║
║  1. OpenClaw 主 agent读自己上一条 assistant 消息,里面有   ║
║     字符串 "CC session id 是 cd649582-..."   ║
║     提取 ccSid = "cd649582-..."               ║
║                                              ║
║  2. 调 bash tool spawn wrapper:              ║
║     claude-as-acp --cwd /tmp/cc-e2e \        ║
║        --resume cd649582-... 'Add a ...'     ║
║                                              ║
╚═══════════════════╦══════════════════════════╝
                    │
                    ▼
╔═════════ wrapper (claude-as-acp) ═══════════╗
║  pid = N4 (新进程!)                          ║
║                                              ║
║  3. parse args                               ║
║  4. spawn /usr/bin/claude-agent-acp          ║
║     pid = N5 (新进程!)                        ║
║                                              ║
║  5. await conn.initialize(...)               ║
║                                              ║
║  6. await conn.loadSession({                 ║
║       sessionId: "cd649582-...",             ║
║       cwd: "/tmp/cc-e2e",                    ║
║       mcpServers: []                         ║
║     })                                       ║
║                                              ║
╚═══════════════════╦══════════════════════════╝
                    │
                    ▼
╔══════ claude-agent-acp (pid N5) ═══════════╗
║                                              ║
║  7. getOrCreateSession:                      ║
║     this.sessions["cd649582-..."]           ║
║       → undefined (新进程,内存里没)          ║
║     → createSession({                        ║
║         cwd: "/tmp/cc-e2e",                  ║
║         resume: "cd649582-..."               ║
║       })                                     ║
║                                              ║
║  8. createSession → query({                  ║
║       cwd, resume, ...                       ║
║     }) [claude-agent-sdk 调用]                ║
║                                              ║
╚═══════════════════╦══════════════════════════╝
                    │
                    ▼
╔════════ claude-agent-sdk ═══════════════════╗
║                                              ║
║  9. spawn /usr/local/bin/claude with:        ║
║     --resume cd649582-...                    ║
║     --input-format stream-json               ║
║     --output-format stream-json              ║
║     --verbose                                ║
║     pid = N6                                 ║
║                                              ║
╚═══════════════════╦══════════════════════════╝
                    │
                    ▼
╔════════ claude CLI (pid N6) ════════════════╗
║                                              ║
║ 10. 启动时读 --resume 参数                    ║
║ 11. 根据 cwd + sid 算出文件路径:              ║
║     /root/.claude/projects/                  ║
║       -tmp-cc-e2e/                           ║
║       cd649582-....jsonl                     ║
║ 12. 打开这个文件,逐行解析,重建对话上下文     ║
║ 13. 准备接收新的 user message(stdin)         ║
║                                              ║
║ 发出 {"type":"system","subtype":"init",...}  ║
║ 到 stdout                                    ║
║                                              ║
╚═══════════════════╦══════════════════════════╝
                    │ stream-json NDJSON
                    ▼
╔══════ claude-agent-acp (pid N5) ═══════════╗
║                                              ║
║ 14. 收到 claude 的 init 事件,session 就绪     ║
║                                              ║
║ 15. 回 loadSession 的返回值给 wrapper        ║
║     但在返回前,继续第 16 步:                  ║
║                                              ║
║ 16. replaySessionHistory(sid):                ║
║     messages = getSessionMessages(sid)        ║
║       → 读 /root/.claude/projects/           ║
║         -tmp-cc-e2e/cd649582-....jsonl       ║
║       → 返回所有历史 message 对象              ║
║     for each message:                         ║
║       → 翻译成 ACP sessionUpdate 事件         ║
║       → await this.client.sessionUpdate(...) ║
║                                              ║
║  注意:历史 replay 是通过 client.sessionUpdate║
║  回调送到 wrapper 的,wrapper 再吐到 stdout   ║
║                                              ║
╚═══════════════════╦══════════════════════════╝
                    │ ACP sessionUpdate events
                    ▼
╔═════════ wrapper (pid N4) ═══════════════════╗
║                                              ║
║ 17. 在 sessionUpdate 回调里把每个事件翻译成   ║
║     NDJSON 行写到 stdout                      ║
║     (你会看到 Round 1/2 的 tool_call 历史     ║
║      在 Round 3 的 stdout 里重新出现一遍)     ║
║                                              ║
║ 18. loadSession 返回后,emit:                 ║
║     {"type":"session","sessionId":"cd...",    ║
║      "resumed":true}                          ║
║                                              ║
║ 19. await conn.prompt({                       ║
║       sessionId: "cd649582-...",              ║
║       prompt: [{type:"text", text:"Add..."}]  ║
║     })                                        ║
║                                              ║
╚═══════════════════╦══════════════════════════╝
                    │
                    ▼
╔══════ claude-agent-acp → claude CLI ═════════╗
║                                              ║
║ 20. ACP server 把 new prompt 转成 stream-json║
║     塞到 claude CLI 的 stdin                  ║
║                                              ║
║ 21. claude CLI 的 in-memory context 里现在有:║
║     [Round 1 user] "Create hello.py..."       ║
║     [Round 1 asst] tool_use Write + result   ║
║     [Round 2 new user] "Add second line..."   ║
║                                              ║
║ 22. claude 做 tool loop:                      ║
║     - Read hello.py (认出这是 R1 写的)        ║
║     - Edit 加一行                              ║
║     - 返回 result                              ║
║                                              ║
║ 23. 把新事件追加到同一个 jsonl 文件末尾       ║
║                                              ║
╚═══════════════════╦══════════════════════════╝
                    │
                    ▼
              (NDJSON 事件回流,最终到OpenClaw 主 agent)
                    │
                    ▼
╔═════════ OpenClaw 主 agent ════════════════════════════════╗
║                                              ║
║ 24. 解析所有 NDJSON 事件,提取新产生的        ║
║     text / tool_call_update,折叠成给用户的   ║
║     简要摘要                                  ║
║                                              ║
║ 25. 回复用户:"已添加第二行..."                ║
║                                              ║
╚══════════════════════════════════════════════╝
```

---

## 5. 几个容易误会的点

### 5.1 "Claude Code 的内存"其实每轮都是新建的

很多人会想象 claude CLI 是一个常驻进程,像 IDE 里那样内存里挂着对话历史,用户发新消息追加到内存。

**不是这样**。在 delegate 这个场景下:

- 每次用户让OpenClaw 主 agent做事,OpenClaw 主 agent都会 spawn 一个全新的 wrapper 进程
- wrapper spawn 一个全新的 claude-agent-acp 进程
- claude-agent-acp spawn 一个全新的 claude CLI 进程
- 新的 claude CLI 进程里,内存是空的

"记住上一轮"**完全依赖重新读 jsonl 文件**。每一轮都是"冷启动 + 从磁盘重建 context"。

这听起来很慢?实测:Round 2 的首个 agent_message_chunk 大约在 wrapper 启动后 3-5 秒出来。这 3-5 秒里干了这些事:

- spawn claude-agent-acp(~100ms)
- spawn claude CLI with --resume(~500ms)
- claude 读 jsonl 并解析(~200ms,32KB 文件)
- claude 把历史作为 prompt context 发给 Bedrock(~500ms 网络)
- Bedrock 首 token 延迟(~2s)
- wrapper 把事件翻译到 stdout(<50ms)

冷启动开销不到 1 秒,大头是 Bedrock 的网络 + 首 token。而且 **cache read** 会生效(看 `cachedReadTokens` 字段),第二轮开始 prompt caching 让 Bedrock 侧实际不用重跑前面的 tokens,只处理新追加部分。

### 5.2 "OpenClaw 主 agent记得 sessionId" 不是什么魔法

OpenClaw 主 agent记住 sessionId 是因为:

1. Round 1 wrapper 把 sessionId 吐到了 stdout 第一行(NDJSON)
2. 这个 stdout 通过 bash tool 的 tool_result 回到OpenClaw 主 agent
3. tool_result 被记录在 openclaw 的 session jsonl 里(作为 `{type:"message", message:{role:"toolResult"}}`)
4. OpenClaw 主 agent在 Round 1 的 assistant 回复里写了 "CC session id 是 cd649582-...",这句话也入了 jsonl(作为 `{type:"message", message:{role:"assistant"}}`)
5. Round 2 用户发消息时,openclaw 把**整个 session 历史**作为 prompt context 送给OpenClaw 主 agent的 LLM
6. OpenClaw 主 agent的 LLM 看到自己上一条消息里有那个 sessionId 字符串,在新 prompt 的 context 里就"记得"了

这是**纯 LLM in-context 记忆**,没有任何结构化存储/变量/状态机。靠的是OpenClaw 主 agent认真读自己 session 历史的能力。

**这是 phase2 翻车最多的地方**:phase2 的 wrapper 吐纯文本,sessionId 没有显式结构化标记,OpenClaw 主 agent读到"Claude 做完了"这种自然语言就够了,不觉得需要记录什么"id";而 phase4 强制第一行是 `{"type":"session","sessionId":"..."}`,OpenClaw 主 agent一看就知道要存,而且 SKILL.md 里也明确教了"提取 sessionId 记住它"。

### 5.3 JSONL 文件不是只写不读

上面说 claude CLI 启动时读 jsonl。那**运行期间**怎么办?如果一轮对话跑了 5 分钟,中间 claude 和 Bedrock 来回了 10 次,这些新事件什么时候进 jsonl?

答案:**边跑边写**。每个 user / assistant / tool_use / tool_result 产生时,claude CLI 就 append 一行到 jsonl。所以 jsonl 始终是"到这一刻为止的完整对话历史"。

这也解释了为什么 wrapper 在中途被 kill,claude CLI 只要写到了 jsonl,就能被下次 `--resume` 恢复。cancel 一轮对话也是一样 —— cancel 让 LLM 提前停,但已经产生的消息已经落盘,下次还在。

### 5.4 多个 session 会不会互相干扰

一个 cwd 下可以有**多个** jsonl 文件(多个 ccSid)。它们是独立的,claude CLI 通过 `--resume <sid>` 精确选一个。例如我的 bot 上测试期间 `/tmp/ccloop-test` 下有 3 个文件:

```
/root/.claude/projects/-tmp-ccloop-test/
  54ef3c30-ee66-4692-b7c1-089c791f55b9.jsonl
  57651057-cb9d-44c2-aaa3-f2c25fd4380c.jsonl
  f1be574c-eb32-4347-887a-444aabbad35e.jsonl
```

3 个是不同 smoke test 造出来的,彼此独立。只要 --resume 的 id 没弄混,互不干扰。

OpenClaw 主 agent如果一次对话里想同时跟 2 个 CC session 说话,理论上可以(分别维护两个 sessionId),但 SKILL.md 目前没专门教这个,OpenClaw 主 agent可能会混乱。这是目前已知的可改进点。

---

## 6. 为什么 session/load 比 session/create 多一步 replay

你可能注意到 `loadSession` 里的 `replaySessionHistory` 步骤。这一步是**给 client 看的**,不是给 claude CLI 看的。

- claude CLI 自己读 jsonl(通过 `--resume` 参数)→ 它的内存里有了历史,**LLM 侧 context 完整**
- 但 wrapper(client)的视角是空的,它之前的那些 tool_call_update 事件在另一个已死的 wrapper 进程里
- 如果 wrapper 要给用户展示"已经做过哪些事",需要这些历史事件 → 所以 ACP server 从 jsonl 主动 replay 一遍给 client

这让 ACP 协议在设计上比 stream-json 更 "stateful client-friendly":
- stream-json:客户端得自己去读 jsonl 才能知道历史
- ACP:`loadSession` 自带 replay,客户端"订阅式"拿到所有历史事件

对我们这个场景,replay 不是严格必需的(OpenClaw 主 agent只关心**新**发生的事),但不影响正确性,只是多了几十条 sessionUpdate 事件被 wrapper 吐到 stdout。wrapper 在 SKILL.md 里教OpenClaw 主 agent"只关注本轮的新 tool_call/tool_update",所以OpenClaw 主 agent能正确过滤掉回放的那部分。

---

## 7. 总结:一张图看懂

```
       Round 1                      Round 2                      Round 3
  ╭───────────────╮            ╭───────────────╮            ╭───────────────╮
  │ wrapper pid   │            │ wrapper pid   │            │ wrapper pid   │
  │ 10514 (死)    │            │ 10624 (死)    │            │ 10721 (死)    │
  ╰───────┬───────╯            ╰───────┬───────╯            ╰───────┬───────╯
          │ 写                          │ 读+追加                     │ 读+追加
          ▼                             ▼                             ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                               │
│  /root/.claude/projects/-tmp-cc-e2e/cd649582-...jsonl                         │
│                                                                               │
│  [ Round 1 消息 ] [ Round 2 消息 ] [ Round 3 消息 ]                            │
│                                                                               │
│  (append-only,整个 conversation 的单一真源)                                  │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
          ▲                             ▲                             ▲
          │                             │                             │
          │                             │                             │
          │                             │                             │
     OpenClaw 主 agent记住                     OpenClaw 主 agent传回                        OpenClaw 主 agent传回
     cc-sid=cd649582             --resume cd649582              --resume cd649582
```

所有的"连续性"都是这一个文件提供的。三个 wrapper 进程是生是死无关紧要,文件还在,下次一定能接上。这个设计的好处是:

- **无状态 wrapper**:wrapper 完全没有自己的状态,是纯函数
- **进程级崩溃隔离**:claude CLI 卡了/崩了,重启下一轮不受影响
- **可调试**:想知道某轮对话发生了什么,`cat <sid>.jsonl | jq` 直接看
- **简单**:没有 IPC、没有锁、没有信号量

**唯一的硬性约束**:那个 jsonl 文件不能被删。如果运维出于磁盘清理需求把 `/root/.claude/projects/` 清了,所有进行中的对话就失忆。SKILL.md 里的 troubleshooting 一节有提到这种情况的 fallback。

---

## 8. 附:关键源码位置

| 功能 | 文件 | 行号 |
|---|---|---|
| `loadSession` 入口 | `@agentclientprotocol/claude-agent-acp/dist/acp-agent.js` | 239-246 |
| `replaySessionHistory` | 同上 | 760-778 |
| `getOrCreateSession` | 同上 | 947-977 |
| `createSession` with `resume` | 同上 | 979-1005 |
| `getSessionMessages` 来源 | `@anthropic-ai/claude-agent-sdk`(闭源 bundle) | `sdk.mjs` |
| `--resume` flag 传给 claude CLI | `@anthropic-ai/claude-agent-sdk/sdk.mjs` | 搜索 `"--resume"` |
| jsonl 写入 | claude CLI 内部(闭源) | 不可直接读 |

若要自己在 bot 上确认,可执行:

```bash
node -e '
const { getSessionMessages } = require("/usr/lib/node_modules/@agentclientprotocol/claude-agent-acp/node_modules/@anthropic-ai/claude-agent-sdk/sdk.mjs");
getSessionMessages("cd649582-f194-48fe-8429-03f467730f40").then(msgs => {
  console.log("got", msgs.length, "messages");
  console.log(msgs[0]);
});
'
```

这会直接从磁盘 jsonl 解析消息,跟 `replaySessionHistory` 内部用的是同一个函数。
