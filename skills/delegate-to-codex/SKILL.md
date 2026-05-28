---
name: delegate-to-codex
description: >
  Hermes 委托 OpenAI Codex CLI 执行编码任务（含 harness 工程多步骤工作流），跑完后能在
  同一会话中继续对话改进。触发词：委托 codex、让 codex 跑、codex 执行、用 codex 改、
  codex resume、跑 harness、跑 financial 流水线、QC fail 后续改、Codex CLI 集成。
  适用场景：（1）单次任务委托——feature/refactor/PR review；（2）harness 工程——多 Task
  流水线（如 financial 项目的 initiating-coverage / analyze-earnings），每个 Task 独立
  thread，QC fail 在原 thread 内 resume 修；（3）并行批处理——worktree 多 issue 同时改。
  不适用场景：Hermes 自己可以直接跑的简单脚本/单文件改动。
version: 2.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Codex, OpenAI, Coding-Agent, Harness, Delegation, Resume]
    related_skills: [hermes-agent]
---

# delegate-to-codex

Hermes 不亲自跑代码任务，而是**委托给 Codex CLI**。Codex 在隔离的子进程里执行，
Hermes 通过 JSONL 流监督进度、抽取 `thread_id`、必要时用 `codex exec resume` 在同一
会话内续接对话改进。

> **核心心智模型**：Codex 是"承包商"，Hermes 是"项目经理"。PM 不下场写代码，PM
> 给清楚的需求 + 验收标准（schema/QC），看产出，不合格就让承包商在原会话里改。

## 适用判断

| 应当委托给 Codex | 不该委托 |
|---|---|
| 多文件 feature 开发、refactor | 单行/几行修改（Hermes patch 工具更快）|
| harness 工程多 Task 流水线 | 简单 shell/python 一次性脚本 |
| 长时间运行（>2 分钟）的代码任务 | 文档润色、命名建议（不写代码）|
| 跑完可能需要"再改一版"的迭代任务 | 不可逆的破坏性操作（rm -rf 等）|

## 前置条件

- `codex --version` 可执行（≥ 0.30.0 才有 `exec resume`，建议 ≥ 0.125）
- 认证：`~/.codex/auth.json`（OAuth 登录）或 `OPENAI_API_KEY`（API key）
- **Codex 默认要求 git repo**——`runs/`/`earnings/` 这种已在 git 仓库内的目录天然满足；
  纯临时任务用 `--skip-git-repo-check` 或 `cd $(mktemp -d) && git init`

## 关键 CLI 能力（v0.30+ 都有）

| 能力 | 命令片段 | 用途 |
|---|---|---|
| 非交互执行 | `codex exec "<prompt>"` | 一次性任务，跑完退出 |
| JSONL 事件流 | `--json` | stdout 输出每行一个 JSON 事件，可解析进度 |
| 续接会话 | `codex exec resume <thread_id> "<prompt>"` | 在原会话内追加新指令 |
| 续接最近 | `codex exec resume --last "<prompt>"` | 不需要 ID，但只能跟最近一次 |
| 沙箱 | `-s workspace-write` / `--full-auto` | 写权限范围 |
| 工作目录 | `-C <dir>` | 指定 codex 的工作根目录 |
| 跳过 git 检查 | `--skip-git-repo-check` | 临时目录用 |
| 结构化产物 | `--output-schema schema.json` | 强制最终消息符合 JSON Schema |
| 最终消息落盘 | `-o <file>` | 把最终 assistant message 写到文件 |
| 不持久化会话 | `--ephemeral` | 不写 `~/.codex/sessions/`，无法 resume |

⚠️ **不要用 `--ephemeral`**——它会让你失去 resume 续接能力，违背本 skill 设计目标。

⚠️ **不要用 PTY**（旧版本 skill 的做法过时）——`codex exec --json` 设计为非交互流式，
PTY 反而会污染 JSONL（混入 ANSI 转义码）、让 `terminal()` 无法干净抓取 stdout。

## JSONL 事件 cheatsheet

`codex exec --json` stdout 每行一个 JSON 对象。解析时关心这些类型即可：

| 事件 | 关键字段 | 含义 |
|---|---|---|
| `thread.started` | `thread_id` | **会话 ID，立刻存下来用于后续 resume** |
| `turn.started` | — | 一轮模型推理开始 |
| `turn.completed` | `usage.{input,output}_tokens` | 一轮结束（成功），含 token 用量 |
| `turn.failed` | `error.message` | 一轮失败（流断、超时等）|
| `item.completed type=agent_message` | `item.text` | 模型最终回复（用户级答案）|
| `item.completed type=command_execution` | `item.command`、`item.exit_code`、`item.status` | codex 执行了什么命令、退出码 |
| `item.completed type=file_change` | `item.changes[].path`、`kind`(add/update/delete) | 改了哪些文件 |
| `item.completed type=mcp_tool_call` | `item.server`、`item.tool` | 调用了哪个 MCP 工具 |
| `error` (top-level) | `message` | 流级错误（"Reconnecting..." 是非致命）|

**最小可用解析**（bash + jq）：

```bash
# 抽 thread_id（永远是第一行）
THREAD_ID=$(head -1 task.jsonl | jq -r 'select(.type=="thread.started") | .thread_id')

# 看本轮改了哪些文件
jq -r 'select(.type=="item.completed" and .item.type=="file_change") | .item.changes[] | "\(.kind)\t\(.path)"' task.jsonl

# 拿最终回复
jq -r 'select(.type=="item.completed" and .item.type=="agent_message") | .item.text' task.jsonl | tail -1

# 判断是否成功
jq -r 'select(.type=="turn.completed" or .type=="turn.failed") | .type' task.jsonl | tail -1
# 输出 turn.completed = 成功，turn.failed = 失败
```

## 模式 1：单次委托（最常用）

委托一个独立任务，用完即走。**仍然存 thread_id**，因为可能要续改。

```bash
# Hermes 调用（terminal 工具，foreground，timeout 给宽裕）
codex exec --json --full-auto \
  -C ~/Code/myproject \
  "添加深色模式开关到 settings 页面" \
  > /tmp/codex-darkmode.jsonl 2>&1

# 抽会话 ID
THREAD_ID=$(head -1 /tmp/codex-darkmode.jsonl | jq -r '.thread_id')
echo "thread: $THREAD_ID"

# 看改了什么
jq -r 'select(.type=="item.completed" and .item.type=="file_change") | .item.changes[] | "\(.kind)\t\(.path)"' /tmp/codex-darkmode.jsonl

# 验收：跑测试 / lint / 构建
cd ~/Code/myproject && npm test
```

**Hermes 端长任务**：用 `terminal(background=true, notify_on_complete=true)` 启动，
拿到 session_id 后用 `process(action="poll" / "log")` 监控。**不需要 PTY**。

## 模式 2：跑完发现问题 → resume 续改（核心场景）

```bash
# 步骤 1：第一次委托（同模式 1）
codex exec --json --full-auto -C ~/Code/myproject \
  "实现登录表单的客户端校验" \
  > /tmp/login-validate.jsonl

THREAD_ID=$(head -1 /tmp/login-validate.jsonl | jq -r '.thread_id')

# 步骤 2：Hermes 跑验收
cd ~/Code/myproject && npm test 2>&1 | tee /tmp/test-output.log
# 假设：test fail，邮箱正则漏了 + 号

# 步骤 3：在【同一会话】内续改——codex 还记得它刚才改了什么
# ⚠️ resume 子命令【不接受 -C/--cd】！必须先 cd 到目标目录再调用
cd ~/Code/myproject
codex exec resume "$THREAD_ID" --json --full-auto \
  "测试输出如下：\n$(cat /tmp/test-output.log)\n邮箱正则需要支持 + 号（如 a+b@example.com），请修复并重跑测试" \
  > /tmp/login-validate-fix.jsonl

# 验证 resume 后的产出
jq -r 'select(.type=="item.completed" and .item.type=="file_change") | .item.changes[] | "\(.kind)\t\(.path)"' /tmp/login-validate-fix.jsonl
cd ~/Code/myproject && npm test
```

**为什么必须存 thread_id**：`codex exec resume --last` 看似省事，但只续"最近一次"。
如果 Hermes 同时委托过多个任务（甚至别的项目）、或中间又跑了别的 codex 命令，`--last`
会续到错的会话。**永远存显式 ID。**

**resume 的 prompt 写法**：
- ✅ 给具体证据：测试输出、QC 报告、错误堆栈贴进 prompt
- ✅ 指明要改的产物路径："请修复 `runs/X/Y/task1.json` 第 23 行的 cogs 字段"
- ❌ 模糊："不太对，再改一下"
- ❌ 隐含上下文："还记得我们刚才说的吗"——记得，但你给原文更稳

## 模式 3：harness 工程委托（多 Task 流水线）

适用 financial 项目的 `initiating-coverage` / `analyze-earnings` 这类**多 Task 串行 + 每步
QC 硬停止**的工作流。**每个 Task 一个独立 thread**（独立上下文，token 预算清爽），
QC fail 时在该 Task 的 thread 内 resume 修，QC pass 才进入下一个 Task。

```bash
RUN_DIR="runs/英伟达/$(date +%Y%m%d-%H%M)"
mkdir -p "$RUN_DIR"
cd ~/Code/financial

# ============ Task 1: 公司研究 ============
codex exec --json --full-auto \
  -C "$(pwd)" \
  --output-schema schemas/task1.schema.json \
  -o "$RUN_DIR/task1.json" \
  "执行 initiating-coverage Task 1（公司研究）for NVDA。最终输出符合 schemas/task1.schema.json，写入 $RUN_DIR/task1.json。" \
  > "$RUN_DIR/task1.jsonl"

T1=$(head -1 "$RUN_DIR/task1.jsonl" | jq -r '.thread_id')
echo "$T1" > "$RUN_DIR/.task1_thread"   # 存 thread_id 供后续 resume

# QC-1 验收
python3 scripts/qc_1.py "$RUN_DIR/task1.json"
QC1_EXIT=$?

# 如果 QC fail，在原 thread 内 resume 修，最多 3 轮
# 注意：resume 子命令不接受 -C，必须靠当前 cwd（脚本开头已 cd 到 financial repo）
for attempt in 1 2 3; do
  if [ $QC1_EXIT -eq 0 ]; then break; fi
  echo "QC-1 fail, attempt $attempt to fix in same thread..."
  codex exec resume "$T1" --json --full-auto \
    "QC-1 报告如下，请修复 $RUN_DIR/task1.json 后重新输出：
$(python3 scripts/qc_1.py "$RUN_DIR/task1.json" 2>&1)" \
    > "$RUN_DIR/task1.fix$attempt.jsonl"
  python3 scripts/qc_1.py "$RUN_DIR/task1.json"
  QC1_EXIT=$?
done

[ $QC1_EXIT -ne 0 ] && { echo "Task 1 give up after 3 fix attempts"; exit 1; }

# ============ Task 2: 财务建模 ============（独立 thread，不 resume Task 1）
codex exec --json --full-auto -C "$(pwd)" \
  --output-schema schemas/task2.schema.json \
  -o "$RUN_DIR/task2.json" \
  "执行 initiating-coverage Task 2（财务建模），消费 $RUN_DIR/task1.json，写入 $RUN_DIR/task2.json" \
  > "$RUN_DIR/task2.jsonl"

T2=$(head -1 "$RUN_DIR/task2.jsonl" | jq -r '.thread_id')
echo "$T2" > "$RUN_DIR/.task2_thread"
# 同样的 QC + resume 修复循环...
```

**核心原则**：
1. **每个 Task = 独立 thread**——上下文干净，token 预算独立，单点失败不污染下游
2. **thread_id 必须落盘**（如 `.task1_thread`）——Hermes 重启或下次会话仍能 resume
3. **QC 硬停止**——QC fail → resume 修 → 再 QC，最多 N 轮（建议 3）；超过则交还人工
4. **Task 间通过文件传递**（`task1.json` → `task2.json`），不通过会话上下文
5. **`--output-schema` + `-o`** 让最终产物结构强制对齐 schema，跟 financial 项目的
   `schemas/*.schema.json` 天然契合

**绝不**：用一个 thread 串跑 Task 1-5。token 会爆、上下文会乱、失败定位极难。

## 模式 4：并行批处理（worktree）

并行修多个 issue、并行 review 多个 PR。**每个并行任务独立 thread**。

```bash
# 创建 worktree
git -C ~/Code/myproject worktree add -b fix/78 /tmp/wt-78 main
git -C ~/Code/myproject worktree add -b fix/99 /tmp/wt-99 main

# 并行启动（terminal background=true，注意 notify_on_complete=true）
codex exec --json --full-auto -C /tmp/wt-78 \
  "修复 issue #78：<描述>。完成后 git commit。" \
  > /tmp/wt-78.jsonl &

codex exec --json --full-auto -C /tmp/wt-99 \
  "修复 issue #99：<描述>。完成后 git commit。" \
  > /tmp/wt-99.jsonl &

wait

# 各自抽 thread_id 备查
T78=$(head -1 /tmp/wt-78.jsonl | jq -r '.thread_id')
T99=$(head -1 /tmp/wt-99.jsonl | jq -r '.thread_id')

# push + PR
git -C /tmp/wt-78 push -u origin fix/78
gh pr create --repo user/repo --head fix/78 --title "fix: ..." --body "..."

# 清理
git -C ~/Code/myproject worktree remove /tmp/wt-78
```

## 沙箱选择

| 模式 | 含义 | 何时用 |
|---|---|---|
| `-s read-only` | 只能读 | review/审计任务 |
| `-s workspace-write`（=`--full-auto`）| 能写 workspace 内文件，网络受限 | **默认**：feature/refactor/harness |
| `-s danger-full-access` | 全权 | 临时实验，确认环境隔离 |
| `--dangerously-bypass-approvals-and-sandbox` | 跳过一切 | 仅在外部已沙箱的 CI 里 |

## 认证排错

- `OPENAI_API_KEY` 未设 ≠ 没认证。检查 `~/.codex/auth.json` 是否存在
- Hermes 自身的 `model.provider: openai-codex` 用 `~/.hermes/auth.json`，跟独立的 codex CLI
  用的 `~/.codex/auth.json` **是两套**。它们互不干扰
- 出现 `Not authenticated` → `codex login` 重新走 OAuth；CI 用 `codex login --api-key "$KEY"`

## 常见坑

1. **`codex exec resume` 不接受 `-C`/`--cd`**（实测确认）：只有主命令 `codex exec` 能传
   `-C`。resume 必须先 `cd` 到目标目录再调用，否则报 `unexpected argument '-C' found`
2. **Codex 在非 git 目录拒绝运行**：要么 `--skip-git-repo-check`，要么 `git init`
3. **`exec resume` 必须紧跟 thread_id（或 `--last`）**：不带就是启新会话，浪费上下文
4. **`--output-schema` 严格但脆弱**：模型偶尔吐不符合 schema 的 JSON，需要 resume 让它修；
   schema 越复杂越容易失败，建议从最小必填 schema 起步
5. **JSONL 里的 `error` 不一定致命**：`"Reconnecting... 1/5"` 是流重连，等就好；
   只有 `turn.failed` / 进程非零退出才算真失败
6. **会话 JSONL 落在 `~/.codex/sessions/YYYY/MM/DD/`**：长期跑产生几百 MB 时
   codex 自身可能卡顿，定期归档或删除老会话
7. **不要混用 `--ephemeral` 和 resume**：ephemeral 不写会话文件，resume 找不到
8. **bash 里给 codex 的 prompt 包含 shell 特殊字符**（反引号、$、!）：用单引号包裹，
   或用 `--` 分隔参数，或写到文件用 `codex exec - < prompt.txt`
9. **小改动可能不触发 `file_change` 事件**（实测发现）：codex 直接用 patch 工具改时，
   JSONL 不一定有 `file_change`。验收时不要只看事件，也要看实际文件落盘状态

## 验证清单（写完 Codex 委托脚本后必跑）

- [ ] 第一次调用拿到了 `thread_id` 并落盘（`echo $T > .thread`）
- [ ] resume 命令用了显式 ID，不是 `--last`
- [ ] JSONL 解析覆盖了 `turn.failed` 失败分支
- [ ] 长任务用 `terminal(background=true, notify_on_complete=true)`，不阻塞 Hermes
- [ ] harness 模式下每个 Task 独立 thread，不串
- [ ] QC fail → resume 循环有最大次数上限，不会无限重试

## 与 financial harness 的对接清单

如果你正在把 `~/Code/financial` 的工作流委托给 codex：

1. **公司名/期次** 来自 `schemas/report.schema.json` 的白名单，prompt 里写中文公司名
2. **Task 1-5 各自独立 thread_id**，存到 `runs/{公司}/{datetime}/.taskN_thread`
3. **QC 脚本**（`qc_1.py` … `qc_5.py`）由 Hermes 跑，不让 codex 自己 QC 自己
4. **Task 6 独立评审**（架构约束规定必须新上下文 subagent）→ 用全新 `codex exec`，
   不要 resume Task 1-5 的任何 thread
5. **earnings sidecar** 跨 Task 共享通过文件，不通过会话——符合 financial 项目原则

## 不该用本 skill 的场景

- 单 grep / sed / find——Hermes 自己的 search_files/patch 工具更快
- 写一句话、改一行——直接 patch
- 问设计/架构问题——你自己想，或问用户，别让 codex 替你思考全局
- 调用别的 LLM——用 Hermes 的 model_router 或 web_search

---

**版本变更（v2.0.0）**

- 移除 `pty=true` 强制要求——`exec --json` 设计为非交互，PTY 反而污染输出
- 新增"模式 2：resume 续改"——核心场景，用 `codex exec resume <thread_id>`
- 新增"模式 3：harness 工程委托"——多 Task 串行 + QC 循环
- 新增 JSONL 事件 cheatsheet 和 jq 解析模板
- 新增 `--output-schema` / `-o` 用法
- 删除 `--yolo`（旧别名，新版用 `--dangerously-bypass-approvals-and-sandbox`）
