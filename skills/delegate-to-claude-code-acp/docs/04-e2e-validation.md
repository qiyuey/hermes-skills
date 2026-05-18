# 04 · 端到端 3 轮对话验证(实测证据)

> **⚠️ 版本提示**:本文档记录的是 **v1.0.0 API** 下的端到端实测(用 `--resume` 手动传 sessionId)。当前 **v2.0.0** 已用单参数 `--task <name>` 替代 `--resume`,v2 的测试证据见 [`CHANGELOG.md`](CHANGELOG.md) 2.0.0 节末尾的 5-case 测试表。**本文档的价值在于证明了 "跨进程续接能 work" 这个底层能力**,v2 只是把主 agent 侧的对接 API 换成更简单的形式,底层机制未变。


本文记录一次真实的 3 轮用户对话,通过 OpenClaw 主 agent → skill → wrapper → Claude Code → Bedrock 的完整链路。所有 command / timestamp / session id 都是**真实执行产生的**,不是示例。

测试 bot:`xinjianbot4test-0`(10.134.213.164,EKS pod)
测试日期:2026-04-15
OpenClaw version:2026.2.9 (33c75cb)
claude CLI version(测试时):2.0.42
claude-agent-acp version:0.27.0

---

## 1. 准备

```bash
# OpenClaw session id(用户侧)
TEST_SID=cc-e2e-1776233961

# 清理环境
rm -f /tmp/claude-as-acp.log
rm -rf /tmp/cc-e2e && mkdir -p /tmp/cc-e2e
```

---

## 2. Round 1 · 创建文件

### 用户消息

```
用 Claude Code 在 /tmp/cc-e2e 目录下写一个 Python 脚本 hello.py,
运行后打印一行:Hi from Round 1
```

### 执行命令

```bash
openclaw agent --session-id cc-e2e-1776233961 --json --timeout 150 \
  -m "用 Claude Code 在 /tmp/cc-e2e 目录下写一个 Python 脚本 hello.py，运行后打印一行：Hi from Round 1"
```

### OpenClaw 主 agent 的执行轨迹(从 openclaw session jsonl 提取)

```
[user] 用 Claude Code 在 /tmp/cc-e2e 目录下写一个 Python 脚本 hello.py ...

[assistant] 我会使用 Claude Code 来完成这个任务。首先让我读取相关的 skill 文档。

[assistant:tool] read({"file_path": "/root/.openclaw/skills/yuanbot/yuanli-skill-hub/
                     skills/delegate-to-claude-code-acp/SKILL.md"})

[toolResult] (SKILL.md 内容被读完)

[assistant] 好的,我现在使用 Claude Code 来完成这个任务。首先确保目录存在,
            然后调用 claude-as-acp。

[assistant:tool] exec({
  "command": "mkdir -p /tmp/cc-e2e"
})

[assistant:tool] exec({
  "command": "claude-as-acp --cwd /tmp/cc-e2e 'Create a Python script hello.py
              in /tmp/cc-e2e that prints: Hi from Round 1'",
  "workdir": "/tmp/cc-e2e",
  "pty": true
})

[toolResult] Command still running (session salty-reef, pid 10512).
             Use process (list/poll/log/write/kill/clear/remove) for follow-up.

[assistant:tool] process({"action": "poll", "sessionId": "salty-reef", "limit": 10000})

[toolResult] {"type":"session","sessionId":"cd649582-f194-48fe-8429-03f467730f40","resumed":false}
             {"type":"text","text":"I'll create the"}
             {"type":"text","text":" Python"}
             {"type":"text","text":" script for you."}
             {"type":"tool_call","id":"toolu_bdrk_015797W7iduAxpwKncgq9pcs",
              "name":"Write","title":"Write","status":"pending"}
             {"type":"tool_update","id":"toolu_bdrk_015797W7iduAx...

[assistant] 完成了!✅

Claude Code 已经在 `/tmp/cc-e2e` 目录下创建了 `hello.py` 脚本,内容是打印 "Hi from Round 1"。

📝 文件已创建:`/tmp/cc-e2e/hello.py`

*(CC session id 是 cd649582-f194-48fe-8429-03f467730f40,
  如果后续需要继续修改这个任务,可以复用这个 session)*
```

### 关键观察(Round 1)

1. ✅ **主 agent 主动读了 SKILL.md**(第二步就是 read SKILL.md)—— 说明触发词识别有效
2. ✅ **主 agent 正确构造 bash 调用**(`pty: true`, `workdir: /tmp/cc-e2e`, 正确的 `claude-as-acp --cwd` 命令)
3. ✅ **主 agent 用 background + process.poll 模式读 stdout**(不是一次性等完)
4. ✅ **从 NDJSON 提取 sessionId** `cd649582-f194-48fe-8429-03f467730f40`
5. ✅ **明确记录在 reply 里**,格式是"CC session id 是 cd649582-..." —— 这是后续轮次能续接的前提

### Wrapper 日志

```
[2026-04-15T06:19:43.396Z] [10514] START resume=null cwd=/tmp/cc-e2e prompt_len=75
[2026-04-15T06:19:46.535Z] [10514] sessionId=cd649582-f194-48fe-8429-03f467730f40
```

### 文件结果

```bash
$ ls /tmp/cc-e2e/
hello.py

$ cat /tmp/cc-e2e/hello.py
print("Hi from Round 1")
```

---

## 3. Round 2 · 续接修改

### 用户消息

```
让 Claude Code 在刚才那个 hello.py 文件里再加一行,打印:Still here in Round 2
```

### 执行命令

```bash
openclaw agent --session-id cc-e2e-1776233961 --json --timeout 150 \
  -m "让 Claude Code 在刚才那个 hello.py 文件里再加一行，打印：Still here in Round 2"
```

注意:**同一个 openclaw session** (`cc-e2e-1776233961`),这样主 agent 才能在 in-context 历史里看到 Round 1 的 assistant reply(含那个 sessionId)。

### 主 agent 的关键决策(从 openclaw session jsonl 提取)

```
[user] 让 Claude Code 在刚才那个 hello.py 文件里再加一行 ...

[assistant:tool] exec({
  "command": "claude-as-acp --cwd /tmp/cc-e2e \
              --resume cd649582-f194-48fe-8429-03f467730f40 \
              'Add a second line to hello.py that prints: Still here in Round 2'",
  "workdir": "/tmp/cc-e2e",
  "pty": true
})

[assistant] 完成了!✅

Claude Code 已经在原有的 `hello.py` 文件中添加了第二行打印语句。
现在文件包含两行输出:
1. Hi from Round 1
2. Still here in Round 2

🔧 操作流程:
- 📖 读取了 hello.py 的当前内容
- ✏️ 编辑文件,添加了第二行打印语句
```

### 关键观察(Round 2)

1. ✅ **主 agent 从 Round 1 的 in-context 历史里提取了 sessionId**,正确地传 `--resume cd649582-...`
2. ✅ **主 agent 识别出这是"续接",而不是新任务**(用户说"刚才那个文件")
3. ✅ **Claude Code 里面走的是 Read + Edit**,不是 Write 覆盖 —— 说明 `loadSession` 真的生效了,claude 的内存里有 Round 1 的上下文,知道那个文件是它自己写的
4. ✅ **主 agent reply 里明确列了操作流程**(Read → Edit),说明它正确 parse 了 NDJSON 里的 `tool_call` / `tool_update` 事件

### Wrapper 日志

```
[2026-04-15T06:20:59.884Z] [10624] START resume=cd649582-f194-48fe-8429-03f467730f40 cwd=/tmp/cc-e2e prompt_len=64
[2026-04-15T06:21:02.854Z] [10624] sessionId=cd649582-f194-48fe-8429-03f467730f40
```

注意 pid 是 **10624**(Round 1 是 10514),**全新的 wrapper 进程**。sessionId 还是 `cd649582-...`。

### 文件结果

```bash
$ cat /tmp/cc-e2e/hello.py
print("Hi from Round 1")
print("Still here in Round 2")
```

---

## 4. Round 3 · 续接运行

### 用户消息

```
让 Claude Code 跑一下这个脚本(python3 hello.py),把输出告诉我
```

### 主 agent 的决策(从 jsonl 提取)

```
[user] 让 Claude Code 跑一下这个脚本 ...

[assistant:tool] exec({
  "command": "claude-as-acp --cwd /tmp/cc-e2e \
              --resume cd649582-f194-48fe-8429-03f467730f40 \
              'Run python3 hello.py and show me the output'"
})

[assistant] 完成!✅ 脚本运行成功!

**输出结果:**
​```
Hi from Round 1
Still here in Round 2
​```

🔧 Claude Code 执行了 python3 hello.py,两行打印语句都正常输出了。
```

### 关键观察(Round 3)

1. ✅ **sessionId 再次正确传递**,继续是 `cd649582-...`
2. ✅ **Claude Code 调 Bash 工具跑了 python3 hello.py**,拿到真实 stdout
3. ✅ **主 agent 把 Bash tool_update 的 stdout 提取到 reply 里展示给用户**

### Wrapper 日志

```
[2026-04-15T06:21:59.248Z] [10721] START resume=cd649582-f194-48fe-8429-03f467730f40 cwd=/tmp/cc-e2e prompt_len=43
[2026-04-15T06:22:02.231Z] [10721] sessionId=cd649582-f194-48fe-8429-03f467730f40
```

wrapper pid: **10721**。又是一个全新进程。

---

## 5. 整体日志汇总

wrapper 3 轮完整日志(只显示关键行):

```
[14:19:43] [pid=10514] START resume=null                                       cwd=/tmp/cc-e2e prompt_len=75
[14:19:46] [pid=10514] sessionId=cd649582-f194-48fe-8429-03f467730f40
[14:20:59] [pid=10624] START resume=cd649582-f194-48fe-8429-03f467730f40       cwd=/tmp/cc-e2e prompt_len=64
[14:21:02] [pid=10624] sessionId=cd649582-f194-48fe-8429-03f467730f40
[14:21:59] [pid=10721] START resume=cd649582-f194-48fe-8429-03f467730f40       cwd=/tmp/cc-e2e prompt_len=43
[14:22:02] [pid=10721] sessionId=cd649582-f194-48fe-8429-03f467730f40
```

**3 个独立的 wrapper 进程 pid(10514 / 10624 / 10721),共享同一个 Claude Code session id `cd649582-...`。** 这就是"跨进程 session 续接"的实证。

---

## 6. 磁盘状态检查

Round 3 完成后的 `~/.claude/projects/`:

```bash
$ ls -la /root/.claude/projects/-tmp-cc-e2e/
-rw------- 1 root root 33642 Apr 15 14:22 cd649582-f194-48fe-8429-03f467730f40.jsonl

$ wc -l /root/.claude/projects/-tmp-cc-e2e/cd649582-f194-48fe-8429-03f467730f40.jsonl
42 /root/.claude/projects/-tmp-cc-e2e/cd649582-f194-48fe-8429-03f467730f40.jsonl
```

42 行,33KB。包含 3 轮所有 user / assistant / tool_use / tool_result。这个文件是 3 轮对话能连续的**唯一载体**。

类型分布:

```
16 × user (user 消息 + toolResult 两种都算 user role)
16 × assistant (LLM 回复 + tool_use 块)
 6 × queue-operation
 3 × last-prompt (正好 3 轮,每轮一个)
 1 × attachment
```

---

## 7. Round 3 进程树(运行瞬间)

```
PID   PPID  COMMAND
107   1     openclaw-gateway          ← 常驻,Round 1/2/3 都是它
10721 107   node /usr/local/bin/claude-as-acp   ← wrapper,只活这一轮
10723 10721 node /usr/bin/claude-agent-acp      ← ACP server 子进程
10732 10723 claude                              ← Claude Code CLI 子进程
```

Round 3 结束后:

```
$ pgrep -f "claude(-agent)?" | wc -l
0
```

整棵树自动清理,无孤儿。

---

## 8. 验证结论

| 验证项 | 结果 | 证据 |
|---|---|---|
| 触发词识别 | ✅ | Round 1 主 agent 立即 read SKILL.md |
| wrapper 调用格式正确 | ✅ | exec 命令完整,pty:true,workdir 正确 |
| sessionId 提取 | ✅ | Round 1 reply 明确写"CC session id 是 cd649582-..." |
| sessionId 跨轮次记忆 | ✅ | Round 2 和 Round 3 的 exec 命令都带 `--resume cd649582-...` |
| Claude Code 跨进程续接 | ✅ | Round 2 用 Read+Edit(不是 Write 覆盖),说明 loadSession 真生效 |
| 文件累积结果正确 | ✅ | 最终两行,且 `python3 hello.py` 输出两行 |
| 进程无泄漏 | ✅ | ps 在每轮结束后无残留 |
| wrapper 日志跟预期一致 | ✅ | 3 行 START + 3 行 sessionId,全部匹配 |

---

## 9. 和 phase2(纯 bash 方案)的对比

phase2 用的是 `claude -p` 直接跑,输出是自然语言文本。在同样的 3 轮对话场景下:

| 维度 | phase2(bash 方案) | phase4(ACP 方案,本 skill) |
|---|---|---|
| sessionId 提取 | 主 agent 要从 `ls -t ~/.claude/projects/<hash>/*.jsonl` 去猜 | NDJSON 第一行显式结构化 |
| 续接机制 | wrapper 内部 auto-resume(双刃剑:同 workdir 跨任务会错误 resume) | 大模型显式传 `--resume <sid>`,无歧义 |
| Tool 结果可见性 | 混合 TTY 文本,难 parse | 结构化 `tool_call` / `tool_update` 事件 |
| Hallucination 风险 | 高(phase2 §4.2 坑 9,claude 编造成功) | 低(工具状态显式枚举,`stopReason` 明确) |
| 权限处理 | 硬编码 `--dangerously-skip-permissions`(root 下失败) | ACP `requestPermission` 协议级 auto-approve |

phase2 3 轮对话的典型失败模式是:主 agent 第二轮忘记带 `--resume`,或者带了但底层 jsonl 找不到(因为 auto-resume 用了错的文件)。phase4 没复现这些坑。

---

## 10. 一张图总结

```
     t=14:19:43                  t=14:20:59                  t=14:21:59
  ╭──────────────╮            ╭──────────────╮            ╭──────────────╮
  │ wrapper      │            │ wrapper      │            │ wrapper      │
  │ pid 10514    │            │ pid 10624    │            │ pid 10721    │
  │ resume=null  │            │ resume=cd649582           │ resume=cd649582
  │              │            │              │            │              │
  │ Round 1      │            │ Round 2      │            │ Round 3      │
  │ Write file   │            │ Read + Edit  │            │ Bash run     │
  ╰──────┬───────╯            ╰──────┬───────╯            ╰──────┬───────╯
         │ 写                         │ 读+追加                    │ 读+追加
         ▼                            ▼                            ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │                                                                 │
    │  /root/.claude/projects/-tmp-cc-e2e/cd649582-*.jsonl (33 KB)     │
    │                                                                 │
    └─────────────────────────────────────────────────────────────────┘
         ▲                            ▲                            ▲
         │                            │                            │
  OpenClaw 主 agent       OpenClaw 主 agent           OpenClaw 主 agent
  存 sessionId            读自己的上轮 reply,          读自己的上轮 reply,
  到自己的 reply          找到 sessionId,             找到 sessionId,
                          传 --resume                 传 --resume
```
