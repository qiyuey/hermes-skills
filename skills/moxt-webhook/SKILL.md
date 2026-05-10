---
name: moxt-webhook
description: 通过 Webhook 向 Moxt AI 推送事件、触发自动化任务。用户说"触发 Moxt Webhook"、"通知 Moxt"、"Moxt 自动化"时加载此 skill。
version: 1.0.0
author: Hermes Agent
metadata:
  hermes:
    tags: [moxt, webhook, automation]
    related_skills: [moxt]
---

# moxt-webhook

向 Moxt Webhook URL 发送 HTTP POST，触发绑定的 AI 执行任务。

---

## 创建 Webhook

在 Moxt 网页端：**Settings → 自动化 → Webhook**

创建后获得：
- **Webhook URL**
- **Token**（用于 Authorization）

每个 Webhook 绑定一个 AI Teammate，处理逻辑在 Moxt 网页端配置。

---

## 调用

```bash
curl -X POST <webhook-url> \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <webhook-token>" \
  -d '{"event": "<事件名>", "data": {...}}'
```

---

## 典型场景

- CI/CD 流水线完成后通知 Moxt AI 生成部署报告
- 工单系统新建 issue 时触发 AI 分类处理
- 定时任务（cron）推送数据给 AI 分析

---

## Pitfalls

- Webhook URL 和 Token 都在网页端创建时生成，CLI 不提供管理 Webhook 的命令
- 如果 AI 没有响应，先在 Moxt 网页端检查 Webhook 的触发日志
- `data` 字段内容由绑定 AI 的 prompt 决定，格式不对时 AI 可能忽略或误解
