---
name: hermes-post-update
description: Use when the user asks to update, upgrade, check, or troubleshoot Hermes Agent itself, especially from Telegram/WeChat gateway sessions.
version: 4.2.0
author: Hermes Agent
metadata:
  hermes:
    tags: [hermes, update, maintenance, gateway, cron, doctor, changelog]
    related_skills: [hermes-agent]
---

# Hermes 更新流程

> NOTE: This skill's canonical file is at `~/.hermes/skills/hermes-update/SKILL.md` due to a directory/frontmatter name mismatch. `skill_manage` cannot resolve it by name. To edit, use `terminal` to edit the file directly, or rename the directory to `hermes-post-update`. This copy is a mirror — see `references/` for session-specific pitfalls.

## 核心原则

更新 Hermes 时要避免让当前 gateway 会话失联。先确认状态，再用**可恢复、可查看日志**的后台流程执行，最后验证版本与进程。

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
```

判断：
- `HEAD == origin/main` 且 `hermes --version` 显示 `Up to date` → 汇报"已是最新"，不要再执行更新。
- 有更新或版本命令提示 behind → 进入后台更新。
- 有本地变更 → **先 `git diff` 检查内容，不要直接交给 autostash**。autostash 会让未提交的工作区改动在更新后被 stash pop 回来，但如果改动其实是一个**正在使用的本地能力**（例如本机给 `/model picker` 加的过滤、某个 patch 的扩展），它就会以 unstaged 状态继续游荡，下一次更新或某次 `git restore` 时丢失。判断流程：
  1. `git diff --stat` + `git diff` 查看每个修改文件实际变更。
  2. 如果是正在用的功能/修复 → 立刻 `git commit` 成正式 commit，并在 `~/.hermes/local-patches/hermes-agent.yaml` 增加对应条目（`marker_file` / `marker_regex` / `commit_candidates: [<新 commit short hash>]` / `commit_message`），下一次更新会自动恢复。
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
  echo '=== update ==='
  hermes update
  echo '=== after ==='
  hermes --version || true
  echo '=== git ==='
  cd ~/.hermes/hermes-agent && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short
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

2. 如果 HEAD 与 origin/main 相同，且 hermes --version 显示 Up to date：
   直接用中文简洁汇报"已是最新"，附版本和 commit，然后结束。

3. 如果需要更新：
   运行 `hermes update`，完整捕获输出。不要用无限交互；如出现提示，使用默认确认。

4. 更新后验证：
   hermes --version
   cd ~/.hermes/hermes-agent && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short

5. 如果依赖安装失败（setuptools/croniter 被 exclude-newer 过滤）：
   参考 skill hermes-post-update 中"依赖安装失败时的修复"步骤手动修复，然后重新 hermes doctor --fix 验证。

6. 恢复本地专属 patches（更新后必须检查，不能只检查单个硬编码 patch）：

   - 首选读取 `~/.hermes/local-patches/hermes-agent.yaml` 作为本机 patch 清单；这个文件在源码仓库外，能跨 `hermes update` 保留。
   - 每个 patch 条目至少包含：`name`、`marker_file`、`marker_regex`、`commit_candidates`、`commit_message`。
   - 对每个条目执行：
     1. `grep -Eq "$marker_regex" "$marker_file"` 成功 → `PATCH_OK <name>`，跳过。
     2. marker 缺失 → 按 `commit_candidates` 顺序找第一个存在的 commit/branch/ref。
     3. 执行 `git cherry-pick <ref> --no-commit`，然后 `git commit -m "$commit_message"`。
     4. 如 cherry-pick 冲突：停止后续 patch，汇报 `PATCH_CONFLICT <name>`、冲突文件、保留工作区等待人工处理；不要自动 abort。
   - 如果 manifest 不存在，使用兼容 fallback：只检查 Bedrock ARN patch（marker: `GetInferenceProfile` in `agent/bedrock_adapter.py`），但汇报"未发现通用 patch manifest，建议补充"。

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
         for h in 7229d0608 55735c123 5f6e04569 222e272ec 07c096e57; do
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

7. 可选：如果 `gh` 可用，读取最新 release notes：
   gh release list --repo NousResearch/hermes-agent --limit 1
   gh release view <tag> --repo NousResearch/hermes-agent
   只提取和日常使用相关的 3-5 条。

8. 最终中文汇报：
   - 是否成功
   - 更新前后版本/commit
   - 是否仍 behind
   - 本地变更/autostash 是否恢复或有冲突
   - 如失败，给关键错误和下一步
```

## 每日自动检查任务

如果更新会重启 `hermes-gateway`，不要让 Hermes cron job 自己在 agent 进程里执行完整 `hermes update`：gateway 重启可能中断 cron session，导致 `last_run_at` 不写回、patch 恢复/验证步骤没跑完。

推荐拆成两段：
1. **外部 systemd user timer** 在 10:00 执行独立脚本（例如 `~/.hermes/scripts/hermes-auto-update-external.sh`）：检查版本、运行 `hermes update`、按 `~/.hermes/local-patches/hermes-agent.yaml` 恢复/验证本地 patches、写 `~/.hermes/state/auto-update/latest.json` 和日志。脚本必须用 lock 防重入，并把 `origin/main` 已包含在本地 patch commit 的情况视为 up-to-date，而不是要求 `HEAD == origin/main`。
2. **Hermes cron reporter** 在 10:15 只读取状态文件并汇报：无更新输出 `[SILENT]`；有更新/失败才发消息。这个 cron 不再执行 update，所以不会被 gateway restart 打断。

如果暂时不拆分，也可以保留一个名为 `hermes-auto-update` 的 recurring cron job，但要知道它在 gateway 重启时可能无法完成收尾。

建议 reporter 设置：
- schedule: `15 10 * * *`（北京时间 10:15）
- script: `hermes-auto-update-report.py`
- prompt：若脚本输出 `[SILENT]` 则最终严格输出 `[SILENT]`；JSON status=updated/failed 时简洁汇报版本、head/origin、git_status、log 路径。

### cron 自动更新投递坑：不要在投递前重启 gateway

如果 cron prompt 里直接运行 `hermes update`，更新成功后它会自动重启 gateway。由于 cron scheduler 本身跑在 gateway 进程里，重启发生在 cron 最终回复投递之前时，Telegram/Weixin 发送经常失败：

```text
cannot schedule new futures after interpreter shutdown
```

本机修复方式：
1. `hermes update` 支持环境变量 `HERMES_UPDATE_SKIP_GATEWAY_RESTART=1`（本地 patch）。cron 自动更新必须这样调用，而不是裸跑 `hermes update`：
   ```bash
   HERMES_UPDATE_SKIP_GATEWAY_RESTART=1 hermes update --yes 2>&1
   ```
2. cron 最终报告生成和投递前不要重启 gateway。
3. 更新验证、本地 patch 恢复都完成后，只安排延迟重启，不等待完成：
   ```bash
   nohup "$HOME/.hermes/scripts/hermes-auto-update-restart-gateway.sh" >/tmp/hermes-auto-update-delayed-restart.nohup 2>&1 &
   ```
4. `~/.hermes/scripts/hermes-auto-update-restart-gateway.sh` 默认 sleep 180 秒后执行 `hermes gateway restart`，给 cron 足够时间完成投递。
5. 把 skip-restart 本地 patch 记录进 `~/.hermes/local-patches/hermes-agent.yaml`，marker 用 `HERMES_UPDATE_SKIP_GATEWAY_RESTART` in `hermes_cli/main.py`；否则下一次 upstream update 可能覆盖该能力，cron 又退回投递竞态。

排查时看：`hermes cron list --all` 的 `last_delivery_error`，以及 `~/.hermes/cron/output/<job_id>/` 中是否已经生成报告但没投递。若用户感觉很久没收到更新通知，先确认 `deliver=origin` 的 `origin.platform/chat_id`，再检查输出文件是否非 `[SILENT]`：报告生成但 `last_delivery_error` 存在时，问题在投递而不是更新逻辑。

本机实现细节见 `references/cron-auto-update-delivery.md`。

## 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `skill_view("hermes-post-update")` 失败 | skill 目录名（hermes-update）和 frontmatter name（hermes-post-update）不一致 | skill_manage 无法按名字定位；用 terminal 直接编辑 `~/.hermes/skills/hermes-update/SKILL.md`，或将目录改名为 hermes-post-update |
| cron 一次性任务没回报 | 还在 scheduled、gateway 重启、或任务被旧 scheduler 状态覆盖 | 先查 cron list，再用版本/git/log 复核实际状态 |
| `hermes update` 后 process not_found | gateway 重启导致进程跟踪句柄丢失 | 读日志 + `hermes --version` 复核 |
| update 提示 local changes restored | autostash 正常恢复本地改动 | 跑 `git status --short`，只在冲突时处理 |
| 工作区里有未提交的"功能性"改动（不是临时调试） | 上一次会话写完忘了 commit；直接 update 会让它以 unstaged 状态留下来，patch manifest 也没登记，下次更新或 restore 时丢失 | **先 `git diff` 判断性质**：是功能/修复就 commit + 登记 manifest（见"标准流程 §1"），是临时调试才交给 autostash |
| 本地 `git log origin/main..HEAD` 有 commit 但 patch manifest 没登记 | 之前手工 commit 但没维护 manifest；未来某次 cherry-pick 时找不到 ref | 更新前补全 manifest（每个本地 commit 一个条目，`commit_candidates` 填 short hash） |
| 用户问"`.hermes` 是 git 仓库吗？"或要求清理更新残留 | `~/.hermes` 本身通常不是 git 仓库；源码仓库在 `~/.hermes/hermes-agent`，skills 可能 symlink 到 `~/Code/hermes-skills` | 先分别验证 `git rev-parse --show-toplevel`，只在确认的仓库根目录执行 `git restore`/删除 untracked；不要把 `~/.hermes` 根目录当仓库 |
| `gh release` 不可用 | gh 未安装或未认证 | 跳过 release notes，不影响更新结论 |
| **hermes update 依赖安装失败：setuptools / croniter / python-dateutil 被 exclude-newer 过滤** | `pyproject.toml` 中 `exclude-newer = "7 days"` + aliyun 镜像时间戳陈旧 | 见上方"依赖安装失败时的修复"，详见 `references/uv-exclude-newer-workaround.md` |

## 完成前验证

必须至少验证：

```bash
hermes --version
cd ~/.hermes/hermes-agent && git rev-parse --short HEAD && git rev-parse --short origin/main && git status --short
ps -ef | grep -i '[h]ermes.*gateway' | head
```

汇报时不要只说"已启动"；要说明"已启动 / 已完成 / 已是最新 / 失败"的真实状态。
