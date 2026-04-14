---
name: hermes-post-update
description: Hermes 更新流程 — 自动执行 hermes update、处理本地变更冲突、运行 hermes doctor 诊断、获取更新日志并汇报。用户说"更新 hermes"或"帮我升级 hermes"时加载此 skill。
version: 1.1.0
author: Hermes Agent
metadata:
  hermes:
    tags: [hermes, update, maintenance, post-update, doctor, changelog]
    related_skills: [hermes-agent]
---

# Hermes 更新流程

用户说"更新 hermes"、"帮我升级 hermes"、"hermes 有新版本吗"时，自动走完整个流程，无需用户手动操作。

---

## Step 1：执行 hermes update

`hermes update` 中途可能有交互确认（stash 恢复），用 `yes` 自动应答：

```bash
yes | hermes update 2>&1
```

捕获完整输出，保存为变量供后续分析。

**注意事项：**
- update 只是 git pull + pip install，不会中断当前运行的 hermes 进程，重启后新版生效
- `yes |` 会自动对所有 [Y/n] 提示回答 Y，包括"Restore local changes now?"
- 如果 update 本身报错（网络超时、git 权限等），告知用户具体原因并停止

---

## Step 2：分析 update 输出

从输出中提取关键信息：

**版本/提交数：**
- 找 `Found N new commit(s)` → 记录 commit 数量，用于后续 git log 范围
- 如果含 `Already up to date` → 告知用户已是最新，仍继续执行 Step 3 做健康检查

**是否有本地变更冲突：**

| 输出特征 | 含义 |
|---------|------|
| `Working tree reset to clean state` | hermes 自动 reset，stash 保留但未应用，工作区干净 |
| `Restoring local changes...` + 无 CONFLICT | stash 已成功 pop，工作区干净 |
| `CONFLICT` | stash 恢复出现冲突，需要进一步处理 |

**如果出现 CONFLICT（自动 Y 后仍冲突）：**

```bash
cd ~/.hermes/hermes-agent

# 查看冲突文件
git status

# 逐个分析：本地改动 vs 上游新代码
# 正确的 stash diff 语法：
git diff "stash@{0}^1"..stash@{0} -- <conflicted_file>

# 查看上游对同一文件的变更（N = update 输出里的 commit 数量）
git diff HEAD~N..HEAD -- <conflicted_file>
```

判断策略：
- 上游已用更好方式覆盖本地改动 → 直接丢弃 stash：`git stash drop stash@{0}`
- 本地有独特自定义修改 → 说明冲突内容，提示用户手动 cherry-pick，给出具体命令

---

## Step 3：运行 hermes doctor --fix

直接用 `--fix` 一步完成诊断 + 修复：

```bash
hermes doctor --fix 2>&1
```

解读输出：

| 输出特征 | 处理方式 |
|---------|---------|
| ✅ 全部通过 | 告知用户一切正常 |
| `Fixed N issue(s)` | 说明修复了什么 |
| ⚠️ 警告 | 说明是否影响使用，一般无需处理 |
| ❌ 错误 | 说明原因，给出修复命令 |
| `Config version outdated` | --fix 会自动处理 |
| `Missing dependency` | 给出安装命令 |

---

## Step 4：获取更新日志

从 update 输出里拿到 commit 数量 N，直接用 git log（本机网络通常无法访问 GitHub Releases）：

```bash
cd ~/.hermes/hermes-agent
git log --pretty=format:"%h %ad %s" --date=short -N
```

对输出按类型归类整理：
- `feat:` → 新功能
- `fix:` → Bug 修复
- `chore:/docs:/refactor:` → 次要变更（可简略）

重点突出：新平台、新工具、Breaking Change、影响日常使用的 fix。

---

## Step 5：发送报告

整合成一条消息（纯文本，不用 Markdown）：

```
【Hermes 更新完成】

更新时间：{当前时间}
新增提交：{N} 个（或"已是最新版本"）

━━ 本地变更 ━━
{无冲突 / stash 已自动恢复 / 具体冲突处理结果}

━━ 健康诊断 ━━
{doctor 摘要：修复项 + 剩余警告}

━━ 本次更新亮点 ━━
新功能：
• {feat 条目，控制在 5 条以内最重要的}

Bug 修复：
• {fix 条目，影响日常使用的优先}

━━ 提示 ━━
更新已生效，如果你在用 gateway，运行 /restart 让新版本完全生效。
```

---

## 注意事项

- hermes 安装目录：`~/.hermes/hermes-agent/`
- 更新是通过 git pull 实现的，本地 changes 会被 stash
- 配置文件和密钥不在 git 管控内，更新不影响
- `hermes doctor --fix` 比单独跑 `hermes config migrate` 更完整，会同时清理废弃配置项
- GitHub Releases 在部分网络环境下会被拦截（Blocked: private network），直接用 git log 即可
- 如果用户的 hermes 不是 git 安装的，`git` 命令不适用，改用 `hermes --version` 对比版本号
- doctor 的 Warning 大多不影响使用；tinker-atropos、rl、moa 相关警告可忽略
