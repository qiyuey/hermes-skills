---
name: moxt-webhook
description: 通过 Webhook 向 Moxt AI 推送事件、触发自动化任务。用户说"触发 Moxt Webhook"、"通知 Moxt"、"Moxt 自动化"时加载此 skill。
version: 1.1.0
author: Hermes Agent
metadata:
  hermes:
    tags: [moxt, webhook, automation]
    related_skills: [moxt]
    required_environment_variables:
      - name: MOXT_WEBHOOK_TOKEN
        description: Moxt Webhook Token，在 moxt.ai → Settings → 自动化 → Webhook 创建后获得
      - name: MOXT_WEBHOOK_URL
        description: Moxt Webhook URL，在 moxt.ai → Settings → 自动化 → Webhook 创建后获得
---

# moxt-webhook

向 Moxt Webhook URL 发送 HTTP POST，触发绑定的 AI Teammate 执行任务。

---

## 前置准备

**在 Moxt 网页端创建 Webhook**：Settings → 自动化 → Webhook

创建后，将 URL 和 Token 配置到 Hermes 环境变量：

```bash
hermes config set env.MOXT_WEBHOOK_URL "https://..."
hermes config set env.MOXT_WEBHOOK_TOKEN "..."
```

或直接在 shell 中临时设置：

```bash
export MOXT_WEBHOOK_URL=<webhook-url>
export MOXT_WEBHOOK_TOKEN=<webhook-token>
```

每个 Webhook 绑定一个 AI Teammate，处理逻辑在 Moxt 网页端的 Webhook 配置中定义。

---

## 调用

```bash
curl -X POST "$MOXT_WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOXT_WEBHOOK_TOKEN" \
  -d '{"event": "<事件名>", "data": {...}}'
```

---

## 典型场景

- CI/CD 流水线完成后通知 Moxt AI 生成部署报告
- 工单系统新建 issue 时触发 AI 分类处理
- 定时任务（cron）推送数据给 AI 分析

---

## Pitfalls

- Webhook URL 和 Token 都在网页端创建时生成，**CLI 不提供管理 Webhook 的命令**
- `data` 字段内容由绑定 AI 的 prompt 决定，格式不对时 AI 可能忽略或误解，先在网页端测试
- AI 没有响应时，先在 Moxt 网页端检查该 Webhook 的触发日志
- 每个 Webhook 绑定一个 AI，多个 AI 需要多个 Webhook URL（分别配置不同环境变量）
