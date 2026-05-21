---
name: hermes-post-update
description: Use when the user asks to update, upgrade, check, or troubleshoot Hermes Agent itself, especially from Telegram/WeChat gateway sessions.
version: 5.0.0
author: Hermes Agent
metadata:
  hermes:
    tags: [hermes, update, maintenance, gateway, cron, doctor, changelog]
    related_skills: [hermes-agent]
---

# Hermes 更新流程

> NOTE: This skill's canonical file is at `~/.hermes/skills/hermes-update/SKILL.md` due to a directory/frontmatter name mismatch. `skill_manage` cannot resolve it by name. To edit, use `terminal` to edit the file directly, or rename the directory to `hermes-post-update`. The sibling copy at `~/.hermes/skills/hermes-post-update/SKILL.md` mirrors this file — keep both in sync until the rename happens.

## 核心原则

更新 Hermes 时要避免让当前 gateway 会话失联。先确认状态，再用**可恢复、可查看日志**的后台流程执行，最后验证版本与进程。

**Patch 处理已经引擎化**：`~/.hermes/scripts/hermes-local-patches.py` 是唯一负责 patch 恢复/验证的入口，所有手动和 cron 流程都应该调用它，**不要再嵌入内联 Python**。

## 触发条件

用户说：
- "更新 Hermes" / "升级 Hermes"
- "Hermes 有新版本吗"
- "检查 Hermes 更新"
- "Hermes update 卡住/没回报/进展如何"

## 标准流程

### 1. 先检查现状

立即运行：

```bash
hermes --version
cd ~/.hermes/hermes-agent && git fetch origin main && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short
~/.hermes/scripts/hermes-local-patches.py status
```

判断：
- `git merge-base --is-ancestor origin/main HEAD` 为真且 `hermes --version` 显示 `Up to date` → 已是最新（HEAD 可能因本地 patch commit 比 origin/main 高几格，这是正常的）。
- 有更新或 `Update available: N commits behind` → 进入后台更新。
- 有本地变更（`git status --short` 非空）→ **先 `git diff` 检查内容，不要直接交给 autostash**：
  1. **如果改动对应一个已注册的 patch（marker 在 worktree 但不在 HEAD）**：执行 `~/.hermes/scripts/hermes-local-patches.py recover`，引擎会自动 commit 这些改动作为 patch。
  2. 如果是正在用的功能/修复 → 立刻 `git commit` 成正式 commit，并在 `~/.hermes/local-patches/hermes-agent.yaml` 增加对应条目（`marker_file` / `marker_regex` / `commit_candidates: [<新 commit short hash>]` / `commit_message` / `touched_files`），下一次更新会自动恢复。
  3. 如果只是临时调试/打印 → 才让 autostash 处理，更新后复查 `git status`。
  4. 未跟踪目录（`tinker-atropos/` 这类）一般是工作目录残留，autostash 不会动它，保留即可。
- 有 `git log origin/main..HEAD` 显示的本地 commit → 核对每条 commit 都已在 patch manifest 里登记，未登记的立即补上，再继续更新。

### 2. 立即更新：优先用 cron 一次性任务

创建一次性 cron job，**deliver 用 `origin`**，不要复用自动更新任务的 WeChat/Telegram 目的地，避免发错地方。

```python
cronjob(
  action="create",
  schedule="1m",
  repeat=1,
  deliver="origin",
  name="hermes-update-now",
  enabled_toolsets=["terminal"],
  prompt=<下方 Immediate Update Prompt>
)
```

创建后告诉用户：后台任务已创建，约 1 分钟后执行。

### 3. 用户追问进展时

先查 cron 状态：

```python
cronjob(action="list")
```

若一次性任务仍是 `scheduled` 且 `last_run_at=null`：
- 说明还没被 scheduler 执行，不要说"正在更新"。
- 可以调用 `cronjob(action="run", job_id=...)` 触发下一 tick。
- 如果用户明确要立刻执行，才启动 terminal 后台兜底，并把日志路径告诉用户。

若 gateway 已重启或任务消失：
- 用 `hermes --version`、`git rev-parse HEAD origin/main`、`git status --short` 复核实际结果。
- 读取 `/tmp/hermes-update-*.log` 或任务 output 汇报。

### 4. terminal 兜底流程

仅在 cron 没跑、用户催进展、或 scheduler 异常时使用：

```bash
log=/tmp/hermes-update-$(date +%Y%m%d-%H%M%S).log
{
  echo '=== before ==='
  hermes --version || true
  echo '=== pre-update patch recovery ==='
  ~/.hermes/scripts/hermes-local-patches.py recover || true
  echo '=== update ==='
  HERMES_UPDATE_SKIP_GATEWAY_RESTART=1 hermes update --yes
  echo '=== after ==='
  hermes --version || true
  echo '=== git ==='
  cd ~/.hermes/hermes-agent && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short
  echo '=== patches ==='
  ~/.hermes/scripts/hermes-local-patches.py apply
} 2>&1 | tee "$log"
```

注意：gateway 重启后，Hermes 的 background process 句柄可能丢失；最终以日志文件和 `hermes --version` 复核为准。

### 5. 依赖安装失败时的修复（uv exclude-newer 问题）

`hermes update` 有时完成 git pull 后在依赖安装步骤报错退出，典型错误：

```
× No solution found … setuptools was filtered by `exclude-newer`
× No solution found … croniter was filtered by `exclude-newer`
```

**原因**：`pyproject.toml` 有 `[tool.uv] exclude-newer = "7 days"`，加上 aliyun 镜像对
setuptools / croniter / python-dateutil 等稳定包的时间戳陈旧，导致被过滤。

**修复**（在 terminal 兜底或手动运行时追加这一步）：

```bash
cd ~/.hermes/hermes-agent

# 绕过 exclude-newer 限制，先单独装受影响的底层包
uv pip install "setuptools>=61.0" "python-dateutil>=2.8.0" "six" \
  --exclude-newer-package "setuptools=false" \
  --exclude-newer-package "python-dateutil=false" \
  --exclude-newer-package "six=false"

uv pip install "croniter>=6.0.0,<7" \
  --exclude-newer-package "croniter=false" \
  --exclude-newer-package "python-dateutil=false"

# 重新安装整个项目
uv pip install -e . \
  --exclude-newer-package "croniter=false" \
  --exclude-newer-package "setuptools=false" \
  --exclude-newer-package "python-dateutil=false" \
  --quiet
```

⚠️ **陷阱**：uv 有时会解析到 `python-dateutil 1.5`（Python 2 时代），安装后 croniter
导入时抛 `SyntaxError`。确保安装 `>=2.8.0`，用 `uv pip show python-dateutil` 验证。

详细分析见 `references/uv-exclude-newer-workaround.md`。

## Immediate Update Prompt

用于一次性更新 cron job，必须自包含：

```text
用户要求立即更新 Hermes Agent。请执行并汇报结果；不要再创建 cron job，不要调用 send_message。

1. 记录更新前状态：
   hermes --version
   cd ~/.hermes/hermes-agent && git fetch origin main && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short
   ~/.hermes/scripts/hermes-local-patches.py status

2. 如果 origin/main 已是 HEAD 的祖先，且 hermes --version 显示 Up to date：
   直接用中文简洁汇报"已是最新"，附版本和 commit，然后结束。
   （HEAD 因本地 patch commit 比 origin/main 高几格属正常，不算 behind。）

3. 如果有未提交的本地改动：
   先跑 `~/.hermes/scripts/hermes-local-patches.py recover`，让引擎把对应 patch 改动 commit 掉。
   剩余未识别的改动在更新前先 `git diff` 检查；非临时调试就 `git commit`，再决定是否登记 manifest。

4. 如果需要更新：
   运行 `HERMES_UPDATE_SKIP_GATEWAY_RESTART=1 hermes update --yes`，完整捕获输出。

5. 更新后验证：
   hermes --version
   cd ~/.hermes/hermes-agent && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short

6. 如果依赖安装失败（setuptools/croniter 被 exclude-newer 过滤）：
   参考 skill 中"依赖安装失败时的修复"步骤手动修复，然后重新 hermes doctor --fix 验证。

7. 恢复本地专属 patches：

   ```bash
   ~/.hermes/scripts/hermes-local-patches.py apply
   ```

   引擎会按 `~/.hermes/local-patches/hermes-agent.yaml` 顺序处理每个 patch，输出形如：
   - `PATCH_OK <name>`           — marker 已在 HEAD，无需操作
   - `PATCH_RECOMMITTED <name>`  — 把 worktree 中匹配 marker 的未提交改动 commit 成 patch
   - `PATCH_RESTORED <name>`     — cherry-pick 候选 ref 成功
   - `PATCH_CONFLICT <name>`     — cherry-pick 冲突，引擎已 abort，工作区已清理；后续 patch 继续处理
   - `PATCH_MISSING_REF <name>`  — 候选 ref 都不可达，需要人工补 manifest
   最后一行是 `PATCH_SUMMARY ok=N recommitted=N restored=N conflict=N missing_ref=N skipped=N`。

   引擎**永不**因为单个 patch 失败而中断后续 patch。如果出现 CONFLICT/MISSING_REF：
   - 把对应 patch 的 manifest 条目读出来给用户看（`name` / `failure_pattern` / `upstream_convergence_hint` / `touched_files`）。
   - 如果 marker 已在 HEAD（说明上游收敛了等效修复）→ 提示用户从 manifest 里删除该条目。
   - 否则用 `git log <touched_files>` 找上游可能合并的等效 commit；解决冲突后用 `git cherry-pick <ref>` 手动重做或修订 manifest。

8. 安排延迟重启：
   `~/.hermes/scripts/hermes-auto-update-restart-gateway.sh` 默认 sleep 180 秒后执行 `hermes gateway restart`，给 cron 足够时间完成投递。
   一次性更新 cron 也应使用同样的 helper，而不是直接 `hermes gateway restart`。

9. 可选：如果 `gh` 可用，读取最新 release notes：
   gh release list --repo NousResearch/hermes-agent --limit 1
   gh release view <tag> --repo NousResearch/hermes-agent
   只提取和日常使用相关的 3-5 条。

10. 最终中文汇报：
   - 是否成功
   - 更新前后版本/commit
   - 是否仍 behind
   - patch 引擎的输出（PATCH_SUMMARY 一行 + 任何 CONFLICT/MISSING_REF 详情）
   - 如失败，给关键错误和下一步
```

## 每日自动检查任务

如果更新会重启 `hermes-gateway`，不要让 Hermes cron job 自己在 agent 进程里执行完整 `hermes update`：gateway 重启可能中断 cron session，导致 `last_run_at` 不写回、patch 恢复/验证步骤没跑完。

当前实现把 `hermes update` 放到 **OS 级 timer**（独立于 gateway）：

1. **外部 timer**：
   - **Linux** — systemd user timer `hermes-auto-update.timer` → `hermes-auto-update.service`，每天 17:00（`OnCalendar` 可改）触发 `~/.hermes/scripts/hermes-auto-update-external.sh`。
   - **macOS** — LaunchAgent `~/Library/LaunchAgents/com.hermes.auto-update.plist`（label `com.hermes.auto-update`），`StartCalendarInterval` 同样设到 17:00 触发同一份 `hermes-auto-update-external.sh`。`StartCalendarIntervalCoalesce=true` 等效 systemd 的 `Persistent=true`，错过点位机器唤醒后会补跑一次。

2. **`hermes-auto-update-external.sh` 流程**（两个平台共用一份）：
   - 锁防重入（Linux 用 `flock(1)`，macOS 用脚本内置的 `mkdir(2)` 锁 polyfill）
   - 启用 git rerere 记忆冲突解决
   - 跑 `hermes-local-patches.py recover`（commit autostash 残留）
   - `HERMES_UPDATE_SKIP_GATEWAY_RESTART=1 hermes update --yes`（macOS 上脚本顶部还 polyfill 了 `timeout(1)` 和 `date -Is`）
   - 跑 `hermes-local-patches.py apply`（cherry-pick 缺失 patch；冲突不会中断）
   - 写状态到 `~/.hermes/state/auto-update/latest.json` + 日志到 `~/.hermes/logs/auto-update/<run_id>.log`
   - 启动 `hermes-auto-update-restart-gateway.sh` 异步等 180 秒后再重启 gateway，避开投递竞态。

3. **Hermes cron reporter** `hermes-auto-update-report.py`：在 17:15 只读取 `latest.json` 并汇报：无更新输出 `[SILENT]`；有更新/失败/patch_failed 才发消息。这个 cron 不再执行 update，所以不会被 gateway restart 打断。

   reporter cron job 的 prompt 规则（`status=updated` 时）：
   - 汇报 version/head/origin、patch 验证/恢复状态、log 路径。
   - **必须额外读取 changelog**：运行 `gh release list --repo NousResearch/hermes-agent --limit 3` + `gh release view <最新tag> --repo NousResearch/hermes-agent`，提取 3-5 条相关更新内容附在报告里（标题"## 更新内容"）。gh 失败时跳过并注明"(changelog 获取失败)"。

修改了什么不要忘了：
- 把 `HERMES_UPDATE_SKIP_GATEWAY_RESTART` 本地 patch 记录进 `~/.hermes/local-patches/hermes-agent.yaml`，否则 upstream update 可能覆盖该能力。
- 自动更新所有脚本的**源文件**都在 `~/Code/hermes-skills/skills/hermes-update/scripts/`，跟 SKILL 一起 git 管理（仓库 `qiyuey/hermes-skills`）。`~/.hermes/scripts/` 下的同名文件是 symlink 或薄 wrapper（见下方"脚本布局"），改逻辑请改 repo 里的源文件。

排查时看：
- `~/.hermes/state/auto-update/latest.json` 中 `status` 和 `patch_report`（patch 引擎完整 stdout）。
- `~/.hermes/logs/auto-update/<run_id>.log` 完整 update 日志。
- `~/.hermes/logs/auto-update/<run_id>.patches.txt` 仅 patch 阶段的 stdout。
- **Linux**: `journalctl --user -u hermes-auto-update.service -e --no-pager | tail`
- **macOS**: `~/.hermes/logs/auto-update/launchd.stderr.log` 和 `launchctl print gui/$(id -u)/com.hermes.auto-update | head -50`（关注 `state` / `next run` / `last exit code`）
- `hermes cron list --all` 的 `last_delivery_error`（如果 reporter 投递失败）。

本机实现细节见 `references/cron-auto-update-delivery.md`。

## 脚本布局

所有自动更新脚本源文件统一放在 `~/Code/hermes-skills/skills/hermes-update/scripts/`，与本 skill 一起 git 管理。`~/.hermes/scripts/` 下的对应入口都指向这里：

| 入口路径（被外部调用方写死的） | 类型 | 指向 |
|---|---|---|
| `~/.hermes/scripts/hermes-local-patches.py` | symlink | `…/hermes-update/scripts/hermes-local-patches.py` |
| `~/.hermes/scripts/hermes-auto-update-external.sh` | symlink | `…/hermes-update/scripts/hermes-auto-update-external.sh` |
| `~/.hermes/scripts/hermes-auto-update-restart-gateway.sh` | symlink | `…/hermes-update/scripts/hermes-auto-update-restart-gateway.sh` |
| `~/.hermes/scripts/hermes-auto-update-report.py` | **real wrapper** | `runpy.run_path(…/hermes-update/scripts/hermes-auto-update-report.py)` |
| `~/.config/systemd/user/hermes-auto-update.{service,timer}` | rendered (Linux) | `…/hermes-update/scripts/templates/hermes-auto-update.{service,timer}` |
| `~/Library/LaunchAgents/com.hermes.auto-update.plist` | rendered (macOS) | `…/hermes-update/scripts/templates/com.hermes.auto-update.plist`（`__SCRIPT__` / `__LOG_DIR__` 由 install.sh 替换为绝对路径） |

为什么 reporter 用 wrapper 而不是 symlink：`hermes-agent/cron/scheduler.py::_run_job_script` 调用 `path.resolve()` 后做 `relative_to(scripts_dir_resolved)` 检查，故意拒绝 symlink 逃逸出 `~/.hermes/scripts/` 的脚本。Cron job `c6b8487f8eb5`（`hermes-auto-update-report`）的 `script` 字段必须是真正落在 `scripts_dir` 内的文件，所以这里留一个 4 行的 wrapper，让真实逻辑仍然在 repo 里。

修改任意脚本都在 repo 里改，然后 `cd ~/Code/hermes-skills && git add … && git commit && git push` 即可，`~/.hermes/scripts/` 不需要动。新增脚本时：
- 如果不通过 cron `script:` 字段调用 → 直接在 `~/.hermes/scripts/` 里 `ln -s "$HOME/Code/hermes-skills/skills/hermes-update/scripts/<new>" <new>` 即可（用绝对路径让 systemd/cron 也能 resolve）。
- 如果会被 cron `script:` 调用 → 在 `~/.hermes/scripts/` 写一个调用 `runpy.run_path` 的 wrapper（用 `pathlib.Path.home()` 拼路径），源文件仍放 repo。

## 全新机器安装

整套自动更新包括 4 个脚本 + 1 个 wrapper + scheduler unit（Linux 2 个 systemd unit / macOS 1 个 LaunchAgent plist）+ 1 个 cron job + 1 份 local-patches manifest + 1 个 hermes-agent 上的 patch。在一台干净的 Hermes 上启用：

```bash
# 1. 拉仓库 + 让 hermes-skills-sync 把所有 skill symlink 到 ~/.hermes/skills/
gh repo clone qiyuey/hermes-skills "$HOME/Code/hermes-skills"
# 然后让 hermes-skills-sync skill 跑一遍场景一

# 2. 安装自动更新粘合层（symlink / wrapper / scheduler unit / local-patches 模板）
#    --apply-patches 让 install.sh 直接 git am 仓库里 patches/ 下的 patch，
#    并把生成的 commit SHA 自动回填到 manifest
"$HOME/Code/hermes-skills/skills/hermes-update/scripts/install.sh" --apply-patches

# 3. 按 install.sh 末尾打印的提示，用 cronjob 工具注册 reporter cron job
#    （cron job 必须通过 hermes 接口创建，install.sh 不能直接改 jobs.json）
```

`install.sh` 通过 `uname -s` 选择 scheduler：

| 平台 | scheduler | 装到哪里 | 激活命令 |
|---|---|---|---|
| Linux | systemd --user | `~/.config/systemd/user/hermes-auto-update.{service,timer}` | `systemctl --user enable --now hermes-auto-update.timer` |
| macOS | launchd | `~/Library/LaunchAgents/com.hermes.auto-update.plist` | `launchctl bootstrap gui/$(id -u) <plist>` |
| 其他 Unix | manual | —— | 自己调度 `hermes-auto-update-external.sh` |

`install.sh` 是**幂等**的：
- 已存在的 symlink / wrapper / systemd unit / LaunchAgent plist 不会被覆盖
- 已存在的 local-patches manifest 不会被改写
- hermes-agent 里 marker 已经在 HEAD 时，patch 不会重复 `git am`
- macOS 上重复跑 `install.sh --force` 会 bootout 再 bootstrap LaunchAgent，等效 systemd 的 `daemon-reload`

不带 `--apply-patches` 也安全，install.sh 会检测 marker 缺失并在 NEXT STEPS 里给出你要手工跑的 `git am` 命令；带上 `--apply-patches` 则一步到位（同时还会把 manifest 的 `REPLACE_WITH_YOUR_COMMIT_SHORT_SHA` 占位符替换成新生成的 commit short SHA）。

仍然需要人工的一件事：**reporter cron job 注册**。install.sh 最后会打印需要执行的 `cronjob(action="create", …)` 调用，自己执行一次即可。

### Patch 文件

仓库里 `skills/hermes-update/patches/` 目录是所有上游 hermes-agent patch 的 canonical 来源，用 `git format-patch` 格式打包，可以直接 `git am` 重放出一致 commit：

- `0001-fix-update-allow-external-schedulers-to-skip-gateway-restart.patch` — `HERMES_UPDATE_SKIP_GATEWAY_RESTART` 环境变量闸门，让外部 scheduler 跳过 gateway 自动重启。`install.sh --apply-patches` 默认应用。

未来新增 patch 时遵循同样的约定（`format-patch` 输出 + manifest `commit_candidates` 占位 + install.sh 自动 am），让一行 `install.sh --apply-patches` 永远是新机器的入口。

## Patch 引擎参考

`~/.hermes/scripts/hermes-local-patches.py` 子命令：
- `status`：只检查每个 patch 的 marker 状态，不写盘。退出码 0=全部 ok，1=有未恢复 patch。
- `recover`：只把 marker 在 worktree 但不在 HEAD 的 patch commit 掉（用于 update 前清理 autostash 残留）。
- `apply`：完整流程 = recover + verify + cherry-pick missing。`--json` 选项额外输出结构化 JSON 报告。

`~/.hermes/local-patches/hermes-agent.yaml` 必备字段：
- `name`：patch 唯一标识
- `marker_file` / `marker_regex`：检测 patch 是否已应用
- `commit_candidates`：候选 ref 列表，从前往后试，第一个能 cherry-pick 的就用
- `commit_message`：cherry-pick / re-commit 用的 message
- `touched_files`：recover 时只允许这些文件被 commit（防止把无关改动卷进来）

可选字段（仅文档用）：
- `failure_pattern`：未 patch 时的可观测症状
- `upstream_convergence_hint`：上游若合入等效修复会改动哪里

`~/.hermes/local-patches/.applied-history.json` 由引擎自动维护，记录每个 patch 最近成功应用的 SHA，下次会优先尝试这些（解决"reflog GC 后旧 SHA 不可达"问题）。

## 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `skill_view("hermes-post-update")` 失败 | skill 目录名（hermes-update）和 frontmatter name（hermes-post-update）不一致 | skill_manage 无法按名字定位；用 terminal 直接编辑 `~/.hermes/skills/hermes-update/SKILL.md`，或将目录改名为 hermes-post-update |
| cron 一次性任务没回报 | 还在 scheduled、gateway 重启、或任务被旧 scheduler 状态覆盖 | 先查 cron list，再用版本/git/log 复核实际状态 |
| `hermes update` 后 process not_found | gateway 重启导致进程跟踪句柄丢失 | 读日志 + `hermes --version` 复核 |
| update 提示 local changes restored | autostash 正常恢复本地改动 | 跑 `git status --short`，再跑 `hermes-local-patches.py apply` 让 unstaged 自动归位 |
| 工作区里有未提交的"功能性"改动（不是临时调试） | 上一次会话写完忘了 commit；直接 update 会让它以 unstaged 状态留下来 | `hermes-local-patches.py recover` 自动 commit；如果不在 manifest 里就先 `git diff` 判断，再补 manifest |
| 本地 `git log origin/main..HEAD` 有 commit 但 patch manifest 没登记 | 之前手工 commit 但没维护 manifest；未来某次 cherry-pick 时找不到 ref | 更新前补全 manifest（每个本地 commit 一个条目，`commit_candidates` 填 short hash） |
| patch 引擎输出 `PATCH_CONFLICT` | cherry-pick 该 patch 时和上游 main 冲突；常见原因：上游重构了 patch 触及的函数 | 引擎已 abort，工作区干净。读 `~/.hermes/local-patches/hermes-agent.yaml` 该条目的 `upstream_convergence_hint`，定位上游重构后的新位置，手工 cherry-pick + 修改路径，commit 后下次 marker 就能匹配 |
| patch 引擎输出 `PATCH_MISSING_REF` | manifest 里所有 `commit_candidates` 都不存在（reflog GC + 上次 update 重写历史） | 用 `git log` / `gh pr view` 找最近一次成功 apply 的 SHA，补到 `commit_candidates` 头部 |
| 用户问"`.hermes` 是 git 仓库吗？"或要求清理更新残留 | `~/.hermes` 本身通常不是 git 仓库；源码仓库在 `~/.hermes/hermes-agent`，skills 可能 symlink 到 `~/Code/hermes-skills` | 先分别验证 `git rev-parse --show-toplevel`，只在确认的仓库根目录执行 `git restore`/删除 untracked；不要把 `~/.hermes` 根目录当仓库 |
| `gh release` 不可用 | gh 未安装或未认证 | 跳过 release notes，不影响更新结论 |
| **hermes update 依赖安装失败：setuptools / croniter / python-dateutil 被 exclude-newer 过滤** | `pyproject.toml` 中 `exclude-newer = "7 days"` + aliyun 镜像时间戳陈旧 | 见上方"依赖安装失败时的修复"，详见 `references/uv-exclude-newer-workaround.md` |
| `hermes-local-patches.py` 报 `PATCH_MANIFEST_ERROR pyyaml not installed` | 系统 `/usr/bin/env python3` 解析到的解释器没装 pyyaml（macOS 上 brew Python 常态） | `pip3 install --user --break-system-packages pyyaml`（macOS）或对应发行版的 `python3-yaml` 包（Linux） |
| macOS 上 `launchctl bootstrap` 报 `Service is disabled` | 之前 `launchctl disable` 过，或 SIP / TCC 拒绝 | 先 `launchctl enable gui/$(id -u)/com.hermes.auto-update`，再 `launchctl bootstrap …` |
| macOS 上 stale lock：`~/.hermes/state/auto-update/update.lock.d` 没被清理 | 上一次 external.sh 被 SIGKILL 或断电杀掉，mkdir 锁没机会 trap 释放 | `rm -rf ~/.hermes/state/auto-update/update.lock.d`（`rmdir` 删不掉，里面有 `pid` 文件）；下一次 timer 触发会自动恢复 |

## 完成前验证

必须至少验证：

```bash
hermes --version
cd ~/.hermes/hermes-agent && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short
~/.hermes/scripts/hermes-local-patches.py status
ps -ef | grep -i '[h]ermes.*gateway' | head
```

汇报时不要只说"已启动"；要说明"已启动 / 已完成 / 已是最新 / 失败"的真实状态，并引用 `PATCH_SUMMARY` 那一行。
