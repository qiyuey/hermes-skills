---
name: moxt
description: 通过 Moxt CLI 操作 Workspace 文件和空间 — 列目录、读取、上传、删除、创建目录、获取分享链接、检索知识库、写回报告。用户说"上传到 Moxt"、"从 Moxt 读取"、"写入 Moxt 工作区"、"查 Moxt 知识库"时加载此 skill。
version: 1.0.0
author: Hermes Agent
metadata:
  hermes:
    tags: [moxt, workspace, file, knowledge-base]
    related_skills: [moxt-webhook]
---

# moxt

通过 `@moxt-ai/cli` 管理 Moxt Workspace 文件，支持文件读写、知识库检索、报告写回等场景。

---

## 前置准备

```bash
# 1. 安装
npm install -g @moxt-ai/cli

# 2. 认证（获取方式：moxt.ai → Settings → API Key）
export MOXT_API_KEY=<your-api-key>

# 3. 获取 Workspace ID
moxt workspace list

# 4. 查看可用空间
moxt space list -w <workspaceId>
```

遇到任何命令不确定时先 `--help`，不要猜参数。

---

## 空间选择（所有 file/space 命令通用）

| 场景 | 参数 |
|------|------|
| 个人空间（默认） | `--personal` 或省略 |
| 团队空间（按名称） | `-s <空间名>` |
| 团队空间（按 ID） | `--team-space-id <id>` |
| AI Teammate 空间 | `--teammate-id <id>` |

四种方式互斥，`-s` 按名称最直观，名称从 `moxt space list` 获取。

---

## 核心命令速查

```bash
# 列目录
moxt file list -w <wid> -s <空间名> [-p <路径，默认/>]

# 读取文件
moxt file read -w <wid> -s <空间名> -p <路径>
# 也支持直接用网页 URL：
moxt file read -u https://moxt.ai/w/<wid>/<fileId>

# 上传文件（-r 自动创建父目录）
moxt file put -w <wid> -s <空间名> -p <远端路径> -l <本地路径> [-r]

# 获取分享链接
moxt file get-url -w <wid> -s <空间名> -p <路径>

# 创建目录（-r 递归创建）
moxt file mkdir -w <wid> -s <空间名> -p <路径> [-r]

# 删除（只能删空目录）
moxt file del -w <wid> -s <空间名> -p <路径>
```

---

## 场景：知识库检索

1. `moxt file list` 浏览目录结构，定位相关文件
2. `moxt file read` 读取内容
3. 综合多个文件后回答，引用原文，找不到就说找不到
4. 需要时用 `moxt file get-url` 把链接发给用户

---

## 场景：报告写回

```bash
# 1. 生成 Markdown 到本地临时文件
report=/tmp/report-$(date +%Y-%m-%d).md

# 2. 上传（-r 确保目录存在）
moxt file put -w <wid> -s <空间名> -p "日报/$(date +%Y-%m-%d).md" -l "$report" -r

# 3. 返回可分享链接
moxt file get-url -w <wid> -s <空间名> -p "日报/$(date +%Y-%m-%d).md"
```

**命名规范**：
- 日报：`日报/YYYY-MM-DD.md`
- 周报：`周报/YYYY-WXX.md`

---

## Pitfalls

- `moxt file del` 只能删**空目录**，非空目录需先递归删内容
- `-s` 参数是空间**名称**（字符串），`--team-space-id` 才是 ID，不要混用
- 环境变量是 `MOXT_API_KEY`（CLI 文档/指南中可能写作 `MOXTAPIKEY`，以实际 CLI 读取的为准，运行 `moxt whoami` 验证认证状态）
- 上传大文件或目录不存在时加 `-r` 避免报错
