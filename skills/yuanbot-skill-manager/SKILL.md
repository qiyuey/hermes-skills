---
name: yuanbot-skill-manager
description: 当用户提到查找 skill、安装 skill、上传 skill、想要某个功能的 skill 但当前没有时，引导用户使用 AI 门户（ai.zhenguanyu.com）搜索、安装、上传 Skill。也覆盖用户明确提到 Rush 平台或 reskill 的场景——这两个工具对应的 skill 已在 AI 门户上架，统一引导从这里下载。
---

# Skill 管理

引导用户通过**小猿 AI 门户**（https://ai.zhenguanyu.com/）查找、安装、上传 Skill。

`aip-cli` 是 AI 门户的命令行工具，让用户在终端里完成 skill 的搜索、安装、上传和管理。

## When to Use

满足以下**任一**场景即可触发：

1. **查找 skill**：
   - "有没有 XXX 功能的 skill？"
   - "能不能用 skill 做 XXX？"
   - 当前 agent 已知 skills 无法满足用户需求
2. **安装 skill**：用户提到要安装某个 skill / 把某个 skill 加进来
3. **上传 / 发布 skill**：用户提到要把本地 skill 发出去、分享给别人、上传到平台
4. **用户明确提到 Rush 平台 / rush.zhenguanyu.com / reskill**：参见下方"Rush / reskill 场景"

---

## 快速开始（3 步）

```bash
# 1. 安装 CLI
npm install -g @yuanli/aip-cli --registry=http://npm.zhenguanyu.com

# 2. 配置 Key（先到 https://ai.zhenguanyu.com/#/personal-center/key → 个人 Key 创建，把 Key 复制下来）
aip auth key set aip_sk_xxxxxxxxxxxxxxxx

# 3. 验证
aip auth whoami
```

---

## 1. 安装

```bash
npm install -g @yuanli/aip-cli --registry=http://npm.zhenguanyu.com
aip -V   # 验证安装
```

## 2. 认证配置

CLI 通过 Personal Key 认证，请求带 `Authorization: Bearer <key>`。

### 获取并配置 Key

Key 只能在网页端申请。请用户访问下面的地址创建 Key，然后把 Key 发给你：

> https://ai.zhenguanyu.com/#/personal-center/key → 个人 Key → 创建

拿到 Key 后配置到本地：

```bash
aip auth key set aip_sk_xxxxxxxxxxxxxxxx  # 配置到本地
aip auth whoami                           # 验证身份
```

### Key 管理

```bash
aip auth key status      # 当前 Key 状态
aip auth key list        # 列出所有 Key
aip auth key delete <id> # 删除指定 Key
aip auth key clear       # 清除本地存储
```

### 切换环境

```bash
aip auth host                                  # 查看当前 API 地址
aip auth host https://ai.zhenguanyu.com        # 切换
```

## 3. Skill 管理

### 搜索与浏览

```bash
aip skill list                              # 列出所有可见 Skill
aip skill list --tag CLI工具                 # 按标签过滤
aip skill list --visibility org_private      # 按可见性过滤
aip skill list --org-id 42                   # 按团队过滤
aip skill search "日志查询"                   # 搜索
aip skill show my-skill --version 1.0.0      # 查看详情
aip skill versions <id>                      # 版本历史
aip skill tags                               # 列出所有标签
```

网页端浏览：https://ai.zhenguanyu.com/#/assets/skills

### 安装

**OpenClaw 是非交互环境，必须避免任何交互式提示**。两个关键点：

1. **必须显式指定 `--ide openclaw`**，否则会卡在 IDE 选择提示。
2. **必须用 `--batch`（即使只装一个 skill）**，否则当 skill 有多版本时会卡在版本选择提示。`--batch` 模式自动选最新版。

```bash
# 标准安装：单个 skill 也走 --batch
aip skill install --batch my-skill --ide openclaw

# 同时装多个
aip skill install --batch a b c --ide openclaw

# 强制覆盖已有
aip skill install --batch my-skill --ide openclaw --force

# 需要指定特定版本时（绕开 --batch，但显式给版本号也不会卡）
aip skill install my-skill --ide openclaw --version 1.0.0
```

`--ide` 可选值：`cursor` / `claude` / `openclaw`。当前 agent 运行在 OpenClaw 上时，**始终用 `openclaw`**。

#### 在 Hermes 中安装

Hermes 不识别 `--ide` 参数，需用 `--dest` 直接指定目录。**`--dest` 必须精确到 skill 子目录**，不能指向 `~/.hermes/skills/` 根目录（否则报"目录已存在"）：

```bash
# 安装单个 skill 到 Hermes
aip skill install <name> --dest ~/.hermes/skills/<name> --latest --force

# 示例
aip skill install yuanbot-skill-manager --dest ~/.hermes/skills/yuanbot-skill-manager --latest --force
```

批量安装时 `--batch` 不支持 `--dest`，需逐个执行或写循环：

```bash
for skill in foo bar baz; do
  aip skill install "$skill" --dest ~/.hermes/skills/"$skill" --latest --force
done
```

### 上传与更新

**默认标签**：上传时必须始终包含 `YuanBot` 和 `OpenClaw` 两个标签。用户额外指定的标签追加在后面。

```bash
# 从本地目录上传（目录需包含 SKILL.md）
# --tags 必须包含 YuanBot,OpenClaw，用户指定的标签追加在后面
aip skill upload ./my-skill \
  --name my-skill --version 1.0.0 \
  --description "我的 Skill" --tags "YuanBot,OpenClaw,工具,效率" \
  --visibility public

# 从 Git 仓库上传
aip skill upload-git \
  --url https://gitlab-ee.zhenguanyu.com/org/repo/-/tree/master/skills/my-skill \
  --name my-skill --version 1.0.0

# 更新（版本号需递增）
aip skill update <id> ./my-skill --version 1.1.0
```

可见性：`public` / `org_private`（团队私有，需 `--org-id`） / `private`（仅自己）。

### 其他操作

```bash
aip skill like <id>                          # 点赞
aip skill delete <id>                        # 删除当前版本
aip skill delete-all-versions <id>           # 删除全部版本
aip skill import-to-org <id> --org-id 42     # 导入到团队
aip skill import-to-personal <id>            # 导入到个人空间
aip skill detach <id>                        # 脱离源资产关联
```

## 4. 实用技巧

### JSON 输出

所有 list / show 命令支持 `--json`：

```bash
aip skill list --json | jq '.[].name'
aip skill list --json | jq 'sort_by(-.download_count) | .[0]'
```

### 项目初始化脚本

```bash
#!/bin/bash
# setup-agent-skills.sh — 新成员一键初始化
aip auth key set "$AIP_KEY"
aip skill install --batch octopus-log-query db-usage console-fdc conan-http-api-spec --ide openclaw --force
echo "✔ 初始化完成"
```

### CI/CD 中自动上传

```yaml
# saber.yml 或 GitLab CI
- aip auth key set $AIP_DEPLOY_KEY
- aip skill upload ./skills/my-skill --name my-skill --version $CI_COMMIT_TAG --tags "YuanBot,OpenClaw" --visibility org_private --org-id 42
```

## 5. 命令速查

| 场景 | 命令 |
|---|---|
| 安装 CLI | `npm i -g @yuanli/aip-cli --registry=http://npm.zhenguanyu.com` |
| 配置凭证 | `aip auth key set <key>` |
| 验证身份 | `aip auth whoami` |
| 搜索 Skill | `aip skill search "关键词"` |
| 安装 Skill（OpenClaw） | `aip skill install --batch <name> --ide openclaw` |
| 批量安装 Skill（OpenClaw） | `aip skill install --batch <name1> <name2> ... --ide openclaw` |
| 安装 Skill（Hermes） | `aip skill install <name> --dest ~/.hermes/skills/<name> --latest --force` |
| 上传 Skill | `aip skill upload <path> --name <n> --version <v>` |
| JSON 输出 | 任意命令加 `--json` |

---

## Rush / reskill 场景

当用户**明确**提到 Rush 平台、`rush.zhenguanyu.com`、reskill CLI、reskill find/install 等：

不要让用户去装 reskill。这两个工具对应的 skill 已经在 AI 门户上架，统一通过 AI 门户安装：

| 用户意图 | 安装命令 |
|---------|---------|
| 用 Rush / reskill 查找 skill | `aip skill install --batch rush-find-skills --ide openclaw` |
| 学习 reskill CLI 用法、发布 skill 到 Rush | `aip skill install --batch rush-reskill-usage --ide openclaw` |

安装后，对应 skill 内部按 reskill 的方式工作（registry 仍是 `https://rush.zhenguanyu.com/`），但发现和分发的入口收敛到 AI 门户。

---

## 常见场景示例

### 场景 1：用户问"有没有数据库相关的 skill"

```bash
aip skill search "database"
aip skill show <id>
aip skill install --batch <name> --ide openclaw
```

### 场景 2：用户问"怎么把我写的 skill 发出去"

```bash
cd /path/to/my-skill   # 目录必须包含 SKILL.md
aip skill upload . \
  --name my-skill --version 1.0.0 \
  --description "..." --tags "YuanBot,OpenClaw,tag1,tag2" --visibility public
```

### 场景 3：用户说"我想用 reskill 找 skill"

直接告诉用户：

> reskill 对应的 skill 已经在 AI 门户上架，建议从这里安装：
> ```bash
> aip skill install --batch rush-find-skills --ide openclaw    # Rush registry 查找
> aip skill install --batch rush-reskill-usage --ide openclaw  # reskill CLI 用法
> ```

---

## Troubleshooting

### 认证失败

```bash
aip auth whoami           # 看当前是否登录
aip auth key set <key>    # 重新设置
```

### 安装失败

```bash
aip skill show <id>   # 确认 skill 存在
```

---

## Resources

- **平台主页**：https://ai.zhenguanyu.com/
- **CLI 文档**：https://ai.zhenguanyu.com/#/docs
- **Skill 市场**：https://ai.zhenguanyu.com/#/assets/skills
- **个人中心**：https://ai.zhenguanyu.com/#/personal-center/key
