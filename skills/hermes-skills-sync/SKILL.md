---
name: hermes-skills-sync
description: 管理 qiyuey/hermes-skills 仓库 — 首次安装（clone + symlink）、更新（git pull）、以及把本地修改过的 skill / plugin 推送回仓库。仓库不止管 skills, 还管 ~/.hermes/plugins/ 下的 user plugin。用户说"同步 skills"、"更新 skills 仓库"、"把这个 skill / plugin 推送到仓库"时加载此 skill。
version: 1.1.0
author: Hermes Agent
metadata:
  hermes:
    tags: [hermes, skills, plugins, sync, github, symlink]
    related_skills: [hermes-update]
---

# hermes-skills-sync

管理本地 `~/Code/hermes-skills` 仓库与 hermes 运行时的同步。仓库管两类东西:

| 仓库目录 | 镜像到 | 内容性质 |
|---------|-------|---------|
| `skills/<name>/` | `~/.hermes/skills/<name>/` | LLM 读的操作手册 |
| `plugins/<category>/<name>/` | `~/.hermes/plugins/<category>/<name>/` | Hermes runtime 加载的 Python 代码 / 脚本 |

两类都通过 **per-item symlink** (单个 skill 或 plugin 目录) 管理, 不要 symlink 顶层 `skills/` 或 `plugins/` (会影响混用本地和仓库内容).

---

## 仓库信息

- **GitHub**: `qiyuey/hermes-skills`
- **本地路径**: `~/Code/hermes-skills/`
- **Skills 目录**: `~/Code/hermes-skills/skills/` ↔ `~/.hermes/skills/`
- **Plugins 目录**: `~/Code/hermes-skills/plugins/<category>/` ↔ `~/.hermes/plugins/<category>/`

`<category>` 例: `model-providers`, `image_gen`, `transcription`, `tts` 等. 镜像 hermes 的 plugin 目录约定.

---

## 场景一：首次安装（仓库不存在）

检测条件：`~/Code/hermes-skills` 不存在。

```bash
# 1. clone 仓库
mkdir -p ~/Code
gh repo clone qiyuey/hermes-skills ~/Code/hermes-skills

# 2. 同步 skills (每个 skill 单独 symlink)
for skill in ~/Code/hermes-skills/skills/*/; do
  name=$(basename "$skill")
  target=~/.hermes/skills/$name
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    mv "$target" "${target}.bak"
    echo "已备份原有 $name 到 ${name}.bak"
  fi
  ln -sfn "$skill" "$target"
  echo "symlink skill: $target -> $skill"
done

# 3. 同步 plugins (每个 plugin 单独 symlink, 保留 category 中间层)
if [ -d ~/Code/hermes-skills/plugins ]; then
  for category_dir in ~/Code/hermes-skills/plugins/*/; do
    category=$(basename "$category_dir")
    mkdir -p ~/.hermes/plugins/$category
    for plugin in "$category_dir"*/; do
      name=$(basename "$plugin")
      target=~/.hermes/plugins/$category/$name
      if [ -e "$target" ] && [ ! -L "$target" ]; then
        mv "$target" "${target}.bak"
        echo "已备份原有 plugin $category/$name"
      fi
      ln -sfn "$plugin" "$target"
      echo "symlink plugin: $target -> $plugin"
    done
  done
fi
```

完成后重启 hermes gateway (`systemctl --user restart hermes-gateway`) 让 plugin 重新加载.

---

## 场景二：更新仓库（git pull）

用户说"更新 skills"、"pull skills 仓库"时执行：

```bash
cd ~/Code/hermes-skills && git pull
```

pull 完成后检查是否有新增或删除的 skill / plugin 目录，并自动维护 symlink：

- **新增 skill 目录** → 创建 symlink 到 `~/.hermes/skills/`
- **新增 plugin 目录** → 创建 symlink 到 `~/.hermes/plugins/<category>/`
- **删除目录** → 删除对应 symlink（仅删 symlink，不动 `~/.hermes/` 下其他真实目录）
- **plugins 有变动** → 提示用户重启 gateway 让新 plugin 加载

---

## 场景三：推送本地 skill / plugin 修改到仓库

用户修改了某个通过 symlink 管理的 skill 或 plugin 后，说"推送到仓库"或"同步"时执行。

因为 symlink 指向仓库内文件，修改本地等于直接改了仓库文件，只需 commit + push：

```bash
cd ~/Code/hermes-skills

# 查看有哪些改动
git status
git diff

# 提交 (commit message 描述具体改的是 skill 还是 plugin)
git add -A
git commit -m "feat(<scope>): <描述变更>"
git push
```

`<scope>` 用 `skills/<name>` 或 `plugins/<category>/<name>` 区分.

如果 push 失败（无认证），提示用户配置 GitHub token 或 SSH key。

---

## 场景四：新增一个 skill 到仓库

用户说"把这个 skill 加入仓库管理"时：

```bash
skill_name="<skill-name>"
src=~/.hermes/skills/$skill_name
repo_target=~/Code/hermes-skills/skills/$skill_name

mv "$src" "$repo_target"
ln -s "$repo_target" "$src"
echo "已纳入仓库管理: $skill_name"

cd ~/Code/hermes-skills
git add skills/$skill_name/
git commit -m "feat(skills/$skill_name): add"
git push
```

---

## 场景五：新增一个 plugin 到仓库

用户说"把这个 plugin 加入仓库管理"时（例如 `~/.hermes/plugins/model-providers/foobar/`）：

```bash
category="<category>"   # 比如 model-providers / image_gen / transcription
name="<plugin-name>"    # 比如 foobar / company_model
src=~/.hermes/plugins/$category/$name
repo_dir=~/Code/hermes-skills/plugins/$category
repo_target=$repo_dir/$name

# 移入仓库 (清掉 __pycache__ 不进 git)
rm -rf "$src/__pycache__"
mkdir -p "$repo_dir"
mv "$src" "$repo_target"

# symlink 回去
ln -s "$repo_target" "$src"
echo "已纳入仓库管理: plugins/$category/$name"

cd ~/Code/hermes-skills
git add plugins/$category/$name/
git commit -m "feat(plugins/$category/$name): add"
git push
```

之后**必须重启 gateway** (`systemctl --user restart hermes-gateway`), 不然 plugin 还在内存里走旧路径.

---

## 注意事项

- symlink 使用绝对路径（`/Users/yuchuan/...` 或 `/home/yuchuan/...`），仓库移动后需重建 symlink
- 推送需要 GitHub 认证（`gh auth login` 或 SSH remote）
- 不要 symlink `~/.hermes/skills/.hub` 和 `~/.hermes/skills/.bundled_manifest`（系统内部文件）
- Plugin 改动**必须重启 hermes gateway** (skill 改动是热加载, plugin 不是)
- `__pycache__/` 目录已加入 `.gitignore`, 但移入仓库前手动清一次更稳妥
- 本 skill 自身也通过 symlink 管理，修改后同样需要执行场景三推送
