# delegate-to-claude-code-acp

> **当前版本:v2.0.0** · 完整迭代历史见 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)


把复杂编码任务从 OpenClaw 主 agent(OpenClaw 主 agent)委托给 **Claude Code CLI**,通过 [ACP(Agent Client Protocol)](https://github.com/zed-industries/agent-client-protocol)协议调用。相比纯文本 bash 调用,本 skill 让OpenClaw 主 agent拿到**结构化事件流**,并且**真正支持跨多轮用户请求的会话续接**。

---

## 这个 skill 解决了什么问题

OpenClaw 主 agent 在生成长代码、跨多文件编辑、按严格步骤执行时表现不稳定。常见对策是让OpenClaw 主 agent把这类任务委托给 Claude Code CLI 执行。难点在三件事:

1. **沟通层**:bash tool 的 stdout 是非结构化文本,OpenClaw 主 agent只能靠自然语言启发式判断 claude 做完没做完、成没成功。易误判、易 hallucinate。
2. **多轮续接**:用户一轮里让 claude 写了代码,下一轮说"再改一下",OpenClaw 主 agent必须让同一个 claude 会话继续工作,不能让 claude 从头开始。
3. **生产约束**:公司 1000+ bot 实例,**不能改 OpenClaw 镜像 / 不能改 openclaw.json / 不能加 plugin**。只能靠磁盘分发文件。

本 skill 用**分两段的 ACP 协议栈**解决这三个问题:

- **OpenClaw 主 agent ↔ 本 skill 的 wrapper**:走 bash tool 的 NDJSON stdout,结构化事件(tool_call / tool_update / text / session / done)
- **wrapper ↔ Claude Code**:走标准 ACP JSON-RPC over stdio,协议级的 permission / cancel / session lifecycle

多轮续接靠 Claude Code CLI 把对话历史持久化到 `~/.claude/projects/<cwd-hash>/<sessionId>.jsonl`,下一次 wrapper 启动时用 `loadSession(sid)` 从这个文件恢复。详见 [`docs/02-multi-turn-mechanism.md`](docs/02-multi-turn-mechanism.md)。

---

## install.sh 做的 6 件事

首次部署到新 bot 时跑一次 `install.sh`(幂等,之后每次部署可以再跑也无害):

1. **装 `@agentclientprotocol/claude-agent-acp@0.27.0`** 到 npm 全局
2. **升级 `@anthropic-ai/claude-code` 到 2.1.109**。如果 bot 上 `/usr/local/bin/claude` 是旧的静态 ELF 二进制(观察到部分 bot 预装了 2.0.42 的静态版本屏蔽 npm 版本),自动 rename 成 `claude.legacy-*` 并把 `/usr/local/bin/claude` 改成指向 npm 版本的 symlink
3. **写 `/root/.aws/credentials`** 的 `[claude-profile]` 段(从 env vars 取值);claude CLI 的 AWS SDK 会因为 `CLAUDE_CODE_AWS_PROFILE=claude-profile` 强制找 profile,没这个文件就会报 `Could not load credentials`
4. **Symlink `/usr/local/bin/claude-as-acp`** 指向 skill 目录里的 wrapper
5. **把 OpenClaw 其他 skill symlink 进 `/root/.claude/skills/`**,让 Claude Code CLI 能看到并调用 OpenClaw 已有的 skill(yuque-cli / opencli-for-openclaw / console-platform 等 24 个)。这样 Claude Code 在执行 delegation 时能复用 OpenClaw 生态
6. **smoke test**:spawn 一次 wrapper,发一个简单 prompt,验证能拿到 session + 响应

## Wrapper 的两个"全权限"开关

生产环境 bot 以 root 跑,claude-agent-acp 默认**拒绝** root 用户使用 `bypassPermissions`(出于安全考虑)。wrapper 做了两件事来放开:

1. **Spawn claude-agent-acp 时注入 `IS_SANDBOX=1`**。claude-agent-acp 源码 `acp-agent.js:44-45` 的逻辑是 `ALLOW_BYPASS = !IS_ROOT || !!process.env.IS_SANDBOX`。设了这个 env 之后,root + bypassPermissions 就变成合法组合
2. **newSession / loadSession 后立即 `conn.setSessionMode({modeId:"bypassPermissions"})`**。session 默认是 `default` 模式(Write/Edit 等会触发 permission_request),通过协议级 RPC 切到 bypassPermissions 后所有 tool 调用都不再触发权限询问

实测结果:Write + Bash 两种工具连续调用,wrapper 日志里 `permission_auto_approved` 计数 = 0,`requestPermission` callback 从未被触发 —— 是在协议层彻底关掉了,不是靠 wrapper 兜底 auto-approve。

Wrapper 里仍然保留了 auto-approve 的 backup 逻辑(在 `Client.requestPermission`),以防某些 tool 即使在 bypassPermissions 模式下还会触发 permission_request(例如 ExitPlanMode)。见 `bin/claude-as-acp` 的注释。

## 文件布局

```
delegate-to-claude-code-acp/
├── README.md                         # 本文件(概览)
├── SKILL.md                          # OpenClaw 主 agent 加载的 skill 定义(v2 单参数 --task API)
├── install.sh                        # bootstrap:装 claude-agent-acp + 写 /root/.aws/credentials + 升级 claude CLI + symlink 24 个 skill
├── bin/
│   └── claude-as-acp                 # ACP client wrapper(Node.js)
└── docs/
    ├── CHANGELOG.md                  # ⭐ 每次迭代的动机、改动、测试(从上往下读,最新在最上)
    ├── 01-architecture.md            # 整体架构与数据流(v1 示例,机制通用)
    ├── 02-multi-turn-mechanism.md    # 🎯 多轮续接内部机制(v1 示例,原理通用)
    ├── 03-lifecycle-and-cleanup.md   # 进程生命周期与清理机制(通用)
    └── 04-e2e-validation.md          # v1 时的 3 轮端到端实测
```

---

## 生产环境部署

本 skill **纯文件分发**,不改 OpenClaw 镜像。步骤:

1. **把本目录分发到 bot**:
   ```bash
   scp -r delegate-to-claude-code-acp/ \
     root@<bot-ip>:/root/.openclaw/skills/yuanbot/yuanli-skill-hub/skills/
   ```
   路径必须在 `yuanli-skill-hub/skills/` 下才会被 openclaw 自动 pick up。

2. **首次 bootstrap**(可以 ssh 上 bot 手动跑,或让OpenClaw 主 agent在 skill 触发时自己跑):
   ```bash
   ssh root@<bot-ip> 'bash /root/.openclaw/skills/yuanbot/yuanli-skill-hub/skills/delegate-to-claude-code-acp/install.sh'
   ```
   **注意**:必须在 openclaw gateway 的 env 上下文里跑,或者手动把 `AWS_ACCESS_KEY_ID` 等 env 导出,否则 `/root/.aws/credentials` 这步会跳过。bot 的标准做法是从 `/proc/<gateway-pid>/environ` 提取:
   ```bash
   GATEWAY_PID=$(pgrep -f openclaw-gateway | head -1)
   while IFS= read -r -d '' line; do
     key="${line%%=*}"; val="${line#*=}"
     case "$key" in CLAUDE_CODE_USE_BEDROCK|ANTHROPIC_MODEL|AWS_*|HTTP_PROXY|HTTPS_PROXY|NO_PROXY|CLAUDE_CODE_AWS_PROFILE)
       export "$key=$val" ;;
     esac
   done < /proc/$GATEWAY_PID/environ
   bash /root/.openclaw/skills/yuanbot/yuanli-skill-hub/skills/delegate-to-claude-code-acp/install.sh
   ```

3. **验证**:
   ```bash
   openclaw skills list | grep delegate-to-claude-code-acp
   # 期望看到:  ✓ ready   📦 delegate-to-claude-code-acp   ...
   ```

不需要重启 openclaw gateway,skill 是热加载的。

---

## 快速用法(命令行直接跑,不走OpenClaw 主 agent)

装完后可以手动跑 wrapper 做 smoke test:

```bash
# Round 1: 新建 session
claude-as-acp --cwd /tmp/test \
  'Create /tmp/test/hello.py that prints "hello world"'
# 输出第一行 {"type":"session","sessionId":"<sid>","resumed":false}

# Round 2: 续接 session(用第一轮的 sid)
claude-as-acp --cwd /tmp/test --resume <sid> \
  'Add a second line printing "goodbye"'
# 第一行 {"type":"session","sessionId":"<sid>","resumed":true}
```

---

## 走OpenClaw 主 agent的路径

这是实际生产用法。用户通过 openclaw channel(企微等)聊天,OpenClaw 主 agent识别本 skill 的触发词(`Claude Code` / `cc` / `让 Claude 帮我...`),自动调 wrapper 并维护 sessionId:

```
[用户]  用 Claude Code 在 /tmp/foo 写个 Python 脚本 hello.py 打印 hi
[OpenClaw 主 agent]  → 读 SKILL.md
      → bash pty:true workdir:/tmp/foo command:"claude-as-acp --cwd /tmp/foo 'Create hello.py...'"
      → 从 stdout 第一行提取 sessionId=abc-123
      → 回复用户:完成!(CC session id 是 abc-123)

[用户]  让它再加一行打印当前时间
[OpenClaw 主 agent]  → bash pty:true command:"claude-as-acp --cwd /tmp/foo --resume abc-123 'Add a second line...'"
      → claude 记得 hello.py 是上一轮写的,直接 Edit 加行
      → 回复用户:已添加第二行
```

实际实测的 3 轮端到端对话及证据见 [`docs/04-e2e-validation.md`](docs/04-e2e-validation.md)。

---

## 推荐阅读顺序

如果你是第一次看这个 skill,按以下顺序读:

1. 本 README(你在读了)
2. [`docs/01-architecture.md`](docs/01-architecture.md) —— 整体架构一张图看懂
3. [`docs/02-multi-turn-mechanism.md`](docs/02-multi-turn-mechanism.md) —— **多轮对话是怎么续上的**(重点)
4. [`docs/03-lifecycle-and-cleanup.md`](docs/03-lifecycle-and-cleanup.md) —— 进程什么时候起、什么时候死
5. [`docs/04-e2e-validation.md`](docs/04-e2e-validation.md) —— 实测证据
6. [`SKILL.md`](SKILL.md) —— OpenClaw 主 agent看的那份(包含触发词和具体调用规范)

---

## 已知限制

1. **并发**:同 bot 上同一用户发多个并行 CC 任务时,sessionId 由OpenClaw 主 agent在对话里手动维护,OpenClaw 主 agent理论上能维护多个但需要自己跟踪。混淆风险存在。
2. **Session 过期**:Claude Code 的 jsonl 文件不会自动清理,但目录可能被运维手动清。清掉后 `--resume` 会失败,wrapper 会报 `loadSession failed` 并 fallback 到新 session。
3. **长时间任务**:>15 分钟的任务,HTTPS_PROXY 或 bash tool 的超时没实测过。
4. **只支持 Bedrock**:install.sh 的凭证 bootstrap 写死了 `[claude-profile]` 段,假设 bot 上 `CLAUDE_CODE_AWS_PROFILE=claude-profile` 且使用 AWS Bedrock。如果用 Anthropic 原厂 API 或其他 provider,需要改 install.sh。

---

## 相关文档

- 上游设计背景:`devdocs/0414-claude-code-companion/`(phase1-4 的演进历史)
- phase2 对比方案(纯 bash + 文本 stdout):`devdocs/0414-claude-code-companion/delegate-to-claude-code-skill/`
- ACP 协议规范:[@agentclientprotocol/sdk](https://www.npmjs.com/package/@agentclientprotocol/sdk)
