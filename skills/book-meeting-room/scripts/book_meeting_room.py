#!/usr/bin/env python3
"""
自动抢会议室脚本
用法：
  python3 book_meeting_room.py [--date YYYY-MM-DD] [--start HH:MM] [--end HH:MM]
                               [--duration 60]  # 时长(分钟)，配合--start/--end窗口扫描
                               [--topic 会议主题] [--min-capacity 4]
                               [--office-id 170 168] [--room-id ROOM_ID]
                               [--dry-run] [--refresh-login]
                               [--snipe]        # 高频抢占模式：内部重试N次后退出
                               [--snipe-times N] [--snipe-interval SEC]

模式说明：
  固定时段：--start 14:00 --end 15:00（不传 --duration，直接抢这个时段）
  窗口扫描：--start 14:00 --end 19:00 --duration 60
            在 14:00-19:00 窗口内按整点扫描，找第一个有空闲 1 小时的时段预约

优先级策略：F区4层 → E区4层 → EF其他层 → 利星行ABD/BD区兜底
下班时间 19:00，不抢 endTime > 19:00 的时段
会议室类型：当天可抢所有类型，非当天只抢 type=5（会议室）
开放规则：普通会议室每天09:30释放新的一天（T+7），脚本自动检测未开放日期并 exit 2 静默跳过
"""

import argparse
import json
import sys
import time
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime, timedelta
from pathlib import Path

# ---- 配置 ----
MEETING_URL = "https://meeting-room.zhenguanyu.com/#/meeting-calendar"
API_BASE    = "https://meeting-room.zhenguanyu.com"
COOKIE_FILE = Path.home() / ".hermes" / "cache" / "meeting_room_cookies.json"
DEFAULT_CITY_ID   = 178
DEFAULT_OFFICE_IDS = [170, 168, 172, 260]   # F区→E区→ABD区→BD区
DEFAULT_TOPIC     = "团队会议"
DEFAULT_MIN_CAPACITY = 4
LDAP = None          # 运行时从 queryUserInfo API 动态获取
WORK_END_HOUR = 19   # 下班时间，不抢结束时间超过此值的时段

# 偏好会议室（按优先级排序）
# 优先级1：F区4层
# 优先级2：E区4层
# 优先级3（同级兜底）：F5/F6 + E3/E5/E6 混排
PREFERRED_ROOM_IDS = [
    # F4（首选）
    2188, 2190, 2186, 2175, 2006,
    # E4（次选）
    2048, 2051, 2055, 2036, 2037,
    # EF其他层（第三优先，F5/F6/E3/E5/E6 混排）
    2191, 2285, 2192, 2178,              # F5
    2194, 2195, 2180, 2181, 2182, 2184,  # F6
    1962, 1937, 1939, 1940, 1960,        # E3
    2220, 2222, 1973, 2236, 2240,        # E5
    2260, 14528, 14529, 14531, 14532, 14534,  # E6
    # 利星行ABD区兜底（type=5 会议室）
    2279, 2280, 2295, 2197, 2284, 2278, 2283, 2275, 2242, 2296,
    2297, 2276, 2201, 2147, 2203, 2202, 2294, 2298, 2208, 2243,
    2151, 2157, 2196, 2246, 2238, 2185, 2211,
    # 利星行BD区兜底
    2308, 2309, 2310, 2311,
]


# ───────────────────────── 参数解析 ─────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="自动抢会议室")
    p.add_argument("--date",          default=None,  help="预约日期 YYYY-MM-DD，默认明天")
    p.add_argument("--start",         default="10:00", help="开始时间 HH:MM（窗口扫描模式下为窗口起点）")
    p.add_argument("--end",           default="11:00", help="结束时间 HH:MM（窗口扫描模式下为窗口终点）")
    p.add_argument("--duration",      type=int, default=None,
                   help="会议时长（分钟）。指定后在 --start/--end 窗口内按整点扫描，找第一个可用时段")
    p.add_argument("--topic",         default=DEFAULT_TOPIC)
    p.add_argument("--min-capacity",  type=int, default=DEFAULT_MIN_CAPACITY)
    p.add_argument("--office-id",     type=int, nargs="*", default=None,
                   help="职场ID列表，默认 170 168（F区+E区）")
    p.add_argument("--room-id",       type=int, default=None, help="指定会议室ID")
    p.add_argument("--dry-run",       action="store_true", help="只查询不预约")
    p.add_argument("--refresh-login", action="store_true", help="强制重新登录")
    p.add_argument("--attendees",     nargs="*", default=[])
    # 高频抢占模式
    p.add_argument("--snipe",         action="store_true",
                   help="高频抢占模式：内部循环重试，适合 cron 调用")
    p.add_argument("--snipe-times",   type=int, default=5,
                   help="snipe 模式下重试次数，默认5次")
    p.add_argument("--snipe-interval",type=float, default=10.0,
                   help="snipe 模式下每次间隔秒数，默认10秒")
    return p.parse_args()


# ───────────────────────── Cookie / 登录 ─────────────────────────

def load_cookies():
    if COOKIE_FILE.exists():
        return json.loads(COOKIE_FILE.read_text())
    return None


def save_cookies(cookies):
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COOKIE_FILE, "w") as f:
        json.dump(cookies, f, indent=2)


def make_session(cookies_list):
    import requests as _req
    s = _req.Session()
    for c in cookies_list:
        s.cookies.set(c["name"], c["value"],
                      domain=c.get("domain", "meeting-room.zhenguanyu.com"))
    return s


def check_session_valid(session):
    """快速验证 cookie 是否仍有效，返回 (valid, ldap)"""
    try:
        r = session.get(f"{API_BASE}/ep-inspire/user/queryUserInfo", timeout=8)
        data = r.json()
        if data.get("code") == 0:
            ldap = (data.get("data") or {}).get("ldap") or (data.get("data") or {}).get("username", "")
            return True, ldap
        return False, ""
    except Exception:
        return False, ""


def get_current_ldap(session):
    """从 API 获取当前登录用户的 ldap"""
    try:
        r = session.get(f"{API_BASE}/ep-inspire/user/queryUserInfo", timeout=8)
        data = r.json()
        if data.get("code") == 0:
            user = data.get("data") or {}
            return user.get("ldap") or user.get("username") or user.get("loginName") or user.get("userId", "")
        return ""
    except Exception:
        return ""


def do_playwright_login():
    """用 Playwright 一键登录，返回新 cookies 列表"""
    from playwright.sync_api import sync_playwright
    cookies = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()

        # 尝试加载旧 cookie 加速
        old = load_cookies()
        if old:
            context.add_cookies(old)

        page = context.new_page()
        print("[*] 打开会议室系统...")
        page.goto(MEETING_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        if page.locator(".user-info, .avatar, [class*='user'], [class*='login-success']").count() == 0:
            # 检查是否需要点击一键登录（未登录状态）
            login_needed = page.locator("text=一键登录").count() > 0 or page.locator("text=登录").count() > 0
            if login_needed:
                page.evaluate("""
                    const btn = Array.from(document.querySelectorAll('button'))
                                  .find(b => b.textContent.trim() === '一键登录');
                    if (btn) btn.click();
                """)
                print("[*] 点击一键登录...")
                time.sleep(4)
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass

        # 用 API 验证登录结果而非依赖页面文字
        cookies = context.cookies()
        session_check = make_session(cookies)
        valid, ldap = check_session_valid(session_check)
        if valid:
            print("[✓] 登录成功")
            cookies = context.cookies()
            save_cookies(cookies)
        else:
            print(f"[✗] 登录失败，URL: {page.url}")

        browser.close()
    return cookies


def ensure_session(refresh=False):
    """返回 (session, ldap)，必要时触发 Playwright 登录"""
    cookies_data = load_cookies()
    if cookies_data and not refresh:
        session = make_session(cookies_data)
        valid, ldap = check_session_valid(session)
        if valid and ldap:
            return session, ldap
        print("[*] Cookie 已失效，重新登录...")

    new_cookies = do_playwright_login()
    if not new_cookies:
        print("[✗] 无法获取有效 session")
        sys.exit(1)
    session = make_session(new_cookies)
    ldap = get_current_ldap(session)
    return session, ldap


# ───────────────────────── API 封装 ─────────────────────────

def query_rooms(session, book_date, office_ids, page_size=100):
    """查询多个职场的会议室列表，返回扁平列表"""
    import requests as _req
    all_rooms = []
    for oid in office_ids:
        try:
            resp = session.post(
                f"{API_BASE}/ep-inspire/booking/queryList",
                json={
                    "pageNo": 1, "pageSize": page_size,
                    "bookDate": book_date,
                    "capacity": [], "cityId": DEFAULT_CITY_ID,
                    "equipments": [], "floors": [],
                    "officeId": [oid],
                    "onlyAvailable": False, "onlyEmpty": False, "type": [],
                },
                timeout=10
            )
            data = resp.json().get("data", {})
            all_rooms.extend(data.get("list", []))
        except Exception as e:
            print(f"[!] queryList officeId={oid} 失败: {e}")
    return all_rooms


def is_date_open(session, book_date):
    """检查目标日期是否已开放预约（系统最多提前7天）"""
    try:
        resp = session.post(
            f"{API_BASE}/ep-inspire/booking/queryList",
            json={
                "pageNo": 1, "pageSize": 1,
                "bookDate": book_date,
                "capacity": [], "cityId": DEFAULT_CITY_ID,
                "equipments": [], "floors": [],
                "officeId": [DEFAULT_OFFICE_IDS[0]],
                "onlyAvailable": False, "onlyEmpty": False, "type": [],
            },
            timeout=10
        )
        data = resp.json()
        if data.get("code") == -1 and "参数非法" in (data.get("message") or ""):
            return False
        return True
    except Exception:
        return True  # 网络异常时不阻断，让后续逻辑处理


def is_available(room_data, start_time, end_time):
    """检查房间在指定时段是否空闲"""
    def t2m(t):
        h, m = map(int, t.split(":")[:2])
        return h * 60 + m
    s, e = t2m(start_time), t2m(end_time)
    for b in room_data.get("bookingInfos", []):
        if b.get("status") in ("已取消", "已结束"):
            continue
        bs, be = t2m(b["startTime"][:5]), t2m(b["endTime"][:5])
        if not (e <= bs or s >= be):
            return False
    return True


def find_available(rooms, start_time, end_time, min_capacity, book_date):
    """从房间列表中找出可用的，按 PREFERRED_ROOM_IDS 排序"""
    today = datetime.now().strftime("%Y-%m-%d")
    is_today = (book_date == today)
    # 当天可抢所有类型；非当天只抢 type=5（会议室）
    allowed_types = None if is_today else {5}

    avail = {
        r["roomInfo"]["roomId"]: r
        for r in rooms
        if r["roomInfo"].get("capacity", 0) >= min_capacity
        and (allowed_types is None or r["roomInfo"].get("type") in allowed_types)
        and is_available(r, start_time, end_time)
    }
    # 按偏好顺序排列
    ordered = [avail[pid] for pid in PREFERRED_ROOM_IDS if pid in avail]
    # 偏好列表之外的追加到末尾
    preferred_set = set(PREFERRED_ROOM_IDS)
    ordered += [r for rid, r in avail.items() if rid not in preferred_set]
    return ordered


def cancel_meeting(session, meeting_id):
    """取消预约。meetingId 必须作为 query string 参数传递，body 为空 {}。"""
    resp = session.post(
        f"{API_BASE}/ep-inspire/booking/cancelMeeting",
        params={"meetingId": meeting_id},
        json={},
        timeout=10
    )
    return resp.json()


def do_book(session, book_date, start_time, end_time, room_id, topic, attendees):
    resp = session.post(
        f"{API_BASE}/ep-inspire/booking/bookRoom",
        json={
            "bookDate":    book_date,
            "startTime":   f"{start_time}:00",
            "endTime":     f"{end_time}:00",
            "meetingName": topic,
            "roomId":      room_id,
            "attendees":   attendees,
        },
        timeout=10
    )
    return resp.json()


# ───────────────────────── 核心逻辑 ─────────────────────────

def scan_slots(window_start, window_end, duration_min):
    """在窗口内生成所有整点时段，返回 [(start_str, end_str), ...]"""
    def t2m(t):
        h, m = map(int, t.split(":"))
        return h * 60 + m
    def m2t(m):
        return f"{m // 60:02d}:{m % 60:02d}"

    ws = t2m(window_start)
    we = t2m(window_end)
    work_end = WORK_END_HOUR * 60

    slots = []
    cur = ws
    while cur + duration_min <= min(we, work_end):
        slots.append((m2t(cur), m2t(cur + duration_min)))
        cur += 60  # 按整点步进
    return slots


def try_once(session, book_date, start_time, end_time, args, current_ldap="", duration=None):
    """查询一次，找到可用房间则预约，返回 (success, message)
    duration 不为 None 时，在 start_time-end_time 窗口内扫描所有整点时段。
    """
    office_ids = args.office_id if args.office_id else DEFAULT_OFFICE_IDS
    rooms = query_rooms(session, book_date, office_ids)
    if not rooms:
        return False, "未获取到会议室数据（可能 cookie 失效）"

    # 窗口扫描模式：生成所有候选时段，逐段查找
    if duration:
        slots = scan_slots(start_time, end_time, duration)
        if not slots:
            return False, f"窗口 {start_time}-{end_time} 内无法容纳 {duration} 分钟时段"
        for slot_start, slot_end in slots:
            available = find_available(rooms, slot_start, slot_end, args.min_capacity, book_date)
            if available:
                return _do_book_from_available(
                    available, session, book_date, slot_start, slot_end, args, current_ldap)
        return False, f"窗口内所有时段均无空闲（查了 {len(rooms)} 间，{len(slots)} 个时段）"

    # 固定时段模式
    available = find_available(rooms, start_time, end_time, args.min_capacity, book_date)
    if not available:
        return False, f"暂无空闲会议室（查了 {len(rooms)} 间）"
    return _do_book_from_available(
        available, session, book_date, start_time, end_time, args, current_ldap)


def _do_book_from_available(available, session, book_date, start_time, end_time, args, current_ldap):
    """从可用列表中逐个尝试预约，返回 (success, message)"""
    if args.dry_run:
        names = [r["roomInfo"]["roomName"] for r in available[:5]]
        return True, f"[DRY RUN] {start_time}-{end_time} 可用: {names}"

    attendees = ([current_ldap] if current_ldap else []) + [a for a in args.attendees if a != current_ldap]
    for room in available:
        ri = room["roomInfo"]
        result = do_book(session, book_date, start_time, end_time,
                         ri["roomId"], args.topic, attendees)
        if result.get("code") == 0:
            data = result.get("data")
            booking_id = data.get("id", data) if isinstance(data, dict) else data
            msg = (f"预约成功！\n"
                   f"  会议室: [{ri['roomId']}] {ri['roomName']} "
                   f"({ri.get('officeName','')})\n"
                   f"  时间: {book_date} {start_time}-{end_time}\n"
                   f"  主题: {args.topic}\n"
                   f"  预约ID: {booking_id}")
            return True, msg
        else:
            msg = result.get("message", "未知错误")
            print(f"  [!] {ri['roomName']} 预约失败: {msg}，尝试下一个...")

    return False, "所有可用房间均预约失败（可能被人抢先）"


def main():
    args = parse_args()

    book_date  = args.date or (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    start_time = args.start
    end_time   = args.end
    duration   = args.duration  # None = 固定时段模式，有值 = 窗口扫描模式

    # 下班时间校验（窗口扫描模式下校验窗口终点）
    end_h = int(end_time.split(":")[0])
    end_m = int(end_time.split(":")[1])
    if not duration and (end_h > WORK_END_HOUR or (end_h == WORK_END_HOUR and end_m > 0)):
        print(f"[!] 结束时间 {end_time} 超过下班时间 {WORK_END_HOUR}:00，已拒绝")
        sys.exit(1)

    mode_str = f"窗口扫描 {start_time}-{end_time} 每{duration}分钟" if duration else f"{start_time}-{end_time}"
    print(f"\n=== 会议室{'抢占' if args.snipe else '预约'} ==="
          f"\n日期: {book_date}  时段: {mode_str}"
          f"\n主题: {args.topic}  容量≥{args.min_capacity}"
          + ("\n[DRY RUN]" if args.dry_run else ""))

    session, current_ldap = ensure_session(refresh=args.refresh_login)
    if not current_ldap:
        print("[!] 警告：无法获取当前用户 ldap，attendees 将为空")
    else:
        print(f"[*] 当前用户: {current_ldap}")

    # 日期开放检查（系统最多提前7天）
    if not is_date_open(session, book_date):
        from datetime import date as _date
        today = _date.today()
        target = datetime.strptime(book_date, "%Y-%m-%d").date()
        days_left = (target - today).days
        print(f"[~] 目标日期 {book_date} 尚未开放（还需 {days_left - 7} 天），本轮跳过")
        sys.exit(2)  # exit 2 = 静默，cron 继续等待

    if args.snipe:
        # ── 高频抢占模式 ──
        print(f"[*] snipe 模式：最多重试 {args.snipe_times} 次，间隔 {args.snipe_interval}s\n")
        for attempt in range(1, args.snipe_times + 1):
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] 第 {attempt}/{args.snipe_times} 次尝试...", end=" ", flush=True)
            try:
                success, msg = try_once(session, book_date, start_time, end_time, args, current_ldap, duration)
            except Exception as e:
                success, msg = False, f"异常: {e}"
            print(msg.split('\n')[0])  # 只打第一行，保持日志简洁
            if success:
                print(msg)
                sys.exit(0)
            # cookie 失效时重新登录一次
            if "cookie" in msg.lower() or "未获取" in msg:
                print("[*] 尝试重新登录...")
                session, current_ldap = ensure_session(refresh=True)
            if attempt < args.snipe_times:
                time.sleep(args.snipe_interval)

        print(f"\n[✗] {args.snipe_times} 次均未抢到，本轮结束（cron 下次再试）")
        sys.exit(2)   # exit code 2 = 未抢到（非错误，cron 继续）

    else:
        # ── 普通单次模式 ──
        success, msg = try_once(session, book_date, start_time, end_time, args, current_ldap, duration)
        print(f"\n{'[✓]' if success else '[✗]'} {msg}")
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
