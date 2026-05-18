# 阶段一:把 Claude Code + Companion 部署进 OpenClaw 容器

> 完成时间:2026-04-15
> 关联文档:
> - [`design.md`](./design.md) §9.1(阶段一原计划)
> - [`acp-investigation.md`](./acp-investigation.md)(为什么不走 OpenClaw 原生 ACP)
>
> 本文档记录**实际部署过程**(不是设计文档!)和踩过的每一个坑,给未来的自己复现和排错用。

---

## 0. 目标

在 OpenClaw 容器里让 `claude` CLI + `the-companion` 常驻,跟现有 OpenClaw gateway 共存,通过 `/api/sessions/*` HTTP + `/ws/browser/*` WebSocket 暴露 Claude Code 的双向流式会话能力。满足:

- ✅ 容器启动自动拉起 Companion(不用手工 `docker exec` 装)
- ✅ 数据持久化(容器重建不丢 session、CLAUDE.md、settings.json)
- ✅ 从宿主机和 Mac(Tailscale)都能访问 `3456` 端口
- ✅ 版本固化成新镜像 `openclaw-with-cc:2026.04.15`
- ✅ 能通过 HTTP API 创建 session + 发消息,Claude 能收到、处理、回流式文本

**非目标**:foreman 侧 Java 代码(留给阶段二)。

---

## 1. 起步前的容器初态

2026-04-14 ACP 调研遗留下来的状态,作为阶段一的出发点:

| 项 | 状态 |
|---|---|
| 镜像 | `openclaw-with-browser:latest` |
| 容器名 | `openclaw-openclaw-gateway-1` |
| 运行用户 | `root`(`user: "0:0"`) |
| `HOME` | `/home/node`(跟 root 身份分裂) |
| Bun | 已装在 `/root/.bun/bin/bun`(镜像自带,版本 1.3.9) |
| node/npm | 22.22.0 / 10.9.4 |
| claude CLI | ❌ 未装 |
| the-companion | ❌ 未装 |
| 已暴露端口 | `18789`(gateway) / `18790`(bridge) / `18800`(CDP) / `18901`(capsule) |
| yunbiaobiao 代理 | 只授权 `claude-sonnet-4-6-thinking` 一个 model,其他 403 |

另外,ACP 调研期间在容器里**临时**装过 `acpx@0.1.13`、改过 `openclaw.json`,这些都在 `docker compose up -d --force-recreate` 后被擦掉 —— 反正也不用了,正好让阶段一起步干净。

---

## 2. 实施时间线(按实际顺序)

### 2.1 调研 the-companion 包本身(动手前 15 分钟)

直接从 npm 拉 `the-companion@0.95.0` tarball,解开读源码关键几个文件:

| 文件 | 关键发现 |
|---|---|
| `package.json` | `engines: { bun: ">=1.0.0" }` ← **必须 Bun,不支持 Node**,入口是 `.ts` 直接执行 |
| `bin/cli.ts` | 命令:`serve` / `start` / `install` / `status` / `logs` / `sessions` / `envs` / `cron` / `skills` / `settings` / `assistant`。默认命令(无参)= `serve` |
| `server/constants.ts` | `DEFAULT_PORT_PROD = 3456` |
| `server/index.ts` | `host = process.env.HOST \|\| "0.0.0.0"` + `port = Number(process.env.PORT) \|\| defaultPort` |
| `server/auth-manager.ts` | Token 优先级:`COMPANION_AUTH_TOKEN` env → `~/.companion/auth.json` → 自动生成 |
| `server/cli-launcher.ts:470` | spawn `claude` 时 binary 从 PATH 找,默认 `claude` |
| `server/cli-launcher.ts:499-518` | **root 降级陷阱**:root 跑 Companion + `permissionMode=bypassPermissions` 时自动降级为 `acceptEdits`,除非 `COMPANION_FORCE_BYPASS_AS_ROOT=1` |
| `server/cli-launcher.ts:520-528` | 传给 claude 的固定参数:`--sdk-url ws://localhost:3456/ws/cli/<id> --print --output-format stream-json --input-format stream-json --include-partial-messages --verbose` |

**收获**:Companion 的 `--sdk-url` 跟 tmux 套壳方案**走同款 NDJSON 协议**,design.md §2.2 里已经纠正过"tmux 脆弱"是我旧印象。两个方案的真实差别在传输层而不是协议层。

### 2.2 补 3456 端口到 docker-compose.override.yml

容器当前没暴露 3456,运行中容器不能加端口映射,必须重建。

主 `docker-compose.yml` 是 upstream 提供的,不想动;只改 `docker-compose.override.yml` 的 `openclaw-gateway.ports`:

```yaml
    ports:
      - "18800:18800"
      - "3456:3456"    # 新增
```

备份:`docker-compose.override.yml.bak-before-companion`。

`docker compose up -d openclaw-gateway` 重建,`docker port openclaw-openclaw-gateway-1` 确认 `3456 → 0.0.0.0:3456` 已映射。

### 2.3 改 HOME=/root(可选的"强迫症改造")

动机:用户直接说了"你就用 root 我就要用 root"。原容器 `HOME=/home/node` 但 root 身份,会让 bun/claude/companion 各自按 `$HOME` 找配置时**落到一个 node user 的目录**,跟真实运行身份不一致,非常别扭。

**尽职调查**(避免乱改把 OpenClaw 搞挂):

```bash
# grep OpenClaw 源码里是否硬编码 /home/node
rg -n "/home/node" /Users/songxinjian/dev/java/openclaw/src
# 结果:只有 1 个 test 文件引用,运行时 0 处
```

OpenClaw 运行时走 `OPENCLAW_CONFIG_DIR` / `XDG_CONFIG_HOME` env,**不靠 $HOME 展开** → 理论上可以安全改 HOME。

但发现一个**新坑**:OpenClaw Dockerfile 里这样装 Playwright 的 chromium:

```dockerfile
mkdir -p /home/node/.cache/ms-playwright && \
PLAYWRIGHT_BROWSERS_PATH=/home/node/.cache/ms-playwright \
node /app/node_modules/playwright-core/cli.js install --with-deps chromium
```

`PLAYWRIGHT_BROWSERS_PATH` 是 **RUN 命令的临时 env**,不是 `ENV` 指令,所以**没写进镜像永久 env**。Playwright 运行时会默认展开 `~/.cache/ms-playwright`。改 HOME 后会找 `/root/.cache/ms-playwright` → 里面是空的 → 某些 OpenClaw skill 调 Playwright 会挂。

不过 compose 启动命令里的 chromium 是 **`/usr/bin/chromium`**(Debian 系统自带),不走 Playwright 的 ms-playwright 缓存,所以主路径不受影响。为了兜底 OpenClaw 可能调 Playwright 的 skill,加显式 env 指回原路径:

```yaml
    environment:
      HOME: /root
      PLAYWRIGHT_BROWSERS_PATH: /home/node/.cache/ms-playwright
    volumes:
      - ${OPENCLAW_CONFIG_DIR}:/root/.openclaw         # 改
      - ${OPENCLAW_WORKSPACE_DIR}:/root/.openclaw/workspace  # 改
```

两个 service(`openclaw-gateway` 和 `openclaw-cli`)都一样改。备份 `docker-compose.yml.bak-before-root-home`。

重建后验证:
- `env | grep HOME` → `HOME=/root` ✅
- `ls /root/.openclaw` → 看到 `agents browser canvas cron identity logs` 等(volume 挂进来的真实内容) ✅
- `ps aux | grep openclaw-gateway` → PID 7(process title 改名,原本是 `node dist/index.js gateway`)
- `curl http://localhost:18789/` → 返回 OpenClaw Control UI 的 HTML ✅

### 2.4 装 claude CLI + the-companion(容器内手工)

```bash
docker exec openclaw-openclaw-gateway-1 bash -c "
  npm install -g @anthropic-ai/claude-code@2.1.107
  bun install -g the-companion@0.95.0
"
```

- `claude --version` → `2.1.107 (Claude Code)` ✅
- `companion help` → 正常输出 subcommand 列表 ✅

这一步装在 **container layer**,是后面一个大坑的源头(见 §3.5)。

### 2.5 第一次 smoke test(手工启动,临时 token)

```bash
# 容器内后台起 companion
docker exec -d openclaw-openclaw-gateway-1 sh -c '
  export COMPANION_AUTH_TOKEN=smoke-test-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  export PATH=/root/.bun/bin:/usr/local/bin:/usr/bin:/bin
  nohup companion serve --port 3456 > /tmp/companion.log 2>&1 &
'
```

启动日志确认:

```
Server running on http://0.0.0.0:3456
  Auth token: smoke-test-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  (using COMPANION_AUTH_TOKEN env var)
  CLI WebSocket:     ws://localhost:3456/ws/cli/:sessionId
  Browser WebSocket: ws://localhost:3456/ws/browser/:sessionId
```

三端验证连通性:
- **容器内** `curl localhost:3456/api/sessions` → `[]` ✅
- **宿主机** `curl localhost:3456/api/sessions` → `[]` ✅
- **Mac**(Tailscale)`curl 192.168.50.123:3456/api/sessions` → `[]` ✅

创建第一个 session:

```bash
curl -X POST -H "Authorization: Bearer ..." \
  -d '{"backend":"claude"}' \
  http://192.168.50.123:3456/api/sessions/create
# → {"sessionId":"01859d46-...","state":"starting","cwd":"/app","backendType":"claude","pid":362}
```

Companion 日志里看到关键一行 —— 说明 Companion 真的 spawn 了 claude:

```
[cli-launcher] Spawning session 01859d46-...: /usr/local/bin/claude --sdk-url ws://localhost:3456/ws/cli/01859d46-... --print --output-format stream-json --input-format stream-json --include-partial-messages --verbose -p
[ws-bridge] CLI connected | sessionId=01859d46-...
```

通过 CTL 发消息:

```bash
companion sessions send-message 01859d46-... "say hi in one short sentence"
# → {"ok":true,"sessionId":"01859d46-..."}
```

日志里看到:
```
[ws-bridge] ⚠ Broadcasting assistant to 0 browsers ... (stored in history: true)
[ws-bridge] ⚠ Broadcasting result to 0 browsers ... (stored in history: true)
```

**"stored in history: true"** 意味着消息已经入库,可以拉。但这里踩了第一个查 API 的小坑(§3.1)。

拉到 history 后看到 assistant 回复:

```
Failed to authenticate. API Error: 403 {"error":{"message":"该令牌无权访问模型 claude-sonnet-4-6 ..."}}
```

预期中的 yunbiaobiao 代理限制 —— 默认 model `claude-sonnet-4-6` 被 403。见 §3.2 的修复。

### 2.6 修 yunbiaobiao model 问题

写 `/root/.claude/settings.json`:

```json
{"model":"claude-sonnet-4-6-thinking"}
```

kill 旧 session,重新创建 + 发"用一句话介绍你自己",拉 history 看到完整 assistant 回复(含 thinking block 和 text block),**证明链路打通**。

到这里为止,"阶段一的 smoke test 最低要求"已经达成。但是距离"foreman 可以接入的稳定态"还缺 4 件事,继续往下做。

### 2.7 bind mount 三个持久化目录

为了:
1. 让 `/root/.claude/settings.json` + `CLAUDE.md` **容器重建不丢**
2. 让 Companion 的 session 元信息落盘到 `/root/.companion/sessions`(而不是 `/tmp/vibe-sessions`,重启就丢)
3. 让宿主机能直接编辑 CLAUDE.md(调整人设不用进容器)

在宿主机建三个目录:

```
C:\Users\songx\openclaw\claude-home\         → /root/.claude
C:\Users\songx\openclaw\companion-data\      → /root/.companion
C:\Users\songx\openclaw\container-bin\       → /opt/container-bin  (只读,放启动脚本)
```

compose 里加 volumes:

```yaml
    volumes:
      - ${OPENCLAW_CONFIG_DIR}:/root/.openclaw
      - ${OPENCLAW_WORKSPACE_DIR}:/root/.openclaw/workspace
      - ./claude-home:/root/.claude              # 新
      - ./companion-data:/root/.companion        # 新
      - ./container-bin:/opt/container-bin:ro    # 新
```

### 2.8 写 CLAUDE.md 人设

参考 design.md §8 原稿,但**改掉了两处路径**:`/home/node/.openclaw/logs/` → `/root/.openclaw/logs/`,因为我们现在 HOME=/root。还加了 yunbiaobiao 代理的说明,以及"**只授权 `claude-sonnet-4-6-thinking` 这一个 model**"(让 Claude 自己知道这个事实,避免之后有 agent 想切 model)。

最终文件 `claude-home/CLAUDE.md` 2.3KB,包含:身份、运行环境、跟 OpenClaw 的关系、工作风格、不该做什么。

### 2.9 固定 COMPANION_AUTH_TOKEN 进 .env

```bash
openssl rand -hex 32
# → <REDACTED-companion-token>
```

追加到 `C:\Users\songx\openclaw\.env`:

```
COMPANION_AUTH_TOKEN=<REDACTED-companion-token>
```

compose 里引用:

```yaml
    environment:
      COMPANION_AUTH_TOKEN: ${COMPANION_AUTH_TOKEN}
```

备份 `.env.bak-before-companion-token`。

### 2.10 写 start-all.sh 让 Companion 常驻

原 compose 的 command 是一长串 bash `-c` 里 Xvfb + chromium + gateway & 串起来的,读起来不好。我把它拆成一个脚本放 bind mount 进来:

```bash
#!/bin/bash
set -u
export PATH="/root/.bun/bin:/usr/local/bin:/usr/bin:/bin"

# 1. 清 Chromium/Xvfb 残留锁
rm -f /tmp/.X99-lock /tmp/chromium-user-data/SingletonLock \
      /tmp/chromium-user-data/SingletonSocket \
      /tmp/chromium-user-data/SingletonCookie

# 2. Xvfb
Xvfb :99 -screen 0 1920x1080x24 -ac -nolisten tcp &

# 3. Chromium
sleep 1
/usr/bin/chromium --remote-debugging-port=18800 ... about:blank &

# 4. Companion(session dir 落 /root/.companion,不走 /tmp)
mkdir -p /root/.companion/sessions /root/.companion/logs
COMPANION_SESSION_DIR=/root/.companion/sessions \
  /root/.bun/bin/companion serve --port 3456 \
  > /root/.companion/logs/companion.stdout.log \
  2> /root/.companion/logs/companion.stderr.log &

# 5. 前台跑 gateway(它退出容器退出)
exec node dist/index.js gateway --bind lan --port 18789
```

compose 的 command 改成:

```yaml
    command: ["bash", "/opt/container-bin/start-all.sh"]
```

### 2.11 重建容器 → 发现 claude/companion 丢了(大坑)

`docker compose up -d openclaw-gateway` 重建后,容器正常起,但检查发现:

```
$ which claude
(空)
$ which companion
(空)
$ ls /root/.bun/bin
bun  bunx       # 只有 image 原生的,没有 companion
```

而:

```
$ ls /root/.claude
CLAUDE.md settings.json     # ✅ bind mount 生效
$ ls /root/.companion
logs  sessions              # ✅ bind mount 生效
$ cat /root/.companion/logs/companion.stderr.log
/opt/container-bin/start-all.sh: line 39: /root/.bun/bin/companion: No such file or directory
```

**根因**:§2.4 装的 claude CLI 和 the-companion 都在 **container layer**(`docker exec` 的改动不入镜像),`docker compose up -d` 的 recreate 相当于基于原 image 起新容器,container layer 被擦。

这是 Docker 的正常行为。应对方式只有两种:
1. 不 recreate,只 restart(但改 compose 必然触发 recreate)
2. 把它们装进新镜像

走方案 2,进入 §2.12。

### 2.12 打新镜像 openclaw-with-cc(固化)

最小 Dockerfile:

```dockerfile
FROM openclaw-with-browser:latest
USER root
ENV HOME=/root
RUN npm install -g @anthropic-ai/claude-code@2.1.107 \
 && /root/.bun/bin/bun install -g the-companion@0.95.0 \
 && ls -l /root/.bun/bin/companion \
 && ln -sf /root/.bun/bin/companion /usr/local/bin/companion \
 && claude --version
```

`docker build -f Dockerfile.cc -t openclaw-with-cc:2026.04.15 .`,耗时 ~20 秒。

**第一次 build 漏了 `ENV HOME=/root`**(见 §3.3),companion 装到了 `/home/node/.bun/bin` 去了,symlink `/usr/local/bin/companion` 指的源路径不存在。补上 `ENV HOME=/root` 后 rebuild 过。

compose 里把 image 切到新 tag:

```yaml
services:
  openclaw-gateway:
    image: openclaw-with-cc:2026.04.15
```

`docker compose up -d --force-recreate openclaw-gateway`,重建后确认:

```
$ which claude
/usr/local/bin/claude
$ which companion
/root/.bun/bin/companion
$ ps aux | grep -E 'gateway|companion'
root  7 openclaw-gateway
root 21 bun /root/.bun/bin/companion serve --port 3456
```

Gateway 和 Companion 都由 `start-all.sh` 自动拉起 ✅

### 2.13 最终 smoke test(从 Mac 通过 Tailscale)

```bash
TOKEN=<REDACTED-companion-token>
curl --noproxy '*' -s -H "Authorization: Bearer $TOKEN" \
  http://192.168.50.123:3456/api/sessions
# → []

curl --noproxy '*' -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"backend":"claude","cwd":"/root"}' \
  http://192.168.50.123:3456/api/sessions/create
# → {"sessionId":"2f6396e3-...","state":"starting","cwd":"/root","pid":399}

companion sessions send-message 2f6396e3-... "你是谁?你在哪?一句话告诉我."
```

(关于 `cwd:"/root"` 为什么必须加,见 §3.4)

拉 history:

```json
{
  "role": "assistant",
  "contentBlocks": [
    {"type": "thinking", "thinking": "I appreciate you sharing that, but I need to clarify..."},
    {"type": "text", "text": "我是 Claude Code，Anthropic 的命令行工具,现在跑在你的 `openclaw` 容器里帮你处理编码和系统任务。"}
  ]
}
```

**text block 的内容明确受 CLAUDE.md 影响**(知道自己在 openclaw 容器里、简洁一句话) —— 端到端全通。

宿主机文件确认 session 已持久化到 bind mount:

```
C:\Users\songx\openclaw\companion-data\sessions\
  2f6396e3-ccf4-4149-9676-fe8bedc1e169.json  (56 KB)
  2fe1b724-ad95-4790-a5b4-eae3ccc9201e.json  (67 KB)
  launcher.json                              (414 B)
```

---

## 3. 踩过的坑(按大小降序)

### 3.1 Companion history endpoint 用的不是你想的那个 sessionId

**现象**:`GET /api/sessions/:sessionId/history` → **404**。

**根因**:Companion 对 Claude 的 transcript 是**代理读 Claude CLI 的 jsonl 文件**(`~/.claude/projects/<cwd-hash>/<cliSessionId>.jsonl`),这个文件名是 **Claude CLI 自己生成的 UUID**,跟 Companion 的 `sessionId` 完全不同。正确端点是:

```
GET /api/claude/sessions/<cliSessionId>/history
```

`cliSessionId` 要先通过 `GET /api/sessions/:sessionId`(不带 /history)拿到:

```json
{"sessionId":"2f6396e3-...","cliSessionId":"3bb59972-..."}
```

源码位置:`server/routes.ts:269` `api.get("/claude/sessions/:id/history", ...)`。

**foreman 侧建议**:`CompanionHttpClient.getSession()` 返回结构里必须把 `cliSessionId` 暴露出来,`CompanionMessageTranslator` 拉 transcript 要用它,不能用 Companion sessionId。

### 3.2 yunbiaobiao 代理 token 只认 `claude-sonnet-4-6-thinking`

**现象**:
```
Failed to authenticate. API Error: 403 {"error":{"message":"该令牌无权访问模型 claude-sonnet-4-6 ..."}}
```

**根因**:yunbiaobiao.com 是我们用的 Anthropic API 反代,这个账号的 token 只授权了 `claude-sonnet-4-6-thinking` 这一个 model。claude CLI 默认会请求 `claude-sonnet-4-6`(非 thinking 版本),直接被拒。

**解法**:`/root/.claude/settings.json`:

```json
{"model":"claude-sonnet-4-6-thinking"}
```

**验证路径** (第一次改没生效时用过):
1. 通过 env `ANTHROPIC_MODEL` 覆盖?→ claude CLI 不一定读
2. 通过 settings.model?→ 读的,而且 error 里的 model 会从 `claude-sonnet-4-6` 变成你写的值,可以确认是否被 read
3. 通过 `curl https://yunbiaobiao.com/v1/models` 能列出可用 model 清单(之前调研过一次)

这个限制以后要是换 API key,相应改 settings.json 就行。

### 3.3 Docker build 时 `$HOME` 不是 /root,bun `-g` 装错位置

**现象**:第一次 build 完的镜像里 `which companion` 空,symlink `/usr/local/bin/companion → /root/.bun/bin/companion` 存在但目标不存在。`find / -name companion` 能找到 `/home/node/.bun/bin/companion`。

**根因**:Dockerfile RUN 时没显式 `ENV HOME=/root`,继承 base image 的 HOME(OpenClaw 镜像设了 `HOME=/home/node` 在某处)。bun `install -g` 的目标路径是 `$BUN_INSTALL ?? ~/.bun`,`~` 展开成 `/home/node`,所以装到了 `/home/node/.bun/bin/`。build 日志里其实有警告:

```
warn: To run "companion", add the global bin folder to $PATH:
/home/node/.bun/bin
```

**解法**:Dockerfile 第一行 `USER root` 之后立刻 `ENV HOME=/root`。重 build。

**教训**:base image 的 ENV 会**传递给**你的 Dockerfile RUN 指令,不要假设 `$HOME` 跟你心里想的一致。尤其注意 `USER` 和 `HOME` 是两回事 —— 换 USER 不会自动切 HOME。

### 3.4 `/app/CLAUDE.md` 盖住 `/root/.claude/CLAUDE.md`(Claude Code 的 project 级优先)

**现象**:写好了 `/root/.claude/CLAUDE.md` 人设,但 Claude 回复仍然一脸懵,thinking block 是 "I don't have access to a Skill tool..." 完全没体现人设。

**根因**:Claude Code CLI 读 CLAUDE.md 的顺序是 **project 级 → user 级**,project 级是**当前工作目录及其父目录**下的 `CLAUDE.md` / `AGENTS.md`。session cwd 默认是 `/app`(Companion 创建 session 时 `cwd` 没传就是容器 WORKDIR),而:

```
/app/CLAUDE.md    ← OpenClaw 源码自带的,开发者指南
/app/AGENTS.md    ← 同上
```

这两个文件是 OpenClaw 仓库根目录的 CLAUDE.md,是给**写 OpenClaw 代码的 AI 看的**,内容跟我们的人设完全不同,而且优先级高于 user 级。

**解法**:创建 session 时显式传 `cwd: "/root"`:

```bash
curl -X POST -d '{"backend":"claude","cwd":"/root"}' .../api/sessions/create
```

/root 下没有 CLAUDE.md,所以 Claude 会 fallback 到 user 级 `/root/.claude/CLAUDE.md`,人设生效。

**验证**:切换 cwd 前后发同一句话"你是谁?你在哪?一句话告诉我.",对比 text block:
- cwd=/app:"I'm Claude..." (英文,完全没提 openclaw 容器)
- cwd=/root:"我是 Claude Code,Anthropic 的命令行工具,现在跑在你的 `openclaw` 容器里..."

**foreman 侧必须记住**:`CompanionHttpClient.createSession(...)` 一定要传 `cwd=/root`,这是硬性规则,不传就是 bug。建议在 design.md §6.2 补一笔,或者直接在 `CompanionLaunchOptions` DTO 里把这个字段的默认值写死 `/root`。

### 3.5 Docker recreate 会擦掉 container layer 装的 npm/bun 全局包

**现象**:§2.11 描述过,`docker exec` 装的 claude CLI 和 the-companion 在 `docker compose up -d` recreate 后全丢。

**根因**:正常 Docker 行为。`docker exec` 改的是 container layer,不是 image。recreate 等于删容器再起新容器,新容器是从 image 重新 create 的,container layer 清空。

**解法**:打新镜像(§2.12)。**长期方案**:任何"装在 container 里的东西都必须进 Dockerfile"。

**验证下次不翻车的办法**:
- `docker diff openclaw-openclaw-gateway-1` 能看到 container layer 改了什么
- bind mount 的目录不会被 recreate 擦

### 3.6 `which companion` 结果依赖 symlink target 是否存在

**现象**:`ln -sf` 创建了 `/usr/local/bin/companion` 指向一个不存在的 `/root/.bun/bin/companion`(因为 §3.3),`which companion` 有时返回空、有时返回路径,行为不一致。

**解法**:用 `find / -name companion -type l -o -type f` 兜底找,确认两边都存在后再 symlink。

### 3.7 thinking block 疑似被 yunbiaobiao 代理注入固定文本

**现象**:两次不同的 smoke test 送了完全不同的问题("say hi in one short sentence" vs "你是谁?你在哪?"),但 assistant 回复的 **thinking block 开头是一字不差**的 "I appreciate you sharing that, but I need to clarify my actual capabilities. I'm Claude, made by Anthropic. I'm a conversational AI assistant — I don't have access to a 'Skill tool' or the ability to call skills like 'update-config.'..."。

这段文字跟用户提问**完全无关**,像是被 yunbiaobiao 代理或 claude-sonnet-4-6-thinking 这个 model 的 system prompt **固定注入**了一段什么东西。

**现状**:不阻塞。foreman 翻译层只需要消费 `contentBlocks[type=text]`,thinking block 可以直接忽略(反正正常 UI 也折叠)。

**未来排查**:等 foreman 接入真实用户流量以后,如果 thinking 变成用户可见(比如企微里有人反馈),再从 `/v1/messages` 原始 request 抓包看是不是代理加的。这是 P2 问题。

### 3.8 SCP 到 Windows OpenSSH 需要 `-O`

**现象**:`scp file windows:path` 报 `subsystem request failed on channel 0 ... scp: Connection closed`。

**根因**:OpenSSH 9.x 默认用 SFTP 协议做 scp,而 Windows OpenSSH Server 不支持 SFTP subsystem(或者版本老)。

**解法**:加 `-O`(大写 O,Legacy mode)强制用老的 SCP 协议:

```bash
scp -O local.file windows:C:/path/remote.file
```

### 3.9 SSH 到 Windows 里跑 shell 命令的嵌套引号

**现象**:`ssh windows "docker exec ... sh -c 'whoami; echo $HOME'"` 经常翻车,Windows cmd 把 `|` 或 `'` 吃掉,或者 `$HOME` 在错误的层被展开。

**应对**:
- **优先方案**:命令复杂一点就写到本地文件 → `scp -O` 上去 → `ssh windows "path\to\script"`
- **简单命令**:用 `sh -c \"...\"` 三层 escape,单引号内用 `\$` 阻止本地展开
- **绝对不用**:四层嵌套 + 反引号 + `|` 管道,死路

---

## 4. 最终架构

### 4.1 宿主机文件结构

```
C:\Users\songx\openclaw\
├── docker-compose.yml                         # HOME=/root, image=openclaw-with-cc, 挂 3 个新 volume
├── docker-compose.override.yml                # 加 3456 端口
├── docker-compose.yml.bak-before-root-home    # 备份
├── docker-compose.override.yml.bak-before-companion
├── .env                                        # + COMPANION_AUTH_TOKEN
├── .env.bak-before-companion-token
├── Dockerfile.cc                               # FROM openclaw-with-browser + npm + bun 安装
├── claude-home\                                # → /root/.claude
│   ├── CLAUDE.md                              # 人设
│   └── settings.json                          # {"model":"claude-sonnet-4-6-thinking"}
├── companion-data\                             # → /root/.companion
│   ├── logs\
│   │   ├── companion.stdout.log
│   │   └── companion.stderr.log
│   └── sessions\                              # launcher.json + <sessionId>.json
├── container-bin\                              # → /opt/container-bin:ro
│   └── start-all.sh                           # Xvfb + chromium + companion + gateway 统一启动
├── config\                                     # → /root/.openclaw  (既有,未动)
└── workspace\                                  # → /root/.openclaw/workspace  (既有,未动)
```

### 4.2 容器启动顺序(start-all.sh)

```
1. 清 Chromium/Xvfb 旧锁文件
2. Xvfb :99 &                                    (后台)
3. sleep 1; /usr/bin/chromium ... about:blank &  (后台, CDP 18800)
4. companion serve --port 3456 &                 (后台, 日志到 /root/.companion/logs/)
5. exec node dist/index.js gateway --bind lan --port 18789   (前台, 守进程)
```

gateway 退出 → start-all.sh 退出 → 容器退出 → `restart: unless-stopped` 拉起新容器,一切重来。Companion 跟 gateway 同生共死。

### 4.3 端口暴露

| 容器端口 | 宿主机端口 | 用途 |
|---|---|---|
| 18789 | 18789 | OpenClaw gateway(既有) |
| 18790 | 18790 | OpenClaw bridge(既有) |
| 18800 | 18800 | Chromium CDP(既有) |
| 18901 | 18901 | OpenClaw capsule plugin(既有) |
| **3456** | **3456** | **Companion HTTP + WebSocket(新)** |

### 4.4 运行身份与 HOME

- 容器 user: `"0:0"`(root)
- `HOME=/root`
- `/root/.openclaw` ← `./config` volume
- `/root/.claude` ← `./claude-home` volume
- `/root/.companion` ← `./companion-data` volume
- `PLAYWRIGHT_BROWSERS_PATH=/home/node/.cache/ms-playwright` ← 兜底,避免 OpenClaw 的 skill 调 Playwright 找不到 chromium

### 4.5 关键版本号

| 组件 | 版本 |
|---|---|
| 镜像 | `openclaw-with-cc:2026.04.15`(fork 自 `openclaw-with-browser:latest`,base 是 `ghcr.io/openclaw/openclaw:2026.2.26`) |
| claude CLI | `@anthropic-ai/claude-code@2.1.107` |
| the-companion | `0.95.0` |
| Bun | `1.3.9`(image 自带) |
| Node | `v22.22.0`(image 自带) |
| Claude model | `claude-sonnet-4-6-thinking`(yunbiaobiao 唯一允许) |
| `ANTHROPIC_BASE_URL` | `https://yunbiaobiao.com`(从 .env) |

### 4.6 Companion auth

- `COMPANION_AUTH_TOKEN=<REDACTED-companion-token>`
- 存在 `C:\Users\songx\openclaw\.env`
- compose environment 引用 `${COMPANION_AUTH_TOKEN}` → 容器 env
- Companion `auth-manager.ts` 优先读 env,不会落到 `/root/.companion/auth.json`(但 auto-generate fallback 仍在)

---

## 5. 回归测试 / 健康检查

写一个简短的 checklist,以后动过容器或改过 compose 都跑一遍:

1. `ssh windows "docker ps --filter name=openclaw-openclaw-gateway-1"` → `Up Xs`
2. `ssh windows "docker exec openclaw-openclaw-gateway-1 ps aux | grep -E 'openclaw-gateway|companion'"` → 两个进程都在
3. 容器内 `curl localhost:18789/` → OpenClaw Control UI HTML
4. 容器内 `curl -H "Authorization: Bearer $COMPANION_AUTH_TOKEN" localhost:3456/api/sessions` → JSON 数组
5. Mac 上 `curl --noproxy '*' -H "Authorization: Bearer $TOKEN" http://192.168.50.123:3456/api/sessions` → 同 4
6. 创建 session(记得传 `cwd:"/root"`)→ 发一句话 → 拉 history → 看到 assistant 的 text block 是中文且知道自己在容器里

如果第 6 步挂了,排查顺序:
- 看 `/root/.companion/logs/companion.stderr.log` → Companion 自己的错误
- 看 `docker logs openclaw-openclaw-gateway-1 --tail 50` → start-all.sh 和 gateway 的
- 看 `/root/.claude/projects/-root/<cliSessionId>.jsonl` → Claude CLI 自己的 transcript(最权威)
- `curl https://yunbiaobiao.com/v1/models -H "Authorization: Bearer ..."` → 确认 API 代理还活着

---

## 6. 遗留问题(留给阶段二)

### 6.1 session cwd 必须 `/root`

**约束**:任何创建 Claude session 的代码必须传 `cwd: "/root"`,否则会加载 `/app/CLAUDE.md` 导致人设失效。

**落地建议**:foreman 侧 `CompanionLaunchOptions.java` 的 `cwd` 字段默认值硬编码成 `/root`,且不提供 setter。或者在 `CompanionHttpClient.createSession()` 里强制覆盖。

### 6.2 thinking block 的注入问题

**约束**:yunbiaobiao + sonnet-4-6-thinking 组合下,assistant 的 thinking block 可能是固定注入内容,跟真实用户提问无关。

**落地建议**:`CompanionMessageTranslator.java` 只消费 `contentBlocks[type=text]`,忽略 `thinking`/`redacted_thinking`。如果未来切换到其他 model(非 thinking)或直连 Anthropic 原厂,这个规则可以撤。

### 6.3 Companion sessionId ↔ cliSessionId 的映射

**约束**:history endpoint 用 cliSessionId,但我们业务 ID 是 sessionKey → Companion sessionId。**多一层映射**:sessionKey → companionSessionId → cliSessionId。

**落地建议**:`BackendModeStore.java` 已经有 `companionSessionId` 字段,再加一个 `cliSessionId`(可空,首次返回 "state=starting" 时还没有,要 poll 一次 `GET /api/sessions/:id` 拿到)。

### 6.4 没做镜像版本固化的 CI

现在 `openclaw-with-cc:2026.04.15` 是本地 build 出来的,没有推到 registry,也没有 CI。如果 Windows 主机盘坏了,重建整条链就很惨。

**落地建议**:短期用 `docker save openclaw-with-cc:2026.04.15 -o openclaw-with-cc-2026.04.15.tar` 导出存到 Mac 或 NAS。长期考虑把 Dockerfile.cc + build 步骤搞成 git 仓库里的 script。

### 6.5 日志保留 / 轮转没配

`/root/.companion/logs/companion.stdout.log` 是 bind mount 出来的文件,没配轮转。长期跑 Companion 日志会无限增长。Companion 自己有一个 `recording` 目录(`/root/.companion/recordings`)也是一样的问题 —— 启动日志里有 `max: 1000000 lines`,但那是 recording 不是 stdout 日志。

**落地建议**:加 `logrotate` 配置到 `start-all.sh`,或者 `companion.stdout.log > companion.stdout.log-$(date +%Y%m%d)` 周期轮转。

### 6.6 foreman 代码(阶段二正主)

按 `design.md §5` 实施 9 个 Java 类。TODO 列在项目 memory `project_claude_code_companion.md` 里。

---

## 7. 各路径速查(给排查用)

### 容器内

| 路径 | 用途 |
|---|---|
| `/usr/local/bin/claude` | Claude CLI 主 binary(npm global) |
| `/root/.bun/bin/companion` | Companion 主 binary |
| `/usr/local/bin/companion` | symlink → `/root/.bun/bin/companion` |
| `/opt/container-bin/start-all.sh` | 启动脚本(宿主机 bind mount,只读) |
| `/root/.claude/CLAUDE.md` | Claude Code 用户级人设(bind mount) |
| `/root/.claude/settings.json` | `{"model":"claude-sonnet-4-6-thinking"}` |
| `/root/.claude/projects/-root/<cliSessionId>.jsonl` | Claude 对话 transcript(session cwd=/root 时) |
| `/root/.companion/sessions/` | Companion session 元信息(bind mount) |
| `/root/.companion/logs/companion.stdout.log` | Companion stdout(bind mount) |
| `/root/.openclaw/` | OpenClaw 配置 + 日志(既有 volume) |
| `/app/` | OpenClaw 源码 workdir(**有 CLAUDE.md / AGENTS.md,注意绕开**) |

### 宿主机

| 路径 | 用途 |
|---|---|
| `C:\Users\songx\openclaw\docker-compose.yml` | 主 compose |
| `C:\Users\songx\openclaw\docker-compose.override.yml` | override(只含 env + ports) |
| `C:\Users\songx\openclaw\.env` | 敏感 env(含 API key / COMPANION_AUTH_TOKEN) |
| `C:\Users\songx\openclaw\Dockerfile.cc` | 新镜像 build 文件 |
| `C:\Users\songx\openclaw\claude-home\` | `/root/.claude` 的宿主机侧 |
| `C:\Users\songx\openclaw\companion-data\` | `/root/.companion` 的宿主机侧 |
| `C:\Users\songx\openclaw\container-bin\` | 启动脚本 |

### 本地(Mac,`/Users/songxinjian/dev/java/my-ai-playground/`)

| 路径 | 用途 |
|---|---|
| `devdocs/0414-claude-code-companion/design.md` | 原设计文档 |
| `devdocs/0414-claude-code-companion/acp-investigation.md` | 为什么不走 OpenClaw ACP |
| `devdocs/0414-claude-code-companion/phase1-container-deployment.md` | **本文档** |

### 相关外部仓库

| 路径 | 用途 |
|---|---|
| `/Users/songxinjian/dev/java/openclaw/` | OpenClaw 源码(grep 查 `/home/node` 等硬编码) |
| `/Users/songxinjian/dev/java/claude-code-source/` | Claude Code CLI 源码 |
| `/tmp/companion-pkg/package/` | 临时解压的 `the-companion@0.95.0` npm 包 |
