---
name: hermes-setup-bedrock
description: 帮助用户在 macOS 上安装 Hermes Agent，并通过 LiteLLM 配置 AWS Bedrock Claude 模型。使用当用户需要安装 Hermes、配置 Claude、设置 Bedrock、提到收到了 ak/sk 邮件、或想完成 Hermes + Bedrock 的配置时。由 Agent 代为执行所有命令，用户只需提供邮件信息。
---

# 安装 Hermes + 配置 AWS Bedrock Claude 模型（macOS）

> **执行原则：由 Agent 代替用户执行所有命令。用户只需提供邮件信息，其余全部由 Agent 完成。**

---

## 第一步：一次性收集所有所需信息

**在做任何操作之前**，先用一条消息向用户索取以下全部信息：

```
您好！我来帮您完成安装配置，全程我来操作，您只需要提供几个信息就好。

请把收到的邮件内容告诉我（或者直接把邮件截图/文字粘贴过来），我需要：
1. ak（Access Key，例：AKIAQNAP4ZDZFQAIP2O4）
2. sk（Secret Key，例：I3o3WFW...）
3. 邮件里所有的模型名称和对应的模型key（arn:... 那一串）

另外，如果安装过程中需要输入电脑开机密码（sudo 密码），请提前告诉我，或者安装时如果系统弹出密码提示，您在终端里输入就好（输入时屏幕不会显示字符，属于正常现象）。
```

等用户提供信息后再继续。

---

## 第二步：解析邮件信息

收到用户的邮件内容后，提取以下变量（后续步骤都用这些变量）：

- `AWS_AK`：ak 的值
- `AWS_SK`：sk 的值
- 模型 ARN 映射（按名称分类）：

| 邮件里的模型名称关键词 | 对应 Hermes 名称 |
|---|---|
| 包含 `SONNET` | `claude-sonnet`（主力模型，默认推荐） |
| 包含 `HAIKU` | `claude-haiku`（快速轻量） |
| 包含 `OPUS` 且版本最高 | `claude-opus`（最强旗舰） |

如果邮件里有多个 OPUS（如 CLAUDE_45_OPUS 和 CLAUDE_46_OPUS），取版本号最高的那个作为 `claude-opus`。

---

## 第三步：检查环境

通过 Shell 工具执行以下检查，**不需要告诉用户命令内容**，直接执行：

```bash
hermes --version 2>/dev/null && echo "HERMES_OK" || echo "HERMES_MISSING"
litellm --version 2>/dev/null && echo "LITELLM_OK" || echo "LITELLM_MISSING"
uv --version 2>/dev/null && echo "UV_OK" || echo "UV_MISSING"
git --version 2>/dev/null && echo "GIT_OK" || echo "GIT_MISSING"
```

若 git 未安装，提示用户：「需要先安装 Xcode Command Line Tools，请在终端输入以下命令并按提示点击"安装"按钮，完成后告诉我」
```bash
xcode-select --install
```

根据检查结果，跳过已安装的步骤。

---

## 第四步：配置国内镜像（MirrorZ）

> 使用校园网联合镜像站加速 Python 包下载，避免因网络问题安装失败。**无论是否已安装过 uv/pip，都执行此步**（幂等，重复运行无副作用）。

**配置 pip 镜像：**
```bash
pip config set global.index-url https://mirrors.cernet.edu.cn/pypi/web/simple
pip config set global.trusted-host mirrors.cernet.edu.cn
```

**配置 uv 镜像**（写入 `~/.config/uv/uv.toml`）：
```bash
mkdir -p ~/.config/uv
```

然后用 Write 工具写入 `~/.config/uv/uv.toml`（如已存在则用 StrReplace 追加，避免覆盖其他配置）：
```toml
[[index]]
url = "https://mirrors.cernet.edu.cn/pypi/web/simple"
default = true
```

**持久化环境变量**（写入 `~/.zshrc`，若已存在则跳过）：
```bash
grep -q "UV_INDEX" ~/.zshrc || echo 'export UV_INDEX="https://mirrors.cernet.edu.cn/pypi/web/simple"' >> ~/.zshrc
source ~/.zshrc
```

---

## 第五步：安装 uv（如未安装）

若第三步检查结果为 `UV_MISSING`：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc
uv --version
```

> uv 是 Hermes 安装时依赖的 Python 包管理器，安装极快（通常 10 秒内完成）。

---

## 第六步：安装 Hermes（如未安装）

若第三步检查结果为 `HERMES_MISSING`：

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

> 安装耗时约 1-3 分钟。如果中途需要密码，告知用户：「安装程序需要您输入电脑开机密码，请在终端窗口输入（输入时不会显示字符，这是正常的）」

安装完成后执行：
```bash
source ~/.zshrc
hermes --version
```

---

## 第七步：安装 LiteLLM（如未安装）

若第三步检查结果为 `LITELLM_MISSING`，此时 pip/uv 镜像已配置好，直接执行：

```bash
uv pip install "litellm[proxy]" --system
```

> 优先用 uv 安装，速度更快。若失败则退回 `pip install "litellm[proxy]"`。

安装后验证：
```bash
litellm --version
```

---

## 第八步：安装 Caffeine（防止休眠）

Hermes 在后台持续运行，电脑休眠会导致服务中断。安装 Caffeine 可以让屏幕锁定时保持后台服务正常工作。

检查是否已安装：
```bash
brew list caffeine 2>/dev/null && echo "CAFFEINE_OK" || echo "CAFFEINE_MISSING"
```

若未安装（同时检查 Homebrew 是否存在）：
```bash
# 检查 Homebrew
brew --version 2>/dev/null && echo "BREW_OK" || echo "BREW_MISSING"
```

- 若 Homebrew 已存在，直接执行：
  ```bash
  brew install --cask caffeine
  ```
- 若 Homebrew 未安装，先安装 Homebrew：
  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  ```
  完成后再执行 `brew install --cask caffeine`。

安装完成后告知用户：

```
☕ Caffeine 已安装！

请按以下步骤启动它：
1. 打开 Launchpad（F4 键或点击底部程序坞的火箭图标）
2. 找到 Caffeine，点击打开
3. 菜单栏右上角会出现一个咖啡杯图标 ☕
4. 点击咖啡杯图标，让它变成"满杯"状态（激活）

激活后电脑休眠时 Hermes 后台服务仍会正常运行。
建议设置为开机自启：点击菜单栏咖啡杯 → Preferences → 勾选 "Launch at Login"
```

---

## 第九步：写入 LiteLLM 配置文件

根据用户提供的信息，用 Write 工具直接写入 `~/.hermes/litellm_config.yaml`。

**注意**：
- 用实际的 ak/sk 替换占位符
- 根据邮件里有哪些模型决定写哪几个 model_list 条目（最少要有 sonnet 和 haiku；如果邮件里没有某个模型，跳过该条目）
- 如果邮件里有多个 HAIKU 版本，取版本号最高的

模板如下（按实际替换）：

```yaml
# LiteLLM Proxy Config — Bedrock bridge for Hermes Agent
model_list:
  # ── 主力模型：Sonnet ──────────────────────────────────────────────────────
  - model_name: "claude-sonnet"
    litellm_params:
      model: "bedrock/converse/<SONNET_ARN>"
      aws_access_key_id: "<AWS_AK>"
      aws_secret_access_key: "<AWS_SK>"
      aws_region_name: "us-west-2"
      timeout: 600
      stream_timeout: 30
    model_info:
      max_input_tokens: 1000000
      max_output_tokens: 64000
      context_window: 1000000

  # ── 快速模型：Haiku ───────────────────────────────────────────────────────
  - model_name: "claude-haiku"
    litellm_params:
      model: "bedrock/converse/<HAIKU_ARN>"
      aws_access_key_id: "<AWS_AK>"
      aws_secret_access_key: "<AWS_SK>"
      aws_region_name: "us-west-2"
      timeout: 600
      stream_timeout: 30
    model_info:
      max_input_tokens: 200000
      max_output_tokens: 64000
      context_window: 200000

  # ── 旗舰模型：Opus ────────────────────────────────────────────────────────
  - model_name: "claude-opus"
    litellm_params:
      model: "bedrock/converse/<OPUS_ARN>"
      aws_access_key_id: "<AWS_AK>"
      aws_secret_access_key: "<AWS_SK>"
      aws_region_name: "us-west-2"
      timeout: 600
      stream_timeout: 30
    model_info:
      max_input_tokens: 1000000
      max_output_tokens: 128000
      context_window: 1000000

general_settings:
  master_key: "sk-hermes-bedrock-local"

litellm_settings:
  drop_params: true
  num_retries: 2
  request_timeout: 600
```

---

## 第十步：写入 LiteLLM 启动脚本

检查 `~/.hermes/litellm-proxy.sh` 是否已存在：

```bash
test -f ~/.hermes/litellm-proxy.sh && echo "EXISTS" || echo "MISSING"
```

若不存在，用 Write 工具写入：

```bash
#!/usr/bin/env bash
CONFIG="$HOME/.hermes/litellm_config.yaml"
PIDFILE="$HOME/.hermes/litellm.pid"
LOGFILE="$HOME/.hermes/litellm.log"
PORT=4000
LITELLM_BIN="$(which litellm 2>/dev/null || echo "$HOME/.local/bin/litellm")"

cmd_start() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "LiteLLM 已在运行 (PID $(cat "$PIDFILE"))，端口 $PORT"
        return 0
    fi
    echo "正在启动 LiteLLM 代理，端口 $PORT ..."
    nohup "$LITELLM_BIN" --config "$CONFIG" --port "$PORT" > "$LOGFILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PIDFILE"
    sleep 3
    if kill -0 "$pid" 2>/dev/null; then
        echo "启动成功 (PID $pid)"
    else
        echo "启动失败，日志如下："
        rm -f "$PIDFILE"
        tail -20 "$LOGFILE"
        return 1
    fi
}

cmd_stop() {
    if [ -f "$PIDFILE" ]; then
        kill "$(cat "$PIDFILE")" 2>/dev/null && rm -f "$PIDFILE" && echo "已停止"
    else
        echo "未运行"
    fi
}

cmd_status() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "运行中 (PID $(cat "$PIDFILE"))，端口 $PORT"
    else
        echo "未运行"
        return 1
    fi
}

case "${1:-start}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_stop; sleep 1; cmd_start ;;
    log)     tail -50 -f "$HOME/.hermes/litellm.log" ;;
    *) echo "用法: $0 {start|stop|status|restart|log}" ;;
esac
```

写入后执行：
```bash
chmod +x ~/.hermes/litellm-proxy.sh
```

---

## 第十一步：配置 Hermes 指向 LiteLLM

检查 `~/.hermes/config.yaml` 是否已有 litellm 自定义 provider：

```bash
grep -q "name: litellm" ~/.hermes/config.yaml && echo "CONFIGURED" || echo "NEED_CONFIG"
```

若结果为 `NEED_CONFIG`，执行：
```bash
hermes config set model.provider custom:litellm
hermes config set model.default claude-sonnet
```

然后检查 config.yaml 是否包含 `custom_providers` 块：
```bash
grep -q "custom_providers" ~/.hermes/config.yaml && echo "HAS_CUSTOM" || echo "NO_CUSTOM"
```

若结果为 `NO_CUSTOM`，用 StrReplace 或直接 Write 在 `~/.hermes/config.yaml` 末尾追加：
```yaml
custom_providers:
  - name: litellm
    base_url: http://localhost:4000/v1
    api_key: sk-hermes-bedrock-local
    model: claude-sonnet
    models:
      claude-opus: {}
      claude-sonnet: {}
      claude-haiku: {}
```

---

## 第十二步：启动代理并验证

执行：
```bash
bash ~/.hermes/litellm-proxy.sh start
```

等待 3 秒后检查状态：
```bash
bash ~/.hermes/litellm-proxy.sh status
```

最后执行一次快速测试（-q 表示单次问答，不进入交互模式）：
```bash
hermes chat -q "请用一句话介绍你自己"
```

---

## 完成后告知用户

配置成功后，向用户说明：

```
✅ 安装配置完成！

您现在可以使用以下方式使用 AI：
- 打开终端，输入 hermes 即可开始对话
- 默认使用 Claude Sonnet（速度和能力平衡）
- 如需最强能力，输入 hermes --model claude-opus

⚠️ 注意：每次重启电脑后需要重新启动 AI 代理。
在终端输入以下命令启动：
  bash ~/.hermes/litellm-proxy.sh start

然后再输入 hermes 就可以开始使用了。
```

---

## 异常处理

| 问题 | 处理方式 |
|---|---|
| 安装脚本需要 sudo 密码 | 提示用户在终端输入开机密码（输入时不显示字符属正常） |
| `hermes: command not found` | 执行 `source ~/.zshrc` |
| uv 安装失败 | 检查网络，或手动执行 `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| pip/uv 下载包很慢或超时 | 确认第四步镜像配置已执行；检查 `pip config list` 是否显示 MirrorZ 地址 |
| LiteLLM 启动失败 | 读取 `~/.hermes/litellm.log` 末尾内容，分析原因 |
| AWS 认证失败（403/401）| 请用户核对邮件里的 ak/sk 是否完整，有无多余空格 |
| 端口 4000 被占用 | 执行 `lsof -i :4000` 查看占用进程，必要时用 `kill -9 <PID>` 释放 |
