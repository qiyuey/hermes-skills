# 03 · 进程生命周期与清理机制

> **⚠️ 版本提示**:本文档示例命令行使用 **v1.0.0 API**(`--resume <sid>`/`--cwd <path>`)。当前是 **v2.0.0** 单参数 `--task <name>`。**生命周期和清理机制完全没变** —— wrapper 仍然是短寿命进程,stdin EOF 级联清理,四层 timeout 模型。只是主 agent 调用格式不同。详见 [`CHANGELOG.md`](CHANGELOG.md) 2.0.0 节。


> 读完这篇你能回答:
>
> 1. 一次 delegation 起了几个进程?
> 2. 这些进程什么时候起,什么时候死?
> 3. 空闲一段时间会不会自动清理?
> 4. 会不会泄漏孤儿进程?

---

## 1. 进程树

```
bash tool (openclaw)
  └── wrapper (node /usr/local/bin/claude-as-acp)       ← L3
        └── claude-agent-acp (node /usr/bin/claude-agent-acp)  ← L4
              └── claude CLI (/usr/local/bin/claude)    ← L5
```

一次 delegation = **3 个新进程**(L3, L4, L5)。bash tool 里的 shell 不算,它是 openclaw gateway 的子进程,一直在。

实测进程树(从 `ps -eo pid,ppid,pgid,comm` 拿的):

```
  PID  PPID PGID COMMAND
 9422  9417 9422 timeout      ← 我们手动 timeout 包了一下
 9424  9422 9422 node         ← wrapper (L3)
 9431  9424 9422 node         ← claude-agent-acp (L4)
 9444  9431 9422 claude       ← claude CLI (L5)
```

注意 PGID 全是 9422 —— 整棵树是**同一个进程组**。这意味着给进程组发信号(比如 `kill -TERM -9422`)会一次性端掉所有人,不会漏。

---

## 2. 生命周期时间轴

```
t=0ms    [wrapper] spawn,parse args
t=10ms   [wrapper] 检查 /root/.aws/credentials(如果缺,bootstrap)
t=20ms   [wrapper] spawn claude-agent-acp 子进程
t=500ms  [L4] Node 启动 + 加载 @anthropic-ai/claude-agent-sdk
t=600ms  [wrapper] conn.initialize 成功,握手完
t=650ms  [wrapper] conn.newSession or conn.loadSession
t=700ms  [L4] 内部 spawn claude CLI (L5)
t=1200ms [L5] claude CLI 启动,读 ~/.claude 配置
                 如果 loadSession:读 jsonl,重建 context
                 emit {"type":"system","subtype":"init",...} 到 stdin/stdout
t=1300ms [L4] 收到 init,newSession/loadSession 返回
t=1400ms [wrapper] emit {"type":"session","sessionId":...} 到 stdout
t=1500ms [wrapper] conn.prompt(...)
t=1500ms [L4] 把 prompt 转成 stream-json,写 L5 stdin
t=1600ms [L5] 发请求到 Bedrock(经 HTTPS_PROXY)
t=4000ms [L5] Bedrock 开始返回首 tokens
              每个 token 触发一个 stream_event 写 L5 stdout
t=4000ms [L4] 解析 stream_event,发 ACP sessionUpdate 给 L3
t=4000ms [wrapper] 在 sessionUpdate 回调里 emit NDJSON 到 stdout
t=6500ms [L5] Tool use 到了 → 调 Bash/Write/Read,把 tool_result 塞回 LLM
t=8000ms [L5] LLM end_turn,emit {"type":"result","subtype":"success",...}
t=8100ms [L4] 收到 result,prompt() 的 promise resolve
t=8100ms [wrapper] conn.prompt 返回,stopReason="end_turn"
t=8150ms [wrapper] emit {"type":"done","stop_reason":"end_turn",...}
t=8200ms [wrapper] proc.stdin.end()  ← 关 ACP server 的 stdin
t=8250ms [L4] 看到 stdin EOF,connection.closed promise resolve,
              shutdown() → agent.dispose() → kill L5
t=8400ms [L5] SIGTERM,死
t=8450ms [L4] process.exit(0)
t=8500ms [wrapper] acp 子进程 exited 回调触发
t=8550ms [wrapper] process.exit(0)
t=8550ms [openclaw bash tool] wrapper exited,收集 stdout 返回给OpenClaw 主 agent
```

**一次典型 delegation 的总耗时** ~8-10 秒。冷启动开销 <1 秒,LLM 调用是大头。

---

## 3. OpenClaw 怎么让 wrapper 进程自己退出

前一节的时间轴是 wrapper 的**内部视角**。但一个外部视角的问题同样重要:**OpenClaw 主 agent 发完 bash 调用之后,什么时候、怎么让 wrapper 这个进程回到"已结束"状态,把 tool_result 收走?**

答案分两种情形。

### 3.1 正常情形(95% 的 case):OpenClaw 什么都不做,wrapper 自己退

这是最让人意外的地方 —— **OpenClaw 根本不主动"关"wrapper**。它只是发了 bash 调用,然后等。wrapper 跑完自己的 await 链后,会执行到脚本最末尾的 `process.exit(0)`,进程就自己死了。OpenClaw 的 bash 执行器通过 Node 的 `child.on("close", ...)` 事件收到通知,把累积的 stdout + exit code 包装成 tool_result 回传给主 agent。

证据在 `@mariozechner/pi-coding-agent/dist/core/bash-executor.js:36`:

```javascript
const child = spawn(shell, [...args, command], {
  detached: true,
  env: getShellEnv(),
  stdio: ["ignore", "pipe", "pipe"],
});
// ... handle stdout/stderr ...
child.on("close", (code) => {
  // code === null 表示被 kill,否则是正常退出码
  resolve({
    output: fullOutput,
    exitCode: cancelled ? undefined : code,
    cancelled: code === null,
    // ...
  });
});
```

也就是说,**OpenClaw 的"回收"就是 Node 的 `child.on("close")` 这一个事件监听**。wrapper 自己 `process.exit(0)` → Linux 内核把 exit code 传给 parent bash → bash 进程也退出(因为它是 `bash -c '...'` 模式) → bash 的 close 事件触发 → OpenClaw 把 stdout 收走。

实测:我们从 bot 上直接观察 wrapper 的父进程,看到的是:

```
  PID    PPID  COMMAND
12248  12238  node /usr/local/bin/claude-as-acp       ← wrapper
12238  12236  bash -c 'claude-as-acp --cwd ... ...'   ← openclaw spawn 的 bash
12236   1XX   node openclaw-gateway                   ← openclaw 主进程
```

wrapper 的直系父是 `bash -c`,爷爷才是 openclaw-gateway。wrapper 退出后,`bash -c` 也会自然退出(因为它唯一的任务就是跑那一条命令),然后 openclaw 的 child.on("close") 就触发了。整个链路是"自下而上"的正常退出,不需要任何信号、不需要任何 IPC。

**对 wrapper 内部的三层级联清理来说,这种正常情形就是 3.2 节讲的 stdin-EOF 路径**:wrapper 的 await 链跑完后主动 `proc.stdin.end()` → claude-agent-acp 看到 EOF → 级联死 → wrapper 本身也 exit → bash 退 → openclaw 收菜。

### 3.2 取消情形:OpenClaw 主动 SIGTERM 整棵进程树

如果任务跑到一半,主 agent 决定取消(比如用户发了 `/stop`、或换了话题、或外层 agent turn 超时触发),这时 OpenClaw 会主动打断 wrapper。

**先澄清四层 timeout,因为老读者(包括早期版本的本文)容易搞混**:

| 层 | 默认值 | 作用范围 |
|---|---|---|
| L1 `openclaw agent --timeout <sec>` | 600s(CLI 默认,通常被 L2 覆盖) | 覆盖整个 agent turn |
| L2 openclaw.json `agents.defaults.timeoutSeconds` | **3600s(1 小时)** | 整个 agent turn,覆盖 L1 |
| L3 bash tool 的 `timeout` 参数 | **无默认**(`pi-coding-agent` 源码原话: "no default timeout") | 单次 bash 调用 |
| L4 wrapper 自己的 `--timeout <sec>` | 3300s(55 min)| 单次 delegation,通过 `conn.cancel` 优雅打断 |

生产环境走企微聊天过来的请求,L1 没人传,L2 默认 3600s,L3 skill 里也没强制传(让 bash 随便跑),L4 wrapper 默认 3300s(给 agent turn 留 5 分钟余地)。**所以一次 delegation 的实际上限是 wrapper 的 L4 决定的,55 分钟**,这是"优雅 cancel"的路径。只有当 wrapper 本身出了问题(卡死没响应)且 L2 的 3600s 也到了,OpenClaw 才会走下面的"强制 SIGTERM"路径。

---

强制路径走的是 `pi-coding-agent` 源码里的:

```javascript
const abortHandler = () => {
  if (child.pid) {
    killProcessTree(child.pid);   // ← 关键
  }
};
options.signal?.addEventListener("abort", abortHandler, { once: true });
```

`killProcessTree(pid)` 的作用是:沿着进程树**从上往下**逐层 `kill -TERM` 所有后代。对我们的场景就是把 `bash` + `wrapper(node)` + `claude-agent-acp(node)` + `claude(node)` 四个进程都端掉。

SIGTERM 到达 wrapper 后,Node runtime 的默认 signal handler 会触发进程 exit。wrapper 退出时它的 stdout/stdin 管道关闭,传导给 claude-agent-acp(同时 claude-agent-acp 自己也直接收到了 SIGTERM,它有注册 `process.on("SIGTERM", shutdown)`,所以会走优雅关停)→ 再传导给 claude CLI → 整棵树一次性死透。

这种情况下 wrapper 的 `proc.stdin.end()` 末尾代码**可能根本执行不到**(进程已经被信号打断了),但这没关系 —— 因为 SIGTERM 本身就是关停信号,进程树里每个人都有 handler 在响应。

**一个 subtle 的点**:wrapper 进程组(`pgid`)跟 ACP server + claude CLI 是同一个。`kill -TERM -<pgid>` 一次就能打完所有人。这是 Unix 进程组机制自带的东西。我们不需要在 wrapper 里做任何"向子进程转发信号"的代码 —— 内核就做完了。

### 3.3 两种情形的 wrapper 出口对比

| 情形 | wrapper 被什么踢出 | wrapper 末尾代码能跑完吗 | 子进程怎么死 |
|---|---|---|---|
| **正常完成** | 自己跑完 await 链到 `process.exit(0)` | 是(主动 `proc.stdin.end()`) | stdin EOF 级联,不走信号 |
| **取消/abort** | OpenClaw SIGTERM 整棵进程组 | 不(Node 默认 SIGTERM 就直接退) | 同一信号同时到每个子进程,各自 handler 响应 |
| **OOM / kill -9 wrapper** | 不可捕捉,立刻死 | 不 | stdin pipe 写端被内核关闭,ACP server 读到 EPIPE/EOF 自清理 |

共同点:**无论哪种路径,整棵进程树都能清理干净,不留孤儿**。这不是 wrapper 的功劳 —— 本质是靠 Linux 内核的三个机制:

1. **parent 死 → child 的 stdin pipe 自动关**(管道语义)
2. **进程组信号广播**(pgid 级 kill)
3. **ACP server 自带的 `connection.closed.then(shutdown)` 监听**(源码里的主动防御)

wrapper 自己对清理的贡献**只有最后那个 `proc.stdin.end()`**,这是锦上添花,让正常退出路径更快(不用等 Node 自己的 pipe close detection),但即使 wrapper 不写这一行,子进程也会死。

### 3.4 实测验证:OpenClaw 侧看到的 wrapper 生命周期

从 E2E 测试(见 `04-e2e-validation.md`)的 openclaw session jsonl 提取的 tool 调用序列:

```
[assistant:tool] exec({
  "command": "claude-as-acp --cwd /tmp/cc-e2e 'Create ...'",
  "workdir": "/tmp/cc-e2e",
  "pty": true
})

[toolResult] Command still running (session salty-reef, pid 10512).
             Use process (list/poll/log/write/kill/clear/remove) for follow-up.

[assistant:tool] process({"action": "poll", "sessionId": "salty-reef", "limit": 10000})

[toolResult] {"type":"session","sessionId":"cd649582-...","resumed":false}
             {"type":"text","text":"I'll create the"}
             ...
             {"type":"done","stop_reason":"end_turn",...}
```

第一个 exec 调用在 pty 模式下立即返回了 "session salty-reef, pid 10512" —— 这是 background 模式,openclaw 把 wrapper 的进程记录在一个 session 对象里。OpenClaw 主 agent 然后主动 `process action:poll` 拉 stdout。它**没有发 process action:kill**;正常完成情况下它只需要等 poll 返回 "session 已完成"(这由 wrapper 的 `child.on("close")` 触发)就行。

如果主 agent 想取消,会发 `process action:kill`,内部调 `killProcessTree(pid)` → 3.2 节的路径。

---

## 4. 三种触发清理的事件

wrapper 的停止可以由 3 种事件触发,对应三种清理路径:

### 4.1 正常完成(最常见)

流程如上面时间轴。关键代码在 wrapper 的 `bin/claude-as-acp` 末尾:

```javascript
emit({ type: "done", stop_reason: stopReason, usage, error: errMsg });

// --- Clean shutdown: close stdin, wait for acp to exit ---
try { proc.stdin.end(); } catch {}
for (let i = 0; i < 10 && !exited; i++) {
  await new Promise(r => setTimeout(r, 200));
}
if (!exited) {
  log("acp did not exit within 2s; force killing");
  try { proc.kill(); } catch {}
}
process.exit(errMsg ? 1 : 0);
```

Wrapper 主动关 ACP server 的 stdin,触发 ACP server 的优雅关停路径(见 3.2)。最多等 2 秒;超时就 fallback 到 SIGTERM。实测 200ms 内就 exit 了,超时从未触发。

### 4.2 stdin EOF(优雅级联清理)

这是 claude-agent-acp 自己的关停机制。关键代码在 `@agentclientprotocol/claude-agent-acp/dist/index.js`(整个文件只有 38 行):

```javascript
const { connection, agent } = runAcp();

async function shutdown() {
  await agent.dispose().catch((err) => {
    console.error("Error during cleanup:", err);
  });
  process.exit(0);
}

// Exit cleanly when the ACP connection closes (e.g. stdin EOF, transport
// error). Without this, `process.stdin.resume()` keeps the event loop
// alive indefinitely, causing orphan process accumulation in oneshot mode.
connection.closed.then(shutdown);
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
process.stdin.resume();
```

源码里的注释直说"不这么做就会有孤儿进程堆积"。

`connection.closed` 是 ACP SDK 提供的一个 Promise,stdin 里 EOF 或 parse 错误都会让它 resolve。resolve 后调 `agent.dispose()`,dispose 内部做的事情:

- kill 掉已经 spawn 的 claude CLI 子进程(SDK 内部持有引用)
- 释放任何 open 的资源
- 返回 → 进程 `process.exit(0)`

整个级联不到 500ms。claude CLI 收到 SIGTERM 后也会做自己的清理(把当前 jsonl 写入 flush 到磁盘,然后退出)。

### 4.3 SIGTERM / SIGINT

如果有人 `kill` wrapper 进程,走一样的路径:wrapper 进程组内所有成员都被信号叫住。Node 的默认行为是:wrapper 收到 SIGTERM → wrapper 直接死 → stdout/stdin 关闭 → claude-agent-acp 看到 EOF → 走 3.2 的路径 → claude 也被 kill。

**关键优势**:哪怕 wrapper 是因为 OOM killer 或 `kill -9` 被杀,这棵进程树也能自己收拾干净。因为信号会级联到同一进程组,而 claude-agent-acp 有自己的 signal handler。

唯一的漏网情况:**kill -9 wrapper** 且不 kill 其他人。但即使这样,Linux 有 `SIGCHLD` + parent death detection,ACP server 会很快发现 parent 死了(通过 stdin 管道 EPIPE)然后自己清理。理论上可以用 `PR_SET_PDEATHSIG` 更稳,但实测 stdin 管道足够可靠。

---

## 5. 有没有时间触发的 idle timeout?

**没有**。这是主动用 grep 排查过的:

```bash
cd /usr/lib/node_modules/@agentclientprotocol/claude-agent-acp/dist/
grep -nE "idleTimeout|IDLE_TIMEOUT|inactivity|cleanupInterval|setInterval" *.js
# (无结果)
```

`claude-agent-acp` 的源码里没有任何"N 秒没消息就自己死"的逻辑。它是靠 stdin 的"客户端连接"状态判断是否要清理 —— 客户端不断开,它就不走,一直等着新 prompt。

实测验证:在同一个 ACP server 进程里发了 Round 1 → idle 10 秒 → Round 2,两轮都用**同一对 L4/L5 进程**,进程 pid 都没变。10 秒 idle 什么都没发生。

### 5.1 wrapper 层要不要自己加 idle timeout?

wrapper 目前是"一次 delegation 一个进程,做完就退"的模型(玩法 A,见 README),所以**根本不需要 idle timeout**:做完就关 stdin 就完了。

如果未来想改成"wrapper 常驻 + 多 delegation 复用同一对 L4/L5 进程",那时才需要 idle timer:大致 20-30 分钟没新 prompt 就关 stdin 走级联清理,下次有新请求再重新 spawn + `loadSession(sid)` 续接。

**但一般不建议**。每次冷启动开销只有 <1 秒,而常驻进程要处理并发隔离、资源泄漏、idle state 等一堆事。一次一个进程的模型最简单也最稳。

---

## 6. 孤儿进程风险

### 6.1 实测 1:正常完成后有没有残留

```bash
# Round 1 → Round 2 → Round 3,跑完后 ps 看
ps -eo pid,ppid,comm | grep -E "claude(-agent)?\b"
# (none)
```

所有三轮结束后,一个 node/claude 进程都没留下。

### 6.2 实测 2:stdin 关后级联是否干净

```
[SPAWN] claude-agent-acp pid=9538
子进程 spawn claude pid=9549
[reply] ALIVE                      ← 正常完成一轮
--- proc.stdin.end() ---
[ACP_EXIT] code=0 sig=null         ← 优雅 exit 不是 SIGTERM
[PS] (no claude processes)         ← 两个都没了
```

stdin 关闭后 1 秒内整棵树清理完。

### 6.3 实测 3:wrapper 被 kill -9

未测试,但理论上 Linux 内核保证:

- wrapper 死 → 它持有的 pipe 的 write end 关闭
- claude-agent-acp 的 stdin(pipe 的 read end)读到 EOF
- claude-agent-acp 走 `connection.closed.then(shutdown)` 路径
- claude CLI 被 dispose 掉

pipe 是 OS 保证的事,即使 wrapper 是 SIGKILL 也不会让 pipe 留着。

### 6.4 理论上的泄漏场景(还没见过)

- **claude-agent-acp 自己的 dispose 卡住**:比如 kill 内部 claude CLI 时失败(非常罕见,用 SIGTERM 正常工作)。级联清理可能不完全。但 wrapper 有 2 秒 fallback → SIGTERM claude-agent-acp → 即使那也失败,系统层还有 supervisor 兜底。
- **OpenClaw bash tool 保留进程引用**:openclaw 的 bash tool 实现里可能在 background 模式下 track 子进程。如果 wrapper 已 exit 但 bash session 没 cleanup,不会有孤儿进程但会有死的 session record。这是 openclaw 侧的问题,跟 wrapper 无关。

---

## 7. 日志与观察

wrapper 自己写日志到 `/tmp/claude-as-acp.log`:

```
[2026-04-15T06:19:43.396Z] [10514] START resume=null cwd=/tmp/cc-e2e prompt_len=75
[2026-04-15T06:19:46.535Z] [10514] sessionId=cd649582-f194-48fe-8429-03f467730f40
[2026-04-15T06:19:46.535Z] [10514] initialize ok
[2026-04-15T06:19:46.xxxZ] [10514] OUT {"type":"session","sessionId":"cd649582-..."}
...
[2026-04-15T06:19:52.xxxZ] [10514] prompt done stopReason=end_turn
[2026-04-15T06:19:52.xxxZ] [10514] acp exit code=0 sig=null
[2026-04-15T06:19:52.xxxZ] [10514] EXIT clean=true
```

关键字段:

- `[pid=N]` 前缀:同一次 delegation 的所有行都带同一个 pid,方便 grep 追踪
- `START resume=... cwd=... prompt_len=...`:这一轮的入口参数,用来确认OpenClaw 主 agent传的是什么
- `sessionId=...`:确认本次 session 的 ccSid
- `acp exit code=... sig=...`:级联清理是否成功,应当看到 `code=0 sig=null`
- `EXIT clean=true`:表示 ACP server 在 2 秒 fallback 窗口内自己清理了,没触发强杀

日志文件**不会自动轮转**。生产环境应该加 logrotate 或定期 truncate,防止长期写爆磁盘。SKILL.md 里有提到这个待办。

运行期间想看 wrapper 进程:

```bash
ps -ef | grep claude-as-acp | grep -v grep
# 正常 delegation 时能看到一个 node 进程,10 秒内消失
```

想看历史上所有 delegation 的轨迹:

```bash
grep "^\[.*\] \[[0-9]*\] START" /tmp/claude-as-acp.log
# 每一行对应一次 delegation 调用
```

---

## 8. 总结

| 问题 | 答案 |
|---|---|
| 进程数 | 1 次 delegation = 3 个新进程(wrapper + claude-agent-acp + claude CLI) |
| 生命周期 | 跟 delegation 同生同死(~10 秒,主要是 LLM 延迟) |
| idle timeout | 无(也不需要,模型是"用完即走") |
| 清理机制 | stdin EOF 触发级联清理,不到 1 秒完成 |
| 孤儿进程 | 实测没有;理论上靠 OS pipe 语义 + Node signal handler 兜底 |
| 观察手段 | `/tmp/claude-as-acp.log` |

一句话版:**wrapper 是无状态的短寿命进程,用完即走,清理靠 OS pipe 语义而非进程追踪,简单可靠**。
