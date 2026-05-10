---
name: moxt
description: 通过 Moxt CLI 操作 Workspace 文件和空间 — 列目录、读取、上传、删除、创建目录、获取分享链接、检索知识库、写回报告。用户说"上传到 Moxt"、"从 Moxt 读取"、"写入 Moxt 工作区"、"查 Moxt 知识库"时加载此 skill。
version: 1.1.0
author: Hermes Agent
metadata:
  hermes:
    tags: [moxt, workspace, file, knowledge-base]
    related_skills: [moxt-webhook]
    required_environment_variables:
      - name: MOXT_API_KEY
        description: Moxt API Key，在 moxt.ai → Settings → API Key 生成
    required_commands:
      - moxt
---

# moxt

通过 `@moxt-ai/cli` 管理 Moxt Workspace 文件，支持文件读写、知识库检索、报告写回等场景。

---

## 前置准备

```bash
# 1. 安装 CLI
npm install -g @moxt-ai/cli

# 2. 认证（moxt.ai → Settings → API Key）
export MOXT_API_KEY=<your-api-key>

# 3. 验证认证
moxt whoami

# 4. 发现 Workspace ID（新环境必做，不能假设已知）
moxt workspace list
# 输出示例：
# 6grrdzpd    yuchuanbj's Workspace
# 1x39doum    Bolt

# 5. 发现各 Workspace 下的空间名称和 ID（后续操作的基础）
moxt space list -w <workspaceId>
# 输出含 TYPE / TEAM_SPACE_ID / TEAMMATE_ID / NAME
# 把需要操作的空间名称或 ID 记录下来
```

遇到任何命令不确定时先 `--help`，不要猜参数。

---

## 空间选择（所有 file 命令通用）

四种方式互斥，优先用名称（`-s`）最直观：

| 场景 | 参数 |
|------|------|
| 按空间名称（个人或团队均可） | `-s <空间名>` |
| 个人空间（显式） | `--personal` |
| 团队空间（按 ID） | `--team-space-id <id>` |
| AI Teammate 空间 | `--teammate-id <id>` |

> `-s` 接受的是 `moxt space list` 输出中 NAME 列的值，个人空间名称形如 `于川's momo`，团队空间名称形如 `General`。

---

## 核心命令速查

```bash
# 列目录
moxt file list -w <wid> -s <空间名> [-p <路径，默认/>]

# 读取文件（按路径）
moxt file read -w <wid> -s <空间名> -p <路径>
# 或直接用网页 URL（fileId ≠ 路径，是文件的唯一 ID）：
moxt file read -u https://moxt.ai/w/<workspaceId>/<fileId>

# 上传文件（-r 自动创建父目录，推荐默认加）
moxt file put -w <wid> -s <空间名> -p <远端路径> -l <本地路径> -r

# 获取可分享的浏览器链接
moxt file get-url -w <wid> -s <空间名> -p <路径>

# 创建目录（-r 递归创建）
moxt file mkdir -w <wid> -s <空间名> -p <路径> [-r]

# 删除文件或空目录
moxt file del -w <wid> -s <空间名> -p <路径>

# 查看 Workspace 成员
moxt members -w <wid>
```

---

## 场景：知识库检索

1. `moxt file list` 浏览目录结构，定位相关文件
2. `moxt file read` 读取内容，可跨多个文件综合
3. 引用原文回答，找不到就说找不到
4. 需要时用 `moxt file get-url` 把链接发给用户

---

## 场景：报告写回

```bash
# 1. 生成 Markdown 到本地临时文件
report=/tmp/report-$(date +%Y-%m-%d).md

# 2. 上传（-r 确保目录自动创建）
moxt file put -w <wid> -s <空间名> \
  -p "日报/$(date +%Y-%m-%d).md" \
  -l "$report" -r

# 3. 返回可分享链接
moxt file get-url -w <wid> -s <空间名> -p "日报/$(date +%Y-%m-%d).md"
```

**命名规范**：
- 日报：`日报/YYYY-MM-DD.md`
- 周报：`周报/YYYY-WXX.md`

---

## Pitfalls

- **新环境必须先发现 ID**：`moxt workspace list` → `moxt space list`，不能硬编码或假设 workspace/space ID 已知
- **`-s` 接受名称，`--team-space-id` 接受 ID**，两者不能互换；名称含空格时加引号
- **`moxt file del` 只能删空目录**，非空目录需先递归删内容再删目录
- **`-u <url>` 中的 fileId 是文件唯一 ID**，不是路径，从网页 URL 复制获得
- **上传时默认加 `-r`**，否则远端目录不存在会报错
- **`moxt members` 子命令**存在但帮助文档较少，不确定时 `moxt members --help`
