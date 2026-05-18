# 阶段四:User-Space ACP Wrapper(phase3 漏掉的第七个方案)

> 起草时间:2026-04-15
> 状态:**纸上设计 + 待验证**,代码未写
>
> 关联文档(必读):
> - [`acp-investigation.md`](./acp-investigation.md) — acpx bundling bug 的原始证据
> - [`phase2-skill-approach.md`](./phase2-skill-approach.md) — 当前 fallback 方案 B 的产物 + 踩坑
> - [`phase3-custom-acp-runtime-exploration.md`](./phase3-custom-acp-runtime-exploration.md) — 6 个方案探索失败记录,**特别是 §7 方案 E 死于 bundling bug**
>
> **本文要做的事**:phase3 把所有"在 OpenClaw 内部走 ACP"的路径都堵死了,但漏掉了一个方向 —— **wrapper 跑在 user space(bash tool 的子进程),自己当 ACP client 直连 `claude-agent-acp`,绕开整个 OpenClaw plugin 体系**。本文记录这个想法的边界、收益、验证步骤、实现方案,交给公司 agent 接手实施。

---

## 0. 一句话总结

把方案 B(`delegate-to-claude-code` skill)的 `claude-as-node` wrapper **从"调 `claude` CLI 输出 stream-json 文本"改成"调 `claude-agent-acp` 走标准 ACP JSON-RPC"**,wrapper 内部解析 ACP `session/update` events 翻译成结构化 NDJSON 写 stdout 给大橘。

传输边界:
```
大橘 ──bash tool──> wrapper(ACP client) ──stdio JSON-RPC ACP──> claude-agent-acp
       ^                ^                                          ^
       |                |                                          |
   非 ACP(bash)      标准 ACP                                  Anthropic 官方 SDK
```

---

## 1. 这跟 phase3 方案 E 有什么本质区别?

phase3 §7 的方案 E **死于 OpenClaw 的 bundling bug**(`infra/agent-events` 模块跨 chunk split,subsystem chunk 的 emit 跨不到 gateway core chunk 的 listener)。

| 维度 | 方案 E(死) | 本方案(活) |
|---|---|---|
| wrapper/backend 跑在哪 | OpenClaw subsystem chunk 内部(作为 plugin) | **bash tool 的 child process(完全 user space)** |
| 注册方式 | `registerAcpRuntimeBackend()` | **不注册任何东西**,大橘通过 bash tool 调 wrapper binary |
| 大橘怎么触发 | `sessions_spawn(runtime:"acp")` | `bash pty:true command:"claude-as-node-acp ..."` |
| 跟 OpenClaw event bus 的关系 | **必须经过 `emitAgentEvent`** | **完全不碰** |
| events 怎么回到大橘 | event bus → parent stream relay(被 bug 挡死) | **bash tool 的 stdout → tool_result(已验证 work)** |
| 撞 bundling bug? | **是** | **否** |
| 改 OpenClaw image / config / plugin? | 要改 plugin allowlist | **零改动**,跟 phase2 一样 |

**关键洞察**:bug 在 OpenClaw 内部 module 加载层,只要 wrapper **不进 OpenClaw 的 module 系统**,就根本不会撞上。bash tool 子进程是 OS 进程级隔离,跟 chunk split 完全不在一个层面。

**佐证**:`acp-investigation.md` 引用的 issue #51345 作者亲手验证了 "Direct acpx Works Fine":
```
$ /home/molty/openclaw/extensions/acpx/node_modules/.bin/acpx --approve-all claude exec "Say hello..."
[client] initialize (running)
[client] session/new (running)
Hello — I'm Claude Code powered by Claude Opus 4.6, running via acpx...
[done] end_turn
```
**只有走 OpenClaw 的 spawn 路径才挂**。我们这个方案就是 "direct" 模式,只是把 acpx 也省了——wrapper 自己当 ACP client 直连 `claude-agent-acp`。

---

## 2. 真实收益(诚实评估,**不要夸大**)

### 2.1 真的拿到的(对比 phase2 方案 B 的纯文本输出)

- ✅ **`tool_call` / `tool_result` 是结构化事件**,大橘明确知道 claude 在跑哪个 tool、参数是什么、输出是什么 —— 不再是从混合 stdout 文本里 grep
- ✅ **`stop_reason` 明确**(`end_turn` / `max_tokens` / `tool_use` / ...),能可靠判断 "claude 真完了 vs 卡住 vs 工具循环中"
- ✅ **`thinking` 单独一个流**,不混在 user-visible text 里
- ✅ **session ID 通过协议 response 拿到**,不用 `ls -t .../*.jsonl | head -1` 猜文件,phase2 的 auto-resume 双刃剑问题(见 phase2 §4.2 坑 6)直接消失
- ✅ **未来通用化**:换 `codex-acp` / `gemini-acp` 只需替换 child 命令,wrapper 内部 ACP 协议层不变。**这是用户最初要求的"通用 ACP 委派"在不改 OpenClaw 前提下唯一能落地的形态**

### 2.2 没拿到的(必须看清楚,别被"标准 ACP" 这四个字骗了)

- ❌ **ACP 协议本身没有 "agent 想问用户内容性问题" 的原语**。`permission_request` 是给"工具调用需要批准"用的(比如 Bash tool 要不要让我跑 `rm -rf`),不是给 "claude 不知道该选 dark theme 还是 light theme" 用的
- ❌ **"agent 内容追问"在 LLM 层永远是 `stop_reason: end_turn` + 最后一段 text 是问句**,跟"任务完成"在 ACP 协议层**无法区分**
- ❌ 所以"识别追问"还是要看 final text 做启发式判断 —— **协议级追问的优势是个误会,不存在**

### 2.3 为什么间接还是值得做

虽然 §2.2 戳破了协议级追问的幻想,但因为 §2.1 的两点:
- `stop_reason` 干净可靠
- final text 单独从 `agent_message_chunk` 里拿,不混 tool 输出 / 不混 thinking

**启发式判断准确率会比 phase2 从混合 stdout 文本里 grep 高很多**。这是间接收益,不是协议给的,是 "输入更干净" 给的。

### 2.4 用户的"标准 ACP"诉求满足了吗?

**满足了一半**:
- wrapper ↔ claude-agent-acp 段是真正的标准 ACP
- 大橘 ↔ wrapper 段不是 ACP(是 bash tool result)—— 但**大橘根本不能消费 ACP**,它只能消费 bash tool 的 string/JSON 结果。这是 OpenClaw 自身的限制,任何在 "不改 OpenClaw" 约束下的方案都改不了这一点

如果用户再次纠结 "为什么不是端到端 ACP",答案是:**端到端 ACP = 走 `sessions_spawn(runtime:"acp")` = 撞 bundling bug = 死**。phase3 §10.3 已经写过:**"不改 OpenClaw" + "端到端标准 ACP" 在当前版本下是数学上互斥的**。本方案是这两条诉求的最大公约数。

---

## 3. 必须先做的 3 个验证(动手写 wrapper 之前)

**预计耗时:30-60 分钟**。3 个验证全绿才值得继续。任何一个红就说明这条路也死了,回 phase2 方案 B。

### 验证 1:`claude-agent-acp` 在 root 下能跑吗?

**为什么问这个**:Claude CLI 在 root 下硬编码拒绝 `--dangerously-skip-permissions`(phase2 §4.2 坑 3,源码 `cli-launcher.ts:499-518`)。phase2 的 wrapper 必须 `su node` 绕过去。

`claude-agent-acp` 用的是 `@anthropic-ai/claude-agent-sdk`(库,不是 CLI binary),**理论上可能没有那个守卫**。但要实测确认。

**怎么验证**:
```bash
# 进容器
ssh windows docker exec -it openclaw-openclaw-gateway-1 bash

# 装 claude-agent-acp(它会作为 npm package 装在 _npx 里,acp-investigation §4.4.8 见过)
npx --yes @agentclientprotocol/claude-agent-acp --version

# 直接以 root 身份跑一下,看启动会不会被守卫拒
HOME=/root \
ANTHROPIC_BASE_URL=https://yunbiaobiao.com \
ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY /proc/1/environ | tr '\0' '\n' | grep ANTHROPIC_API_KEY | cut -d= -f2) \
claude-agent-acp 2>&1 | head -20
```

**预期**:
- ✅ 看到 ACP server 启动 banner / 等 stdin JSON-RPC 输入 → root 下能跑,wrapper 可以省掉 `su node`
- ❌ 看到 "cannot be used with root/sudo privileges" 或类似 → 也得 `su node`,跟 phase2 一样

**任一结果都不致命**,只是决定 wrapper 要不要保留 `su node` 兜底。

### 验证 2:`claude-agent-acp` 认 yunbiaobiao 代理 + `claude-sonnet-4-6-thinking` 吗?

**为什么问这个**:容器里的 token 只授权 `claude-sonnet-4-6-thinking`(phase1 §3.2 / acp-investigation §4.6.10)。phase2 是写 `/home/node/.claude/settings.json` `{"model":"claude-sonnet-4-6-thinking"}` 解决的。`claude-agent-acp` 走 SDK 不走 CLI,要看它怎么读 model 配置。

**怎么验证**:写一个最小 ACP client smoke test。用 `@agentclientprotocol/sdk` 的 client API,发 `initialize` + `session/new` + `session/prompt "say hello"`,看能不能拿到 `agent_message_chunk` 里的 "hello"。

```bash
# 容器内创建 /tmp/acp-smoke.mjs
cat > /tmp/acp-smoke.mjs <<'EOF'
import { spawn } from 'node:child_process';
import { ClientSideConnection } from '@agentclientprotocol/sdk';

const proc = spawn('npx', ['--yes', '@agentclientprotocol/claude-agent-acp'], {
  stdio: ['pipe', 'pipe', 'inherit'],
  env: {
    ...process.env,
    HOME: '/home/node',  // 让它读 /home/node/.claude/settings.json
    ANTHROPIC_BASE_URL: 'https://yunbiaobiao.com',
    ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY,
  },
});

const conn = new ClientSideConnection(proc.stdout, proc.stdin);
await conn.initialize({ protocolVersion: 1 });
const { sessionId } = await conn.newSession({ cwd: '/tmp' });
console.log('session created:', sessionId);

// 订阅 session/update
conn.on('sessionUpdate', (update) => {
  console.log('UPDATE:', JSON.stringify(update));
});

const result = await conn.prompt({
  sessionId,
  prompt: [{ type: 'text', text: 'say hello in one short sentence' }],
});
console.log('RESULT:', JSON.stringify(result));
process.exit(0);
EOF

# 装 SDK
cd /tmp && npm install @agentclientprotocol/sdk

# 跑
HOME=/home/node node /tmp/acp-smoke.mjs
```

**预期**:
- ✅ 看到 `UPDATE: {"sessionUpdate":"agent_message_chunk", ...}` 流式输出 + 最后 `RESULT: {"stopReason":"end_turn", ...}` → SDK + settings.json 路径全通,可以动手写 wrapper
- ❌ 看到 `403 该令牌无权访问模型 ...` → 说明 claude-agent-acp 没读 settings.json,要查它的 model 配置入口(可能是 ACP `session/new` 的 options 字段,或 env `ANTHROPIC_MODEL`)
- ❌ 看到 `initialize` hang / connection refused → ACP SDK 版本问题,要查 protocolVersion

**修复路径**(如果 ❌):
- 试 `ANTHROPIC_MODEL=claude-sonnet-4-6-thinking` env(claude-agent-acp 源码 `acp-agent.ts:~1800` 优先级:env > settings.json > models[0],见 acp-investigation §4.6.6)
- 试 `session/new` 时传 `options: { model: 'claude-sonnet-4-6-thinking' }`(协议字段名要查 SDK)

### 验证 3:`session/update` events 在 `--approve-all` 等价模式下还会包含 `permission_request` 吗?

**为什么问这个**:wrapper 必须用 "全自动批准工具" 模式跑,因为 OpenClaw 没法弹审批 UI(企微更不能)。如果 approve-all 模式下 `permission_request` event 完全不出现,我们就拿不到任何"工具权限拦截"的信号。

但其实**这不影响主路径**——我们要的是 `tool_call` / `tool_result` / `stop_reason`,不是 permission。Permission 只是"如果有就更好",没有也不是阻塞项。

**怎么验证**:在验证 2 的 smoke test 里加一个会触发工具的 prompt,比如 `prompt: 'run bash command: echo hello'`,看是否:
- 收到 `tool_call_start` event(name=Bash)
- 收到 `tool_call_complete` event(output 含 hello)
- 不收到 permission_request(因为我们要求 approve-all 模式)

**怎么传 approve-all**:`session/new` 的 options 里传 `permissionMode: 'bypassPermissions'` 或 `permissionMode: 'acceptEdits'`(具体值要查 ACP SDK / claude-agent-acp 文档)。如果传不进去,fallback 是 `ANTHROPIC_PERMISSION_MODE` env 或类似。

---

## 4. wrapper 实现设计(验证全绿后)

### 4.1 文件结构

```
/root/.openclaw/skills/delegate-to-claude-code-acp/
├── SKILL.md              # 改触发词,教大橘读结构化 NDJSON 输出
├── install.sh            # 装 claude-agent-acp + @agentclientprotocol/sdk + wrapper
└── bin/
    └── claude-as-node-acp   # 新 wrapper(Node 程序,~300 行)
```

**注意**:保留旧的 `delegate-to-claude-code` skill 不动。两个 skill 并存,新 skill 用不同名字 + 不同强触发词,先灰度对比再决定切换/弃用。

### 4.2 wrapper 行为(Node,使用 `@agentclientprotocol/sdk`)

输入(命令行参数):
```
claude-as-node-acp [--workdir <path>] [--resume <session-id>] [--reset] '<prompt>'
```

输出(stdout NDJSON,大橘从 bash tool_result 看到):
```jsonl
{"type":"session_started","sessionId":"abc-123","cwd":"/tmp/snake"}
{"type":"text","text":"我先创建文件骨架。"}
{"type":"tool_call","name":"Write","input":{"file_path":"/tmp/snake/main.py","content":"..."},"id":"tool_1"}
{"type":"tool_result","id":"tool_1","output":"File created"}
{"type":"text","text":"骨架完成,下一步加移动逻辑。"}
{"type":"thinking","text":"用户希望按方向键控制..."}
{"type":"done","stopReason":"end_turn"}
```

**关键设计**:
1. **stdin 不接受输入**(单 turn 模式)。多轮通过下次新调用 `--resume <session-id>` 续。这跟 phase2 方案 B 的形态一致,大橘做 orchestrator 用 bash tool 重复调用即可
2. **每个 NDJSON 行立即 `process.stdout.write(line + '\n'); process.stdout.write` 不 buffer**,让大橘流式看到(虽然 bash tool 的真实流式取决于 OpenClaw 实现,见 phase2 §4)
3. **session ID 从 `session/new` response 拿**,写到 stdout 第一行 `session_started`,大橘可以解析存下来作为下次 `--resume` 的参数
4. **错误统一格式**:`{"type":"error","code":"...","message":"...","retryable":true|false}`
5. **进程退出码**:成功 0,任何 ACP 协议错误或非 retryable 错误 1,retryable 错误(网络/limit)2

### 4.3 wrapper 内部流程(伪代码)

```
parseArgs() → {workdir, resume, reset, prompt}

if (workdir) chdir(workdir)
if (workdir) chown -R node:node workdir  # 兜底,跟 phase2 一样

env = {
  HOME: '/home/node',  // 或 /root,看验证 1 结果
  ANTHROPIC_BASE_URL,
  ANTHROPIC_API_KEY,
  ANTHROPIC_MODEL: 'claude-sonnet-4-6-thinking',  // 兜底,验证 2 决定要不要
}

proc = spawn('claude-agent-acp', [], { stdio: ['pipe','pipe','inherit'], env })
conn = new ClientSideConnection(proc.stdout, proc.stdin)

await conn.initialize({ protocolVersion: 1 })

if (resume && !reset) {
  session = await conn.loadSession({ sessionId: resume })
} else {
  session = await conn.newSession({
    cwd: workdir || '/tmp',
    options: { permissionMode: 'bypassPermissions' },  // 验证 3 决定字段名
  })
}

writeNdjson({ type: 'session_started', sessionId: session.sessionId, cwd: workdir })

conn.on('sessionUpdate', (update) => {
  // 翻译 ACP session/update event → 我们的 NDJSON schema
  switch (update.sessionUpdate) {
    case 'agent_message_chunk':
      writeNdjson({ type: 'text', text: update.content.text })
      break
    case 'tool_call_start':
      writeNdjson({ type: 'tool_call', name: update.name, input: update.input, id: update.id })
      break
    case 'tool_call_complete':
      writeNdjson({ type: 'tool_result', id: update.id, output: update.output })
      break
    case 'thinking_chunk':
      writeNdjson({ type: 'thinking', text: update.content.text })
      break
    case 'permission_request':
      // 如果验证 3 显示 approve-all 下还会发,这里要回 approve
      conn.respondPermission(update.id, { approved: true })
      break
    default:
      writeNdjson({ type: 'unknown', raw: update })  // 不丢,留排查用
  }
})

const result = await conn.prompt({
  sessionId: session.sessionId,
  prompt: [{ type: 'text', text: prompt }],
})

writeNdjson({ type: 'done', stopReason: result.stopReason })
proc.kill()
process.exit(0)
```

**字段名都是占位符**,验证 2/3 跑完之后用真实的 SDK API 替换。

### 4.4 install.sh

```bash
#!/bin/bash
set -euo pipefail

ACP_PKG_VERSION="latest"  # 或 pin 具体版本
SDK_PKG_VERSION="latest"
SKILL_DIR="/root/.openclaw/skills/delegate-to-claude-code-acp"
NODE_HOME="/home/node"

echo "=== delegate-to-claude-code-acp install ==="

# 1. 装 claude-agent-acp(全局或 npx 缓存)
npm install -g @agentclientprotocol/claude-agent-acp@${ACP_PKG_VERSION}
npm install -g @agentclientprotocol/sdk@${SDK_PKG_VERSION}

# 2. 装 wrapper 本身
install -m 0755 "$SKILL_DIR/bin/claude-as-node-acp" /usr/local/bin/claude-as-node-acp

# 3. node 用户的 settings.json(model pin)
mkdir -p "$NODE_HOME/.claude"
if [ ! -f "$NODE_HOME/.claude/settings.json" ]; then
  echo '{"model":"claude-sonnet-4-6-thinking"}' > "$NODE_HOME/.claude/settings.json"
fi
chown -R node:node "$NODE_HOME/.claude"

# 4. smoke test
echo "[smoke test] running claude-as-node-acp -p 'say OK'..."
if claude-as-node-acp 'say OK in one short sentence' 2>&1 | tee /tmp/acp-smoke.log | grep -q '"type":"done"'; then
  echo "✅ install complete"
else
  echo "❌ smoke test failed, see /tmp/acp-smoke.log"
  exit 1
fi
```

### 4.5 SKILL.md 改动方向

phase2 的 SKILL.md 教大橘 `bash pty:true command:"claude-as-node -p '...'"`,输出是自然语言文本。新 SKILL.md 要教:

1. **触发词**(强,跟 phase2 一样):`【MUST USE】当用户消息出现 "Claude Code" / "cc" / "让 Claude 帮我写" ...`
2. **调用方式**:`bash pty:true workdir:<dir> command:"claude-as-node-acp '<prompt>'"`
3. **关键变化:输出是 NDJSON,不是自然语言**。教大橘:
   - 每行是一个 event,`type` 字段决定语义
   - 进度展示给用户:把 `type:"text"` 的内容拼起来,把 `type:"tool_call"` 折叠成 "🔧 调用 Bash tool"
   - 完成判定:看到 `type:"done"`,根据 `stopReason` 决定下一步
   - 追问识别:如果 `stopReason: "end_turn"` 且最后一段 text 含问句特征(`?` / `请告诉我` / `哪个`),提取问句发给用户,**停 turn 等用户回复**
   - 多轮续接:从第一行 `session_started` event 拿 `sessionId`,下次调 `claude-as-node-acp --resume <sessionId> '<新 prompt>'`

### 4.6 跟 phase2 方案 B 的共存策略

**两个 skill 并存,触发词区分**:
- `delegate-to-claude-code`(phase2,纯文本)→ 触发词改成"快速委派"或保持原样作为 fallback
- `delegate-to-claude-code-acp`(phase4,结构化)→ 触发词写"标准委派" + 默认推荐

或者更简单:**phase4 跑稳后直接替换 phase2**,phase2 整个 skill 目录归档到 `archive/` 不再装。

---

## 5. 风险点

### 5.1 已知风险

| 风险 | 严重度 | 缓解 |
|---|---|---|
| `claude-agent-acp` 在 root 下也有守卫 | 中 | wrapper 加 `su node` 兜底,跟 phase2 一样 |
| ACP SDK 版本漂移(`@agentclientprotocol/sdk` 还很年轻,0.18.x) | 中 | install.sh pin 具体版本号,不用 `latest` |
| approve-all 模式下 permission_request 还出现 | 低 | wrapper 自动 respond approved |
| `claude-agent-acp` 对 yunbiaobiao 代理的 model 不兼容 | 中 | 验证 2 决定;真不行只能换 token / 接 Anthropic 直连 |
| OpenClaw bash tool 对 stdout 不是真流式 | 中 | phase2 §4 已经反复说过这是 channel plugin 决定的,wrapper 这边写多快都没用。但即使非流式,大橘看到的也是完整 NDJSON,比 phase2 的混合文本好 parse |
| `claude-agent-acp` 进程残留(参考 acp-investigation §4.4.8 的 19 个孤儿) | 中 | wrapper 严格 `proc.kill()` + `process.on('exit', ...)` 兜底,不依赖 ACP server 自己退出 |

### 5.2 没解决的(承认)

- **Agent 内容追问仍然要做启发式判断**(§2.2)。这条路仍然不能给协议级追问
- **大橘 prompt 遵守度问题没消失**(phase2 §4.4 的教训)。比如教大橘 "看到 `done` 就停 turn,不要自作主张继续",大橘可能还是会自作主张。**正确性还是要在 wrapper 层兜底**,不能依赖 SKILL.md prompt 写得多好

---

## 6. 给公司 agent 的 TODO 清单(可执行顺序)

**Phase A:验证(30-60 分钟)** —— 任何一个红就停下找原因或回 phase2

- [ ] **A1**:进 OpenClaw 容器(`ssh windows docker exec -it openclaw-openclaw-gateway-1 bash`),`npm install -g @agentclientprotocol/claude-agent-acp` 看能不能装上,`claude-agent-acp --version` 出版本号
- [ ] **A2**:验证 1 —— 以 root 身份直接跑 `claude-agent-acp`,看启动有没有 root 守卫
- [ ] **A3**:验证 2 —— 写 §3 验证 2 那段 50 行 Node smoke test,跑通 `initialize` + `session/new` + `prompt` + 拿到 `agent_message_chunk` 流。**关键看 model 是否被识别成 `claude-sonnet-4-6-thinking`**
- [ ] **A4**:验证 3 —— smoke test 加一个会触发 Bash tool 的 prompt,看 `tool_call_start` / `tool_call_complete` events 是否到达,permission 怎么处理

**Phase B:写 wrapper(1-2 天)**

- [ ] **B1**:基于验证结果确定 `session/new` 的 options 字段名(model / permissionMode / cwd)
- [ ] **B2**:写 `bin/claude-as-node-acp`(Node 程序,~300 行),按 §4.3 伪代码实现,**字段名用真实 SDK API 替换占位符**
- [ ] **B3**:写 `install.sh`(§4.4),包含 smoke test 的退出码检查
- [ ] **B4**:本地容器跑通 `bash /root/.openclaw/skills/delegate-to-claude-code-acp/install.sh`,看到 ✅
- [ ] **B5**:手动测一次 `claude-as-node-acp 'write hello.py in /tmp/test'`,看 stdout NDJSON 输出是否符合 §4.2 的 schema,**文件真生成**

**Phase C:写 SKILL.md(0.5 天)**

- [ ] **C1**:复制 phase2 的 SKILL.md 起手,改触发词为 "delegate-to-claude-code-acp"(或保持触发词类似但 description 改成"使用 ACP 协议委派")
- [ ] **C2**:写"读 NDJSON 输出"的章节,§4.5 列出的 5 件事每件都给大橘看的例子
- [ ] **C3**:写"多轮追问"章节,演示从 `session_started` event 提取 sessionId,下次 `--resume` 续接
- [ ] **C4**:重要:**写一个"识别追问场景"的明确流程**,告诉大橘看到 `done` event + final text 含问句特征时怎么做

**Phase D:fresh session 端到端测试(0.5 天)**

- [ ] **D1**:`docker exec` + `openclaw agent --session-id $(uuidgen) -m "用 cc 帮我在 /tmp/snake-acp 下写贪吃蛇"`,fresh session 验证大橘自动选用新 skill
- [ ] **D2**:验证大橘是否正确 parse NDJSON 输出 + 是否按 SKILL.md 教的方式给用户呈现进度
- [ ] **D3**:测一次追问场景:让 claude 故意产生需要澄清的输出(比如 "我需要知道你想用 light theme 还是 dark theme"),看大橘是否识别 + 转发给用户

**Phase E:决定切换策略(1 小时讨论)**

- [ ] 灰度共存 vs 直接替换 phase2
- [ ] 写 phase4 的 "实战记录" 文档(像 phase2-skill-approach.md 那样,记录踩坑)

---

## 7. 不要做的事(避免重蹈覆辙)

phase3 §10 的事实清单适用于本方案,特别强调:

1. **不要再尝试通过 OpenClaw plugin 体系注册任何东西**。registerAcpRuntimeBackend 是死路(phase3 §7),`chat.inject` 是 hack(用户已否决方案 F 的形态),所有 OpenClaw 内部注册 / RPC 路径都不要碰
2. **不要修 OpenClaw 源码 / image / openclaw.json**。本方案的全部价值就在于"零改动" + "标准 ACP",任何对 OpenClaw 的改动都让方案退回到 phase3 已经死掉的方案 A/E
3. **不要相信 LLM 的 prompt 遵守度做正确性保证**(phase2 §4.4)。wrapper 层兜底是必须的
4. **遇到 ACP / acpx / event bus 相关报错,第一反应是重读 acp-investigation.md §3.4**,不要试图"debug 看看是不是版本不一样了" —— 4 条独立 issue + 17 天 0 review 的 PR 已经证明上游不修
5. **不要把 wrapper 的输出改回自然语言** —— 哪怕大橘一开始不会 parse NDJSON,也是教大橘的问题,不是改 wrapper 的问题。结构化输出是本方案的核心收益,丢了就退回 phase2

---

## 8. 跟 phase3 §9 选项矩阵的对照

phase3 §9 列了 6 个方案 + G(等上游)。本方案是漏掉的第 7 个:

| 选项 | 技术可行 | 改 image | 改 config | 改 plugin | 协议标准 | 追问质量 | 流式颗粒度 |
|---|---|---|---|---|---|---|---|
| B (phase2) | ✅ | ✅ 零 | ✅ 零 | ✅ 零 | 无(纯文本) | LLM 字符识别 | 无流式 |
| **本方案(phase4)** | **❓ 待验证** | ✅ 零 | ✅ 零 | ✅ 零 | **wrapper↔child 段是标准 ACP** | LLM 启发式(但输入更干净) | **结构化事件流** |
| E (phase3) | ❌ bundling bug | ✅ 零 | ⚠️ 一次 | ❌ 必须 | 标准 ACP | 协议级 | 结构化 |

**本方案的定位**:在 "B 的硬约束(零改动)"基础上,把"输出层的结构化"补回来。**不是协议级追问的银弹,是 B 的进化版**。

---

## 9. 文档 ownership

本文是 song 跟 Claude(Sonnet 4.6 on Claude Code)在 2026-04-15 早上的对话产出。**song 要带去公司给那边的 agent 接手实施**,所以本文要 self-contained:

- 如果接手 agent 没读过 phase1/2/3,**至少要读 §0-§3**(一句话总结 + 跟方案 E 的区别 + 真实收益 + 3 个验证)
- 实施前**必须**读 acp-investigation.md §3.4(bundling bug 根因),否则可能再次走回 plugin 路径
- 实施前**必须**读 phase2-skill-approach.md §4.2(9 个坑),特别是坑 5(LLM 自作主张去掉 flag)和坑 8(hallucination)
- 实施前**必须**读 phase3-custom-acp-runtime-exploration.md §10(几个不能忘的事实)

接手 agent 如果对任何一段有疑问,**先重读对应的源文档**,不要靠猜测改设计。

---

**文档结束**。下一步是公司 agent 跑 §6 的 Phase A 三个验证,30-60 分钟内能给出 "可以继续 / 这条路也死了" 的明确结论。
