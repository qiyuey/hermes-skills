---
name: hermes-post-update
description: Hermes 更新流程 — 通过 cron job 在后台执行，避免 gateway 重启中断当前会话。用户说"更新 hermes"或"帮我升级 hermes"时加载此 skill。
version: 2.0.0
author: Hermes Agent
metadata:
  hermes:
    tags: [hermes, update, maintenance, post-update, doctor, changelog]
    related_skills: [hermes-agent]
---

# Hermes 更新流程

用户说"更新 hermes"、"帮我升级 hermes"、"hermes 有新版本吗"时，**必须通过 cron job 在后台执行**，不能直接在当前会话中运行 `hermes update`。

原因：`hermes update` 会重启 gateway 进程，导致当前会话中断，后续消息全部丢失。cron job 是独立子进程，不受 gateway 重启影响，完成后主动把结果 deliver 回用户。

---

## 执行方式

直接用 cronjob 工具创建一次性任务，prompt 包含完整的更新逻辑（见下方模板），deliver 设为 origin（回到当前 chat）：

```
cronjob(
  action="create",
  schedule="1m",   # 立刻跑，1分钟后执行
  repeat=1,        # 只跑一次
  deliver="origin",
  name="hermes-update",
  prompt=<见下方 cron prompt 模板>
)
```

创建完 cron job 后，立刻回复用户：
"已在后台启动更新，gateway 重启后会把结果发回这里，稍等片刻。"

---

## Cron Prompt 模板

以下是传给 cron job 的 prompt，必须自包含，不依赖当前会话上下文：

```
执行 Hermes 更新流程，完成后把结果发回这个 chat。

## Step 1：记录当前版本

cd ~/.hermes/hermes-agent && git rev-parse HEAD

## Step 2：执行更新

yes | hermes update 2>&1

捕获完整输出。

## Step 3：检查是否有新提交

git rev-parse HEAD

对比 Step 1 的 commit hash：
- 相同 → 已是最新，记录"无新提交"
- 不同 → 继续后续步骤

## Step 4：获取更新日志（用 gh，不用 git log）

gh release list --repo NousResearch/hermes-agent --limit 1

取最新 release tag，然后：

gh release view <tag> --repo NousResearch/hermes-agent

从 release notes 中提取本次更新亮点（Highlights 部分，控制在 5 条以内最重要的），以及关键 Bug 修复。

## Step 5：运行健康诊断

hermes doctor --fix 2>&1

## Step 6：处理本地变更冲突

分析 Step 2 的输出：
- "Already up to date" → 无需处理
- "CONFLICT" → 分析冲突文件：
  git diff "stash@{0}^1"..stash@{0} -- <file>
  git diff HEAD~N..HEAD -- <file>
  判断是否可以直接 drop stash，或需提示用户手动处理。

## Step 7：发送报告

整合成纯文本报告（不用 Markdown）：

【Hermes 更新完成】

更新时间：{当前时间}
新增提交：{N 个 / 已是最新版本}

━━ 本地变更 ━━
{无冲突 / stash 已自动恢复 / 具体冲突处理结果}

━━ 健康诊断 ━━
{doctor 摘要：修复项 + 剩余警告}

━━ 本次更新亮点 ━━
新功能：
• {来自 gh release notes，最多 5 条}

Bug 修复：
• {影响日常使用的优先}

━━ 提示 ━━
Gateway 已重启，新版本已生效。
```

---

## 注意事项

- hermes 安装目录：`~/.hermes/hermes-agent/`
- 更新日志优先用 `gh release view` 读取，不用 git log（内容更结构化）
- `hermes doctor --fix` 比单独跑 `hermes config migrate` 更完整
- doctor Warning 大多不影响使用：tinker-atropos、rl、moa 相关警告可忽略
- cron job 的 deliver 必须设为 origin，确保报告回到用户所在的 chat
- 配置文件和密钥不在 git 管控内，更新不影响

## 修改 skill 后的同步流程

每次修改本地 skill 后，必须同时推送到远程仓库 qiyuey/hermes-skills：

```bash
# 仓库如未克隆则先克隆
gh repo clone qiyuey/hermes-skills /tmp/hermes-skills

# 拉取最新（避免冲突）
cd /tmp/hermes-skills && git pull

# 复制本地 skill 到仓库
cp ~/.hermes/skills/hermes-post-update/SKILL.md /tmp/hermes-skills/skills/hermes-post-update/SKILL.md

# 提交推送
git add skills/hermes-post-update/SKILL.md
git commit -m "feat(hermes-post-update): <描述变更内容>"
git push
```
