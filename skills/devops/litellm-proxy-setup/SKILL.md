---
name: litellm-proxy-setup
description: Configure Hermes Agent to use a self-hosted LiteLLM proxy as the model provider. Covers config.yaml setup, multi-model selection, and the custom_providers pattern.
version: 1.0.0
author: qiyuey
license: MIT
metadata:
  hermes:
    tags: [hermes, litellm, proxy, model, configuration]
---

# Hermes + LiteLLM Proxy Setup

Use a self-hosted LiteLLM proxy as the Hermes model provider, exposing multiple models (opus/sonnet/haiku) through a single endpoint.

## Prerequisites

- LiteLLM proxy running and accessible (e.g. `http://192.168.1.1:4000`)
- LiteLLM proxy configured with model aliases (claude-opus, claude-sonnet, claude-haiku)
- Hermes installed

## config.yaml

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

## Key Points

1. `provider` 写 `custom:litellm`，名字与 `custom_providers[].name` 对应
2. `model.base_url` 留空，实际地址在 `custom_providers[].base_url` 定义
3. `models:` 字典决定 `/model` 切换时的可选列表，key 必须与 LiteLLM proxy 里配置的路由名一致
4. 默认模型 (`model:`) 在列表中始终排第一

## Compression / Auxiliary Model

上下文压缩建议用轻量模型（haiku），单独配置：

```yaml
auxiliary:
  compression:
    provider: auto   # 自动使用主 provider，无需重复配置
```

## Switching Models

```
/model            # 交互式选择，列出 custom_providers.models 下所有模型
/model claude-opus  # 直接切换
```

## Pitfalls

- LiteLLM proxy 里的模型别名必须和 `models:` 字典的 key 完全一致，否则请求会 404
- 版本化名字（如 `anthropic/claude-sonnet-4.6`）要在 LiteLLM 那边配置路由，hermes 这边写什么名字取决于 proxy 暴露什么
- `models:` 字段需要 hermes 打了对应 patch（PR #7783 / #8770），upstream 版本可能只显示默认模型
