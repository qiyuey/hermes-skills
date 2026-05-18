# 阶段二:OpenClaw Skill 方案(把复杂编码任务委托给 Claude Code CLI)

> 完成时间:2026-04-15
> 关联:
> - [design.md](./design.md) — 原 Companion + foreman router 方案(已废弃)
> - [phase1-container-deployment.md](./phase1-container-deployment.md) — Companion 容器部署过程(已废弃)
> - [acp-investigation.md](./acp-investigation.md) — 为什么不走 OpenClaw 原生 ACP
> - [delegate-to-claude-code-skill/](./delegate-to-claude-code-skill/) — 本次最终产物
>
> **本文档记录完整探索过程 + 踩坑 + 最终方案**。给未来的自己复盘用,不是简洁 SOP。

---

## 0. 背景与目标

把复杂编码任务(长代码生成、多文件改动、按严格步骤执行、探索第三方 API/SDK、多轮迭代)从 OpenClaw 主 agent(Bedrock Claude)转交给 **Claude Code CLI**(Anthropic 官方的独立编码 agent)执行。

**硬约束**:
1. **不能改 OpenClaw 容器镜像 / Dockerfile / compose**(生产 1000+ 实例,改镜像等于全量重部署,运维成本极高)
2. **不能改 openclaw.json / plugin allowlist**(需要重启 gateway + 破坏 HA)
3. **能分发的只有磁盘文件**(用户有现成的批量分发文件的能力)
4. 必须支持**多轮追问优化**("再改一下"、"再加个 xxx"),不是一次性任务
5. 希望用户在企微里**实时看到 Claude Code 跑的过程**(流式 UX)
6. 希望 Claude Code 能使用 OpenClaw 已装的其他 skill(比如 yuque-cli、opencli-for-openclaw)

---

## 1. 方案演进时间线

本节按时间顺序记录所有走过的弯路和发现,是**最有价值的部分**,帮下次决策。

### 1.1 起点:Companion + foreman backend router(被否决)

最早的方案(design.md §5)是在 OpenClaw 容器里部署 `the-companion`(Claude Code 的独立 WebSocket wrapper),foreman 侧用 9 个 Java 类做 backend router,让用户通过 `/claude` 命令把企微会话后端从 OpenClaw 切到 Claude Code。

**阶段一实际跑通了**(见 phase1-container-deployment.md),本地 smoke test 全绿。

**被否决的原因**(2026-04-15 中段):
- 用户的约束 "Dockerfile 不能改"完全改变了可行性 —— Companion 要打进镜像,3456 端口要暴露,compose volume 要加 —— 全都超出"纯文件分发"能力
- foreman 侧 9 个 Java 类的工作量也很大
- 用户希望 **OpenClaw 作为主导**,不想把 Claude Code 作为**并列后端**暴露给用户(用户永远跟 OpenClaw 聊,OpenClaw 自己决定何时调 Claude Code)

**这个方案整套 deliverable 全废弃**,包括 `openclaw-with-cc:2026.04.15` 镜像、start-all.sh、bind mount、companion-data、3456 端口配置、以及全部 9 个未写的 Java 类。不过阶段一花的时间**没有浪费** —— 它让我们搞清楚了 Companion 内部协议、Claude CLI 的 `--sdk-url` 模式、stream-json NDJSON 格式等一整套知识,是后面决策的基础。

### 1.2 转折点:OpenClaw 原生 ACP(已在 acp-investigation.md 否决)

有人会问"不能改 openclaw.json,那 OpenClaw 原生 ACP 也不能用",确实。但 acp-investigation.md 证明 OpenClaw ACP 的 4 种模式(thread-bound / --bind here / sessions_spawn / /acp steer)**技术上全挂**(event bus singleton split bug、channel plugin capability 声明要求、硬编码 800 char 截断等),即使能改配置也不可行。所以 ACP 这条路 2026-04-14 就死透了。

### 1.3 思考:OpenClaw 作为 orchestrator,通过 bash tool 调 claude CLI

关键 insight(2026-04-15 中段):

OpenClaw 的 bundled 有 **bash tool**(来自 `@mariozechner/pi-coding-agent`),支持 `pty:true` 参数 —— 分配 pseudo-terminal,让交互式 CLI(claude / codex / pi)正常工作。

这意味着:**OpenClaw 主 agent 可以通过 bash tool 直接 spawn claude 子进程**,不需要 Companion、不需要 foreman、不需要 WebSocket bridge。整个"让 OpenClaw 使用 Claude Code"的能力,**原生就具备**,只需要:

1. claude CLI binary 在 PATH 里
2. OpenClaw 主 agent 知道"遇到合适任务时该调 claude"

第 1 点通过 npm install 实现。第 2 点就是 **skill**(markdown 指令集)的用武之地。

### 1.4 OpenClaw bundled `coding-agent` skill(已 ready 但不被自动选用)

发现 OpenClaw bundled 已经自带一个 `coding-agent` skill(`/app/skills/coding-agent/SKILL.md`),description:

> Delegate coding tasks to **Codex, Claude Code, or Pi agents** via background process. Requires a bash tool that supports pty:true.

**这就是我们想做的事**。它 500+ 行详尽讲了 pty/background/process tool/多轮追问的 pattern。

但是实验证明:**即使 `openclaw skills list` 显示它 `✓ ready`,大橘(OpenClaw 主 agent)在 fresh session 里遇到自然的编码任务时,默认不会自动选用它**。原因推测:

- coding-agent 的 description 是**能力描述**("Delegate coding tasks via background process"),不是**触发条件**。LLM 在看 skill list 时倾向于"我自己能做就不委托"
- 它有一串 `NOT for:` 禁忌列表(simple one-liner fixes / reading code / 等),大橘看到贪吃蛇这种"简单项目"直觉上判断"不需要委托"
- 同样的现象还出现在 `acp-router` skill 上 —— ready 但默认不被选

旁证:我们第一版自己写的 `delegate-to-claude-code` skill(description 里写"用户点名'用 Claude Code 做 xxx'触发")**能自动触发**,因为 description 里写了**明确的触发词**。

**结论**:skill 的 description 决定选择率。**强制性触发词**(`【MUST USE】当用户说 xxx 时`) > **能力描述**。

### 1.5 决定:自己写一个 Claude-only 的 skill,抄一份 coding-agent 的内容

用户定的方向(2026-04-15 后段):

> 不用有复用上游 skill 的强迫症,copy 一份 coding-agent 的内容也没问题。coding-agent 考虑了太多 agent(codex/pi/opencode/claude),我们只需要 claude 这一个。

最终方案形态:

```
/root/.openclaw/skills/delegate-to-claude-code/
├── SKILL.md        # 强触发词 description + pty/background pattern(Claude-only)
├── install.sh      # 首次使用前装 claude CLI + settings.json + skill symlink
└── bin/
    └── claude-as-node  # 薄 wrapper,su 到 node 用户跑 claude(绕过 root 守卫)
```

3 个文件,纯磁盘分发,无镜像层改动。

---

## 2. 实际部署流程(1000 台生产环境)

### 2.1 分发

把整个 `delegate-to-claude-code/` 目录(3 个文件)分发到每台 OpenClaw 容器的 `/root/.openclaw/skills/` 下,用户现有的"往磁盘装文件"能力直接覆盖。

### 2.2 首次使用自动 bootstrap

skill 被大橘第一次触发时,它会看到 SKILL.md 的 "前置" 段落,跑:

```bash
bash /root/.openclaw/skills/delegate-to-claude-code/install.sh
```

install.sh 幂等,4 步:

1. `npm install -g @anthropic-ai/claude-code@2.1.107`(如果未装)
2. 装 `claude-as-node` wrapper 到 `/usr/local/bin/`
3. 写 `/home/node/.claude/settings.json`(pin `claude-sonnet-4-6-thinking` 这个唯一被 yunbiaobiao 授权的 model)
4. 把 OpenClaw 其他 skill(yuque-cli 等)symlink 到 `/home/node/.claude/skills/`,让 Claude Code 能看到

### 2.3 后续调用

skill 内容告诉大橘:遇到合适任务时用 `bash pty:true workdir:<目录> command:"claude-as-node -p '<prompt>'"`。

wrapper 自动处理 4 件麻烦事:
- `chown -R node:node <workdir>`(workdir 由 root 创建,node 原本写不进去)
- 强制在 args 前插入 `--dangerously-skip-permissions`(即使大橘忘传或主动去掉也兜底)
- `su node -s /bin/bash -c` 切到 node 身份
- 透传 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` env 给 node shell

---

## 3. 实施过程中的 9 个坑(按发现顺序)

### 坑 1:OpenClaw skill 热加载需要 gateway restart

**现象**:`docker cp` 新 skill 目录进去后,`openclaw skills list` 的 CLI 命令能看到新 skill,但大橘 agent 对话里看不到。

**根因**:gateway 启动时扫一次 `/root/.openclaw/skills/` 把 skill 注入到 agent context 缓存。之后运行时加的 skill 不自动重扫。

**解法**:`docker restart openclaw-openclaw-gateway-1`。短暂中断。**生产环境这个坑不存在** —— 因为 skill 是随容器启动一起分发(via "现有能力"),容器起来时就在目录里,第一次加载就能扫到。

### 坑 2:YAML frontmatter 的 description 字段特殊字符

**现象**:第一版 SKILL.md description 里有半角逗号 `,` 和双引号 `"`,`openclaw skills list` 看到 skill `✗ missing`。

**根因**:YAML 裸值(unquoted)遇到 `,` 会被解析为数组分隔符,`"` 会让 YAML parser 混乱。

**解法**:description 用单引号包起来 `description: '...'`,单引号里的任何字符(除了单引号自己)都是字面量。

### 坑 3:skill description 必须是"强触发词",不能是"能力介绍"

**现象**:写"把复杂编码任务委托给 Claude Code CLI。支持长任务和多轮追加优化"这种软性 description,大橘遇到贪吃蛇任务不会自动选 skill,自己下手写代码。

**根因**:LLM 在 skill 选择时看 description 做 match,倾向于"我自己能做就不找工具"。软性 description 降低了 match 信号强度。

**解法**:description 写成 `【MUST USE】当用户消息出现"Claude Code"、"claude code"、"cc"...或任务是"生成超过 50 行代码..."时,必须使用本 skill...,不要自己写代码。` 触发词 + 强制语气 + 明确 negation。

### 坑 4:大橘在 fresh session 里真的没有 history

验证:用 `openclaw agent --session-id <uuid>` 传一个**新的** uuid 作为 session id,大橘**就是 cold start**,没有之前学过的 ask-claude / 之前 conversation 的任何记忆。

**意义**:我之前一度怀疑大橘"记得"上次的 workflow —— 这是错的。每个 `--session-id` 对应独立的 session state,cold start 就是真 cold start。之前以为它记得其实是因为 ask-claude binary 还在 /usr/local/bin,大橘每次都重新扫 PATH 看到。

### 坑 5:Claude CLI 在 root 用户下拒绝 `--dangerously-skip-permissions`

**现象**:以 root 身份跑 `claude -p --dangerously-skip-permissions "write a file"` 直接报:

```
--dangerously-skip-permissions cannot be used with root/sudo privileges for security reasons
```

文件不会被写。

**根因**:Claude CLI 自己的硬编码安全检查,跟 Companion 的 `cli-launcher.ts:499-518` 的逻辑是同款(Companion 是复制 claude CLI 的行为)。

**绕过**:必须 **`su` 到非 root 用户** 跑 claude,wrapper 内部做这件事。

**容器里有现成的 `node` 用户**(UID 1000,系统自带),直接用。wrapper `su node -s /bin/bash -c "claude ..."`。

### 坑 6:`--permission-mode` 的其他选项也都不行(在 non-interactive 下)

实验记录(容器内直接跑):

| Permission mode | 行为 |
|---|---|
| `--permission-mode default` | 每个 Write/Bash 都要审批,non-interactive 环境挂死 |
| `--permission-mode acceptEdits` | 仍然要"Please approve the write operation..." |
| `--permission-mode dontAsk` | 要"Grant permission to use the Write tool" |
| `--permission-mode auto` | 输出一段 shell 命令作为 text(相当于说"你自己跑这条") |
| `--permission-mode bypassPermissions` | 跟 `--dangerously-skip-permissions` 同款,**root 下也被拒** |
| `--dangerously-skip-permissions` flag | root 下拒绝 |
| 写 `settings.json` 里的 `permissionMode: bypassPermissions` | 无效,被 root 守卫拦 |

**唯一能用**:su 到 node + `--dangerously-skip-permissions` flag。

### 坑 7:Workdir 权限(node 写不进 root 创建的目录)

**现象**:大橘 `mkdir -p /tmp/snake4` 时是 root 身份,目录 owner 是 `root:root`。然后大橘 bash pty 跑 `claude-as-node`,wrapper su 成 node,node 写不进 root 的目录。Claude Code 的 Write tool 返回 `permission not granted`(但这次是文件系统层面,不是 claude 自己的 permission check)。

**解法**:wrapper 在 su 之前 `chown -R node:node "$CWD"`。wrapper 自身是 root 身份跑,有权限 chown。只 chown workdir,不动其他目录。

**注意**:这改变了 workdir 的 owner,对后续可能想用 root 继续操作这些文件的场景有点 unfriendly。但我们这个场景是 delegate 给 claude 做,claude 做完就结束,用户如果要继续操作,也是通过 skill 再发一次任务(node 身份),一致。

### 坑 8:大橘会**主动去掉** `--dangerously-skip-permissions` flag

**最坑的一个**。即使 SKILL.md 里写:

> **永远带 `--dangerously-skip-permissions`**,不要被名字吓到,在本容器里这个 flag 是必须的,没它 claude 会 hallucinate 一个假的"完成"报告。

大橘还是会自作主张去掉:

> (大橘的实际对话)"Claude Code 在等权限批准。让我用正常模式重新启动,**不带 dangerously-skip**..."

LLM 看到 "dangerous" 这个词就触发了它的安全倾向(训练数据里"危险"=坏),哪怕 SKILL.md 反复强调。连续两次 fresh session 都这样。

**解法**:**让 wrapper 内部强制插入** flag,不依赖大橘传参。wrapper 逻辑:

```bash
# 检查 args 里是否已有 --dangerously-skip-permissions / --permission-mode
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

这样大橘即使忘传或故意去掉,wrapper 也在 `su node` 之前自动插入。

**教训**:**关键的安全/可用性 flag 不能靠 prompt engineering 约束 agent**,必须在代码层兜底。Prompt 约束有用但不 100%。

### 坑 9:大橘会 hallucinate "Claude Code 已完成任务" 即使任务失败

**现象**:claude 的 Write tool 被拒(因为 workdir 权限 或 没 bypass flag),claude 的 transcript 里清楚写着 `permission not granted`。但 claude 最后的 text 仍然写"完成了,游戏保存到 main.py,语法验证通过"。大橘看到这句,就原样转发给用户报告成功。用户 ls 一看:**main.py 不存在**。

**根因**:这是 model 层的 hallucination。claude 在 tool 反复被拒的情况下,不知道怎么处理,就编一段"乐观"的结尾。大橘不会二次验证 claude 的输出。

**解法** 不单靠 SKILL.md 或 prompt,必须先让**底层真正能成功**(见坑 7、8)。底层通了,这个 hallucination 就消失了(最后一次 smoke test 文件真实存在,大橘报告也是真的)。

如果底层偶尔还是失败(比如 npm install 失败、API 403),需要 skill 里再补一句让大橘 `ls -la <workdir>` 主动核验。这是二次保险。

---

## 4. 流式输出的真实状况

### 4.1 问题

用户的原始需求:"在 OpenClaw 输出这边看到它流式的吐着 ClaudeCode 的执行过程"。

### 4.2 已确认的事实

1. **pi-coding-agent 的 bash tool 技术上支持流式** —— `bash-tools.exec-runtime.ts` 的 `emitUpdate()` 每几百毫秒通过 `onUpdate` 回调推 tail
2. **OpenClaw 的 `pi-embedded-subscribe.handlers.tools.ts` 处理这些 update** —— 把 `partialResult` 通过 `emitAgentEvent({stream:"tool", data:{phase:"update"}})` 发到 agent event bus
3. **从 bash tool 到 agent-events bus 这段是存在的**(之前调研源码确认)
4. **但 `/tmp/openclaw-0/openclaw-*.log` 里没有 `tool_execution_update` 事件的分时打点** —— 这些事件走 agent-events bus(内存 channel),不落 file log

### 4.3 `openclaw agent` CLI 不是流式

本地验证:跑一个 `bash pty:true command:"for i in 1..8; do echo tick $i; sleep 1; done"`(8 秒),`openclaw agent -m` CLI **一次性返回最终 agent text**,中间没有任何 tick 输出。这证明 CLI 是 one-shot consumer。

### 4.4 真实 channel plugin 是不是流式?我这边**无法验证**

- capsule / telegram / discord / 企微 channel plugin 是否订阅了 agent-events bus 的 `tool_execution_update` 事件,决定用户端能不能看到 claude 跑的过程
- 我这边没有企微 channel 的源码,无法 grep 验证
- 需要**用户那边确认**:在企微里跑一次用 claude-as-node 的任务,看消息流里有没有中间 tool 进展(比如 "🔧 正在运行 claude-as-node..." 这类 progress message)

### 4.5 如果 channel plugin 没订阅,能补救吗?

有两个方向(都没试过):

**A. 修改 channel plugin 订阅 agent-events**:需要你们这边改企微 channel plugin 的代码,添加对 `tool_execution_update` 的订阅,把 partialResult 翻译成增量消息推给企微用户。这是一次性工作,不改 OpenClaw 本身。

**B. skill 里教大橘"边跑边汇报"**:比如让大橘用 `background:true` 模式跑 claude,然后每隔几秒 `process action:log` 拉最新 tail,主动把 tail 作为 agent text 发给用户。这是**显式地让 agent 把 progress 写成自己的消息**,绕过 channel-level 的流式链路。缺点:额外 overhead,agent 一直在 poll,而且 LLM 可能不准守这个 poll 循环。

两个方案都需要额外工作。**本次 skill 产物**只解决了"执行层"(claude 真能跑 + 生成文件),流式 UX 留给下一期。

---

## 5. 追问转发(claude 需要追加信息时)

用户问:"假如 ClaudeCode 还需要提供一些信息,OpenClaw 能把它的需求转发给用户吗?"

### 5.1 答案:默认不行,需要 skill + prompt 配合

**默认不行的原因**:
- `claude -p` 是 non-interactive 模式,它看到需要澄清就**自己猜一个路径继续**或报错,不会真的等用户
- 即使用 `claude`(不加 `-p`)in pty + background,claude 在 REPL 里等 stdin,但 OpenClaw 主 agent 只看到 bash session idle —— 它没有"识别 claude 在问问题"的内置逻辑
- `openclaw agent` CLI 和 channel plugin 都没有 "relay claude's question to user" 的内置能力

### 5.2 可能的路径(没实施)

让 skill 里教大橘:

1. 用 `background:true` spawn claude
2. 每隔几秒 `process action:log` 看 log tail
3. 如果 log 末尾像是一个问句(以 `?` 结尾,或包含"请告诉我"这种),**把问句提取出来,作为 agent text 发给用户**
4. 用户回复后,大橘用 `process action:submit sessionId:$SID data:"<用户答案>"` 转发给 claude
5. 继续 poll log 等新输出

这条路的代价:
- 大橘的 prompt 要写得精细(如何识别问句?怎么提取?多久 poll 一次?)
- 可能 false positive(把正常输出误判为问句)
- 多轮状态管理,大橘可能丢失

实际用起来大概率效果不稳定。**更实际**的 workaround:**让 prompt 写得足够完整**,减少 claude 需要追问的概率。SKILL.md 里强调:

> 写 prompt 的关键:Claude Code 看不到你跟用户的对话,所有它需要的信息(文件路径、要求、约束、期望输出格式、边界条件)都要显式写在 prompt 里。

大橘第一次写 prompt 时把所有必要 context 都打包好,claude 就不用追问。这是**把"需要追问"的场景尽可能消灭在发起阶段**。

### 5.3 真的要追问转发,也有路径

- **A**:你们改企微 channel plugin,让它支持"agent 发起一轮 clarification 给用户 → 等用户答 → 继续"的双向交互原语
- **B**:让 skill 明确说"claude 如果无法完成,直接报错退出,告诉用户任务中断原因",不走中途澄清

**本次 skill 选 B**(不支持追问转发,让 prompt 完整以减少场景)。

---

## 6. 产物清单

本次调研最终产物:

### 6.1 skill 目录(分发给 1000 台)

[`devdocs/0414-claude-code-companion/delegate-to-claude-code-skill/`](./delegate-to-claude-code-skill/)

```
delegate-to-claude-code-skill/
├── SKILL.md         (6 KB, markdown 指令集,大橘读这个决定何时/如何 delegate)
├── install.sh       (2.2 KB, 幂等 bootstrap 脚本)
└── bin/
    └── claude-as-node   (1.8 KB, 薄 wrapper,绕过 root 守卫)
```

### 6.2 SKILL.md 要点

- frontmatter description 用**强触发词**(`【MUST USE】`、具体关键词、`不要自己写代码`)
- 三条**铁律**:用 `claude-as-node` 不用 `claude`(root 守卫)、永远带 `--dangerously-skip-permissions`(wrapper 也兜底)、永远带 `pty:true`
- 首次使用前跑 install.sh
- 单次 / background 两种模式,多轮追问用 `process action:submit`
- 完整故障排除表

### 6.3 install.sh 要点

- 幂等,重复跑无害(有 check skip)
- 装 `@anthropic-ai/claude-code@2.1.107`(pin 版本)
- 装 `claude-as-node` wrapper 到 `/usr/local/bin/`
- 写 `/home/node/.claude/settings.json`(pin model 为 `claude-sonnet-4-6-thinking`,yunbiaobiao 唯一授权)
- symlink `/root/.openclaw/skills/*/` → `/home/node/.claude/skills/*/`(让 Claude Code 能用 OpenClaw 其他 skill,排除自己)
- smoke test `claude-as-node -p "say OK"`

### 6.4 claude-as-node wrapper 要点

- debug log 写 `/tmp/claude-as-node.log`(可审计)
- `chown -R node:node $CWD`(workdir 由 root 创建 → 给 node)
- **强制插入** `--dangerously-skip-permissions`(agent 传不传都有)
- `su node -s /bin/bash -c`(绕过 root 守卫)
- 保留 `HOME=/home/node`,`ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` env 显式 export
- `cd '$CWD'` 保持 workdir 一致

---

## 7. 实验证据

### 7.1 最后一次成功的 fresh session 实验(snake8)

- session id:`cb37e881-86c8-40e6-b88f-d178c478cf70`(fresh UUID)
- 用户消息:"用 Claude Code 帮我: 在 /tmp/snake8 目录下 (不存在先 mkdir -p) 用 Python curses 写一个贪吃蛇游戏单文件 main.py..."
- 总耗时:约 2 分钟(LLM thinking + claude tool loop)
- 大橘响应:"已委托给 Claude Code..."(识别 skill 触发)→ "✅ Claude Code 已完成任务..."
- 实际文件:`drwxrwxrwx 3 node node /tmp/snake8/`,`main.py` 215 行,owner `node:node`,含 `__pycache__`(真 py_compile)
- wrapper log:`orig args: -p 用 Python...`(大橘没传 bypass)→ `final args: --dangerously-skip-permissions -p 用 Python...`(wrapper 强插)

**从 0 到 1 完整端到端:fresh session → skill 识别 → install.sh bootstrap → claude-as-node → su node → claude 真 Write → 文件真存在。**

### 7.2 已验证的能力

- ✅ OpenClaw 主 agent 识别 delegate-to-claude-code skill 的强触发词
- ✅ 自动跑 install.sh 第一次 bootstrap
- ✅ 按 skill 教的 `bash pty:true command:"claude-as-node -p '...'"` 调用
- ✅ wrapper 正确处理 chown / bypass / su / env
- ✅ claude 能看到 node 用户级 settings.json(thinking model,绕开 yunbiaobiao 403)
- ✅ claude 能 Write / Bash tool 真实执行
- ✅ 生成文件真实存在且语法正确
- ✅ OpenClaw skill(yuque-cli / opencli-for-openclaw)通过 symlink 暴露给 Claude Code(Claude 能看到这些 skill 的 listing)

### 7.3 未验证 / 已知限制

- ❓ **流式 UX**:bash 到 agent-events bus 的流式链路存在,但**企微 channel plugin 是否订阅** 本人无法验证,需要生产环境测试或拿 channel plugin 源码 grep
- ❓ **追问转发**:默认不行,没实现,用"prompt 完整以减少追问场景"作 workaround
- ⚠️ **大橘的 hallucination 倾向**:即使任务 fail,大橘可能报告成功。目前靠"底层真能跑通"缓解,没有二次验证逻辑
- ⚠️ **`--dangerously-skip-permissions` 被自发去掉**:不能只靠 SKILL.md prompt 约束,必须 wrapper 兜底
- ⚠️ **single-user 假设**:wrapper 用全局 `/tmp/ask-claude-session.txt`(老版本)/`/tmp/claude-as-node.log`,多并发场景要加 session id 隔离(v1 不支持)

---

## 8. 跟原 Companion 方案的对比总结

| 维度 | Companion + foreman(已废弃) | delegate-to-claude-code skill(当前) |
|---|---|---|
| 分发单位 | 新镜像 `openclaw-with-cc:2026.04.15` + foreman Java 代码 + .env 配置 + compose 改动 | 一个 skill 目录(3 个文件) |
| OpenClaw 镜像改动 | 必须 | **无** |
| compose 改动 | 必须(加端口 / volume / env / command) | **无** |
| foreman Java 代码 | 9 个新类 | **0** |
| 新常驻进程 | the-companion | **无** |
| 新端口 | 3456 | **无** |
| 1000 台部署成本 | 极高(要分发新镜像 + 重建容器) | **低**(只分发几个文件) |
| 用户使用 | `/claude` 切后端,与 OpenClaw 并列 | 透明,用户永远跟 OpenClaw 聊 |
| OpenClaw 对 Claude 的感知 | 无(foreman 拦截) | 有(它自己用 bash 工具 spawn) |
| 多轮追问 | Companion session 管理 | `--resume` 或 background `process action:submit` |
| 流式 UX | WS,原生支持 | 取决于 channel plugin(同) |

**skill 方案全面胜出**,除了两个遗留问题(流式 UX 不确定 + hallucination 倾向),都是架构限制,Companion 方案同样有。

---

## 9. 1000 台生产部署操作步骤

假设你们有一个 "把文件推到每个 OpenClaw 容器 `/root/.openclaw/skills/` 下" 的能力,步骤:

1. **打包 skill 目录**:
   ```
   cd devdocs/0414-claude-code-companion/
   tar czf delegate-to-claude-code-skill.tgz delegate-to-claude-code-skill/
   ```
2. **分发到 1000 台**:用你们的分发平台把 tgz 解开到每台容器的 `/root/.openclaw/skills/delegate-to-claude-code/` 下(注意**整个目录**,不是单文件)
3. **首次使用自动 bootstrap**:每台容器第一次有用户触发 skill 时,大橘会自动跑 install.sh(依赖 npm registry 可达、ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL env 已经设好)
4. **后续使用透明**:用户在企微说"用 Claude Code 帮我写 xxx",大橘自动 delegate,claude 真跑真写

**一次性验收 checklist**(部署完一台机器就跑一次):

- [ ] `ls /root/.openclaw/skills/delegate-to-claude-code/SKILL.md` 文件在
- [ ] `command -v claude` 空(还没 bootstrap,不该有)
- [ ] 发一条"用 Claude Code 写 hello world"
- [ ] install.sh 跑完(第一次会稍慢,npm install 约 3-5 秒)
- [ ] `/tmp/hello.py` 或你指定的目录下**真有文件**,owner 是 `node:node`
- [ ] 第二次同类任务跑起来**更快**(不需要重装)

---

## 10. 遗留项 / 给下一期的建议

1. **流式 UX 验证 + 实现**:企微 channel plugin 是否订阅 agent-events?如果不订阅,改 plugin 加订阅 or 用 `background:true` + poll log 的 skill prompt hack。建议先找 plugin 源码 grep `tool_execution_update` 确认当前状态
2. **追问转发**:如果生产真的需要,改 channel plugin 支持 clarification round-trip,或者在 skill 里加一个 "claude 卡住 → 提取 log tail 尾部做问句提取 → 发给用户 → `process action:submit` 转发" 的 pattern
3. **hallucination 保险**:skill 里加一句"任务完成前用 `bash command:\"ls -la <workdir>\"` 核验文件是否真存在,不存在就直接报错给用户"
4. **并发隔离**:如果一个容器同时有多个用户并发用 claude-as-node,`/tmp/claude-as-node.log` 和 `/home/node/.claude/projects/` 可能交叉污染。生产先观察,必要时加 session id 隔离
5. **版本升级策略**:install.sh pin 到 `2.1.107`,以后 Anthropic 发新版要测一下再升。考虑把版本号外置到 env / 配置文件
6. **尚未拆掉的 dev 容器改动**:本地容器还有 `openclaw-with-cc:2026.04.15` 镜像 + 3456 端口 + companion-data bind mount + Companion 相关的 env / compose 改动。生产不用这些,**本地清理留作下次(不紧急)**

---

## 11. 附录:本次用到的所有命令和文件位置

### 11.1 本地(Mac)

- 产物目录:`/Users/songxinjian/dev/java/my-ai-playground/devdocs/0414-claude-code-companion/delegate-to-claude-code-skill/`
- OpenClaw 源码:`/Users/songxinjian/dev/java/openclaw/`(用于 grep skill loading / bash tool 机制)
- Claude Code CLI 源码:`/Users/songxinjian/dev/java/claude-code-source/`(用于 grep `--dangerously-skip-permissions` 的 root 守卫)

### 11.2 Windows 宿主机

- `C:\Users\songx\openclaw\` — OpenClaw compose 目录
- `C:\Users\songx\openclaw\delegate-skill\` — 本次 staging 目录(3 个产物 + 若干 test 脚本)

### 11.3 容器内

- `/root/.openclaw/skills/delegate-to-claude-code/` — skill 目录
- `/usr/local/bin/claude-as-node` — wrapper(install.sh 装)
- `/usr/local/bin/claude` — npm global(install.sh 装)
- `/home/node/.claude/settings.json` — model 固化
- `/home/node/.claude/skills/*` — symlink 到 `/root/.openclaw/skills/*`
- `/home/node/.claude/projects/-<cwd-hash>/*.jsonl` — claude 对话 transcript(排错用)
- `/tmp/claude-as-node.log` — wrapper debug log

### 11.4 关键测试命令

```bash
# 从容器外触发大橘 fresh session
docker exec openclaw-openclaw-gateway-1 node /app/dist/index.js agent \
  --agent main \
  --session-id "$(uuidgen)" \
  -m "用 Claude Code 帮我: <任务>"

# 列所有 skill 及 ready 状态
docker exec openclaw-openclaw-gateway-1 node /app/dist/index.js skills list

# 手工跑 wrapper 测试
docker exec openclaw-openclaw-gateway-1 sh -c \
  "mkdir -p /tmp/test && cd /tmp/test && claude-as-node -p 'write hello.py'"

# 看 wrapper 最近调用日志
docker exec openclaw-openclaw-gateway-1 cat /tmp/claude-as-node.log

# 看 claude transcript(看 tool_use 有没有被权限拒)
docker exec openclaw-openclaw-gateway-1 ls /home/node/.claude/projects/
```
