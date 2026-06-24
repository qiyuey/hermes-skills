---
name: wireguard
description: >-
  (qiyuey) Manage the personal WireGuard tunnel on macOS as a LaunchDaemon
  (top.qiyuey.wireguard.wg0) that drives wireguard-go + wg + ifconfig + route
  directly. Use when the user asks to start/stop/restart the WireGuard service,
  check tunnel handshake/status, view its logs, troubleshoot "断网"/route
  conflicts with the official WireGuard.app, or edit the wg0.conf config.
  Standard launchctl commands; sudo password is required (no sudoers bypass
  installed).
---

# WireGuard (macOS, LaunchDaemon)

本机 WireGuard 隧道由一个常驻 LaunchDaemon 接管。runner 直接驱动
`wireguard-go` + `wg setconf` + `ifconfig` + `route`，配合 launchd 自带的
`KeepAlive` 实现异常自恢复。

## 触发条件

用户说：
- "启动/停止/重启 WireGuard"、"连一下 VPN"、"断开 VPN"
- "WireGuard 状态"、"看一下握手"、"看 WireGuard 日志"
- "WireGuard 把网搞断了" / "路由冲突" / "和官方 App 冲突"
- "改 WireGuard 配置"、"换 peer"、"换 endpoint"

## 关键路径

| 用途 | 路径 | 权限 |
|---|---|---|
| **真实配置（含私钥，不入 git）** | `${XDG_CONFIG_HOME:-~/.config}/wireguard/wg0.conf` | user 0600 |
| 系统部署的配置 | `/opt/homebrew/etc/wireguard/wg0.conf` | root:wheel 0600 |
| Runner（被 launchd 拉起） | `/usr/local/libexec/wg-svc-runner` | root:wheel 0755 |
| LaunchDaemon plist | `/Library/LaunchDaemons/top.qiyuey.wireguard.wg0.plist` | root:wheel 0644 |
| 日志 | `/var/log/wireguard-wg0.log` | root:wheel 0644 |
| 配置模板（git 管理） | `~/Code/hermes-skills/skills/wireguard/wg0.conf.example` | — |
| runner / plist / installer 源 | `~/Code/hermes-skills/skills/wireguard/` | — |

Service label: `top.qiyuey.wireguard.wg0`

**安全约定**：`wg0.conf` 里有 `PrivateKey` 和 `PresharedKey`，**绝不能进 git**。
canonical 配置在 XDG 标准位置 `~/.config/wireguard/wg0.conf`（独立于任何 app，
和 Hermes 解耦），仓库根 `.gitignore` 已经显式排除 `**/wg0.conf`（同时白名单了
`wg0.conf.example`）做二次防呆。

## 服务管理（标准 launchctl）

**所有 launchctl 命令都需要 sudo 密码**——故意不装 sudoers 旁路。

```bash
# 启动（首次 bootstrap，之后开机自启 + 异常自动重启）
sudo launchctl bootstrap system /Library/LaunchDaemons/top.qiyuey.wireguard.wg0.plist

# 停止（unload + 取消开机自启）
sudo launchctl bootout system /Library/LaunchDaemons/top.qiyuey.wireguard.wg0.plist

# 原地重启（保持 enabled 状态；改完配置就用这条）
sudo launchctl kickstart -k system/top.qiyuey.wireguard.wg0

# launchd 视角的状态（看 state / pid / last exit code）
sudo launchctl print system/top.qiyuey.wireguard.wg0
```

## 状态检查（无 sudo / 弱 sudo）

```bash
# WireGuard 握手 / 流量 / 对端
sudo wg show

# 接口和地址（不需要 sudo）
ifconfig | awk '/^utun[0-9]+:/{i=$1} /inet 192\.168\.10\./{print i, $0}'

# 路由是否注入了对端网段
netstat -rn -f inet | awk '/^192\.168\.(1|10)\./ {print}'

# runner 日志（644，普通用户可读）
tail -n 50 /var/log/wireguard-wg0.log

# 实时跟踪日志
tail -F /var/log/wireguard-wg0.log
```

健康判断：
- `sudo wg show` 出现 `latest handshake: N seconds ago`（且 < 3 分钟）→ 隧道活
- `ifconfig` 在某 `utunN` 上看到 `inet 192.168.10.3` → 接口已建
- `netstat -rn` 看到 `192.168.1.0/24` 和 `192.168.10.0/24` 走 `utunN` → 路由 OK

## 修改配置

canonical 文件是 `~/.config/wireguard/wg0.conf`（不入 git）。修改后**同步到系统位置 + 重启服务**：

```bash
# 1. 编辑本机配置
$EDITOR ~/.config/wireguard/wg0.conf

# 2. 同步到系统位置（保持 root:wheel 0600）
sudo install -m 0600 -o root -g wheel \
    ~/.config/wireguard/wg0.conf \
    /opt/homebrew/etc/wireguard/wg0.conf

# 或者直接跑 install.sh（幂等，会从 XDG 位置读）
sudo bash ~/Code/hermes-skills/skills/wireguard/install.sh

# 3. 原地重启 daemon 让新配置生效
sudo launchctl kickstart -k system/top.qiyuey.wireguard.wg0

# 4. 验证
sudo wg show
tail -n 30 /var/log/wireguard-wg0.log
```

`wg-svc-runner` 在启动时会从 `[Interface]` 解析 `Address`、从所有 `[Peer]` 收集 `AllowedIPs`
然后注入 `ifconfig`/`route`；改 `AllowedIPs` 后必须重启服务才能更新路由表。

模板变了（增加字段、改注释）请改 `wg0.conf.example` 并提交，不要去碰你本机的私钥文件。

## 首次部署（新机器）

```bash
# 1. 准备真实配置（从模板填私钥/peer/endpoint，仅本机，不入 git）
mkdir -p ~/.config/wireguard
chmod 700 ~/.config/wireguard
install -m 0600 \
    ~/Code/hermes-skills/skills/wireguard/wg0.conf.example \
    ~/.config/wireguard/wg0.conf
$EDITOR ~/.config/wireguard/wg0.conf

# 2. 把 4 个文件装到系统位置（不启动服务）
sudo bash ~/Code/hermes-skills/skills/wireguard/install.sh
```

`install.sh` 做（且只做）：
1. 从 `${XDG_CONFIG_HOME:-~/.config}/wireguard/wg0.conf` 读 → `/opt/homebrew/etc/wireguard/wg0.conf`（root:wheel 0600）
2. 装 `/usr/local/libexec/wg-svc-runner`（root:wheel 0755）
3. 装 `/Library/LaunchDaemons/top.qiyuey.wireguard.wg0.plist`（root:wheel 0644）
4. 创建 `/var/log/wireguard-wg0.log`

如果配置不存在，`install.sh` 会拒绝运行并打印复制模板的命令。
也可以 `sudo WG_CONF_SRC=/path/to/wg0.conf bash install.sh` 显式指定路径。

跑完之后：先退出官方 WireGuard.app，再 `sudo launchctl bootstrap …` 启动服务。

## 和官方 WireGuard.app 共存的雷区

**绝对不能同时启动**。两边都用 `192.168.10.3/32` + 同 `AllowedIPs` 时，
两个 utun 接口会抢路由表项，外网/内网都可能间歇性不通。表现：
- 上一秒能 ping 通 `192.168.1.x`，下一秒不通
- `netstat -rn` 看到同一个目标网段指向两个不同的 utun
- `wg show` 一边握手正常但流量没走过去

**规则**：用 daemon 时**菜单栏退出官方 App**；偶尔需要图形配置时**先 `sudo launchctl bootout …` 再开 App**。

`wg-svc-runner` 在启动前不会主动检测——直接 bootstrap 失败时去看日志：
```bash
tail -n 50 /var/log/wireguard-wg0.log
```

如果看到 `RTM_ADD: File exists` 或 `ifconfig: ioctl (SIOCAIFADDR): File exists`，
99% 是官方 App 还在跑。

## 常见症状速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `bootstrap` 报 `Service is disabled` | 之前 `launchctl disable` 过 | 先 `sudo launchctl enable system/top.qiyuey.wireguard.wg0` 再 bootstrap |
| `bootstrap` 报 `Bootstrap failed: 5: Input/output error` | plist 语法错 | `plutil -lint /Library/LaunchDaemons/top.qiyuey.wireguard.wg0.plist` 看具体哪一行 |
| `wg show` 没有 `latest handshake` 或显示 "(none)" | 还没握手成功 | 等 25s（`PersistentKeepalive`）；仍 0 → 检查 endpoint DNS、对端公网可达性、对端 server 是否在跑 |
| 握手有但内网不通 | 路由没注入 | `netstat -rn` 确认 `192.168.1.0/24` 走 utunN；没注入就 `kickstart -k` 重启 |
| `kickstart -k` 后日志反复 `tun exited before created` | wireguard-go 启动失败 | 通常是 utun 资源耗尽或上次没干净退出；`sudo launchctl bootout …` 后等 5s 再 bootstrap |
| 修改了 `~/Code/hermes-skills/skills/wireguard/wg0.conf` 但不生效 | 忘了同步到 `/opt/homebrew/etc/wireguard/` | 见上方"修改配置"3 步走 |
| `wg-svc-runner` 在日志里反复重启 | `KeepAlive.Crashed=true` 在重新拉起；多数是配置错误 | 日志找最早的 ERROR 行，按上面定位 |
| 关机/休眠后路由错乱 | macOS 休眠时 utun 状态可能损坏 | `sudo launchctl kickstart -k system/top.qiyuey.wireguard.wg0` |
| 外网恢复了但隧道仍不通，`wg show` 的 endpoint 是旧 IP | DDNS 切了 IP，wireguard-go 不会自动重解析 | 先 `cat /var/run/wg-svc-<iface>.state` 看 last_decision；典型恢复 ~2.5 分钟（早期判定）/ 最坏 ~4 分钟（兜底）。急用就 `sudo launchctl kickstart -k system/top.qiyuey.wireguard.wg0` |
| 日志反复 `ddns: ... deferring`（DNS 变了但 watchdog 不动手） | 隧道当前还在通（hs_age 健康），按设计抑制对 DDNS 抖动的追逐 | 这是正常行为；要立即生效就 `kickstart -k` |
| 日志反复 `ddns: WARN ... FAKE-IP` | 本地/网络 DNS 被劫持成 fake-ip（如 mosdns/AdGuard 的 198.18 池） | watchdog 已经拒绝写入，但本机系统 resolver 也走的同源 → wireguard-go 启动时拿到的就是假 IP；改用未污染的 DNS（`scutil --dns` 检查），或在 `wg0.conf` 把 `Endpoint` 写成 IP 字面量 |
| 日志反复 `ddns: WARN unable to resolve` | DNS 不可用（断网或 resolver 故障） | 多数能自愈；持续 → `scutil --dns`、`dig @8.8.8.8 <endpoint>` 排查上游 |

## Runner 内部行为（排错时参考）

`/usr/local/libexec/wg-svc-runner wg0` 的步骤（按顺序，任一步失败立即 cleanup 退出）：

1. 从 `wg0.conf` 的 `[Interface]` 取 `Address`
2. 收集所有 `[Peer]` 的 `AllowedIPs`（按逗号拆开）和 `PublicKey`/`Endpoint` 对（给 DDNS watchdog 用）
3. `WG_TUN_NAME_FILE=/tmp/wg-wg0-name.XXX wireguard-go -f utun &`，作为子进程
4. 轮询 ≤5s 等 `NAME_FILE` 出现，读出真实 `utunN`
5. 把 UAPI 不识别的字段（`Address/DNS/MTU/Table/Pre|PostUp/Down/SaveConfig`）从配置中 strip 掉，写临时文件
6. `wg setconf utunN <临时文件>` 把 PrivateKey + Peer 灌进内核 UAPI
7. `ifconfig utunN inet <ip>/32 <ip> alias` + `mtu 1420` + `up`
8. 逐个 `route -q -n add -inet <AllowedIPs> -interface utunN`
9. **触发 mihomo 重绑**（`restart_mihomo`，见下节）——接口+路由就绪后 kick mihomo，让它绑在 `192.168.10.3` 上的 tunnel listener 重新生效
10. **启动 DDNS watchdog 子进程**（见下节）
11. `wait $wireguard_go_pid`；收到 SIGTERM/INT/HUP 时反向做：kill watchdog → 删路由 → `ifconfig down` → kill 子进程

排错思路：日志里看上面哪一步留下的 `[wg-svc-runner wg0] ...` 行最后出现，就是卡在哪。

## mihomo 重绑 hook（WG 重连后自愈关键路径）

**问题**：home-proxy gateway（launchd daemon `top.qiyuey.home-proxy.gateway`）的 `tunnels` 段
在 `192.168.10.3:8388/8389/31443` 上 listen，把公司 SOCKS5 代理（`proxy-jp/aws-us.zhenguanyu.com`）
和飞连 native 中继给 WG 网段的远端设备用。这些 listener **只在 mihomo 启动时 `bind()` 一次**，
绑定的是当时那个 utun 接口实例上的 `192.168.10.3`。

WG 断线 → 旧 utun 销毁、`192.168.10.3` 消失 → listener socket 失效。
WG 重连 → 新 utun 重新挂 `192.168.10.3`，但 mihomo **不会自动重绑** →
远端连 `192.168.10.3:838x` 直接 refused/timeout，**隧道本身是通的，是中继挂了**。

典型症状：WG `wg show` 健康、内网 ping 通，但远端 `tcping 192.168.10.3 8388` 不通；
本机 `nc -z 192.168.10.3 8388` 也 closed。

**方案 A（已实现，事件驱动自愈）**：runner 在路由注入完成后调用 `restart_mihomo`：
- `launchctl kickstart -k system/top.qiyuey.home-proxy.gateway` 重启 home-proxy gateway → listener 在 WG 已就绪状态下重绑
- **后台 detached 执行**（`( sleep 2; kickstart ) &`），不阻塞 runner 到达 `wait`
- **全程 `|| true` 吞错**：mihomo 出问题绝不能通过 `set -e` 反噬 runner 把隧道拖垮
- `MIHOMO_REBIND_DELAY=2` 给接口留 2s settle 时间
- 开关：`MIHOMO_RESTART=1`（默认开），设 `0` 禁用
- 幂等：mihomo 在跑就重启，没跑就启动

每次 WG 起来 / DDNS 重连 / `kickstart -k` 后，mihomo 自动跟着重绑，零手动。
日志确认：grep `mihomo .* kickstarted to rebind tunnel listeners`。

**手动兜底**（hook 万一没生效）：`cd ~/Code/kanyun-proxy && sudo ./setup-mihomo.sh restart`。

**重要区分**：远端设备访问公司代理要连**中继地址 `192.168.10.3:8388`（JP）/ `:8389`（AWS-US）**，
**不是** `proxy-jp.zhenguanyu.com:8388`——后者解析成 `10.43.x.x`（公司 K8s ClusterIP），只在 Mac 侧经 en5 可达，
远端设备到不了。Mac 上的 mihomo tunnel 替你做了「内网可达性」这一跳。

## DDNS Watchdog（自动重连关键路径）

wireguard-go **只在启动时解析一次** peer 的 `Endpoint` 主机名，之后再不重解析。
如果上游 DDNS 切换了真实 IP，wireguard-go 会一直往**旧 IP** 发握手包永不恢复，直到服务重启。

为此 runner 后台跑一个监视循环（`DDNS_POLL_SEC=60`）：

- 启动后 2s 做一次 fake-ip 检测，写 WARN 日志（不阻断）
- 之后每 60s 重新解析每个非 IP 字面量的 peer endpoint
- 解析路径**优先 DoH**（按顺序：阿里 DoH → Cloudflare DoH → `223.5.5.5` DoH → `dig @223.5.5.5` → `host @223.5.5.5` → `dscacheutil`），**绕开本机 stub resolver 和任何代理工具的 DNS 缓存**（如 Clash）
  - 解析失败 → WARN，下轮再试
  - 解析到 **fake-ip**（`198.18/15`、`240/4`、`100.64/10`、`0.0.0.0`/`127`、CGNAT）→ WARN 但**不更新** endpoint（避免被劫持 DNS 引到错误地址）
  - 解析到合法新 IP 且与上次缓存不同 → 看握手健康度：
    - `hs_age < HEALTHY_HS_AGE_SEC`（默认 180s，隧道还在通）→ **deferring**，本轮只 log 不动作，避免追 DDNS 抖动打断 in-progress 会话
    - `hs_age >= HEALTHY_HS_AGE_SEC`（隧道已断）→ `wg set <iface> peer <pubkey> endpoint <ip>:<port>` 热更新
- 热更新是 UAPI 调用，**不重启 wireguard-go、不动 utun、不动路由**，下次握手就能恢复

三层判据（按从激进到保守的顺序，命中即触发更新）：

1. **early-stall**：`hs_age >= 75s` 且上一轮 `tx_delta >= 100B` 且 `rx_delta == 0`
   → keepalive 在重试但对端没回任何包，可断定隧道已断
2. **stale**：`hs_age >= 180s` → 兜底硬阈值，无视 telemetry
3. **defer**：DNS 变了但前两条都不命中（隧道还活）→ 不动作

恢复时间预算：

| 场景 | 表现 |
|---|---|
| 网络抖动 / 端口临时不可达（DDNS IP 没变） | wireguard-go keepalive=25s 重试，网络恢复后 **≤5s** 自动握手 |
| 远端重启或换 IP，且 watchdog 的 telemetry 看得到 | 早期判定 ≤ 75s + 60s poll + keepalive ≤ 25s ≈ **最快 ~2.5 分钟** |
| 早期判定不命中（罕见，比如对端虽断但偶发回包） | 兜底 hs_age 180s + 60s poll + 25s ≈ **最坏 ~4 分钟** |

调参（在 `wg-svc-runner` 顶部）：
- `DDNS_POLL_SEC=60` — 解析频率
- `EARLY_STALL_HS_AGE_SEC=75` — 早期判定的握手龄阈值（约 3 倍 keepalive）
- `EARLY_STALL_TX_DELTA=100` — 早期判定要求的 tx 增量字节数（一个 keepalive 包约 32B）
- `HEALTHY_HS_AGE_SEC=180` — 兜底硬阈值
- `DOH_URLS=(...)` — 可信 DoH 端点列表

排错：`grep ddns /var/log/wireguard-wg0.log` 看 watchdog 的轨迹，关键事件：
- `boot-resolved to <ip>` — 启动时第一次记录
- `<host> <old> -> <new> but hs_age=Ns tx_delta=N rx_delta=N, deferring` — DNS 变了但隧道还活，**主动忽略**
- `<host> <old> -> <new> (hs_age=Ns tx_delta=N rx_delta=N, update-early-stall) ; updating peer` — 早期判定切
- `<host> <old> -> <new> (... update-stale) ; updating peer` — 兜底切
- `endpoint updated to <ip>:<port>` — `wg set` 成功
- `WARN ... FAKE-IP` — 解析到保留段（DNS 被劫持，DoH 也没救回来）
- `WARN unable to resolve` — 所有 DoH 和 fallback 全失败

## State 文件（AI / 排错首选入口）

watchdog 每轮把决策快照原子写入 `/var/run/wg-svc-<iface>.state`（644，普通用户可读）。
排错时**先看这个文件**，比 grep 日志快得多：

```bash
cat /var/run/wg-svc-utun5.state
```

字段含义：

| 字段 | 含义 |
|---|---|
| `endpoint_ip` / `endpoint_port` | watchdog 视角下当前 peer endpoint（可能短暂滞后于 wireguard-go 内核状态 ≤1 个 poll） |
| `handshake_age_sec` | 距上次握手秒数；999999 表示从未握手 |
| `rx_bytes` / `tx_bytes` | 累计字节，wireguard-go 进程重启即清零 |
| `rx_delta_last_poll` / `tx_delta_last_poll` | 上一轮 poll 之间的增量；`-1` 表示首轮无历史 |
| `last_decision` | `healthy` / `defer` / `update-early-stall` / `update-stale` / `update-failed` |
| `last_decision_at` | 上次决策的 ISO 时间戳 |
| `watchdog_pid` | watchdog 后台子进程 pid（`kill -0` 探活用） |
| `thresholds` | 当前生效的所有阈值，方便调参验证 |

判断隧道健康度的快速规则：

- `last_decision == healthy` 且 `handshake_age_sec < 60` → 完全正常
- `last_decision == defer` → DNS 抖了但被设计性忽略，正常
- `rx_delta_last_poll == 0` 且 `tx_delta_last_poll > 100` → 已经在黑洞，下一轮会触发 early-stall
- `last_decision == update-failed` → `wg set` 拒绝，看日志找原因

文件在 daemon 退出时自动删除，所以"文件不存在" = "服务没在跑"。

## 完成前验证

任何一次启动/重启/改配后必须验证：

```bash
sudo launchctl print system/top.qiyuey.wireguard.wg0 | awk '/state =|pid =|last exit/'
sudo wg show
ifconfig | awk '/^utun[0-9]+:/{i=$1} /inet 192\.168\.10\./{print i, $0}'
ping -c 2 -W 1 192.168.1.1 2>&1 | tail -3
```

汇报时给出：launchd `state` + `pid`、`latest handshake`、对端内网 ping 是否通。
