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
- **默认职场**：利星行中心A座E区（officeId: 168）
- **Cookie 缓存**：`~/.hermes/cache/meeting_room_cookies.json`（约7天有效）
- **偏好会议室**：314（id=1960）

## 职场列表（北京，cityId=178）

| 职场 | officeId | 楼层 | 会议室数 | 备注 |
|------|----------|------|---------|------|
| 利星行中心A座E区 | 168 | 3-6层 | 21间 | 默认职场 |
| 利星行中心A座F区 | 170 | 4-6层 | 16间 | |
| 利星行中心A座ABD区 | 172 | 4层 | 25间 | 会议室最多 |
| 利星行中心A座BD区 | 260 | 2层 | 4间 | |
| 望京SOHO T3B座 | 256 | 19层 | 0间（全小教室）| |
| 望京SOHO T3A座 | 254 | - | 无数据 | |
| 嘉润花园B座 | 290 | - | 无数据 | |
| 宝能中心B座 | 323 | - | 无数据 | |

> E区抢不到时可扩大到 officeId=[168,170,172] 同时查三个区。

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
| `--office-id` | 168 | 职场ID（168=利星行E区） |
| `--room-id` | 无 | 指定会议室ID，跳过自动筛选 |
| `--attendees` | [] | 与会者 ldap 列表 |
| `--dry-run` | False | 只查询不预约 |
| `--refresh-login` | False | 强制重新登录 |

## 用户偏好与抢占策略

下班时间 **19:00**，只抢 19:00 之前结束的时段（即 endTime ≤ 19:00）。

用户在 **F区4层**，扩展优先级：
1. F区4层（officeId=170，floor=4层）— 最优先
2. E区4层（officeId=168，floor=4层）— 次优先
3. F5/F6/E3/E5/E6 同级兜底

建议 `PREFERRED_ROOM_IDS` 顺序：
- F4: 2188(404), 2190(407), 2186(410), 2175(405), 2006(412)
- E4: 2048(402), 2051(409), 2055(410), 2036(411), 2037(413)
- 兜底（同级）: F5/F6 + E3/E5/E6 混排

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

## 会议室类型（meetingRoomTypeId）

| 类型名 | typeId | 说明 |
|--------|--------|------|
| 会议室 | 5 | 普通会议室，主要抢这类 |
| 面试间 | 4 | 小容量，适合1对1 |
| 培训室 | 6 | 大容量，408可容纳100人 |
| 小教室 | 3 | 1人工位，3F_xx 编号 |

默认脚本筛选 type=5（会议室），排除教室/小教室。

## 会议室 ID 参考（利星行E区，type=5 会议室）

| 名称 | roomId | 容量 |
|------|--------|------|
| 302 | 1962 | 4 |
| 309 | 1937 | 20 |
| 311 | 1939 | 12 |
| 312 | 1940 | 12 |
| 314 | 1960 | 10 ← 默认偏好 |
| 411 | 2036 | 12 |
| 501 | ? | 16 |
| 503 | ? | 16 |
| 505 | ? | 14 |
| 506 | ? | 12 |
| 507 | ? | 12 |
| 610 | ? | 6 |
| 616 | ? | 6 |
| 617 | ? | 10 |
| 619 | ? | 6 |

**面试间（type=4）**

| 名称 | roomId | 容量 |
|------|--------|------|
| 304 | ? | 3 |
| 305 | ? | 3 |
| 403 | ? | 4 |

**培训室（type=6）**

| 名称 | roomId | 容量 |
|------|--------|------|
| 303 | 1963 | 4 |
| 408 | ? | 100 |

> 注：带 ? 的 roomId 需运行 `--dry-run` 时从 queryList 响应里确认。

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
5. 系统可提前最多 **7天** 预约

## 依赖

```bash
pip3 install playwright requests
python3 -m playwright install chromium
```
