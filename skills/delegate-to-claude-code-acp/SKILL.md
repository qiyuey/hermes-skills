---
name: delegate-to-claude-code-acp
description: '【MUST USE】当用户消息中出现 Claude Code / cc / 让 Claude 帮我做 / 让 CC 写等关键词,或任务是生成超过 30 行代码 / 跨多文件编辑 / 严格按步骤执行多轮编码任务时,必须使用本 skill 通过 claude-as-acp 命令委托给 Claude Code,不要自己写代码。'
version: 2.1.0
openclaw: true
---

# Delegate to Claude Code (via ACP)

把复杂编码任务委托给 **Claude Code CLI**(通过 ACP 协议调用)。本 skill 用 `claude-as-acp` wrapper 调 `claude-agent-acp`,Claude Code 的输出以**结构化 NDJSON** 返回,每行一个事件。

## 何时用

**用**(满足任一):
1. 用户消息里明确提到 `Claude Code` / `claude code` / `cc`
2. 用户说 "让 Claude 帮我..." / "让它继续改..."(延续之前用 CC 做的任务)
3. 任务需要生成 > 30 行代码
4. 任务跨多个文件
5. 任务严格按 1/2/3 步骤执行

**不用**:简单问答、概念解释、一两行命令、单纯查文档。

## 调用命令

```
bash pty:true command:"claude-as-acp --task <任务名> '<完整任务描述>'"
```

**只有一个参数要想:`--task`。** 其他东西(workdir、session 续接、sessionId)wrapper 自己根据 task 名决定。

| 参数 | 说明 |
|---|---|
| `pty:true` | 必须(claude-agent-acp 需要终端) |
| `--task <name>` | **必填**。任务名。**同名 = 同一个 Claude Code 会话(有记忆),不同名 = 全新会话(从零)**。规则详见下节 |
| `--timeout <sec>` | 可选。wrapper 最长运行秒数,超时会优雅 cancel。默认 3300 秒(55 分钟),上限 3500 |

### 怎么选 `--task` 名字

**核心规则**:**同名 = 有记忆续接,不同名 = 从零开始**。

**命名格式**(wrapper 会强制校验,错了立即报错):
- 小写字母、数字、`-`,1-64 字符
- 不能以 `-` 开头
- 正则:`^[a-z0-9][a-z0-9-]{0,63}$`
- 好名字:`yuque-adapter`、`hello-py`、`refactor-user-auth`、`todo-poc`
- 坏名字:`Yuque Adapter`(大写+空格)、`task_1`(下划线)、`-foo`(起始破折号)

**什么时候保持同名**:

用户消息里出现下面任一信号时,**延续上一次的 task 名**:
- "继续/再/改/加/让它/刚才/那个/上次"
- 用户没切话题,还在聊同一个东西
- 用户没明确说"新任务"

**什么时候换新名**:
- 用户明确说"新任务"/"换一个"/"从头做个别的"
- 用户聊的是完全不相关的事情
- 你心里有 50% 以上的把握这不是同一个任务

**不确定时保守选择 = 用旧名**。Claude Code 能容忍多余上下文,但没法凭空造出丢失的上下文。

**记住当前 task 名的方式**:每次调 wrapper 后,在 reply 结尾显式写一行 `📎 CC task: <名字>`,下一轮从自己上一条 reply 里抄。

### `--timeout` 参考

| 任务类型 | 推荐 |
|---|---|
| 简单问题 / 少量代码 | `--timeout 300`(5 分钟) |
| 多文件编辑 / 跑测试 | `--timeout 900`(15 分钟) |
| 大规模重构 / 深度调研 | `--timeout 1800`(30 分钟) |
| 不确定 | **不传**,用默认 3300 秒(55 分钟) |

wrapper 超时**不能超过 3500 秒**(外层 openclaw agent turn 默认 3600,要留 100 秒给 wrapper 自己清理)。

## Wrapper 内部做的事(简单了解)

你只需要传 `--task` 一个参数。wrapper 内部自动做:

1. 把 task 名映射成 workdir:`/root/claudecode-workspace/<task>/`,自动 mkdir
2. 看 `/root/.claude/projects/-root-claudecode-workspace-<task>/` 里有没有历史 jsonl
   - 有 → 挑最新那个 **自动续接**(你 0 感知)
   - 没 → **新建 session**
3. 元数据通过 stdout NDJSON 事件告诉你(见下节)

整个目录都在持久盘上(`/root` 是 bot 的 persistent volume),**pod 重启也不丢**。

## 输出事件的解读

wrapper 吐 NDJSON,每行一个事件:

| event type | 含义 | 怎么用 |
|---|---|---|
| `task` | 第一行必出,含 `{name, cwd, resuming}`,`resuming:true` 表示续接已有 session | 感知 wrapper 确认了你的 task 名 |
| `session` | ACP 层 session id(技术细节,你不用存) | 可忽略 |
| `mode_set` | 权限模式切到了 bypassPermissions | 可忽略 |
| `text` | Claude 的回复文本(流式分片) | 拼起来当 CC 主回复内容 |
| `tool_call` | Claude 开始调一个工具 | 展示为 "🔧 正在 <name>..." |
| `tool_update` | 工具调用中/完成,含 input/stdout/resp_type | `status:completed` 折叠为简要摘要,`status:failed` 展示失败原因 |
| `usage` | 本轮 token 消耗和费用 | 可忽略或作为元信息 |
| `wrapper_timeout` | wrapper 触发超时,正在 cancel | 告诉用户"任务超时了,已保存当前进度,可以用同一 task 名继续" |
| `done` | 本轮结束,含 `stop_reason`、`error`、`task`、`next_call_hint` | `error` 非空就告诉用户失败原因;否则给收尾总结 |

## 呈现给用户的方式

bash 的 tool_result 把 wrapper 的全部 NDJSON 给你。你要做:

1. 拼所有 `text` 作为 CC 主回复内容
2. `tool_call` / `tool_update` 折叠成进度标记(如 "🔧 Bash 运行成功" / "📝 写入 hello.py")
3. 看 `done.error`,非空时报告失败原因
4. reply 结尾写上 `📎 CC task: <名字>`,方便下一轮延续(以及你自己读回来)

**不要**把整坨 NDJSON 直接扔给用户看。那是给你(orchestrator)读的,用户要看的是自然语言总结。

## 完整流程示例

**用户 Round 1**:"用 Claude Code 写一个打印 Hi 的 Python 脚本"

你决定任务名叫 `hello-py`。调:

```
bash pty:true command:"claude-as-acp --task hello-py 'Write hello.py which prints: Hi'"
```

你看到 `{"type":"task","name":"hello-py","cwd":"/root/claudecode-workspace/hello-py","resuming":false}`,再看到 Write tool 完成事件,最后 `done.stop_reason:end_turn`。告诉用户:

> 已创建 `/root/claudecode-workspace/hello-py/hello.py`,内容是 `print("Hi")`
>
> 📎 CC task: **hello-py**

**用户 Round 2**:"让它再加一行打印当前时间"

你从上一条 reply 末尾看到 `CC task: hello-py`,**继续用同一个名字**:

```
bash pty:true command:"claude-as-acp --task hello-py 'Add a second line printing datetime.now()'"
```

wrapper 自动发现这个 task 的历史 jsonl,emit `{"type":"task","name":"hello-py","resuming":true}`,Claude 看到 Round 1 的历史,直接 Edit 文件。

**用户 Round 3**:"让它跑一下"

同名继续:

```
bash pty:true command:"claude-as-acp --task hello-py 'Run python3 hello.py and show me the output'"
```

**用户 Round 4**:"写个新的,用 Node 写一个 todo list"

**切任务了**,换新名字:

```
bash pty:true command:"claude-as-acp --task todo-list-node 'Write a simple Node.js todo list CLI'"
```

wrapper 看到新名字没有历史 jsonl,新建 session,跟 `hello-py` 完全隔离。

## 故障排除

| 现象 | 原因 | 解决 |
|---|---|---|
| `{"type":"error","message":"missing --task arg"}` | 你忘了传 `--task <name>` | 必须传 |
| `{"type":"error","message":"invalid task name ..."}` | task 名含非法字符 | 换成 kebab-case,比如 `my-task` 不是 `My Task` 或 `my_task` |
| `{"type":"error","message":"Authentication required"}` | AWS 凭证没初始化 | 检查 `/root/.aws/credentials` 是否有 `[claude-profile]` 段 |
| `command not found: claude-as-acp` | wrapper 没装 | `bash /root/.openclaw/skills/yuanbot/yuanli-skill-hub/skills/delegate-to-claude-code-acp/install.sh` |
| 长时间没输出 | Bedrock 网络慢 | 正常,首 token 延迟 3-10 秒 |

## 两条铁律

1. **必须 `pty:true`**(claude-agent-acp 和 claude CLI 都要正常终端)
2. **延续任务必须用同一 `--task` 名字**,不同 = 失忆从头 = 白做一遍浪费用户时间

仅此而已。
