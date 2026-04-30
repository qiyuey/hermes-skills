---
name: hermes-post-update
description: Use when the user asks to update, upgrade, check, or troubleshoot Hermes Agent itself, especially from Telegram/WeChat gateway sessions.
version: 4.0.0
author: Hermes Agent
metadata:
  hermes:
    tags: [hermes, update, maintenance, gateway, cron, doctor, changelog]
    related_skills: [hermes-agent]
---

# Hermes 更新流程

## 核心原则

更新 Hermes 时要避免让当前 gateway 会话失联。先确认状态，再用**可恢复、可查看日志**的后台流程执行，最后验证版本与进程。

## 触发条件

用户说：
- “更新 Hermes” / “升级 Hermes”
- “Hermes 有新版本吗”
- “检查 Hermes 更新”
- “Hermes update 卡住/没回报/进展如何”

## 标准流程

### 1. 先检查现状

立即运行：

```bash
hermes --version
cd ~/.hermes/hermes-agent && git fetch origin main && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short
```

判断：
- `HEAD == origin/main` 且 `hermes --version` 显示 `Up to date` → 汇报“已是最新”，不要再执行更新。
- 有更新或版本命令提示 behind → 进入后台更新。
- 有本地变更 → 记录变更摘要；不要手动清理，交给 `hermes update` 的 autostash，更新后复查 `git status`。

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
- 说明还没被 scheduler 执行，不要说“正在更新”。
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
  echo '=== update ==='
  hermes update
  echo '=== after ==='
  hermes --version || true
  echo '=== git ==='
  cd ~/.hermes/hermes-agent && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short
} 2>&1 | tee "$log"
```

注意：gateway 重启后，Hermes 的 background process 句柄可能丢失；最终以日志文件和 `hermes --version` 复核为准。

## Immediate Update Prompt

用于一次性更新 cron job，必须自包含：

```text
用户要求立即更新 Hermes Agent。请执行并汇报结果；不要再创建 cron job，不要调用 send_message。

1. 记录更新前状态：
   hermes --version
   cd ~/.hermes/hermes-agent && git fetch origin main && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short

2. 如果 HEAD 与 origin/main 相同，且 hermes --version 显示 Up to date：
   直接用中文简洁汇报“已是最新”，附版本和 commit，然后结束。

3. 如果需要更新：
   运行 `hermes update`，完整捕获输出。不要用无限交互；如出现提示，使用默认确认。

4. 更新后验证：
   hermes --version
   cd ~/.hermes/hermes-agent && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short

5. 恢复本地专属 patch（更新后必须检查）：

   cd ~/.hermes/hermes-agent
   grep -q "GetInferenceProfile" agent/bedrock_adapter.py && echo "PATCH_OK" || echo "PATCH_MISSING"

   - PATCH_OK → 跳过
   - PATCH_MISSING → 执行恢复：

   PATCH_COMMIT=$(git log feat/bedrock-application-inference-profile-arn-support --oneline 2>/dev/null | grep "application-inference-profile ARNs" | head -1 | awk '{print $1}')
   if [ -z "$PATCH_COMMIT" ]; then
     for h in 55735c123 5f6e04569; do
       git cat-file -e "${h}^{commit}" 2>/dev/null && PATCH_COMMIT=$h && break
     done
   fi
   git cherry-pick "$PATCH_COMMIT" --no-commit && git commit -m "feat(bedrock): re-apply application-inference-profile ARN support after update"

   汇报 patch 状态：PATCH_OK / 已恢复 / 冲突（列冲突文件）

6. 可选：如果 `gh` 可用，读取最新 release notes：
   gh release list --repo NousResearch/hermes-agent --limit 1
   gh release view <tag> --repo NousResearch/hermes-agent
   只提取和日常使用相关的 3-5 条。

7. 最终中文汇报：
   - 是否成功
   - 更新前后版本/commit
   - 是否仍 behind
   - 本地变更/autostash 是否恢复或有冲突
   - 如失败，给关键错误和下一步
```

## 每日自动检查任务

可以保留一个名为 `hermes-auto-update` 的 recurring cron job。它只负责每日检查；如果无更新，输出 `[SILENT]`，避免打扰。

建议设置：
- schedule: `0 10 * * *`（北京时间 10 点）
- enabled_toolsets: `["terminal"]`
- prompt 使用上面的逻辑，但无更新时最终只输出 `[SILENT]`。

## 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `skill_view("hermes-post-update")` 失败 | skill 目录名和 frontmatter name 不一致 | 给 `~/.hermes/skills/hermes-post-update` 建兼容 symlink |
| cron 一次性任务没回报 | 还在 scheduled、gateway 重启、或任务被旧 scheduler 状态覆盖 | 先查 cron list，再用版本/git/log 复核实际状态 |
| `hermes update` 后 process not_found | gateway 重启导致进程跟踪句柄丢失 | 读日志 + `hermes --version` 复核 |
| update 提示 local changes restored | autostash 正常恢复本地改动 | 跑 `git status --short`，只在冲突时处理 |
| 用户问“`.hermes` 是 git 仓库吗？”或要求清理更新残留 | `~/.hermes` 本身通常不是 git 仓库；源码仓库在 `~/.hermes/hermes-agent`，skills 可能 symlink 到 `~/Code/hermes-skills` | 先分别验证 `git rev-parse --show-toplevel`，只在确认的仓库根目录执行 `git restore`/删除 untracked；不要把 `~/.hermes` 根目录当仓库 |
| `gh release` 不可用 | gh 未安装或未认证 | 跳过 release notes，不影响更新结论 |

## 完成前验证

必须至少验证：

```bash
hermes --version
cd ~/.hermes/hermes-agent && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short
ps -ef | grep -i '[h]ermes.*gateway' | head
```

汇报时不要只说“已启动”；要说明“已启动 / 已完成 / 已是最新 / 失败”的真实状态。
