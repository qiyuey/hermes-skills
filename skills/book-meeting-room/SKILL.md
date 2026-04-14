---
name: book-meeting-room
description: 自动抢预会议室系统 meeting-room.zhenguanyu.com，支持查询空闲会议室和自动预约
triggers:
  - 帮我预约会议室
  - 抢会议室
  - 预订会议室
  - 查询空闲会议室
  - book meeting room
---

# 自动抢会议室

用于自动操作 https://meeting-room.zhenguanyu.com/#/meeting-calendar 会议室预约系统。

**核心用法：通过 Hermes cron 每分钟触发，自动抢占目标时段，抢到后发 Telegram 通知。**

## 脚本路径

`~/.hermes/scripts/book_meeting_room.py`

## 系统预定规则（来自官方"预定说明"）

| 类型 | 可预定范围 | 单次时长 | 每日上限 | 特殊说明 |
|------|-----------|---------|---------|---------|
| 普通会议室 | T+7日内（共8天） | ≤2小时 | 4小时 | **每天09:30释放新的一天** |
| 面试间 | T+7日 | ≤2小时 | 无限制 | 0点未预约时段转为普通会议室 |
| 小教室 | T+7日 | ≤3小时 | 6小时 | 0点未预约时段转为普通会议室 |
| 培训间 | T+7日 | 不限 | 无限制 | 0点未预约时段转为普通会议室 |

> 关键：普通会议室**每天09:30**新增一天（T+7）。抢热门会议室要在09:30之后立刻出手。

签到规则：会议开始后 **10分钟内**必须签到，否则系统自动取消预定。

## Cron 自动化抢占（推荐方式）

### 抢法：双层轮询

- **外层**：cron 每1分钟触发一次
- **内层**：脚本 `--snipe` 模式，每次内部重试5次（每10秒），等效约每10秒尝试一次
- **日期未开放时**：脚本自动检测，exit 2 静默跳过，等到09:30开放后立即开始抢

### 创建 cron 示例

```
# 抢下周三（2026-04-22）下午 8人以上 1小时（14:00-19:00窗口）
hermes cron create \
  --name "会议室狙击-下周三下午" \
  --schedule "every 1m" \
  --prompt "执行以下命令：
cd ~/.hermes/scripts && python3 book_meeting_room.py \
  --date 2026-04-22 --start 14:00 --end 19:00 \
  --duration 60 --min-capacity 8 \
  --office-id 170 168 172 260 \
  --topic \"团队会议\" \
  --snipe --snipe-times 5 --snipe-interval 10

判断规则：
- exit 0 + 含\"预约成功\" → 发通知（会议室名、时间、预约ID），任务完成
- exit 2 → [SILENT]，等下一分钟
- exit 1 → 报告错误"
```

### cron prompt 判断规则（固定模板）

```
- exit code 0 且输出含"预约成功" → 发送成功通知（回复会议室名称、时间、预约ID）
- exit code 2 → 静默退出（[SILENT]），等待下一分钟继续
- exit code 1 → 报告错误详情
```

exit code 说明：
- `0` = 预约成功
- `1` = 发生错误
- `2` = 未抢到 / 日期未开放（静默，cron 继续）

## 命令行用法

```bash
# 干跑：查询明天下午空闲（不预约）
python3 ~/.hermes/scripts/book_meeting_room.py \
  --date tomorrow --start 14:00 --end 19:00 --duration 60 --dry-run

# 直接预约：明天 14:00-15:00
python3 ~/.hermes/scripts/book_meeting_room.py --start 14:00 --end 15:00

# 窗口扫描：下午任意空闲1小时，8人以上
python3 ~/.hermes/scripts/book_meeting_room.py \
  --start 14:00 --end 19:00 --duration 60 --min-capacity 8

# 高频抢占（手动）
python3 ~/.hermes/scripts/book_meeting_room.py \
  --date 2026-04-22 --start 14:00 --end 19:00 --duration 60 \
  --min-capacity 8 --office-id 170 168 172 260 \
  --snipe --snipe-times 5 --snipe-interval 10

# Cookie 失效时强制重新登录
python3 ~/.hermes/scripts/book_meeting_room.py --refresh-login --dry-run
```

## 完整参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--date` | 明天 | 预约日期 YYYY-MM-DD |
| `--start` | 10:00 | 开始时间（窗口模式下为窗口起点） |
| `--end` | 11:00 | 结束时间（窗口模式下为窗口终点） |
| `--duration` | 无 | 会议时长（分钟）。指定后在窗口内扫描整点时段，找第一个可用的 |
| `--topic` | 团队会议 | 会议主题 |
| `--min-capacity` | 4 | 最小容量 |
| `--office-id` | 170 168 172 260 | 职场ID列表（F区→E区→ABD区→BD区） |
| `--room-id` | 无 | 指定会议室ID，跳过自动筛选 |
| `--attendees` | [] | 与会者 ldap 列表 |
| `--dry-run` | False | 只查询不预约 |
| `--refresh-login` | False | 强制重新登录 |
| `--snipe` | False | 高频抢占模式，适合 cron 调用 |
| `--snipe-times` | 5 | snipe 模式重试次数 |
| `--snipe-interval` | 10 | snipe 模式每次间隔秒数 |

## 用户偏好

- **工作地点**：F区4层
- **下班时间**：19:00，不抢 endTime > 19:00 的时段
- **时段约定**：下午 = 14:00–19:00；不指定时间 = 在范围内找第一个空闲时段
- **会议室类型**：当天可抢所有类型；非当天只抢 type=5（普通会议室）

### 抢占优先级

1. **F区4层**（officeId=170）：2188(404,6人), 2190(407,6人), 2186(410,8人), 2175(405,12人), 2006(412,12人)
2. **E区4层**（officeId=168）：2048(402,12人), 2051(409,4人), 2055(410,4人), 2036(411,12人), 2037(413,12人)
3. **EF其他层**：F5/F6/E3/E5/E6 混排
4. **利星行ABD/BD区**（officeId=172/260）：兜底

只考虑利星行中心A座（officeId=168/170/172/260），不考虑望京SOHO等其他职场。

## 系统信息

- **认证方式**：飞连 SSO 一键登录（Playwright 自动化）
- **用户**：从 `GET /ep-inspire/user/queryUserInfo` 动态获取，不硬编码
- **城市**：北京（cityId: 178）
- **Cookie 缓存**：`~/.hermes/cache/meeting_room_cookies.json`（约7天有效）

### 职场列表（利星行中心A座）

| 职场 | officeId | 楼层 | 备注 |
|------|----------|------|------|
| F区 | 170 | 4-6层 | 默认/首选 |
| E区 | 168 | 3-6层 | 次选 |
| ABD区 | 172 | 4层 | 兜底 |
| BD区 | 260 | 2层 | 兜底 |

### 关键 API

```
POST /ep-inspire/booking/queryList      # 查询会议室列表+预约情况
POST /ep-inspire/booking/bookRoom       # 执行预约
POST /ep-inspire/booking/cancelMeeting?meetingId=xxx  # 取消预约（meetingId 必须在 query string，body 为 {}）
GET  /ep-inspire/booking/queryMyBooking # 我的预约
GET  /ep-inspire/user/queryUserInfo     # 用户信息
```

### queryList 请求体
```json
{
  "pageNo": 1, "pageSize": 100,
  "bookDate": "2026-04-22",
  "capacity": [], "cityId": 178,
  "equipments": [], "floors": [],
  "officeId": [170],
  "onlyAvailable": false, "onlyEmpty": false, "type": []
}
```

### bookRoom 请求体
```json
{
  "bookDate": "2026-04-22",
  "startTime": "14:00:00",
  "endTime": "15:00:00",
  "meetingName": "团队会议",
  "roomId": 2188,
  "attendees": ["yuchuanbj"]
}
```

## 会议室完整列表

### F区（officeId=170）

| 层 | 名称 | roomId | 容量 | 类型 |
|----|------|--------|------|------|
| 4层 | 404 | 2188 | 6人 | 会议室 |
| 4层 | 405 | 2175 | 12人 | 会议室 |
| 4层 | 407 | 2190 | 6人 | 会议室 |
| 4层 | 410 | 2186 | 8人 | 会议室 |
| 4层 | 412 | 2006 | 12人 | 会议室 |
| 5层 | 501 | 2191 | 6人 | 会议室 |
| 5层 | 502 | 2285 | 4人 | 会议室 |
| 5层 | 503 | 2286 | 3人 | 会议室 |
| 5层 | 507 | 2192 | 6人 | 会议室 |
| 5层 | 509 | 2178 | 10人 | 会议室 |
| 6层 | 602 | 2194 | 6人 | 会议室 |
| 6层 | 603 | 2195 | 6人 | 会议室 |
| 6层 | 605 | 2180 | 10人 | 会议室 |
| 6层 | 606 | 2181 | 10人 | 会议室 |
| 6层 | 607 | 2182 | 10人 | 会议室 |
| 6层 | 608 | 2184 | 10人 | 会议室 |

### E区（officeId=168）

| 层 | 名称 | roomId | 容量 | 类型 |
|----|------|--------|------|------|
| 3层 | 302 | 1962 | 4人 | 会议室 |
| 3层 | 309 | 1937 | 20人 | 会议室 |
| 3层 | 311 | 1939 | 12人 | 会议室 |
| 3层 | 312 | 1940 | 12人 | 会议室 |
| 3层 | 314 | 1960 | 10人 | 会议室 |
| 4层 | 402 | 2048 | 12人 | 会议室 |
| 4层 | 409 | 2051 | 4人 | 会议室 |
| 4层 | 410 | 2055 | 4人 | 会议室 |
| 4层 | 411 | 2036 | 12人 | 会议室 |
| 4层 | 413 | 2037 | 12人 | 会议室 |
| 5层 | 501 | 2220 | 16人 | 会议室 |
| 5层 | 503 | 2222 | 16人 | 会议室 |
| 5层 | 505 | 1973 | 14人 | 会议室 |
| 5层 | 506 | 2236 | 12人 | 会议室 |
| 5层 | 507 | 2240 | 12人 | 会议室 |
| 6层 | 610 | 2260 | 6人 | 会议室 |
| 6层 | 616 | 14528 | 6人 | 会议室 |
| 6层 | 617 | 14529 | 10人 | 会议室 |
| 6层 | 619 | 14531 | 6人 | 会议室 |
| 6层 | 620 | 14532 | 6人 | 会议室 |
| 6层 | 622 | 14534 | 6人 | 会议室 |

### ABD区（officeId=172，4层，兜底）

| 名称 | roomId | 容量 |
|------|--------|------|
| 401 | 2279 | 6人 |
| 403 | 2280 | 6人 |
| 404 | 2295 | 4人 |
| 406 | 2197 | 6人 |
| 413 | 2284 | 5人 |
| 414 | 2278 | 9人 |
| 417 | 2283 | 6人 |
| 418 | 2275 | 30人 |
| 420 | 2242 | 10人 |
| 421 | 2296 | 4人 |
| 423 | 2297 | 4人 |
| 425 | 2276 | 30人 |
| 426 | 2201 | 6人 |
| 432 | 2208 | 6人 |
| 433 | 2243 | 10人 |
| 437 | 2246 | 10人 |
| 439 | 2238 | 10人 |
| 440 | 2185 | 5人 |
| 441 | 2211 | 6人 |

### BD区（officeId=260，2层，兜底）

| 名称 | roomId | 容量 |
|------|--------|------|
| 202 | 2308 | 16人 |
| 203 | 2309 | 16人 |
| 204 | 2310 | 16人 |
| 205 | 2311 | 16人 |

## 取消预约（手动触发流程）

用户说"取消会议室"/"取消预约"时，按以下步骤操作：

### 第一步：查找目标预约的 meetingId

```python
sys.path.insert(0, str(Path.home() / ".hermes/scripts"))
import book_meeting_room as b

session, ldap = b.ensure_session()

payload = {
    "pageNo": 1, "pageSize": 100,
    "bookDate": "YYYY-MM-DD",
    "capacity": [], "cityId": 178,
    "equipments": [], "floors": [],
    "officeId": [170, 168, 172, 260],
    "onlyAvailable": False, "onlyEmpty": False, "type": []
}
r = session.post(f"{b.API_BASE}/ep-inspire/booking/queryList", json=payload)
rooms = (r.json().get("data") or {}).get("list") or []
# 遍历 room["bookingInfos"]，找 selfBooking=True 且匹配目标时段/会议名的条目，获取 meetingId
```

### 第二步：取消

```python
result = b.cancel_meeting(session, meeting_id)
# code=0 → 成功
# code=-1/"会议已被取消或不存在" → 已取消或 meetingId 错误
# code=-1/"系统错误" → 已结束，无法取消
```

**⚠️ 关键：`cancelMeeting` 接口的 meetingId 必须放在 query string，body 为空 `{}`。**
放在 JSON body 里会返回"系统错误"，这是已踩过的坑。

`cancel_meeting()` 函数已封装在 `book_meeting_room.py` 中，直接调用即可。

## 注意事项

1. **Cookie 有效期**约7天，过期加 `--refresh-login` 重新登录
2. **一键登录**依赖飞连 SSO，需公司内网或 VPN
3. **签到**：会议开始后10分钟内必须签到，否则自动取消
4. **普通会议室单次 ≤2小时**，一天最多4小时
5. **开放时间**：每天09:30释放新一天（T+7）。脚本自动检测，未开放时 exit 2 静默跳过
6. 周期会议长期占用，热门时段往往无空位，建议09:30后立刻抢

## 依赖

```bash
pip3 install playwright requests
python3 -m playwright install chromium
```
