---
name: hermes-setup-company-model
description: 帮助公司同事在 macOS 上安装 Hermes Agent，并配置使用公司 Model 平台（model.zhenguanyu.com）的 OpenAI-compatible 接口。使用当用户需要安装 Hermes、配置 Hermes 模型、配置公司 model 平台、从 Codex 迁移模型 key、或想完成 Hermes + 公司 GPT 的配置时。由 Agent 代为执行命令，用户只需提供公司 Model 平台 API key 或授权读取本机 ~/.codex/auth.json。
---

# 安装 Hermes + 配置公司 Model 平台（macOS）

> **执行原则：由 Agent 代替用户执行所有可自动化命令。用户只需提供公司 Model 平台 API key，或授权从本机 `~/.codex/auth.json` 读取现有 key。**

---

## 平台信息

- 公司 Model 平台 endpoint：`https://model.zhenguanyu.com/v1`
- Hermes 走 OpenAI-compatible Chat Completions：`/v1/chat/completions`
- `/v1/models` 可能返回 404，不要依赖它做可用性判断
- 默认模型：`gpt-5.5`
- API key 建议写入 `~/.hermes/.env`：`COMPANY_GPT_API_KEY=...`
- Hermes config 使用 keyed `providers:` schema，provider 名称：`custom:company_gpt`
- 必须显式设置 `api_mode: chat_completions`，避免 GPT-5.x 启发式误走 Responses API

---

## 第一步：一次性收集必要信息

**在做任何安装或写配置之前**，先用一条消息向用户索取以下信息：

```text
您好！我来帮您完成 Hermes 安装和公司 Model 平台配置，全程我来操作。

我需要以下二选一：
1. 公司 Model 平台 API key（通常是 sk-mg-... 格式）；或
2. 如果您本机 Codex 已经能使用公司模型，请授权我从 ~/.codex/auth.json 读取 OPENAI_API_KEY 并复用到 Hermes。

另外，如果安装过程中需要输入电脑开机密码（sudo 密码），系统提示时请在终端里输入即可；输入时屏幕不显示字符是正常现象。
```

等用户提供 key 或明确授权读取 `~/.codex/auth.json` 后再继续。

---

## 第二步：解析和保存 API key

### 2.1 获取 key

按优先级获取 `COMPANY_GPT_API_KEY`：

1. 用户直接提供 `sk-mg-...` key：使用该值。
2. 用户授权读取 Codex 配置：从 `~/.codex/auth.json` 提取 `OPENAI_API_KEY`。

读取 Codex key 的命令：

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

确保 `~/.hermes` 存在，并把 key 写入 `~/.hermes/.env`。不要在聊天回复中回显完整 key。

```bash
mkdir -p ~/.hermes
python3 - <<'PY'
import os, pathlib
key = os.environ['COMPANY_GPT_API_KEY']
env_path = pathlib.Path.home() / '.hermes' / '.env'
lines = []
if env_path.exists():
    lines = env_path.read_text(encoding='utf-8').splitlines()
new_lines = []
written = False
for line in lines:
    if line.startswith('COMPANY_GPT_API_KEY='):
        new_lines.append('COMPANY_GPT_API_KEY=' + key)
        written = True
    else:
        new_lines.append(line)
if not written:
    new_lines.append('COMPANY_GPT_API_KEY=' + key)
env_path.write_text('\n'.join(new_lines).rstrip() + '\n', encoding='utf-8')
os.chmod(env_path, 0o600)
print('WROTE_COMPANY_GPT_API_KEY')
PY
```

> 执行时通过 shell 环境变量传入 `COMPANY_GPT_API_KEY`，避免把 key 写进命令历史。若工具不方便传 env，可用 Python 脚本读取临时文件，写完后删除临时文件。

---

## 第三步：检查环境

执行以下检查，**不需要告诉用户命令内容**，直接执行并根据结果跳过已安装步骤：

```bash
hermes --version 2>/dev/null && echo "HERMES_OK" || echo "HERMES_MISSING"
uv --version 2>/dev/null && echo "UV_OK" || echo "UV_MISSING"
git --version 2>/dev/null && echo "GIT_OK" || echo "GIT_MISSING"
```

若 git 未安装，提示用户：

```text
需要先安装 Xcode Command Line Tools，请在终端输入以下命令并按提示点击“安装”，完成后告诉我。
```

```bash
xcode-select --install
```

---

## 第四步：配置国内镜像（MirrorZ）

> 使用校园网联合镜像站加速 Python 包下载，避免因网络问题安装失败。**无论是否已安装过 uv/pip，都执行此步**（幂等，重复运行无副作用）。

配置 pip 镜像：

```bash
pip config set global.index-url https://mirrors.cernet.edu.cn/pypi/web/simple
pip config set global.trusted-host mirrors.cernet.edu.cn
```

配置 uv 镜像（写入或合并 `~/.config/uv/uv.toml`）：

```bash
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
```

持久化环境变量（若已存在则跳过）：

```bash
grep -q "UV_INDEX" ~/.zshrc || echo 'export UV_INDEX="https://mirrors.cernet.edu.cn/pypi/web/simple"' >> ~/.zshrc
```

---

## 第五步：安装 uv（如未安装）

若第三步检查结果为 `UV_MISSING`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc
uv --version
```

---

## 第六步：安装 Hermes（如未安装）

若第三步检查结果为 `HERMES_MISSING`：

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

安装完成后执行：

```bash
source ~/.zshrc
hermes --version
```

---

## 第七步：安装 Caffeine（防止休眠）

Hermes 在后台持续运行，电脑休眠会导致服务中断。安装 Caffeine 可以让屏幕锁定时保持后台服务正常工作。

检查是否已安装：

```bash
brew list caffeine 2>/dev/null && echo "CAFFEINE_OK" || echo "CAFFEINE_MISSING"
```

若未安装，先检查 Homebrew：

```bash
brew --version 2>/dev/null && echo "BREW_OK" || echo "BREW_MISSING"
```

- 若 Homebrew 已存在：

  ```bash
  brew install --cask caffeine
  ```

- 若 Homebrew 未安装，先安装 Homebrew，再安装 Caffeine：

  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  brew install --cask caffeine
  ```

安装完成后告知用户：

```text
☕ Caffeine 已安装！

请按以下步骤启动它：
1. 打开 Launchpad（F4 键或点击底部程序坞的火箭图标）
2. 找到 Caffeine，点击打开
3. 菜单栏右上角会出现一个咖啡杯图标 ☕
4. 点击咖啡杯图标，让它变成“满杯”状态（激活）

激活后电脑休眠时 Hermes 后台服务仍会正常运行。
建议设置为开机自启：点击菜单栏咖啡杯 → Preferences → 勾选 “Launch at Login”
```

---

## 第八步：写入 Hermes Model Provider 配置

直接配置 Hermes 指向公司 Model 平台。不需要安装 LiteLLM，也不需要额外代理或云厂商凭据。

### 8.1 设置默认模型

```bash
hermes config set model.provider custom:company_gpt
hermes config set model.default gpt-5.5
hermes config set model.base_url ''
hermes config set model.api_key ''
```

### 8.2 合并 `providers.company_gpt`

使用 Python 安全合并 YAML，保留用户现有配置：

```bash
python3 - <<'PY'
from pathlib import Path
import sys
try:
    import yaml
except ImportError:
    raise SystemExit('PY_YAML_MISSING')

p = Path.home() / '.hermes' / 'config.yaml'
cfg = yaml.safe_load(p.read_text(encoding='utf-8')) if p.exists() else {}
if not isinstance(cfg, dict):
    cfg = {}

cfg.setdefault('model', {})
cfg['model'].update({
    'provider': 'custom:company_gpt',
    'default': 'gpt-5.5',
    'base_url': '',
    'api_key': '',
})

cfg.setdefault('providers', {})
cfg['providers']['company_gpt'] = {
    'name': '公司 Model 平台',
    'base_url': 'https://model.zhenguanyu.com/v1',
    'key_env': 'COMPANY_GPT_API_KEY',
    'default_model': 'gpt-5.5',
    'api_mode': 'chat_completions',
    'models': {
        'gpt-5.5': {},
    },
}

p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding='utf-8')
print('CONFIGURED_COMPANY_GPT')
PY
```

如果提示 `PY_YAML_MISSING`，先执行：

```bash
python3 -m pip install pyyaml
```

然后重跑合并脚本。

配置应包含：

```yaml
model:
  default: gpt-5.5
  provider: custom:company_gpt
  base_url: ''
  api_key: ''

providers:
  company_gpt:
    name: 公司 Model 平台
    base_url: https://model.zhenguanyu.com/v1
    key_env: COMPANY_GPT_API_KEY
    default_model: gpt-5.5
    api_mode: chat_completions
    models:
      gpt-5.5: {}
```

---

## 第九步：验证配置

### 9.1 验证 Hermes runtime provider 解析

```bash
python3 - <<'PY'
from hermes_cli.config import load_config
from hermes_cli.runtime_provider import resolve_runtime_provider
cfg = load_config(); mc = cfg['model']
r = resolve_runtime_provider(requested=mc['provider'], target_model=mc['default'])
print('model.provider =', mc['provider'])
print('model.default =', mc['default'])
print('resolved.provider =', r['provider'])
print('api_mode =', r.get('api_mode'))
print('base_url =', r.get('base_url'))
print('has_api_key =', bool(r.get('api_key')))
PY
```

期望输出：

```text
model.provider = custom:company_gpt
model.default = gpt-5.5
resolved.provider = custom
api_mode = chat_completions
base_url = https://model.zhenguanyu.com/v1
has_api_key = True
```

### 9.2 验证公司 endpoint 可用

不要用 `/v1/models` 验证；直接请求 chat completions：

```bash
set -a; source ~/.hermes/.env; set +a
curl -sS https://model.zhenguanyu.com/v1/chat/completions \
  -H "Authorization: Bearer $COMPANY_GPT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.5","messages":[{"role":"user","content":"hi"}],"max_tokens":5}' \
  | python3 -m json.tool
```

成功时会返回 `choices`。如果返回“token 不允许使用模型 X”，说明 endpoint 正常但 token 没有该模型权限；让用户确认公司 Model 平台 token 权限。

### 9.3 验证 Hermes 对话

```bash
hermes chat -q "请用一句话介绍你自己"
```

---

## 完成后告知用户

配置成功后，向用户说明：

```text
✅ 安装配置完成！

您现在可以使用以下方式使用 Hermes：
- 打开终端，输入 hermes 即可开始对话
- 默认模型已配置为公司 Model 平台的 gpt-5.5
- API key 已保存在 ~/.hermes/.env 的 COMPANY_GPT_API_KEY 中

如果后续更换 key，只需要更新 ~/.hermes/.env 里的 COMPANY_GPT_API_KEY。
```

---

## 异常处理

- 安装脚本需要 sudo 密码：提示用户在终端输入开机密码（输入时不显示字符属正常）。
- `hermes: command not found`：执行 `source ~/.zshrc`，或检查 `~/.local/bin` 是否在 PATH。
- uv 安装失败：检查网络，或手动执行 `curl -LsSf https://astral.sh/uv/install.sh | sh`。
- pip/uv 下载包很慢或超时：确认 MirrorZ 镜像配置已执行；检查 `pip config list` 是否显示 MirrorZ 地址。
- `/v1/models` 返回 404：正常现象；公司平台不暴露 models route，用 `/v1/chat/completions` 验证。
- `token 不允许使用模型 gpt-5.5`：endpoint 正常但 token 权限不足，请用户确认 token 是否开通 `gpt-5.5`。
- Hermes 误走 `/v1/responses`：确认 `providers.company_gpt.api_mode: chat_completions` 已写入 config。
- `model.provider 'custom:company_gpt' is not a recognised provider`：可能是 Hermes doctor 校验漂移；以 runtime provider 解析和实际 `hermes chat -q` 为准，必要时更新 Hermes。
