---
name: hermes-post-update
description: Hermes 更新后处理流程 — 分析 hermes update 输出，处理本地变更冲突，运行 hermes doctor 诊断，并从官方获取更新日志推送给用户。在用户完成 hermes update 后加载此 skill。
version: 1.0.0
author: Hermes Agent
metadata:
  hermes:
    tags: [hermes, update, maintenance, post-update, doctor, changelog]
    related_skills: [hermes-agent]
---

# Hermes 更新后处理流程

每次运行 `hermes update` 之后，按以下步骤完整处理。

## 触发条件

用户提供了 `hermes update` 的运行输出，或明确说"刚刚更新了 hermes"。

---

## Step 1：分析 hermes update 输出

收到用户粘贴的更新日志后，重点检查：

**是否有本地变更冲突？** 关键词：
- `local changes` / `local modifications`
- `stash` / `merge conflict`
- `would be overwritten`
- `diverged`
- `CONFLICT`

**判断逻辑：**

```
如果输出包含 "Already up to date" 且无冲突警告
  → 告知用户"无需额外处理，版本已是最新"，仍继续执行 Step 2

如果输出包含本地变更相关警告
  → 进入 Step 1b：处理本地变更

如果更新失败（exit code 非零 / 明显错误信息）
  → 告知用户更新失败原因，尝试指导修复
```

### Step 1b：处理本地变更（如有）

**首先判断 hermes update 对工作区的处理结果：**

- 如果输出含 "Working tree reset to clean state" → hermes 已自动 reset，stash 保留但未应用，工作区干净
- 如果输出含 "Restoring local changes..." 且成功 → stash 已 pop，直接检查状态
- 如果输出含 CONFLICT 且无 reset 信息 → 工作区有冲突文件，需手动处理

```bash
cd ~/.hermes/hermes-agent
git status
git stash list
```

**查看 stash 里的具体改动（注意语法）：**

```bash
# 正确语法：用 ^ 找 stash 基准提交
git diff "stash@{0}^1"..stash@{0} -- <file>

# 错误语法（不支持）：git stash show -p stash@{0} -- file
```

**判断本地改动是否可以丢弃：**

对比 stash 里的改动和上游新代码，如果：
- 上游已用更好的方式实现了相同功能 → 可以直接丢弃
- 本地有独特的个性化修改（如 openrouter 过滤等自定义功能）→ 需要手动 cherry-pick 到新版本

```bash
# 安全丢弃 stash（确认不需要后）
git stash drop stash@{0}

# 查看上游对同一文件的变更
git diff HEAD~<commit_count> HEAD -- <conflicted_file>
```

**注意：** hermes 的配置文件和密钥文件（config.yaml、.env）不在 git 仓库管控内，不会被 update 覆盖，无需担心。

---

## Step 2：运行 hermes doctor

```bash
hermes doctor
```

解读输出：

| 输出特征 | 处理方式 |
|---------|---------|
| ✅ 全部通过 | 告知用户一切正常 |
| ⚠️ 警告（Warning） | 说明影响，是否需要处理 |
| ❌ 错误（Error/Failed） | 说明原因，执行修复步骤 |
| Missing dependency | 给出安装命令 |
| Config issue | 运行 `hermes config migrate` |
| API key missing | 提醒用户配置对应的 key |

**修复命令（优先用 --fix，比单独 migrate 更完整）：**
```bash
hermes doctor --fix            # 自动修复所有可修复项（推荐，含 config 迁移 + 清理废弃 key）
hermes config migrate          # 仅迁移 config 版本（doctor --fix 已包含此步骤）
hermes config check            # 检查配置问题
```

---

## Step 3：获取更新日志

从官方权威来源获取 changelog。按以下优先级：

### 方式 A：GitHub Releases（最权威，但在部分网络环境下会被拦截）

```
web_extract(["https://github.com/NousResearch/hermes-agent/releases"])
```

如果返回 "Blocked: URL targets a private or internal network address" → 直接跳到方式 C。

### 方式 B：CHANGELOG.md（如 A 失败且网络可用）

```
web_extract(["https://raw.githubusercontent.com/NousResearch/hermes-agent/main/CHANGELOG.md"])
```

或本地文件：
```bash
head -100 ~/.hermes/hermes-agent/CHANGELOG.md
```

### 方式 C：git log（兜底，在受限网络下直接用此方式）

从 update 输出中提取 commit 数量（"Found N new commit(s)"），然后：

```bash
cd ~/.hermes/hermes-agent
# 用实际 commit 数量替换 N
git log --pretty=format:"%h %ad %s" --date=short -N
```

对 git log 输出按类型归类：feat / fix / docs / chore，提炼出用户关心的内容。

---

## Step 4：整理并发送报告

将以下内容整合成一条消息发给用户（**不使用 Markdown**，Telegram 纯文本）：

```
【Hermes 更新报告】

版本：{旧版本} → {新版本}（或"已是最新"）
更新时间：{当前时间}

━━ 本地变更检查 ━━
{无变更 / 或具体冲突内容 + 处理结果}

━━ 健康诊断（hermes doctor）━━
{doctor 输出摘要，分 ✅ / ⚠️ / ❌ 列出}

━━ 更新内容 ━━
版本 {版本号}（{日期}）
{release notes 关键内容，控制在 500 字内，突出新功能/Breaking Changes/Bug Fix}

官方发布页：https://github.com/NousResearch/hermes-agent/releases
```

---

## 注意事项

- hermes 安装目录：`~/.hermes/hermes-agent/`
- 更新是通过 git pull 实现的，本地 changes 会被 stash
- 配置文件和密钥不在 git 管控之外，更新不影响
- 如果用户的 hermes 不是 git 安装的（脚本直接安装），`git` 命令可能不适用，改用 `hermes --version` 对比版本号
- doctor 发现的大多数 Warning 不影响正常使用，但 Error 需要修复后重启 gateway
