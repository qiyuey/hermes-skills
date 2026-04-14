---
name: hermes-post-update
description: Hermes 更新流程 — 通过 cron job 在后台执行，避免 gateway 重启中断当前会话。用户说"更新 hermes"或"帮我升级 hermes"时加载此 skill。
version: 3.0.0
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

## 首次运行：询问并创建定时任务

检查是否已存在名为 `hermes-auto-update` 的 cron job（用 cronjob list 查看）。

**如果不存在**，询问用户两个问题：

1. 是否创建每日自动检查更新的定时任务？
2. 更新报告发送到哪个渠道？列出当前会话上下文中已连接的平台供选择（如 Telegram、微信等），让用户选择或输入。

拿到用户确认后，创建 cron job：

```
cronjob(
  action="create",
  schedule="0 2 * * *",  # UTC 02:00 = 北京时间 10:00
  repeat=forever,
  deliver=<用户选择的渠道>,
  name="hermes-auto-update",
  prompt=<见下方 Cron Prompt 模板>
)
```

**如果已存在**，跳过询问，直接触发一次性更新（见下方"立即执行一次更新"）。

---

## 立即执行一次更新

无论是否创建了定时任务，用户当下请求更新时，都需要立刻触发一次更新。用 cron job 一次性执行：

```
cronjob(
  action="create",
  schedule="1m",
  repeat=1,
  deliver=<用户选择的渠道，或已有定时任务的 deliver 渠道>,
  name="hermes-update-now",
  prompt=<见下方 Cron Prompt 模板>
)
```

创建完后立刻回复用户：
"已在后台启动更新，gateway 重启后会把结果发回来，稍等片刻。"

---

## Cron Prompt 模板

以下 prompt 用于定时任务和一次性更新，必须自包含：

```
检查 Hermes 是否有新版本，有则执行更新，完成后汇报结果。

## Step 1：记录当前 commit

cd ~/.hermes/hermes-agent && git fetch origin main 2>&1 && git rev-parse HEAD && git rev-parse origin/main

对比本地和远程 commit hash：
- 相同 → 已是最新，静默结束，不发送报告。
- 不同 → 继续执行后续步骤。

## Step 2：执行更新

yes | hermes update 2>&1

捕获完整输出。

## Step 3：获取更新日志

gh release list --repo NousResearch/hermes-agent --limit 1

取最新 release tag，然后：

gh release view <tag> --repo NousResearch/hermes-agent

从 release notes 中提取：
- Highlights 部分，最多 5 条最重要的新功能
- 关键 Bug 修复（影响日常使用的优先）

## Step 4：运行健康诊断

hermes doctor --fix 2>&1

## Step 5：处理本地变更冲突

分析 Step 2 的输出：
- 无 CONFLICT → 记录"无冲突"
- 有 CONFLICT → 分析冲突文件：
  git diff "stash@{0}^1"..stash@{0} -- <file>
  git diff HEAD~N..HEAD -- <file>
  判断是否可以直接 drop stash，给出结论。

## Step 6：发送报告

整合成纯文本（不用 Markdown）：

【Hermes 自动更新完成】

更新时间：{当前时间}

━━ 本地变更 ━━
{无冲突 / stash 已自动恢复 / 具体冲突处理结果}

━━ 健康诊断 ━━
{doctor 摘要：修复项 + 剩余警告（忽略 tinker-atropos、rl、moa 相关警告）}

━━ 本次更新亮点 ━━
新功能：
• {来自 gh release notes，最多 5 条}

Bug 修复：
• {影响日常使用的优先，最多 3 条}

━━ 提示 ━━
Gateway 已重启，新版本已生效。
```

---

## 注意事项

- hermes 安装目录：`~/.hermes/hermes-agent/`
- 更新日志用 `gh release view` 读取，不用 git log
- `hermes doctor --fix` 比单独跑 `hermes config migrate` 更完整
- doctor Warning 大多不影响使用：tinker-atropos、rl、moa 相关警告可忽略
- 配置文件和密钥不在 git 管控内，更新不影响
- 定时任务 deliver 渠道由用户首次选择，不硬编码

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
