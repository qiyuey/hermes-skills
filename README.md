# hermes-skills

Personal [Hermes Agent](https://github.com/hermesagent/hermes) skills collection by [@qiyuey](https://github.com/qiyuey).

Skills are reusable procedures that extend Hermes — API endpoints, proven workflows, tool quirks, and user-specific conventions. Hermes automatically loads relevant skills based on task context.

## Install

```bash
hermes skills tap add qiyuey/hermes-skills
```

Then install any skill:

```bash
hermes skills install book-meeting-room
```

## Skills

| Skill | Description |
|-------|-------------|
| [book-meeting-room](./skills/book-meeting-room/SKILL.md) | 自动抢预会议室系统 meeting-room.zhenguanyu.com，支持查询空闲会议室、高频 cron 狙击抢占、弹性时长/容量、0点黄金窗口放行 |
| [hermes-skills-sync](./skills/hermes-skills-sync/SKILL.md) | 管理本仓库 — 首次安装（clone + symlink）、更新（git pull）、把本地修改的 skill 推送回仓库 |
| [hermes-update](./skills/hermes-update/SKILL.md) | Hermes 更新流程 — 通过 cron job 在后台执行，避免 gateway 重启中断当前会话 |
| [hermes-setup-company-model](./skills/hermes-setup-company-model/SKILL.md) | 帮助公司同事在 macOS 上安装 Hermes Agent，并配置使用公司 Model 平台（model.zhenguanyu.com） |
| [wireguard](./skills/wireguard/SKILL.md) | 本机 WireGuard 隧道以 LaunchDaemon 形式运行（不依赖 wg-quick），直接驱动 wireguard-go + wg + ifconfig + route；用 launchctl 管理启停/重启/状态 |

## Structure

```
skills/
├── book-meeting-room/          # 会议室自动抢占
├── hermes-skills-sync/         # 本仓库同步管理
├── hermes-update/              # Hermes 升级流程
├── hermes-setup-company-model/ # Hermes + 公司 Model 平台安装配置
└── wireguard/                  # macOS WireGuard LaunchDaemon（无 wg-quick）
```

Each skill contains a `SKILL.md` with trigger conditions, step-by-step instructions, and pitfalls, plus optional `scripts/` and `references/` directories.
