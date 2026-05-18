# CHANGELOG · delegate-to-claude-code-acp

本文件按时间倒序记录本 skill 的每次迭代。每条记录包含:动机、改动、测试结果、commit 引用。
阅读顺序:**从上往下看,最近的迭代在最上**。

---

## 2.1.0 · 2026-04-15 晚上 · 默认 CLAUDE.md 提示 skill/adapter 输出路径

**commit**: 待 push

### 动机

v2.0.0 部署到 test bot 后,用户实测了一轮完整的 "用 Claude Code 写 yuque 适配器 + skill" 任务。wrapper 和 session 续接机制都没问题(两次同 task 名调用,自动 resume 同一 sessionId,产出 20+ 文件),但发现了 Claude Code **自身的两个路径决策错误**:

1. **生成的 skill 放到了错路径**:Claude Code 写到 `/root/.openclaw/skills/yuanli-skill-hub/skills/yuque-manager`(缺少 `yuanbot/` 前缀),**不会被 openclaw 的 skill loader 加载**,等于白写。正确路径是 `~/.openclaw/workspace/skills/<name>/`。
2. **生成的 opencli 适配器在错位置**:Claude Code 在 workdir 下建了 `adapters/` 子目录,然后 `~/.opencli/clis/yuque/` 里全是指向 workdir 的 symlink。正确做法是**直接写到** `~/.opencli/clis/<provider>/`,不要绕 symlink。

这两个都不是 wrapper 的 bug —— wrapper 只负责跑 Claude Code + 续接 session。问题是 Claude Code 没有关于"在这台机器上东西应该落在哪里"的先验知识。

### 解决方式

把这两条路径约定写到 **workspace 根目录的 CLAUDE.md** 里:

```
/root/claudecode-workspace/CLAUDE.md
```

Claude Code 启动时会**自动 walk up 搜索 CLAUDE.md**(从当前 cwd 向上找父目录)。因为所有 task 都在 `/root/claudecode-workspace/<task>/` 下,所以这个位置的 CLAUDE.md 会被所有 task 自动加载,不需要主 agent 在每次 prompt 里重复写这些规则。

**Wrapper 在 bootstrap 阶段写这个文件**:只在不存在时写(idempotent),允许用户后续自己编辑覆盖。

### CLAUDE.md 的 3 条硬规则

1. **Skill 产出** → `~/.openclaw/workspace/skills/<skill-name>/`(不是 git-synced 路径)
2. **OpenCLI 适配器** → 直接写到 `~/.opencli/clis/<provider>/<command>.ts`(不要绕 symlink)
3. **临时工作文件** → 当前 cwd (`/root/claudecode-workspace/<task>/`) 下自由组织

### 为什么写进 CLAUDE.md 而不是每次 prompt 里 prepend

| 维度 | CLAUDE.md | prompt 前缀 |
|---|---|---|
| 自动发现 | Claude Code 原生支持 walk-up 搜索 | 需要主 agent 每次写 |
| 主 agent 负担 | 零 | 每次都要记得加 |
| prompt cache | 进 system prompt 的一部分,被缓存 | 每轮都可能破坏 cache |
| 持久性 | 写到磁盘一次,永久生效 | 无状态,靠主 agent 记忆 |
| 用户可编辑 | 是,一个文件 | 无 |

**CLAUDE.md 完胜**,唯一代价是 Claude Code 必须从 cwd 开始 walk-up 能找到它 —— 这由 wrapper 保证 cwd 总在 `/root/claudecode-workspace/<task>/` 下实现。

### Wrapper 内部改动(文件层面)

`bin/claude-as-acp` 的 bootstrap 阶段新增一段:

```js
try {
  const workspaceClaudeMd = `${WORKSPACE_ROOT}/CLAUDE.md`;
  if (!existsSync(workspaceClaudeMd)) {
    writeFileSync(workspaceClaudeMd, defaultContent);
    log("bootstrap: wrote default CLAUDE.md at workspace root");
  }
} catch (e) { ... }
```

幂等,第一次运行创建,之后每次 wrapper 启动都检查(开销可忽略)。用户编辑过的不覆盖。

### 测试

部署到 bot 后需要验证:
1. 首次 wrapper 调用 → CLAUDE.md 被创建
2. 第二次调用 → CLAUDE.md 未被覆盖(mtime 没变)
3. Claude Code 新 session 里,ask 它写一个 skill 看它是不是会放到 `~/.openclaw/workspace/skills/`
4. 让它写一个 opencli 适配器,看是不是直接写到 `~/.opencli/clis/`

---

## 2.0.0 · 2026-04-15 下午 · 极简 `--task` 单参数

**commit**: `c81661c` (MR !49 第 2 个 commit)

### 动机

v1.0.0 发出去之后,**本人实测 + 在 xinjianbot4test 上用真实 web UI 复现**,发现多轮续接在生产中不稳定。日志显示 OpenClaw 主 agent 6 次 wrapper 调用里只有 1 次正确传了 `--resume <sid>`,其他 5 次都是:

- 想续接但忘了传 `--resume` → 启动 brand-new Claude Code session,丢失上下文
- 选错 sessionId(传了更早的那个,不是最近的)
- 甚至有 1 次调 `claude-as-acp` 完全不带 prompt 参数触发 `missing prompt arg` 错误

根因:**让 LLM agent 抄一个 UUID + 判断"这是不是同一任务"属于 prompt engineering,可靠性不到 100%**。即使 SKILL.md 写得再细,主 agent 在真实对话里仍然频繁失手。

### 设计取舍(跟用户多轮讨论得出)

第一版"修复"方案想给主 agent 更多工具(`--fresh` flag、auto-resume 兜底、next_resume_hint 提示),用户一句话否决:

> "机制好像太复杂了。那个 agent 给他提供的选择太多,他反而做不好,你最好只给他一个很简单的参数。"

最终决策:**参数从 4 个缩到 1 个**。主 agent 的唯一决策 = 给任务取个名字。

### API 变更(向后不兼容)

**Before (v1)**:
```
claude-as-acp [--resume <sid>] [--cwd <path>] [--fresh] [--timeout <sec>] "<prompt>"
```
主 agent 必须:
- 记得从前一轮 stdout 提取 sessionId
- 判断本次是否续接
- 带上 `--resume <sid>` 或选择 `--fresh`
- 指定正确的 `--cwd`

**After (v2)**:
```
claude-as-acp --task <name> [--timeout <sec>] "<prompt>"
```
主 agent 只需:
- 给任务取个名字(kebab-case,比如 `yuque-adapter`)
- 同一任务 → 同名
- 不同任务 → 不同名

### Wrapper 内部做的事(自动 + 确定性,主 agent 零参与)

1. `task 名 → workdir`: `/root/claudecode-workspace/<task>/`(位于持久盘,pod 重启不丢)
2. `mkdir -p workdir`(幂等)
3. `workdir → transcript 目录`: `/root/.claude/projects/-root-claudecode-workspace-<task>/`(按 Claude Code CLI 的 slash-to-dash 规则)
4. 扫 transcript 目录里的 `*.jsonl`,有就按 mtime 挑最新的 → 自动 `loadSession`;没有就 `newSession`
5. 输出 `{"type":"task","name":"<task>","cwd":"...","resuming":<bool>}` 作为 stdout 第一行告诉主 agent 结果
6. `done` 事件加 `next_call_hint: "claude-as-acp --task <同名> ..."`,主 agent 一键抄

### Task 名格式校验

- 正则 `^[a-z0-9][a-z0-9-]{0,63}$`
- 小写字母 / 数字 / hyphen,1-64 字符,不能以 hyphen 开头
- 非法立即 `exit 2` + 清晰 error message
- 例:`✅ yuque-adapter, hello-py, todo-poc` / `❌ Bad Task, task_1, -foo`

### 持久性确认

两个 bot(test + main)的 mount 结构:
```
/root        → /dev/vdc  ext4  独立持久盘 (10GB)
/root/.claude, /root/.openclaw → 同一盘
/tmp         → overlay   非持久
```

所以 `/root/claudecode-workspace/` 和 `/root/.claude/projects/` 都在持久盘上,pod 重启后续接完全可用。

### 测试(在 xinjianbot4test, 10.134.213.164)

5 case 全绿:

| # | 输入 | 预期 | 实测 |
|---|---|---|---|
| 1 | `--task "Bad Task" "hi"` | 正则校验拒绝 | ✅ `invalid task name "Bad Task"` exit 2 |
| 2 | `"hi"`(缺 --task) | 必填校验拒绝 | ✅ `missing --task arg` exit 2 |
| 3 | `--task test-a "Write greet.py..."`(首次) | `resuming:false`,新 session | ✅ sessionId=b3c1795b,文件 1 行 |
| 4 | `--task test-a "Add second line..."`(续接) | `resuming:true`,同 sessionId | ✅ 同一 sessionId=b3c1795b,文件变 2 行 |
| 5 | `--task test-b "Do you remember greet.py?"`(切任务) | `resuming:false`,新 sessionId,不记得 test-a | ✅ sessionId=4a80992e,Claude 答 "NO" |

Wrapper 日志(pid 变但 sessionId 一致证明自动续接):
```
[17344] task=test-a resume=none         → sessionId=b3c1795b    (R3 新建)
[17407] task=test-a resume=b3c1795b     → sessionId=b3c1795b    (R4 wrapper 从磁盘自动挑)
[17469] task=test-b resume=none         → sessionId=4a80992e    (R5 不同任务独立)
```

### 已知未处理

- `docs/01-architecture.md`、`docs/02-multi-turn-mechanism.md`、`docs/03-lifecycle-and-cleanup.md`、`docs/04-e2e-validation.md` 里的命令行示例仍然用 v1 的 `--resume/--cwd`。架构和机制本身没变(仍然是 jsonl 单一真源 + cross-process resume),只是用户对接参数不同。后续可以一次性 refresh,或者随下次迭代顺手改。

---

## 1.0.0 · 2026-04-15 中午 · Initial release

**commit**: `f973674` (MR !49 第 1 个 commit)

### 动机

phase2 的 skill 用 bash + 纯文本调 `claude -p`,主 agent 只能从自然语言 stdout 启发式判断工具成功与否,容易 hallucinate,且多轮续接靠 wrapper 内部 auto-resume jsonl 猜测(phase2 §4.2 坑 6,有双刃剑问题)。

phase4 方案:走 ACP (Agent Client Protocol) 协议,结构化事件流,协议级 cancel / permission / session lifecycle。

### 产物

```
skills/delegate-to-claude-code-acp/
├── README.md                    概览
├── SKILL.md                     主 agent 加载的 skill 定义
├── install.sh                   6 步 bootstrap
├── bin/claude-as-acp           wrapper (186 行,v1 API)
└── docs/
    ├── 01-architecture.md       架构拓扑与数据流
    ├── 02-multi-turn-mechanism.md  多轮续接内部机制
    ├── 03-lifecycle-and-cleanup.md 进程生命周期 + OpenClaw 清理路径
    ├── 04-e2e-validation.md     端到端 3 轮实测证据
    └── background/              phase1-4 历史调研原稿
```

### 核心设计点

1. **跨进程 session 续接**:靠 `/root/.claude/projects/<cwd-hash>/<sid>.jsonl` 磁盘单一真源,wrapper 是短寿命进程,用完即走
2. **权限全开**:`IS_SANDBOX=1` 绕过 root 守卫 + `setSessionMode("bypassPermissions")` 协议级禁用 permission prompt,实测 Write + Bash 零 permission_request 事件
3. **Claude CLI 升级 + 路径修复**:install.sh 升级到 2.1.109,自动检测 `/usr/local/bin/claude` 是否是 stale 静态二进制并替换为 npm 版本的 symlink
4. **优雅超时**:`--timeout` 参数 + `conn.cancel` 协议级中断,JSONL 保持一致
5. **复用 OpenClaw 全部 skill 生态**:install.sh 把 OpenClaw 的 24 个 skill symlink 到 `/root/.claude/skills/`,Claude Code 立即拥有 yuque / confluence / gitlab / wecom / 等能力,零重写成本

### v1 API

```
claude-as-acp [--resume <sid>] [--cwd <path>] [--timeout <sec>] "<prompt>"
```

**参数**:
- `--resume <sid>`(可选,延续前一轮 session)
- `--cwd <path>`(可选,默认 `process.cwd()`)
- `--timeout <sec>`(可选,默认 3300)
- 位置参数 `prompt`

### 测试(在 xinjianbot4test)

3 轮端到端(见 docs/04-e2e-validation.md):
- Round 1: `--cwd /tmp/cc-e2e` 首次,写 `hello.py`,sessionId=cd649582-...
- Round 2: `--resume cd649582 --cwd /tmp/cc-e2e`,Edit 加一行
- Round 3: `--resume cd649582 --cwd /tmp/cc-e2e`,Bash 跑脚本

3 个 pid 独立(10514/10624/10721),共享同一 ccSid,跨进程 resume 工作。

主 agent 在 reply 里主动写 `CC session id 是 cd649582-...`,下一轮从自己的 reply 提取 sessionId。

### 已知风险(v1 阶段)

⚠️ **生产环境真实对话里主 agent 不稳定传 `--resume`**。3 轮 e2e 在我手工命名 session 且 prompt 明确("刚才那个 hello.py")时跑得绿,但真实用户发"继续/稍等/重新来"这种模糊指令时,主 agent 大概率选错。

→ **2.0.0 迭代解决这个问题**
