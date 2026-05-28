---
name: hermes-setup-company-model
description: 帮助公司同事在 macOS 上安装 Hermes Agent，并通过 qiyuey/hermes-skills 仓库的 company_model plugin 一键接入公司 Model 平台 (model.zhenguanyu.com) 的 chat / image / TTS / ASR 能力。当用户需要安装 Hermes、配置公司 model 平台、从 Codex 迁移 API key、或想完成 Hermes + 公司 GPT 一体化配置时使用此 skill。由 Agent 代为执行命令，用户只需提供 API key 或授权读取 ~/.codex/auth.json。
---

# 安装 Hermes + 配置公司 Model 平台 (macOS)

> **执行原则: 由 Agent 代替用户执行所有可自动化命令。用户只需提供公司 Model 平台 API key, 或授权从 `~/.codex/auth.json` 读取现有 key。**

---

## 工作思路

公司 Model 平台 (`model.zhenguanyu.com`) 是 OpenAI-compatible 网关, 支持 chat / image / TTS / ASR 四种 endpoint。Hermes 通过一个统一的 user plugin 接入这四种能力, plugin 来自 `qiyuey/hermes-skills` 仓库:

```
~/.hermes/plugins/model-providers/company_model/   # symlink 到仓库里的 plugin
├── plugin.yaml
├── __init__.py            # 注册 chat profile (提供 max_tokens、live model fetch 等)
├── image_gen.py           # gpt-image-2 image generation
├── tts.sh                 # qwen3-tts-flash 语音合成
└── stt.sh                 # qwen3-asr-flash 语音识别
```

`config.yaml` 只需声明"使用这个 plugin", 不再手写一大坨 yaml 字段。

---

## 第一步: 一次性收集必要信息

**在做任何安装或写配置之前**, 先用一条消息向用户索取以下信息:

```text
您好! 我来帮您完成 Hermes 安装和公司 Model 平台配置, 全程我来操作。

我需要以下二选一:
1. 公司 Model 平台 API key (通常是 sk-mg-... 格式); 或
2. 如果您本机 Codex 已经能使用公司模型, 请授权我从 ~/.codex/auth.json 读取 OPENAI_API_KEY 并复用到 Hermes。

另外, 如果安装过程中需要输入电脑开机密码 (sudo 密码), 系统提示时请在终端里输入即可; 输入时屏幕不显示字符是正常现象。
```

等用户提供 key 或明确授权读取 `~/.codex/auth.json` 后再继续。

---

## 第二步: 解析和保存 API key

### 2.1 获取 key

按优先级获取 `COMPANY_MODEL_API_KEY`:

1. 用户直接提供 `sk-mg-...` key: 使用该值。
2. 用户授权读取 Codex 配置: 从 `~/.codex/auth.json` 提取 `OPENAI_API_KEY`。

读取 Codex key 的命令:

```bash
python3 - <<'PY'
import json, os
p = os.path.expanduser('~/.codex/auth.json')
with open(p, 'r', encoding='utf-8') as f:
    data = json.load(f)
key = data.get('OPENAI_API_KEY') or data.get('tokens', {}).get('OPENAI_API_KEY')
if not key:
    raise SystemExit('NO_OPENAI_API_KEY_IN_CODEX_AUTH')
print(key)
PY
```

### 2.2 写入 Hermes `.env`

确保 `~/.hermes` 存在, 把 key 写入 `~/.hermes/.env`。**不要在聊天回复中回显完整 key**。

```bash
mkdir -p ~/.hermes
python3 - <<'PY'
import os, pathlib
key = os.environ['COMPANY_MODEL_API_KEY']
env_path = pathlib.Path.home() / '.hermes' / '.env'
lines = []
if env_path.exists():
    lines = env_path.read_text(encoding='utf-8').splitlines()
new_lines = []
written = False
for line in lines:
    if line.startswith('COMPANY_MODEL_API_KEY='):
        new_lines.append('COMPANY_MODEL_API_KEY=' + key)
        written = True
    else:
        new_lines.append(line)
if not written:
    new_lines.append('COMPANY_MODEL_API_KEY=' + key)
env_path.write_text('\n'.join(new_lines).rstrip() + '\n', encoding='utf-8')
os.chmod(env_path, 0o600)
print('WROTE_COMPANY_MODEL_API_KEY')
PY
```

通过 shell 环境变量传入 `COMPANY_MODEL_API_KEY`, 避免把 key 写进命令历史。

---

## 第三步: 检查环境

执行以下检查, **不需要告诉用户命令内容**, 直接执行并根据结果跳过已安装步骤:

```bash
hermes --version 2>/dev/null && echo "HERMES_OK" || echo "HERMES_MISSING"
uv --version 2>/dev/null && echo "UV_OK" || echo "UV_MISSING"
git --version 2>/dev/null && echo "GIT_OK" || echo "GIT_MISSING"
gh --version 2>/dev/null && echo "GH_OK" || echo "GH_MISSING"
```

若 git 未安装, 提示用户:

```text
需要先安装 Xcode Command Line Tools, 请在终端输入以下命令并按提示点击"安装", 完成后告诉我。
```

```bash
xcode-select --install
```

---

## 第四步: 配置国内镜像 (MirrorZ)

> 使用校园网联合镜像站加速 Python 包下载, 避免因网络问题安装失败。**无论是否已安装过 uv/pip, 都执行此步** (幂等)。

```bash
pip config set global.index-url https://mirrors.cernet.edu.cn/pypi/web/simple
pip config set global.trusted-host mirrors.cernet.edu.cn

mkdir -p ~/.config/uv
python3 - <<'PY'
from pathlib import Path
p = Path.home() / '.config' / 'uv' / 'uv.toml'
text = p.read_text(encoding='utf-8') if p.exists() else ''
block = '[[index]]\nurl = "https://mirrors.cernet.edu.cn/pypi/web/simple"\ndefault = true\n'
if 'mirrors.cernet.edu.cn/pypi/web/simple' not in text:
    p.write_text((text.rstrip() + '\n\n' + block).lstrip(), encoding='utf-8')
print('UV_MIRROR_OK')
PY

grep -q "UV_INDEX" ~/.zshrc || echo 'export UV_INDEX="https://mirrors.cernet.edu.cn/pypi/web/simple"' >> ~/.zshrc
```

---

## 第五步: 安装 uv (如未安装)

若第三步检查为 `UV_MISSING`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc
uv --version
```

---

## 第六步: 安装 Hermes (如未安装)

若第三步检查为 `HERMES_MISSING`:

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
source ~/.zshrc
hermes --version
```

---

## 第七步: 安装 Caffeine (防止休眠)

Hermes 在后台持续运行, 电脑休眠会导致服务中断。

```bash
brew list caffeine 2>/dev/null && echo "CAFFEINE_OK" || echo "CAFFEINE_MISSING"
brew --version 2>/dev/null && echo "BREW_OK" || echo "BREW_MISSING"
```

- 若 Homebrew 已存在 + Caffeine 未安装:
  ```bash
  brew install --cask caffeine
  ```

- 若 Homebrew 也未安装:
  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  brew install --cask caffeine
  ```

安装完成后告知用户:

```text
☕ Caffeine 已安装!

请按以下步骤启动它:
1. 打开 Launchpad (F4 键或点击底部程序坞的火箭图标)
2. 找到 Caffeine, 点击打开
3. 菜单栏右上角会出现一个咖啡杯图标 ☕
4. 点击咖啡杯图标, 让它变成"满杯"状态 (激活)

激活后电脑休眠时 Hermes 后台服务仍会正常运行。
建议设置为开机自启: 点击菜单栏咖啡杯 → Preferences → 勾选 "Launch at Login"
```

---

## 第八步: clone qiyuey/hermes-skills 仓库并 symlink plugin

通过 `qiyuey/hermes-skills` 仓库一次性获得 company_model plugin (chat + image + TTS + ASR) 和相关 skills。

### 8.1 检查 GitHub CLI 认证

```bash
gh auth status 2>&1 | head -5 || echo "GH_NOT_AUTHED"
```

如果 `GH_MISSING` 或 `GH_NOT_AUTHED`, 提示用户:

```text
需要 GitHub CLI 来 clone 私有仓库 (如果有). 我帮您安装并登录:
- 安装: brew install gh (若 Homebrew 已装)
- 登录: gh auth login (在浏览器里完成)

如果 qiyuey/hermes-skills 是公开仓库, 可以跳过登录, 用 git clone 也行。
```

### 8.2 clone 仓库

```bash
mkdir -p ~/Code
if [ ! -d ~/Code/hermes-skills ]; then
    git clone https://github.com/qiyuey/hermes-skills.git ~/Code/hermes-skills
fi
ls ~/Code/hermes-skills/
```

### 8.3 symlink skills

```bash
mkdir -p ~/.hermes/skills
for skill in ~/Code/hermes-skills/skills/*/; do
    name=$(basename "$skill")
    target=~/.hermes/skills/$name
    if [ -e "$target" ] && [ ! -L "$target" ]; then
        mv "$target" "${target}.bak"
        echo "已备份原有 skill $name"
    fi
    ln -sfn "$skill" "$target"
    echo "skill: $target"
done
```

### 8.4 symlink plugins (核心)

```bash
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
            echo "plugin: $target"
        done
    done
fi
```

完成后 `~/.hermes/plugins/` 应该有:

```
~/.hermes/plugins/model-providers/company_model -> ~/Code/hermes-skills/plugins/model-providers/company_model
```

---

## 第九步: 写入 Hermes config.yaml

最简化的 config — 把所有具体细节交给 plugin。

```bash
python3 - <<'PY'
from pathlib import Path
try:
    import yaml
except ImportError:
    raise SystemExit('PY_YAML_MISSING')

p = Path.home() / '.hermes' / 'config.yaml'
cfg = yaml.safe_load(p.read_text(encoding='utf-8')) if p.exists() else {}
if not isinstance(cfg, dict):
    cfg = {}

# Model 默认 — sonnet 偏向写代码 / agent 任务. base_url / api_key 由 plugin 提供,
# 不写空字符串占位 (hermes 用 .get() or "" 解析, None 和 "" 等价).
cfg.setdefault('model', {}).update({
    'provider': 'company_model',
    'default': 'claude-sonnet-4-6',
})

# Provider 段必须保留 company_model 这个 key (即使 dict 是空), 否则 hermes doctor
# 的 resolve_provider_full() 不识别 plugin 注册的 provider, 会误报 "is unknown".
# plugin 提供 base_url / api_mode / key_env / models 列表等所有具体协议字段.
cfg.setdefault('providers', {})
cfg['providers']['company_model'] = {}

# TTS — 公司平台 qwen3-tts-flash
cfg.setdefault('tts', {})
cfg['tts'].update({
    'provider': 'company-tts',
    'providers': {
        'company-tts': {
            'type': 'command',
            'command': 'bash ~/.hermes/plugins/model-providers/company_model/tts.sh {input_path} {output_path}',
            'output_format': 'wav',
            'max_text_length': 10000,
        }
    },
})

# STT — 公司平台 qwen3-asr-flash
cfg.setdefault('stt', {})
cfg['stt'].update({
    'enabled': True,
    'provider': 'company-asr',
    'providers': {
        'company-asr': {
            'type': 'command',
            'command': 'bash ~/.hermes/plugins/model-providers/company_model/stt.sh {input_path} {output_path}',
            'output_format': 'txt',
        }
    },
})

# Image gen — 公司平台 gpt-image-2
cfg['image_gen'] = {
    'provider': 'company',
    'model': 'gpt-image-2-medium',
}

# 视觉模型 (PDF / 图像理解) 也用公司平台
cfg.setdefault('auxiliary', {}).setdefault('vision', {}).update({
    'provider': 'company_model',
    'model': 'claude-sonnet-4-6',
})

p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding='utf-8')
print('CONFIGURED_COMPANY_MODEL')
PY
```

如果提示 `PY_YAML_MISSING`:

```bash
python3 -m pip install pyyaml
```

然后重跑。

---

## 第十步: 验证

### 10.1 验证 plugin 加载

```bash
HERMES_HOME=~/.hermes python3 - <<'PY'
import os, sys
# 加 hermes-agent 到 path (从 hermes 安装位置探测)
import subprocess
out = subprocess.check_output(['hermes', '--version'], text=True).strip()
print('hermes:', out)

# Plugin discovery
from providers import get_provider_profile
prof = get_provider_profile('company_model')
print('chat profile loaded:', prof is not None and prof.base_url)

from agent.image_gen_registry import _providers as img
print('image_gen registered:', bool(img.get('company')))
PY
```

期望:
```text
chat profile loaded: https://model.zhenguanyu.com/v1
image_gen registered: True
```

### 10.2 验证 chat endpoint

```bash
set -a; source ~/.hermes/.env; set +a
curl -sS https://model.zhenguanyu.com/v1/chat/completions \
    -H "Authorization: Bearer $COMPANY_MODEL_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"hi"}],"max_tokens":50}' \
    | python3 -m json.tool
```

成功返回 `choices` 即正常。

### 10.3 验证 Hermes 对话

```bash
hermes chat -q "请用一句话介绍你自己"
```

### 10.4 验证 TTS / ASR (可选)

```bash
TMP_IN=/tmp/hermes-test-in.txt
TMP_AUDIO=/tmp/hermes-test.wav
TMP_OUT=/tmp/hermes-test-out.txt
echo "今天天气真好" > "$TMP_IN"

bash ~/.hermes/plugins/model-providers/company_model/tts.sh "$TMP_IN" "$TMP_AUDIO" \
    && file "$TMP_AUDIO" \
    && bash ~/.hermes/plugins/model-providers/company_model/stt.sh "$TMP_AUDIO" "$TMP_OUT" \
    && cat "$TMP_OUT" && echo

rm -f "$TMP_IN" "$TMP_AUDIO" "$TMP_OUT"
```

期望: 生成 WAV → 识别回原文。

---

## 完成后告知用户

```text
✅ 安装配置完成!

您的 Hermes 现在通过 qiyuey/hermes-skills 仓库的 company_model plugin 接入公司 Model 平台,
支持 chat / image / TTS / ASR 四种能力, 全部走公司 model.zhenguanyu.com:

- Chat: claude-opus-4-7, claude-sonnet-4-6 (默认), gpt-5.5, deepseek-v4-pro,
        kimi-k2.6, qwen3.7-max, mimo-v2.5-pro
- Image: gpt-image-2 (low / medium / high)
- TTS: qwen3-tts-flash (Cherry voice)
- ASR: qwen3-asr-flash

使用方式:
- 终端输入 hermes 开始对话
- 默认模型 claude-sonnet-4-6, 想换用 /model 命令
- API key 在 ~/.hermes/.env 的 COMPANY_MODEL_API_KEY

升级 plugin 和 skills (一行):
  cd ~/Code/hermes-skills && git pull && systemctl --user restart hermes-gateway

(Linux 用 systemctl, macOS 实际就是关掉 hermes 进程后重启。)
```

---

## 异常处理

- **安装脚本需要 sudo 密码**: 提示用户在终端输入开机密码 (输入时不显示字符属正常)。
- **`hermes: command not found`**: 执行 `source ~/.zshrc`, 或检查 `~/.local/bin` 是否在 PATH。
- **uv 安装失败**: 检查网络, 或手动执行 `curl -LsSf https://astral.sh/uv/install.sh | sh`。
- **pip/uv 下载包很慢或超时**: 确认 MirrorZ 镜像配置已执行; 检查 `pip config list`。
- **`/v1/models` 返回 404**: 正常现象; 公司平台不暴露 models route, 用 `/v1/chat/completions` 验证。
- **`token 不允许使用模型 X`**: endpoint 正常但 token 权限不足, 让用户确认 token 是否开通该模型。
- **plugin 加载失败 (`get_provider_profile('company_model')` 返回 None)**: 检查 `~/.hermes/plugins/model-providers/company_model/` 是否是有效 symlink, 内容是否完整。
- **TTS / STT 脚本权限错误**: 给脚本加可执行权限 `chmod +x ~/.hermes/plugins/model-providers/company_model/*.sh`。
- **Hermes 误走 `/v1/responses`**: plugin 已强制 `api_mode: chat_completions`, 如果还出现, 检查 plugin 是否被正确加载。
- **`hermes doctor` 报 "model.provider 'company_model' is unknown"**: 检查 `config.yaml` 里是否有 `providers.company_model: {}` 这一行 (空 dict 也行, 但 key 必须存在). 这是 hermes 设计裂缝 — `resolve_provider_full()` 不查 plugin 注册的 PROVIDER_REGISTRY, 必须靠 config 显式声明。
- **`hermes doctor` 出现 "AWS Bedrock (AWS_PROFILE, ...)"**: hermes 探测到本机有 AWS 凭据 (`AWS_PROFILE` env var 或 `~/.aws/credentials` 的 `[default]` 段). 想去掉就清理这些凭据来源 — Bedrock 探测是 hermes 内置, 没有显式开关。
- **GitHub clone 失败**: 如果是私有仓库, 确认 `gh auth login` 完成; 公开仓库直接 https clone 应该没问题。
