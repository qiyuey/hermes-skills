---
name: hermes-setup-bedrock
description: 面向非技术用户（如产品经理）的 Hermes Agent 安装 + AWS Bedrock 配置向导。引导用户完成：安装 Hermes、安装 LiteLLM、写配置文件、启动代理服务、配置 Hermes 连接。需要用户提供 AWS Bedrock 凭证。
version: 1.0.0
author: Hermes Agent
tags: [hermes, setup, bedrock, litellm, onboarding, beginner]
---

# Hermes + AWS Bedrock 安装配置向导

面向没有深厚技术背景的用户（如产品经理）。请用友好、耐心的语气，一步一步引导。
每完成一步都要让用户确认再继续，不要一次甩出所有命令。

---

## 前提确认

在开始之前，先问用户以下几件事（一次问完）：

1. 操作系统是什么？（Mac / Windows / Linux）
2. 是否已安装 Python？（不知道没关系，我们会检查）
3. 手里有没有 AWS 的以下三样东西：
   - AWS Access Key ID（形如 AKIA...）
   - AWS Secret Access Key（一串较长的字符）
   - AWS 区域（如 us-east-1，是你的 Bedrock 开通的区域）

如果用户不确定去哪里找这些信息，引导他：
AWS 控制台 → 右上角头像 → "安全凭证" → "访问密钥" 可以创建或查看 Access Key

---

## 第一步：检查 Python

让用户打开终端（Mac 叫"终端 Terminal"，Windows 叫"命令提示符 CMD"或"PowerShell"），运行：

    python3 --version

- 显示 Python 3.x.x（3.9 以上）：可以继续
- 报错"command not found"：需要先安装 Python
  - Mac/Windows：访问 https://www.python.org/downloads/ 下载安装
  - Windows 安装时记得勾选 "Add Python to PATH"
  - 安装完成后重新打开终端再试一次

---

## 第二步：安装 Hermes Agent

在终端里运行（一行，复制粘贴即可）：

    curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

等待安装完成。安装结束后，关闭终端，重新打开一个新终端，运行：

    hermes --version

显示版本号（比如 hermes 1.x.x），说明安装成功。

常见问题：
- 提示"hermes: command not found"：可能是环境变量没刷新
  - Mac/Linux：运行 source ~/.bashrc 或 source ~/.zshrc
  - Windows：重新打开 PowerShell

---

## 第三步：安装 LiteLLM

LiteLLM 是一个"翻译器"，把 AWS Bedrock 的接口转换成 Hermes 能理解的格式。

在终端运行：

    pip3 install "litellm[proxy]"

安装完后验证：

    litellm --version

显示版本号即成功。

---

## 第四步：创建 LiteLLM 配置文件

创建文件夹（Mac/Linux 在终端运行）：

    mkdir -p ~/litellm

然后用文本编辑器创建并编辑文件 ~/litellm/config.yaml，粘贴以下内容（把 YOUR_REGION 替换成你的 AWS 区域，比如 us-east-1）：

    model_list:
      - model_name: claude-sonnet
        litellm_params:
          model: bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0
          aws_region_name: YOUR_REGION

      - model_name: claude-haiku
        litellm_params:
          model: bedrock/anthropic.claude-3-5-haiku-20241022-v1:0
          aws_region_name: YOUR_REGION

    litellm_settings:
      drop_params: true

打开文件的方式：
- Mac：open -e ~/litellm/config.yaml
- Windows：文件路径是 %USERPROFILE%\litellm\config.yaml，用记事本打开
- Linux：nano ~/litellm/config.yaml

---

## 第五步：设置 AWS 凭证

把 AWS 密钥告诉系统，推荐写入配置文件，重启后依然有效。

Mac（zsh，macOS 默认 shell），在终端运行（引号里换成你自己的值）：

    echo 'export AWS_ACCESS_KEY_ID="你的AccessKeyID"' >> ~/.zshrc
    echo 'export AWS_SECRET_ACCESS_KEY="你的SecretAccessKey"' >> ~/.zshrc
    echo 'export AWS_DEFAULT_REGION="你的区域"' >> ~/.zshrc
    source ~/.zshrc

Linux（bash），把 .zshrc 换成 .bashrc：

    echo 'export AWS_ACCESS_KEY_ID="你的AccessKeyID"' >> ~/.bashrc
    echo 'export AWS_SECRET_ACCESS_KEY="你的SecretAccessKey"' >> ~/.bashrc
    echo 'export AWS_DEFAULT_REGION="你的区域"' >> ~/.bashrc
    source ~/.bashrc

Windows PowerShell，依次运行：

    [System.Environment]::SetEnvironmentVariable("AWS_ACCESS_KEY_ID", "你的AccessKeyID", "User")
    [System.Environment]::SetEnvironmentVariable("AWS_SECRET_ACCESS_KEY", "你的SecretAccessKey", "User")
    [System.Environment]::SetEnvironmentVariable("AWS_DEFAULT_REGION", "你的区域", "User")

设置完后重新打开终端。

---

## 第六步：启动 LiteLLM 代理服务

在终端运行：

    litellm --config ~/litellm/config.yaml --port 4000

启动成功后终端会显示类似：

    LiteLLM: Proxy initialized
    Running on http://localhost:4000

这个终端窗口要保持开着，不要关闭！

验证服务是否正常（新开一个终端）：

    curl http://localhost:4000/health

返回 JSON 内容即正常。

---

## 第七步：配置 Hermes 连接到 LiteLLM

在新开的终端运行这两行：

    hermes config set model.base_url http://localhost:4000/v1
    hermes config set model.default claude-sonnet

然后把占位 API Key 写入 Hermes 密钥文件（LiteLLM 本地不需要真实 key）：

    echo 'OPENAI_API_KEY=sk-dummy' >> ~/.hermes/.env

---

## 第八步：验证整体运行

运行：

    hermes chat -q "你好，简单介绍一下你自己"

如果 Hermes 正常回复，整个配置成功！

---

## 让 LiteLLM 后台持续运行（可选）

每次电脑重启后需要重新启动 LiteLLM。如果不想手动，可以让它在后台运行：

Mac/Linux：

    nohup litellm --config ~/litellm/config.yaml --port 4000 > ~/litellm/litellm.log 2>&1 &

停止后台 LiteLLM：

    pkill -f "litellm"

---

## 常见问题排查

报错 NoCredentialsError
AWS 凭证没有生效。确认第五步的环境变量已经设置，并重新打开终端后再试。

litellm 命令找不到
尝试用 python3 -m litellm 替代，或重新运行安装命令。

hermes 报错 Connection refused
LiteLLM 没有启动。先回到第六步启动它，再来配置 Hermes。

返回 AccessDeniedException
AWS 账号没有开通 Bedrock 对应模型的访问权限。需要到 AWS 控制台 → Bedrock → 模型访问 → 申请开通对应的 Claude 模型。

想换用其他模型（比如 Llama）
在 ~/litellm/config.yaml 里参照已有格式添加新条目，model 字段换成 Bedrock 上对应的模型 ID。

---

## 整体链路说明

你 → Hermes → LiteLLM（本地 localhost:4000）→ AWS Bedrock → Claude 模型

每次使用前，确保 LiteLLM 在后台运行即可直接使用 hermes 命令。
