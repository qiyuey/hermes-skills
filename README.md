# hermes-skills（已归档）

> [!IMPORTANT]
> **本仓库已停止维护，并已归档。**
>
> 个人 Agent 工作流已经全面迁移到 **Codex**；Codex 已完全替代 Hermes，因此这里的 Hermes skills、plugins 和相关配置不会再更新，也不再接受 Issue 或 Pull Request。

以下内容仅作为历史资料保留。

Personal [Hermes Agent](https://github.com/hermesagent/hermes) configuration repository by [@qiyuey](https://github.com/qiyuey).

包含两类 hermes 个人化产物:

- **`skills/`** — Reusable procedures that extend Hermes. API endpoints, proven workflows, tool quirks, user-specific conventions. Hermes automatically loads relevant skills based on task context.
- **`plugins/`** — User-installed Hermes plugins (model providers, image gen backends, etc.). Mirrors `~/.hermes/plugins/<category>/<name>/` structure.

两类都通过 [`hermes-skills-sync`](./skills/hermes-skills-sync/SKILL.md) skill 管理 — clone + per-item symlink + git pull/push 工作流.

## Install

```bash
hermes skills tap add qiyuey/hermes-skills
```

Then install any skill:

```bash
hermes skills install book-meeting-room
```

或者跑 `hermes-skills-sync` skill 一次性同步所有 skills 和 plugins.

## Skills

| Skill | Description |
|-------|-------------|
| [book-meeting-room](./skills/book-meeting-room/SKILL.md) | 自动抢预会议室系统 meeting-room.zhenguanyu.com，支持查询空闲会议室、高频 cron 狙击抢占、弹性时长/容量、0点黄金窗口放行 |
| [hermes-skills-sync](./skills/hermes-skills-sync/SKILL.md) | 管理本仓库 — 首次安装（clone + symlink）、更新（git pull）、把本地修改的 skill / plugin 推送回仓库 |
| [hermes-setup-company-model](./skills/hermes-setup-company-model/SKILL.md) | 帮助公司同事在 macOS 上安装 Hermes Agent，并配置使用公司 Model 平台（model.zhenguanyu.com） |
| [wireguard](./skills/wireguard/SKILL.md) | 本机 WireGuard 隧道以 LaunchDaemon 形式运行，直接驱动 wireguard-go + wg + ifconfig + route；用 launchctl 管理启停/重启/状态 |

## Plugins

| Plugin | Category | Description |
|--------|----------|-------------|
| [company_model](./plugins/model-providers/company_model/) | model-providers | 公司 Model 平台 (model.zhenguanyu.com) 一体化插件 — chat completion (claude / gpt / deepseek / kimi / qwen / mimo), image gen (gpt-image-2), TTS (qwen3-tts-flash), ASR (qwen3-asr-flash) |

## Structure

```
hermes-skills/
├── skills/                            # 每个子目录 symlink 到 ~/.hermes/skills/<name>/
│   ├── book-meeting-room/
│   ├── hermes-skills-sync/
│   ├── hermes-setup-company-model/
│   └── wireguard/
└── plugins/                           # 每个子目录 symlink 到 ~/.hermes/plugins/<category>/<name>/
    └── model-providers/
        └── company_model/
```

Each skill contains a `SKILL.md` with trigger conditions, step-by-step instructions, and pitfalls, plus optional `scripts/` and `references/` directories.

Each plugin follows hermes plugin conventions (`plugin.yaml` manifest + `__init__.py` entry).
