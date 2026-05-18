# 阶段三:不用 OpenClaw 官方 ACPX,自己做 Claude Code 桥接的探索记录

> 完成时间:2026-04-15
>
> 关联文档:
> - [`acp-investigation.md`](./acp-investigation.md) — OpenClaw 原生 ACP(acpx)的 bug 证据,本文不重复
> - [`design.md`](./design.md) — 最初的 Companion + foreman router 设计(已弃)
> - [`phase1-container-deployment.md`](./phase1-container-deployment.md) — Companion 容器部署完整过程(已弃)
> - [`phase2-skill-approach.md`](./phase2-skill-approach.md) — delegate-to-claude-code skill + bash + wrapper 本地跑通的过程
> - [`delegate-to-claude-code-skill/`](./delegate-to-claude-code-skill/) — 阶段二的产物
>
> **本文专门记录一件事**:在**不使用 OpenClaw 官方 acpx runtime** 的前提下,我们尝试让 OpenClaw 主 agent(大橘)跟 Claude Code 进行**标准协议级**通信的所有努力 —— 走过的弯路、拿到的源码证据、撞到的结构性约束、最终的结论。
>
> 目标读者:几周/几个月后的自己,或者接手这个项目的同事。
> 写法:按时间线记录讨论演进和踩坑,**不是**最终方案文档。

---

## 0. 本文的起点:为什么要找 acpx 的替代

[acp-investigation.md](./acp-investigation.md) 的结论是:**OpenClaw 内置的 `acpx` runtime(通过 `sessions_spawn runtime:"acp"` 触发的 ACP subagent spawn 路径)在当前版本下不可用**,原因是 `infra/agent-events` 模块的 cross-chunk singleton split bug(详见 §3.4.3),parent 永远收不到 child 的事件。这是一个 **bundling 层** 的结构性问题,不是 acpx 自身代码的 bug。

本次调研的动机是:**既然 acpx 路径死了,能不能我们自己做一个 "跟 acpx 差不多的东西" 绕开它**,让 OpenClaw 主 agent 用**标准 ACP 协议**跟 Claude Code(或任何 ACP-compatible agent)对话,而且**最好能兼容未来可能的其他 agent**(codex / gemini / ...)?

这个问题串起了一系列反复试错的讨论,本文完整记录。

---

## 1. 硬约束(生产环境)

用户在讨论中逐步明确了生产环境的约束集,按严格程度降序:

| 约束 | 明确程度 | 备注 |
|---|---|---|
| **1000+ 个 OpenClaw 实例** | 绝对 | 公司内广泛部署,每台都改代价极高 |
| **不能改 Dockerfile / 镜像层** | 绝对 | 改镜像 = 全量重部署,运维成本等同于换产品 |
| **不能改 `openclaw.json` 持续维护** | 强 | "反复改配置"不可接受,**"一次性装"可以商量** |
| **不能改 plugin / 加 plugin** | 偏强 | 跟 openclaw.json 约束关联,改 plugin 通常也要改 allowlist |
| **不能重启 gateway 来热加载配置** | 强 | 连带用户会话中断,1000 台一起中断很糟 |
| **skill 可以随便装** | ✅ | 每个 skill 是一个磁盘目录 + markdown + 资源文件,支持热加载 + 现成的分发能力 |
| **能分发一个 binary 到 /usr/local/bin** | ✅ | 附在 skill 的 install.sh 里装,被用户接受 |

最后一条核心 trade-off(用户原话澄清):
> 如果只为了支持 Claude Code 一个 agent,不能接受修改 OpenClaw 配置/plugin/重启。但如果能做出一个真正标准的、不 buggy 的 ACP 协议方案,能对接各种 ACP-compatible agent(Claude Code / Codex / Gemini / 未来的),**可以接受一次性改 OpenClaw 配置**。

这句话给了"放松约束"的空间,但前提是**方案必须真的通用、真的标准、真的不撞 acpx 同款 bug**。

---

## 2. 尝试过的方案总览

按讨论推进的时间顺序:

1. **方案 A(阶段一)**:`the-companion` + foreman backend router —— 已部署本地跑通,被 "Dockerfile 改不了" 约束 kill
2. **方案 B(阶段二)**:delegate-to-claude-code skill + 本地 `claude-as-node` wrapper,通过 `bash pty:true` + `-p --dangerously-skip-permissions` 单次调用 —— 本地跑通,但纯文本输出、追问需要字符匹配、多轮 continuity 需要靠 `--resume` 兜底
3. **方案 C**:`bash pty:true background:true` + `process action:submit/log` 长跑 claude TTY(interactive REPL 模式)—— 等价于 "进程级 continuity",但传输层仍是 bash tool,输出是混合 TTY 文本流不结构化
4. **方案 D**:远程 Claude Code Service + 自造 HTTP/SSE 协议 + OpenClaw 侧 thin client —— 被用户拒绝,理由是"不应该自造协议,应该 follow ACP 标准"
5. **方案 E**:自己写 `AcpRuntimeBackend` plugin,通过 OpenClaw 的 `registerAcpRuntimeBackend` 扩展点注册,绕开 acpx —— 读源码后发现**会撞 acpx 同款 bundling bug**,方案死
6. **方案 F(未探索)**:通过 `chat.inject` 或其他 gateway RPC 路径,主 agent 跨 session 注入消息 —— 本次没验证,留作未来调研

本文从 §3 开始逐个详细记录。

---

## 3. 方案 A:Companion + foreman backend router

### 3.1 动机

最初的 design.md §5 设计:

- 在 OpenClaw 容器里**额外**跑 `the-companion`(第三方开源 Claude Code WebSocket wrapper)
- 暴露端口 3456
- foreman(企微后端)作为 channel plugin 直连 OpenClaw gateway,识别用户的 `/claude` 命令
- 收到 `/claude` → 绕过 OpenClaw → 直接调 Companion HTTP/WS API → 跟 Claude Code 对话
- 用户发 `/openclaw` 切回来

### 3.2 实际跑通(阶段一)

在本地容器跑通了整条链路(见 [`phase1-container-deployment.md`](./phase1-container-deployment.md)),包括:

- 装 `the-companion@0.95.0` + `@anthropic-ai/claude-code@2.1.107`
- 打新镜像 `openclaw-with-cc:2026.04.15`
- 改 docker-compose 加端口 + bind mount + 改 HOME=/root
- smoke test 完整走过一次 "用户发消息 → Companion → claude CLI → 流式回包 → history"

### 3.3 为什么被 kill

用户明确说:**生产环境 Dockerfile 改不了**。打新镜像、改 compose、加端口、加 bind mount 这些全是 "改镜像" 级别的改动,运维成本不可接受。

方案 A 只能作为"本地验证"用,**生产不可用**。

### 3.4 留下的价值

- 深入理解了 Claude Code CLI 的 4 种 IO 模式(TTY / `-p` / `-p --input-format stream-json` / `--sdk-url`)
- 理解了 Companion 的工作原理(`--sdk-url` ws client + 长跑进程 + WS 协议)
- 理解了 `@anthropic-ai/claude-agent-sdk` 是 Claude CLI 底层的 Node SDK,可以被任何 Node 应用 embed
- 这些知识是后面方案 B~E 讨论的底子

---

## 4. 方案 B:SKILL.md + 本地 `claude-as-node` wrapper + `-p` 单次调用

### 4.1 动机

既然装 the-companion 都太重,退一步:**只装 `claude` CLI + 一个薄 wrapper**,通过 OpenClaw 的 **skill 机制**(磁盘文件分发,热加载,不需要改配置)教大橘什么时候调用 claude。

三件套最终形态:

```
/root/.openclaw/skills/delegate-to-claude-code/
├── SKILL.md                # 强触发词 description + 调用方式
├── install.sh              # 一次性装 claude CLI + settings.json + symlink
└── bin/
    └── claude-as-node      # wrapper 脚本, su node 绕 root 守卫
```

这就是 [`phase2-skill-approach.md`](./phase2-skill-approach.md) 的产物。

### 4.2 踩坑录(顺序)

**坑 1:skill frontmatter 里 description 的特殊字符导致 YAML parse 失败**
- 裸 description 含逗号和双引号 → YAML parser 炸 → `openclaw skills list` 显示 `✗ missing`
- 修复:description 用单引号包起来

**坑 2:skill description 不够强,大橘不自动选用**
- 第一版 description 是"把复杂编码任务委托给 Claude Code CLI"这种软介绍,大橘看到后不认为要用
- 修复:改成 `【MUST USE】当用户消息出现"Claude Code"/"claude code"/"cc"/"让 Claude 帮我写"等关键词...时,必须使用本 skill,不要自己写代码`

**坑 3:Claude CLI 硬编码拒绝 root 用户使用 `--dangerously-skip-permissions`**
- 容器跑 `user: "0:0"`(root),大橘通过 bash 调 `claude -p --dangerously-skip-permissions` 直接报:
  ```
  --dangerously-skip-permissions cannot be used with root/sudo privileges for security reasons
  ```
- 修复:写一个 wrapper `claude-as-node` 内部做 `su node -s /bin/bash -c "claude ..."` 切到 node 用户(UID 1000,容器内系统自带)
- 验证:node 跑成功,文件真生成,owner `node:node`

**坑 4:workdir 由 root 创建,node 写不进**
- 大橘 `mkdir -p /tmp/snake` 时是 root 身份,目录 owner `root:root`,node 写不进
- 修复:wrapper 启动时 `chown -R node:node "$CWD"` 把 workdir 给 node

**坑 5:大橘反复"自作主张"去掉 `--dangerously-skip-permissions`**
- 大橘第一次调用时正确带了 flag,看到 claude 的一些 warning 消息就**主动去掉 flag 重试**,然后 claude 所有 Write/Bash 都被拒,claude 自己 hallucinate 一句"完成了",大橘照样汇报成功 —— 但文件实际不存在
- 修复:wrapper 层面**强制 prefix** `--dangerously-skip-permissions`,不管调用方传不传都兜底:
  ```bash
  HAS_BYPASS=0
  for a in "$@"; do
    case "$a" in
      --dangerously-skip-permissions|--permission-mode) HAS_BYPASS=1 ;;
    esac
  done
  if [ "$HAS_BYPASS" = "0" ]; then
    set -- --dangerously-skip-permissions "$@"
  fi
  ```

**坑 6:大橘还会"自作主张"不传 `--resume`,每次新 claude session 丢 context**
- 追问场景下,大橘本该带上一次 claude 的 session_id `--resume` 接续,但它懒得从输出 parse session_id 就直接重写 prompt
- 修复:wrapper 层 auto-resume —— 查 `/home/node/.claude/projects/-<cwd-hash>/` 下最新 jsonl 文件,自动插入 `--resume <session_id>`
- **后来被识别有问题**:auto-resume 不能区分"同任务追问"和"新任务复用同 workdir",场景 2(大橘在同一个 workdir 做不同任务)会错误 resume 上一个任务的 context,造成精神分裂 —— 所以 auto-resume 是个双刃剑

**坑 7:`openclaw agent --session-id <new-uuid>` fresh 测试时,大橘仍然"记得"用 ask-claude**
- 这让我一度以为有 conversation state 持久化,后来发现不是 —— fresh session id 下大橘确实冷启动,但 `/usr/local/bin/ask-claude` 这个 binary 还在 PATH 里,大橘 grep PATH 看到它就会用
- 清理 /usr/local/bin/ask-claude 之后 fresh session 就真的不认得了

**坑 8:hallucination**
- 即使底层真的失败(write permission、目录不存在、API 403),claude 的 final text 仍然可能写"✅ 完成了,game 已经生成"
- 大橘不做二次验证,照样汇报给用户
- 只有 ls 查文件才能发现撒谎
- **解法只能是让底层真能跑通**,hallucination 根源在 claude model 本身

### 4.3 方案 B 的最终状态

**本地**:端到端跑通,贪吃蛇/CSV 导出/多轮追问等场景都能真的工作。phase2-skill-approach.md 有完整证据。

**生产**:可以走的,这是目前在 "不改 OpenClaw image" 约束下**唯一已验证跑通**的路径。但它的形态是:

- 大橘用 `bash pty:true command:"claude-as-node ..."`
- stdout 是**人类可读文本**(claude 的自然语言输出)
- 追问靠大橘 LLM 从文本语义识别(非结构化)
- 多轮靠 wrapper auto-resume(有副作用,见坑 6)

用户对这个形态的满意度:**勉强接受**,但反复表达 "不够优雅、不是标准协议、依赖大橘的 prompt 遵守度"。这推动了后面方案 C/D/E 的探索。

### 4.4 关键教训

**写 skill 教大橘如何调用 tool,不如在 wrapper 层兜底**。同样的问题出现了 3 次(bypass flag、resume、chown)每次都是"skill 里写规则 → 大橘不守 → wrapper 兜底"。

**"依赖 LLM agent 遵守 skill 精细指令"本质上不可靠**,LLM 的行为是 best-effort,关键正确性必须在 deterministic 代码层(wrapper / gateway / middleware)兜底。

---

## 5. 方案 C:`bash background:true` + `process.submit/log` 长跑 claude TTY

### 5.1 动机

方案 B 的 `-p` 单次模式每次 claude 都是 fresh 进程,context 靠 `--resume` 从 jsonl 重建。**更优雅**的做法是让 claude 以**交互式 TTY 模式**长跑,大橘通过 OpenClaw 的 `process.submit` 往 stdin 喂消息,通过 `process.log` 拉 stdout。这样:

- 一个 claude 进程对应一个 session,内存里 context 永远在线
- 追问时不用 resume,直接往同一个 TTY 塞用户的新回复
- 类似 IDE 里用 Claude Code REPL 的体验

### 5.2 对应到 OpenClaw 能力

这恰好是 OpenClaw bundled 的 `coding-agent` skill(在 `/app/skills/coding-agent/SKILL.md`)教的 pattern:

```
bash pty:true workdir:~/project background:true command:"claude 'task'"
# 返回 bash session id
process action:log sessionId:$SID
process action:submit sessionId:$SID data:"追加的消息"
process action:kill sessionId:$SID
```

### 5.3 用户的质疑(这是方向转折点)

用户看到这个方案,抛出一个根本问题:

> "我没太看明白,你的意思是 auto-resume 对吗?那我有问题来了,它怎么知道这次任务属于是一个新的 case 呢?"

然后继续:

> "我怎么感觉这个路子还是不对呀?怎么还是,怎么到最后还是那个 bash 呢?我就是不想让他用 bash 呢。你知道 OpenClaw 自己内置的 ACP 是什么方式实现的吗?它肯定不是 bash 实现的吧?我觉得 bash 这个路子不对呀。这个 bash 它就不是一个标准的 ACP,我就是想让 OpenClaw 使用标准的 ACP 协议,为什么要让它用 bash 呢?"

**核心诉求清晰了**:

1. 不要走 bash tool(传输层不结构化)
2. 要走**标准 ACP 协议**(结构化 events,parent 能精确知道 child 在哪个阶段)
3. 问 "OpenClaw 内置 ACP 是怎么实现的?我们能不能做一个差不多的,但不用官方 acpx?"

至此方案 C 被搁置(它跟 B 都是 bash 路径),讨论转向方案 D/E(标准协议路径)。

---

## 6. 方案 D:远程 Claude Code Service + 自造 HTTP/SSE 协议 + thin client

### 6.1 想法

- 重型的 Claude Code 运行环境放到**独立的远程机器**上(可以是 Companion / 自建 FastAPI wrapper / 任何实现)
- OpenClaw 容器里装一个**薄 binary**(thin client),~几 MB
- thin client 通过 HTTPS/WebSocket 连远程服务,把远程的流式响应转成**结构化 JSONL** 写 stdout
- OpenClaw 主 agent 仍然通过 `bash background:true command:"thin-client"` + `process.submit/log` 调用 thin client
- 协议层是**我们自己定义的** HTTP POST + SSE 接口

### 6.2 优点

- Claude CLI 和所有复杂依赖都在远程,OpenClaw 容器里零装
- 远程服务可以任意升级、池化、scale
- 权限问题在远程解决(远程机器设非 root)
- 1000 台 OpenClaw 共享一个远程服务池

### 6.3 用户的第二次反对(又一个转折)

> "我怎么感觉这个 B 也不行呢?因为,我感觉你好像没懂我的意思。不是本地和远程,重点还不是这个本地还是远程,重点是说,重点是说它这个是不是一个标准的协议。"
>
> "这个 ClaudeCode ACP,它明显就更好呀,它的输出是结构化的。这个 OpenClaw 就是这个主 Agent 的,它能很明确的判断这个子 Agent 的,它的输出到了哪个阶段,是不是可以,以及那个追问,它都可以,就是我觉得 ACP 它是一个未来,它是一个标准方案,我们应该 follow 这个原则,而不是自己另造一套。"
>
> "我们能不能另造一套远程的 ACP 呢?另外搞一套 ACP。这次可能不是,这次我是这个 OpenClaw 要把这个任务交给一个 ClaudeCode,下次我有可能要把任务交给这个 codex 或者别的。而且这些它 agent 呢不一定都跑在本地的,有可能这些 agent 呢在远程的。"

**明确 rejection**:

- 不要自造协议,用标准 ACP
- 要泛化(不只是 claude code,还要能支持 codex / 其他未来的 ACP agent)
- 远程 / 本地都要能支持

这推动到方案 E。

### 6.4 方案 D 没执行

**没写代码,纯讨论阶段**。留下的价值是 "走向标准协议" 这个方向确认了。

---

## 7. 方案 E:自己写 AcpRuntimeBackend plugin,通过 `registerAcpRuntimeBackend` 注册

### 7.1 源码调研

在用户的推动下,读 OpenClaw 源码找"怎么能不用 acpx 但走 ACP 协议"的扩展点。关键发现:

**7.1.1 `sessions_spawn` 是 OpenClaw core 内置 tool**(`src/agents/tools/sessions-spawn-tool.ts:19`):

```typescript
const SESSIONS_SPAWN_RUNTIMES = ["subagent", "acp"] as const;
```

大橘可以调 `sessions_spawn runtime:"acp" task:"..."`,这是跟 `bash` 并列的独立 tool,**不走 bash**。

**7.1.2 ACP runtime backend 是一个注册表**(`src/acp/runtime/registry.ts`):

```typescript
export type AcpRuntimeBackend = {
  id: string;
  runtime: AcpRuntime;
  healthy?: () => boolean;
};

export function registerAcpRuntimeBackend(backend: AcpRuntimeBackend): void { ... }
export function getAcpRuntimeBackend(id?: string): AcpRuntimeBackend | null { ... }

// 关键 error message:
throw new AcpRuntimeError(
  "ACP_BACKEND_MISSING",
  "ACP runtime backend is not configured. Install and enable the acpx runtime plugin.",
);
```

**acpx 只是默认 backend 的一个实现**。注册表是开放的,任何东西可以注册自己的 backend。

**7.1.3 注册 API 通过 plugin-sdk 公开**(`src/plugin-sdk/acp-runtime.ts:11`):

```typescript
export {
  getAcpRuntimeBackend,
  registerAcpRuntimeBackend,
  requireAcpRuntimeBackend,
  unregisterAcpRuntimeBackend,
} from "../acp/runtime/registry.js";
```

第三方 plugin 可以 import 这个,写一段:

```typescript
registerAcpRuntimeBackend({
  id: "our-custom-acp",
  runtime: myCustomAcpRuntime,
});
// 可选:卸载 acpx 让我们的成为唯一
unregisterAcpRuntimeBackend("acpx");
```

**7.1.4 `AcpRuntime` 接口是结构化的 async iterator**(`src/acp/runtime/types.ts:118-138`):

```typescript
export interface AcpRuntime {
  ensureSession(input: AcpRuntimeEnsureInput): Promise<AcpRuntimeHandle>;
  runTurn(input: AcpRuntimeTurnInput): AsyncIterable<AcpRuntimeEvent>;
  cancel(input: { handle: AcpRuntimeHandle; reason?: string }): Promise<void>;
  close(input: { handle: AcpRuntimeHandle; reason: string }): Promise<void>;
  // optional: getCapabilities / getStatus / setMode / setConfigOption / doctor
}
```

事件类型(`types.ts:85-116`):

```typescript
export type AcpRuntimeEvent =
  | { type: "text_delta"; text: string; stream?: "output" | "thought"; tag?: AcpSessionUpdateTag; }
  | { type: "status"; text: string; tag?: AcpSessionUpdateTag; used?: number; size?: number; }
  | { type: "tool_call"; text: string; tag?: AcpSessionUpdateTag; toolCallId?: string; status?: string; title?: string; }
  | { type: "done"; stopReason?: string; }
  | { type: "error"; message: string; code?: string; retryable?: boolean; };
```

这是**标准 ACP session update 的 TypeScript 映射**。我们的 backend 只要 yield 正确的 events,parent 就能精确知道 child 在什么阶段。

### 7.2 方案 E 的架构设计

基于以上发现,方案 E 的形态:

```
大橘(OpenClaw 主 agent)
  ↓ 调 sessions_spawn(runtime:"acp", task:"...")
  ↓
OpenClaw core sessions-spawn-tool.ts
  ↓ getAcpRuntimeBackend()
  ↓
我们自己注册的 backend ("remote-acp-bridge")
  ↓ runTurn() yield AsyncIterable<AcpRuntimeEvent>
  ↓ 内部做 WebSocket 连接到远程 ACP server
  ↓
远程 WS-stdio bridge (跑在独立机器,~100 行 Node/Python)
  ↕ stdio JSON-RPC (标准 ACP 传输层)
  ↓
claude-agent-acp (或 codex-acp 或任何 ACP-compatible agent)
```

优点:

- 大橘调 `sessions_spawn` 是 OpenClaw **built-in tool**,不是 bash
- events 通过**结构化 `AcpRuntimeEvent` async iterator** 返回,不是字符流
- 追问可以是协议级的 `permission/request` 或 `text_delta` 里的问句,大橘有明确 event 类型可判断
- 远程可以是任何 ACP 兼容 agent,这次 claude-code,下次 codex,skill 内容不变
- plugin 只注册一个 backend 一次,之后不用维护

用户看到这个设计,**同意**"一次性装 plugin"在可接受范围内,前提是**方案必须真能跑,不能撞 acpx 同款 bug**。

### 7.3 致命问题:同款 bundling bug

动手写 backend 之前,去读 `acp-investigation.md §3.4.3` 重新确认 acpx bug 的具体位置。这一步救了几天的工作。

原文:

> `infra/agent-events` 模块是一个 singleton,负责 `emitAgentEvent(event)` 和 `onAgentEvent(subscriber)` 两个方法。但是由于 OpenClaw 的 bundling 配置问题,这个模块在运行时被重复加载:
>
> - **实例 A**:存在于 gateway core chunk 里
> - **实例 B**:存在于 subsystem(包含 acpx)chunk 里
>
> `spawnAcpDirect` 跑在 subsystem chunk 里,调 `emitAgentEvent(...)` 的是实例 B。
> `parent-stream-relay` 跑在 gateway core chunk 里,调 `onAgentEvent(...)` 订阅的是实例 A。
>
> 两个实例是不同的 JavaScript 对象,`emit` 到 B 的事件,A 永远收不到。

@sumurtk2 在 issue #46795 的原话:

> "emitAgentEvent() and onAgentEvent() are operating on different module instances of infra/agent-events — gateway core vs subsystem chunk boundary."

**bug 不在 acpx 的代码,在 OpenClaw 的 bundling 配置**。`infra/agent-events` 模块被 bundler 复制成两个独立的 chunk 实例。

### 7.4 我们的自定义 backend 必然撞同款 bug

关键推论:

1. **我们的 backend plugin 装在 `/app/extensions/` 或 `/root/.openclaw/extensions/`**,被 gateway 启动时 load。
2. OpenClaw 的 bundle 配置把 extensions 下的 plugin **归入 subsystem chunk** 加载(跟 acpx 同一个 chunk)。
3. 我们的 backend 在 `runTurn` 里 yield events,底层**必须**通过 `emitAgentEvent` 才能让 parent relay 看到(因为 `spawnAcpDirect` → `callGateway("agent", ...)` 重新入队一个 child agent turn,child 在独立 session 里跑,events 必须通过全局 event bus 转发到 parent 的 relay)。
4. **我们的 backend 调 `emitAgentEvent` 用的是 subsystem chunk 里的 module 实例(实例 B)**,parent relay 订阅的还是 gateway core chunk 的实例 A。
5. **事件仍然从 B 发到 A,一个都收不到**。

**写自己的 backend 根本不能解决这个 bug**。bug 在 bundling 层,跟 backend 是谁、实现多干净、协议多标准无关。

### 7.5 验证源码证据(当时读的)

**`src/infra/agent-events.ts:54-62`**(singleton 实现看起来是对的):

```typescript
const AGENT_EVENT_STATE_KEY = Symbol.for("openclaw.agentEvents.state");

function getAgentEventState(): AgentEventState {
  return resolveGlobalSingleton<AgentEventState>(AGENT_EVENT_STATE_KEY, () => ({
    seqByRun: new Map(),
    listeners: new Set(),
    runContextById: new Map(),
  }));
}
```

这段看起来是正确的 `Symbol.for` + `globalThis` singleton,**理论上应该抗 chunk split**。但 `acp-investigation.md §3.4.3` 的现场调试证据和上游 4 条独立 issue(#44720 / #46795 / #51345 / #65308)**证明实际运行时两个 chunk 看到的不是同一个 module instance**。

可能原因推测(没完整验证):

- `resolveGlobalSingleton` 内部通过 `globalThis[Symbol.for(...)]` 查 state,但不同 chunk 里各自持有 `resolveGlobalSingleton` 函数的不同实例,即使最终读 `globalThis[key]` 是同一个引用,但**上层 closure 里 cache 的 reference 分叉了**
- 或者 `notifyListeners` / `registerListener` 也是 subsystem chunk 复制过的,listeners `Set` 跟 state 是两个独立 cache
- 具体哪一层 split 需要 production debug(本次没做)

**总之**:无论具体机制是什么,**事实**是 `emitAgentEvent(from subsystem) → onAgentEvent(in core)` 的链路在当前 OpenClaw 版本下是断的。

**PR #56442** 是唯一一个针对这个问题的修复尝试,但它不 fix 根因,只是加了一个 opt-in 的 `parentUpdates: "notify"` 参数走另一条 completion delivery path,而且 17 天 0 human review,没合并,社区冷冻。

### 7.6 方案 E 死亡宣告

**写自定义 `AcpRuntimeBackend` plugin 这条路是死的**,只要:

- 我们的 backend 装在 extensions 目录作为 plugin
- 它运行在 subsystem chunk
- 它需要通过 event bus 传递 events 给 parent
- bundling bug 没被修

三个条件都满足,**无法绕过**。

### 7.7 唯一的理论活路(但都超出约束)

1. **把 backend 做成 non-plugin 注入**:在 OpenClaw 启动前 monkey-patch agent-events 模块让 subsystem 和 core 共享。需要改 OpenClaw 启动脚本 —— 改 image / compose,超出约束
2. **修 OpenClaw 的 bundling 配置**:让 `infra/agent-events` 不跨 chunk split。需要改 OpenClaw 源码重编译 —— 改 image,超出约束
3. **patch `infra/agent-events.ts` 代码,强制 state 挂 `globalThis`**:同上,改源码
4. **绕开 event bus,让 backend 直接 write 到 parent session 的 transcript**:这是一个没验证过的方向,可能是方案 F 的雏形

---

## 8. 方案 F(未探索):`chat.inject` 跨 session 注入

### 8.1 灵感

方案 E 死因是 "event bus 跨 chunk split"。那**能不能绕开 event bus,走另一个 OpenClaw 机制把消息从 subsystem chunk 送到 parent 的用户视野?**

看 OpenClaw RPC 列表时注意到一个 `chat.inject` method(`src/gateway/server-methods/chat.ts:1915`):

```typescript
"chat.inject": async ({ params, respond, context }) => { ... }
```

它不是给主 agent 调的,是给外部 client(比如 operator UI)往 session transcript 注入消息的。

**假设性路径**:

- 我们的 backend(作为 plugin 跑在 subsystem chunk)
- child session 跑完后,backend 通过 **`callGateway("chat.inject", ...)`** 把 child 的 final text **注入 parent session 的 transcript**
- `chat.inject` 写 transcript 走 **session.message WebSocket event**(前面调研过,foreman 订阅这个就能看到)
- 这条路径**不依赖 `infra/agent-events` event bus**
- 如果 `chat.inject` 能从 plugin 里调用,且 session.message 的广播机制不撞 bundling bug,**可能就是一条活路**

### 8.2 为什么没探索

讨论到这里,用户说 "把所有调研结论写一份文档"(本文)。这个方向还没动手验证。具体未验证的点:

1. **`chat.inject` 能不能从 plugin 侧 `callGateway` 调用**?还是只能外部 WS client 调?
2. **`chat.inject` 注入的 message 会不会触发 session.message broadcast**?走的是不是同款机制?
3. **`chat.inject` 的 session.message 路径有没有撞另一个 bundling bug**?
4. **如果能,怎么让 "child 的流式 text_delta" 映射成"分段的 chat.inject 调用"?每 N 字符注入一次?还是只注入 final text?
5. **追问机制怎么做?**child 如果问问题,inject 到 parent 的 transcript,parent LLM 读到了,怎么 relay 给 foreman / 用户?

以上 5 点需要源码级调研 + 实验,**至少 2 小时 source code reading + 可能的 prototype**。

### 8.3 建议的下一步

本次 session 不继续做这个方向,本文只是记录它作为"没死透"的剩余方向。

**下次如果要重启这个问题**:

1. 读 `src/gateway/server-methods/chat.ts:1915` 附近的 `chat.inject` 完整实现
2. 看它往哪里写 transcript、怎么广播 session.message、权限怎么 check
3. 看 `callGateway` 能不能从 subsystem chunk 调 gateway method
4. 如果以上都 OK,写一个最小 plugin:
   - 注册一个 `AcpRuntimeBackend` 叫 "remote-inject-acp"
   - `runTurn` 内部 spawn 一个远程 ACP client 连 remote claude-agent-acp
   - 收到 child 的 `sessions/update`,立即通过 `callGateway("chat.inject", {sessionKey: parentKey, message: ...})` 注入 parent 的 transcript
   - parent 的 session.message broadcast 把 events 推给 foreman(绕开 event bus)

---

## 9. 现实选项矩阵(当前状态)

按"技术可行 × 约束冲突"排:

| 选项 | 技术可行 | 改 image | 改 config | 改 plugin | 协议标准 | 追问质量 | 流式颗粒度 |
|---|---|---|---|---|---|---|---|
| A. Companion + foreman router | ✅ 跑通 | ❌ 必须 | ❌ 必须 | — | 自造 | 协议级 | 字符级 |
| B. skill + claude-as-node wrapper (`-p`) | ✅ 跑通 | ✅ 零 | ✅ 零 | ✅ 零 | 无(纯文本) | LLM 识别,不稳 | 无流式(只有 tool_result) |
| C. bash background + process.submit (TTY REPL) | 理论可行 | ✅ 零 | ✅ 零 | ✅ 零 | 无(混合 TTY) | LLM 字符匹配 | 字符级(但混 ANSI) |
| D. 远程服务 + 自造 HTTP/SSE + thin client | 理论可行 | ✅ 零 | ✅ 零 | ✅ 零 | **自造**(用户否决) | 协议级 | 字符级 |
| E. 自定义 `AcpRuntimeBackend` plugin | ❌ **撞 bundling bug** | ✅ 零 | ⚠️ 一次 | ❌ 必须 | 标准 ACP | 协议级 | 结构化事件 |
| F. `chat.inject` 跨 session | ❓ 未验证 | ✅ 零 | ⚠️ 看具体 | ⚠️ 看具体 | 标准 ACP(如果 F 路径上的 child 是 acp agent) | 取决于实现 | message 级 |
| G. 等上游修 #46795 | ❓ | — | — | — | 标准 ACP | 协议级 | 结构化事件 |

**当前唯一已跑通的生产路径是 B**(方案 B 的 delegate-to-claude-code skill + claude-as-node wrapper,产物在 [`delegate-to-claude-code-skill/`](./delegate-to-claude-code-skill/))。

**理论最好但因 bundling bug 死亡的是 E**。

**值得未来调研的是 F**。

---

## 10. 几个需要记住的事实(避免下次再忘)

### 10.1 ACPX 的 bug 不是 acpx 代码问题

下次再看到 "acpx 有 bug" 这句话时:**bug 在 OpenClaw 的 bundling 配置,把 `infra/agent-events` 模块跨 chunk 复制**。acpx 作为上层 plugin 调 `emitAgentEvent` 是正常用法,它只是 bundling split 的**受害者**。换任何一个 ACP runtime backend 都会撞同样问题(包括我们自己写的)。

### 10.2 `registerAcpRuntimeBackend` 是真扩展点但不是活路

下次读 plugin-sdk 源码看到 `registerAcpRuntimeBackend` 时,**不要再兴奋地说"这就是我们要的扩展点"**。扩展点是真的,但**任何通过这个扩展点注册的 backend 都跑在 subsystem chunk,撞 bundling bug**。

### 10.3 "不改 OpenClaw" 约束和 "标准 ACP" 在当前版本下不兼容

这两个诉求**在当前 OpenClaw 版本下是互斥的**。要么接受改 image / 改 bundling(修 bug),要么接受 "非标准" 的传输层(比如 bash + JSONL)。

下次有人提议 "我们在不改 OpenClaw 的前提下跑标准 ACP",**先回到这个矛盾**,再决定要不要投入调研。

### 10.4 LLM agent 的 prompt 遵守度不能作为正确性基础

方案 B 的 wrapper 层总共兜底了 4 个"大橘应该做但做不到"的行为:

1. 强制插入 `--dangerously-skip-permissions`(大橘会自作主张去掉)
2. 自动 `chown workdir` 给 node(大橘不会记得做)
3. 强制切 node 用户(大橘不知道 root 守卫)
4. (曾经的)auto-resume(大橘不会从 jsonl 里 parse session_id)

所有这些都可以 skill 里写"必须这样做",**但大橘反复不守**。**结论是:skill 里的规则只是 guideline,正确性保证必须在 deterministic 代码层**。

### 10.5 source code > GitHub issue > 我的记忆

本次探索里,我多次根据"我的记忆"或"GitHub issue 描述"做判断,然后被打脸:

- **案例 1**:说"acpx event bus 可能已经修了"(因为看当前源码 `agent-events.ts` 用了 `resolveGlobalSingleton`)—— 实际上 acp-investigation.md §3.4.3 **我自己半个月前写的**已经明确证据了 bundling split,并验证过
- **案例 2**:说"验证 acpx 内部路径 work"(想用 acpx 做实验)—— 用户提醒:"我们之前不是验证过这个 ACP 方案行不通吗"
- **案例 3**:说"自定义 backend 能绕 bug"—— 读完 acp-investigation.md 才发现同 chunk 同 bug,方案死

**教训**:下次讨论到 acpx / ACP runtime bug 时,**第一件事是重读 acp-investigation.md**,不是依赖记忆。

---

## 11. 本次探索的产物清单

### 文档
- 本文(`phase3-custom-acp-runtime-exploration.md`)
- [`phase2-skill-approach.md`](./phase2-skill-approach.md) — 方案 B 的完整记录
- [`phase1-container-deployment.md`](./phase1-container-deployment.md) — 方案 A 的完整记录
- [`acp-investigation.md`](./acp-investigation.md) — 本次反复参考的 acpx bug 原始调研

### 代码(只有方案 B 有产出,其他都是纸上讨论)
- [`delegate-to-claude-code-skill/SKILL.md`](./delegate-to-claude-code-skill/SKILL.md)
- [`delegate-to-claude-code-skill/install.sh`](./delegate-to-claude-code-skill/install.sh)
- [`delegate-to-claude-code-skill/bin/claude-as-node`](./delegate-to-claude-code-skill/bin/claude-as-node)

### 没有产出的方向
- 方案 C (bash background + process) — 没写 skill,没跑实验
- 方案 D (远程服务 + 自造协议) — 没写服务,没写 thin client,用户否决
- 方案 E (自定义 AcpRuntimeBackend plugin) — 没写 plugin,读源码后判定方案死
- 方案 F (chat.inject 跨 session) — 没调研,只有 hypothesis

---

## 12. 给未来自己的建议

1. **如果下次要做 ACP 路径**,**先问一遍**:"OpenClaw bundling bug 修了吗?" 查上游 issue #44720 / #46795 / #51345 / #65308,查 PR #56442 是否合并,查最新 OpenClaw release notes。修了才值得走 ACP 路径;没修,别再绕这个圈。

2. **如果下次要做 "不改 OpenClaw + 结构化协议" 的方案**,唯一没探索的是方案 F(`chat.inject` 跨 session 注入)。花 1-2 小时读 chat.inject 源码判断可行性,可行就写 prototype,不行就老实接受方案 B(bash + JSONL)的形态。

3. **方案 B 是目前的 fallback**,已跑通,有产物,至少是一个 "能用" 的基线。文档里提到的所有 skill + wrapper 的坑(root 守卫、bypass flag 被去掉、auto-resume 副作用)都有记录,要做改进时参考 phase2 文档。

4. **不要再混淆 "自造协议" 和 "标准 ACP"**。用户明确反对自造协议(即使协议设计是好的),他要 "跟未来任意 ACP agent 兼容" 这一点不能妥协。方案 D 永远回不来。

5. **`bash` 不是耻辱**。方案 B 走 bash + NDJSON 载荷,从 LLM 大橘的视角看 parse structured NDJSON 是完全可行的,跟 ACP session update 的 `{sessionUpdate: "agent_message_chunk", ...}` **本质上同构**,只是传输层不同。下次讨论时不要再被 "bash = 低级" 的直觉误导 —— 在 OpenClaw 的硬约束下,bash 是**唯一保证不被 bundling bug 影响**的传输层。

6. **对话体验 ≠ ACP 协议**。用户的真实诉求是"结构化 events + 追问 + 多轮 continuity",这些完全可以在 bash + NDJSON 上实现,**不必非要 ACP 协议原语**。协议只是载荷格式,真实价值在 event 的结构化,不是 transport 是什么。

---

## 附录:本次讨论中用户的关键约束语录(原话)

**关于"不能改 OpenClaw"**:

> "OpenClaw 现在实例特别多,我们公司里面有 1000 多个,你想在上面安装什么东西都很难,都很麻烦。你要是简单的加装一个 binary 还可以,但是你要是装一个软件,或者是修改 OpenClaw 里面的配置都很复杂的。"

> "这个 OpenClaw 它是可以热加载 skill 的。但是你如果去改它的 plugin,要给它加一个 plugin,或者说你要去修改它的 OpenClaw.json 这个配置文件,让它重新加载,这个就很困难。"

**关于"不要用 bash"**:

> "我怎么感觉这个路子还是不对呀?怎么还是,怎么到最后还是那个 bash 呢?我就是不想让他用 bash 呢。这个 bash 它就不是一个标准的 ACP,我就是想让 OpenClaw 使用标准的 ACP 协议,为什么要让它用 bash 呢?"

**关于"要标准 ACP 不要自造"**:

> "我就是想让 OpenClaw 使用标准的 ACP 协议,为什么要让它用 bash 呢?因为现在不是 OpenClaw 自身的 ACP,它自己写的 ACP 不好使吗?所以我们能不能另造一套远程的 ACP 呢?另外搞一套 ACP。这次可能不是,这次我是这个 OpenClaw 要把这个任务交给一个 ClaudeCode 下次我有可能要把任务交给这个 codex 或者别的。"

**关于"通用才值得改 plugin"**:

> "如果说我们这次支持就只支持 ClaudeCode 这么一种非常特殊、非常具体的 Agent 它的这一个,就是说 Agent 的分发,或者说对这个委派功能。如果说我们这次就只支持一个 ClaudeCode,那我就是完全不能接受,需要修改 OpenCloud 自己的机制了。但是如果这次我们能够研究出来一种标准的 ACP 协议,它可以对接各种各样的兼容 ACP 协议的 Agent,那我觉得如果是可以做到这种,并且这个 ACP 协议需要能工作啊,不能有 bug,那我可以接受去修改 OpenCloud 的配置。"

这四段话是整个讨论的约束核心,未来任何方案都要先对照这四段检查是否冲突。
