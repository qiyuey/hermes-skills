---
name: hermes-post-update
description: Use when the user asks to update, upgrade, check, or troubleshoot Hermes Agent itself, especially from Telegram/WeChat gateway sessions.
version: 4.1.0
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
   运行 `hermes update`，完整捕获输出。
   - **不要用 `yes | hermes update`**，避免未来 hermes update 增加危险确认时误操作。
   - 如出现普通确认提示，可接受默认确认；但绝对不要自动丢弃本地变更、不要 drop stash、不要 reset/clean。

4. 更新后验证：
   hermes --version
   cd ~/.hermes/hermes-agent && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short

5. 恢复本地专属 patches（更新后必须检查，不能只检查单个硬编码 patch）：

   - 首选读取 `~/.hermes/local-patches/hermes-agent.yaml` 作为本机 patch 清单；这个文件在源码仓库外，能跨 `hermes update` 保留。
   - 每个 patch 条目至少包含：`name`、`marker_file`、`marker_regex`、`commit_candidates`、`commit_message`。
   - 对每个条目执行：
     1. `grep -Eq "$marker_regex" "$marker_file"` 成功 → `PATCH_OK <name>`，跳过。
     2. marker 缺失 → 按 `commit_candidates` 顺序找第一个存在的 commit/branch/ref。
     3. 执行 `git cherry-pick <ref> --no-commit`，然后 `git commit -m "$commit_message"`。
     4. 如 cherry-pick 冲突：停止后续 patch，汇报 `PATCH_CONFLICT <name>`、冲突文件、保留工作区等待人工处理；不要自动 abort。
   - 如果 manifest 不存在，使用兼容 fallback：只检查 Bedrock ARN patch（marker: `GetInferenceProfile` in `agent/bedrock_adapter.py`），但汇报“未发现通用 patch manifest，建议补充”。

   参考执行脚本：

   ```bash
   cd ~/.hermes/hermes-agent
   manifest="$HOME/.hermes/local-patches/hermes-agent.yaml"
   if [ -f "$manifest" ]; then
     python - <<'PY'
import subprocess, sys, yaml, pathlib, re, os
repo = pathlib.Path.home()/'.hermes/hermes-agent'
manifest = pathlib.Path.home()/'.hermes/local-patches/hermes-agent.yaml'
data = yaml.safe_load(manifest.read_text()) or {}
patches = data.get('patches', [])
for p in patches:
    name = p['name']
    marker_file = repo / p['marker_file']
    marker_regex = p['marker_regex']
    if marker_file.exists() and re.search(marker_regex, marker_file.read_text(errors='ignore'), re.M):
        print(f'PATCH_OK {name}')
        continue
    ref = None
    for cand in p.get('commit_candidates', []):
        r = subprocess.run(['git','cat-file','-e',f'{cand}^{{commit}}'], cwd=repo)
        if r.returncode == 0:
            ref = cand; break
    if not ref:
        print(f'PATCH_MISSING_REF {name} candidates={p.get("commit_candidates", [])}')
        sys.exit(2)
    r = subprocess.run(['git','cherry-pick',ref,'--no-commit'], cwd=repo)
    if r.returncode != 0:
        subprocess.run(['git','diff','--name-only','--diff-filter=U'], cwd=repo)
        print(f'PATCH_CONFLICT {name}')
        # 不要自动 abort/reset，保留现场等待人工处理
        sys.exit(r.returncode)
    msg = p.get('commit_message') or f'local: re-apply {name} after update'
    subprocess.check_call(['git','commit','-m',msg], cwd=repo)
    print(f'PATCH_RESTORED {name} from {ref}')
PY
   else
     echo "PATCH_MANIFEST_MISSING $manifest"
     if grep -q "GetInferenceProfile" agent/bedrock_adapter.py; then
       echo "PATCH_OK bedrock-application-inference-profile-arns"
     else
       PATCH_COMMIT=$(git log pr-16805-bedrock-arn --oneline 2>/dev/null | grep "application-inference-profile ARNs" | head -1 | awk '{print $1}')
       if [ -z "$PATCH_COMMIT" ]; then
         for h in 222e272ec 07c096e57 55735c123 5f6e04569; do
           git cat-file -e "${h}^{commit}" 2>/dev/null && PATCH_COMMIT=$h && break
         done
       fi
       if [ -n "$PATCH_COMMIT" ]; then
         git cherry-pick "$PATCH_COMMIT" --no-commit && git commit -m "feat(bedrock): re-apply application-inference-profile ARN support after update"
       else
         echo "PATCH_MISSING_REF bedrock-application-inference-profile-arns"
       fi
     fi
   fi
   ```

   汇报所有 patch 状态：`PATCH_OK` / `PATCH_RESTORED` / `PATCH_CONFLICT` / `PATCH_MISSING_REF`，并说明是否使用了 manifest 或 fallback。

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
