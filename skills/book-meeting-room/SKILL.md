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

## 脚本路径

`~/.hermes/scripts/book_meeting_room.py`

## 系统信息

- **认证方式**：飞连 SSO 一键登录（Playwright 自动化）
- **用户**：登录后从 `GET /ep-inspire/user/queryUserInfo` 动态获取当前用户的 ldap 和姓名，不要硬编码
- **城市**：北京（cityId: 178）
- **默认职场**：利星行中心A座F区（officeId: 170）
- **Cookie 缓存**：`~/.hermes/cache/meeting_room_cookies.json`（约7天有效）
- **偏好会议室**：314（id=1960）

## 职场列表（北京，cityId=178）

| 职场 | officeId | 楼层 | 会议室数 | 备注 |
|------|----------|------|---------|------|
| 利星行中心A座E区 | 168 | 3-6层 | 21间 | |
| 利星行中心A座F区 | 170 | 4-6层 | 16间 | 默认职场 |
| 利星行中心A座ABD区 | 172 | 4层 | 31间 | 兜底 |
| 利星行中心A座BD区 | 260 | 2层 | 4间 | 兜底 |

> 只考虑利星行中心A座（officeId=168/170/172/260），不考虑望京SOHO、嘉润花园、宝能中心等其他职场。

## 关键 API

```
POST /ep-inspire/booking/queryList     # 查询会议室列表 + 预约情况
POST /ep-inspire/booking/bookRoom      # 执行预约
POST /ep-inspire/booking/cancelMeeting # 取消预约
GET  /ep-inspire/booking/queryMyBooking # 我的预约
GET  /ep-inspire/user/queryUserInfo    # 用户信息
GET  /ep-inspire/filter/queryOffice?cityId=178  # 职场列表
```

### queryList 请求体
```json
{
  "pageNo": 1,
  "pageSize": 100,
  "bookDate": "2026-04-18",
  "capacity": [],
  "cityId": 178,
  "equipments": [],
  "floors": [],
  "officeId": [168],
  "onlyAvailable": false,
  "onlyEmpty": false,
  "type": []
}
```

### queryList 响应结构
```json
{
  "code": 0,
  "data": {
    "count": 51,
    "list": [
      {
        "roomInfo": {
          "roomId": 1960,
          "roomName": "314",
          "capacity": 10,
          "type": 5,
          "officeId": 168,
          "officeName": "利星行中心A座E区",
          "idleState": "空闲"
        },
        "bookingInfos": [
          {
            "meetingId": 123,
            "startTime": "10:00:00",
            "endTime": "11:00:00",
            "status": "待签到"
          }
        ]
      }
    ]
  }
}
```

### bookRoom 请求体
```json
{
  "bookDate": "2026-04-18",
  "startTime": "14:00:00",
  "endTime": "15:00:00",
  "meetingName": "团队会议",
  "roomId": 1960,
  "attendees": ["<current_ldap>"]
}
```

## 用法示例

### 通过 terminal 直接运行

```bash
# 干跑模式：查询明天 10:00-11:00 空闲
python3 ~/.hermes/scripts/book_meeting_room.py --dry-run

# 预约明天 14:00-15:00（自动选最优会议室）
python3 ~/.hermes/scripts/book_meeting_room.py --start 14:00 --end 15:00

# 预约指定日期
python3 ~/.hermes/scripts/book_meeting_room.py --date 2026-04-18 --start 10:00 --end 12:00

# 预约指定会议室
python3 ~/.hermes/scripts/book_meeting_room.py --room-id 1960 --start 14:00 --end 15:00

# 指定主题 + 容量要求
python3 ~/.hermes/scripts/book_meeting_room.py --topic "产品评审" --min-capacity 8 --start 10:00 --end 11:00

# 强制重新登录（cookie 过期时）
python3 ~/.hermes/scripts/book_meeting_room.py --refresh-login --dry-run
```

## 完整参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--date` | 明天 | 预约日期 YYYY-MM-DD |
| `--start` | 10:00 | 开始时间 |
| `--end` | 11:00 | 结束时间 |
| `--topic` | 团队会议 | 会议主题 |
| `--min-capacity` | 4 | 最小容量 |
| `--office-id` | 170 168 172 260 | 职场ID列表（F区→E区→ABD区→BD区） |
| `--room-id` | 无 | 指定会议室ID，跳过自动筛选 |
| `--attendees` | [] | 与会者 ldap 列表 |
| `--dry-run` | False | 只查询不预约 |
| `--refresh-login` | False | 强制重新登录 |
| `--snipe` | False | 高频抢占模式：内部循环重试，适合 cron 调用 |
| `--snipe-times` | 5 | snipe 模式重试次数 |
| `--snipe-interval` | 10 | snipe 模式每次间隔秒数 |

## 用户偏好与抢占策略

下班时间 **19:00**，只抢 19:00 之前结束的时段（即 endTime ≤ 19:00）。

时段约定：
- **下午**：14:00–19:00
- 用户不指定具体时间时，视为不限，在范围内找第一个空闲时段即可

用户在 **F区4层**，扩展优先级：
1. F区4层（officeId=170，floor=4层）— 最优先
2. E区4层（officeId=168，floor=4层）— 次优先
3. E区/F区其他楼层 — 第三优先
4. 利星行中心其他区（ABD区 officeId=172、BD区 officeId=260）— 兜底

建议 `PREFERRED_ROOM_IDS` 顺序：
- F4: 2188(404), 2190(407), 2186(410), 2175(405), 2006(412)
- E4: 2048(402), 2051(409), 2055(410), 2036(411), 2037(413)
- EF其他层: F5/F6/E3/E5/E6 混排
- 全部兜底: 加入 officeId=172（ABD区）

只考虑利星行中心A座（officeId=168/170/172/260），不考虑望京SOHO、嘉润花园、宝能中心等其他职场。

## 会议室完整列表

### F区（officeId=170）

**4层（首选）**

| 名称 | roomId | 容量 |
|------|--------|------|
| 404 | 2188 | 6人 |
| 405 | 2175 | 12人 |
| 407 | 2190 | 6人 |
| 410 | 2186 | 8人 |
| 412 | 2006 | 12人 |

**5层**

| 名称 | roomId | 容量 |
|------|--------|------|
| 501 | 2191 | 6人 |
| 502 | 2285 | 4人 |
| 503 | 2286 | 3人 |
| 507 | 2192 | 6人 |
| 509 | 2178 | 10人 |

**6层**

| 名称 | roomId | 容量 |
|------|--------|------|
| 602 | 2194 | 6人 |
| 603 | 2195 | 6人 |
| 605 | 2180 | 10人 |
| 606 | 2181 | 10人 |
| 607 | 2182 | 10人 |
| 608 | 2184 | 10人 |

### E区（officeId=168）

**4层（次选）**

| 名称 | roomId | 容量 |
|------|--------|------|
| 402 | 2048 | 12人 |
| 409 | 2051 | 4人 |
| 410 | 2055 | 4人 |
| 411 | 2036 | 12人 |
| 413 | 2037 | 12人 |

**3层**

| 名称 | roomId | 容量 |
|------|--------|------|
| 302 | 1962 | 4人 |
| 309 | 1937 | 20人 |
| 311 | 1939 | 12人 |
| 312 | 1940 | 12人 |
| 314 | 1960 | 10人 |

**5层**

| 名称 | roomId | 容量 |
|------|--------|------|
| 501 | 2220 | 16人 |
| 503 | 2222 | 16人 |
| 505 | 1973 | 14人 |
| 506 | 2236 | 12人 |
| 507 | 2240 | 12人 |

**6层**

| 名称 | roomId | 容量 |
|------|--------|------|
| 610 | 2260 | 6人 |
| 616 | 14528 | 6人 |
| 617 | 14529 | 10人 |
| 619 | 14531 | 6人 |
| 620 | 14532 | 6人 |
| 622 | 14534 | 6人 |

### ABD区（officeId=172，4层，兜底）

| 名称 | roomId | 容量 | 类型 |
|------|--------|------|------|
| 401 | 2279 | 6人 | 会议室 |
| 403 | 2280 | 6人 | 会议室 |
| 404 | 2295 | 4人 | 会议室 |
| 406 | 2197 | 6人 | 会议室 |
| 413 | 2284 | 5人 | 会议室 |
| 414 | 2278 | 9人 | 会议室 |
| 417 | 2283 | 6人 | 会议室 |
| 418 | 2275 | 30人 | 会议室 |
| 420 | 2242 | 10人 | 会议室 |
| 421 | 2296 | 4人 | 会议室 |
| 423 | 2297 | 4人 | 会议室 |
| 425 | 2276 | 30人 | 会议室 |
| 426 | 2201 | 6人 | 会议室 |
| 427 | 2147 | 3人 | 会议室 |
| 428 | 2203 | 4人 | 会议室 |
| 429 | 2202 | 6人 | 会议室 |
| 430 | 2294 | 4人 | 会议室 |
| 431 | 2298 | 4人 | 会议室 |
| 432 | 2208 | 6人 | 会议室 |
| 433 | 2243 | 10人 | 会议室 |
| 437 | 2246 | 10人 | 会议室 |
| 439 | 2238 | 10人 | 会议室 |
| 440 | 2185 | 5人 | 会议室 |
| 441 | 2211 | 6人 | 会议室 |

### BD区（officeId=260，2层，兜底）

| 名称 | roomId | 容量 | 类型 |
|------|--------|------|------|
| 202 | 2308 | 16人 | 会议室 |
| 203 | 2309 | 16人 | 会议室 |
| 204 | 2310 | 16人 | 会议室 |
| 205 | 2311 | 16人 | 会议室 |



| 类型名 | typeId | 说明 |
|--------|--------|------|
| 会议室 | 5 | 普通会议室，主要抢这类 |
| 面试间 | 4 | 小容量，适合1对1 |
| 培训室 | 6 | 大容量，408可容纳100人 |
| 小教室 | 3 | 1人工位，3F_xx 编号 |

筛选规则：
- **当天**：可抢所有类型（type=3/4/5/6）
- **非当天**：只抢 type=5（会议室），排除面试间/培训室/小教室


## 高频抢占策略（会议室极难抢场景）

会议室竞争激烈，采用双层轮询：
- **外层**：Hermes cron 每1分钟触发一次（系统最小粒度）
- **内层**：脚本 `--snipe` 模式，每次触发后再内部重试5次（每10秒一次）
- 效果：等效约每10秒尝试一次，抢到后 exit 0，cron 收到成功信号后停止并发 Telegram 通知

```bash
# cron 调用示例（每分钟，内部重试5次×10秒）
python3 ~/.hermes/scripts/book_meeting_room.py \
  --snipe --snipe-times 5 --snipe-interval 10 \
  --date 2026-04-18 --start 14:00 --end 15:00 --topic "团队会议"
```

exit code 说明：
- 0 = 预约成功（cron 停止）
- 1 = 发生错误
- 2 = 本轮未抢到（cron 继续下一分钟）

## 注意事项

1. **Cookie 有效期**约 7 天，过期时加 `--refresh-login` 重新登录
2. **一键登录**依赖飞连 SSO，需要在公司内网或 VPN 下才能成功
3. 周期会议（`frequency: true`）会长期占用会议室，工作日热门时段往往无空位
4. 预约后需要在会议开始前**签到**，否则会被取消并记录黑点
5. 系统可提前最多 **7天** 预约（第8天及以后返回"参数非法"）。抢未来某天时，需等到距目标日期 ≤7 天时才能查询，cron 应在日期未开放时静默等待，开放后立即开始抢占。

## 依赖

```bash
pip3 install playwright requests
python3 -m playwright install chromium
```
