---
name: litellm-proxy-setup
description: 配置 Hermes Agent 使用自托管的 LiteLLM proxy 作为模型提供商。涵盖 config.yaml 设置、多模型切换和 custom_providers 配置方式。
version: 1.0.0
author: qiyuey
license: MIT
metadata:
  hermes:
    tags: [hermes, litellm, proxy, model, configuration]
---

# Hermes + LiteLLM Proxy 配置

将自托管的 LiteLLM proxy 作为 Hermes 的模型提供商，通过单一端点暴露多个模型（opus/sonnet/haiku）。

## 前置条件

- LiteLLM proxy 已运行并可访问（如 `http://192.168.1.1:4000`）
- LiteLLM proxy 已配置模型别名（claude-opus、claude-sonnet、claude-haiku）
- Hermes 已安装

## 第一步：获取 LiteLLM 配置

请用户提供 LiteLLM proxy 的 `config.yaml` 文件内容作为上下文（粘贴到对话中），以便准确获取：

- proxy 地址和端口
- 已配置的模型别名（`model_name`）
- API key 设置方式

## config.yaml 配置

```yaml
model:
  default: claude-sonnet
  provider: custom:litellm
  base_url: ''         # 留空，从 custom_providers 读取
  context_length: 1000000

custom_providers:
- name: litellm
  base_url: http://<your-litellm-host>:4000/v1
  api_key: <your-litellm-api-key>
  model: claude-sonnet          # 默认模型，/model 切换时排第一
  models:
    claude-opus: {}
    claude-sonnet: {}
    claude-haiku: {}
```

## 关键说明

1. `provider` 写 `custom:litellm`，名字与 `custom_providers[].name` 对应
2. `model.base_url` 留空，实际地址在 `custom_providers[].base_url` 定义
3. `models:` 字典决定 `/model` 切换时的可选列表，key 必须与 LiteLLM proxy 里配置的路由名一致
4. 默认模型（`model:`）在列表中始终排第一

## 上下文压缩 / 辅助模型

建议使用 `auto`，自动复用主 provider，无需额外配置：

```yaml
auxiliary:
  compression:
    provider: auto
```

## 切换模型

```
/model              # 交互式选择，列出 custom_providers.models 下所有模型
/model claude-opus  # 直接切换
```

## 本地补丁：修复 /model 只显示一个模型

Hermes 上游代码（`hermes_cli/model_switch.py`）的 `list_authenticated_providers()` 函数只读取 `custom_providers[].model` 单个字段，忽略了 `models:` 字典，导致 `/model` 切换列表只显示默认模型。

相关上游 PR：#7783、#8770（截至 2026-04-15 尚未合并）。

**修复方法**：在 `model_switch.py` Section 4（custom_providers 处理块）读取完 `default_model` 后，追加以下代码：

```python
# Also include models listed in the "models" dict/list field
cfg_models = entry.get("models") or {}
if isinstance(cfg_models, dict):
    cfg_models = list(cfg_models.keys())
if isinstance(cfg_models, list):
    for m in cfg_models:
        m = (m or "").strip()
        if m and m not in groups[slug]["models"]:
            groups[slug]["models"].append(m)
```

定位方式：搜索 `groups[slug]["models"].append(default_model)` 这行，在其后插入上述代码块。

上游合并后可直接 `git pull` 覆盖本地补丁。

## 注意事项

- LiteLLM proxy 里的模型别名必须和 `models:` 字典的 key 完全一致，否则请求会 404
- 版本化名字（如 `anthropic/claude-sonnet-4.6`）要在 LiteLLM 那边配置路由，hermes 这边写什么名字取决于 proxy 暴露什么
- `models:` 字段需要 hermes 打了对应 patch（PR #7783 / #8770），upstream 版本可能只显示默认模型
