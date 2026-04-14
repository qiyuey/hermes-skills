---
name: hermes-skills-sync
description: 管理 qiyuey/hermes-skills 仓库 — 首次安装（clone + symlink）、更新（git pull）、以及把本地修改过的 skill 推送回仓库。用户说"同步 skills"、"更新 skills 仓库"、"把这个 skill 推送到仓库"时加载此 skill。
version: 1.0.0
author: Hermes Agent
metadata:
  hermes:
    tags: [hermes, skills, sync, github, symlink]
    related_skills: [hermes-update]
---

# hermes-skills-sync

管理本地 `~/Code/hermes-skills` 仓库与 `~/.hermes/skills/` 之间的 skill 同步。

---

## 仓库信息

- **GitHub**: `qiyuey/hermes-skills`
- **本地路径**: `~/Code/hermes-skills/`
- **Skills 目录**: `~/Code/hermes-skills/skills/`
- **Hermes skills 目录**: `~/.hermes/skills/`

---

## 场景一：首次安装（仓库不存在）

检测条件：`~/Code/hermes-skills` 不存在。

```bash
# 1. clone 仓库
mkdir -p ~/Code
gh repo clone qiyuey/hermes-skills ~/Code/hermes-skills

# 2. 列出仓库中所有 skill
ls ~/Code/hermes-skills/skills/

# 3. 为每个 skill 创建 symlink（先删除本地同名目录/链接）
for skill in ~/Code/hermes-skills/skills/*/; do
  name=$(basename "$skill")
  target=~/.hermes/skills/$name
  # 如果目标存在且不是 symlink，备份
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    mv "$target" "${target}.bak"
    echo "已备份原有 $name 到 ${name}.bak"
  fi
  ln -sfn "$skill" "$target"
  echo "symlink: $target -> $skill"
done
```

完成后告知用户已 link 了哪些 skill。

---

## 场景二：更新仓库（git pull）

用户说"更新 skills"、"pull skills 仓库"时执行：

```bash
cd ~/Code/hermes-skills && git pull
```

pull 完成后检查是否有新增或删除的 skill 目录，并自动维护 symlink：

- **新增目录** → 创建 symlink 到 `~/.hermes/skills/`
- **删除目录** → 删除对应 symlink（仅删 symlink，不动 `~/.hermes/skills/` 中的真实目录）

---

## 场景三：推送本地 skill 修改到仓库

用户修改了某个通过 symlink 管理的 skill 后，说"推送到仓库"或"同步这个 skill"时执行。

因为 symlink 指向仓库内文件，修改本地 skill 等于直接改了仓库文件，只需 commit + push：

```bash
cd ~/Code/hermes-skills

# 查看有哪些改动
git status
git diff

# 提交（commit message 由用户确认或自动生成）
git add -A
git commit -m "feat(<skill-name>): <描述变更>"
git push
```

如果 push 失败（无认证），提示用户配置 GitHub token 或 SSH key。

---

## 场景四：新增一个 skill 到仓库

用户说"把这个 skill 加入仓库管理"时：

```bash
skill_name="<skill-name>"
src=~/.hermes/skills/$skill_name
repo_target=~/Code/hermes-skills/skills/$skill_name

# 1. 把现有 skill 目录移入仓库
mv "$src" "$repo_target"

# 2. 创建 symlink 替代原目录
ln -s "$repo_target" "$src"
echo "已纳入仓库管理: $skill_name"

# 3. commit + push
cd ~/Code/hermes-skills
git add skills/$skill_name/
git commit -m "feat: add $skill_name"
git push
```

---

## 注意事项

- symlink 使用绝对路径（`/home/yuchuan/...`），仓库移动后需重建 symlink
- 推送需要 GitHub 认证（`gh auth login` 或 SSH remote）
- 不要 symlink `~/.hermes/skills/.hub` 和 `~/.hermes/skills/.bundled_manifest`（系统内部文件）
- 本 skill 自身也通过 symlink 管理，修改后同样需要执行场景三推送
