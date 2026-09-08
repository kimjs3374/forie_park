"""차량별 실주차일수 집계와 월 한도.

'실주차일수'는 신청기간이 아니라 관제 입출차 로그(parking_visit_logs)에서 센다.
입/출차를 짝지어 KST 날짜별로 체류시간을 쪼개 합산하고, 하루 합계가
TURNAROUND_MINUTES 이하인 날은 회차(들어왔다 곧 나간 차)로 보아 세지 않는다.
자정을 넘긴 체류는 날짜별로 잘라 각각 판정한다 — 8/1 22:00~8/2 09:00 은
8/1(120분)·8/2(540분) 둘 다 초과하므로 2일이다.

한도는 세대가 아니라 **차량번호** 기준이고 매월 1일에 리셋된다. 소진량은

    실제 주차한 날(과거·오늘)  ∪  아직 오지 않은 활성 등록의 예약일(오늘 이후)

의 날짜 합집합이다. 예약일이 나중에 실주차일이 되어도 중복 차감되지 않고,
등록만 해 두고 오지 않은 지난 날은 저절로 풀린다.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from . import models

# 한 차량이 한 달에 쓸 수 있는 실주차일수
MONTHLY_LIMIT_DAYS = 10

# 시행일. 이 날짜부터 등록 제한과 초과 알림이 실제로 걸린다.
# 그 전에는 집계만 해서 관리자 화면에 미리보기로 보여 주고 등록은 막지 않는다
# — 시행 사실을 모른 채 갑자기 등록이 거부되는 일이 없어야 하기 때문이다.
ENFORCE_FROM = date(2026, 10, 1)

# 하루 합계가 이 시간 이하면 회차차량으로 보고 주차일로 세지 않는다
TURNAROUND_MINUTES = 30

# 체류를 날짜별로 쪼갤 때의 안전장치. 짝 없는 입차 로그가 남아 있으면
# 루프가 끝없이 돌 수 있어 이 일수에서 끊는다.
MAX_SPLIT_DAYS = 400


def _kst(dt):
    """timestamptz(UTC) → KST naive."""
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt
    return (dt.astimezone(timezone.utc) + timedelta(hours=9)).replace(tzinfo=None)


def _wall(dt):
    """신청 입/출차일 → 그대로. 앱이 KST 벽시계 값을 그대로 저장한다."""
    return dt.replace(tzinfo=None) if (dt and dt.tzinfo) else dt


def today_kst():
    return _kst(datetime.now(timezone.utc)).date()


def is_enforced(day=None):
    """오늘 기준으로 한도가 실제로 걸리는가."""
    return (day or today_kst()) >= ENFORCE_FROM


def month_bounds(d):
    """그 날짜가 속한 달의 (1일, 말일)."""
    first = d.replace(day=1)
    return first, (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def parse_month(value):
    """'YYYY-MM' → 그 달의 1일. 형식이 아니면 None."""
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m").date()
    except (ValueError, TypeError, AttributeError):
        return None


def daterange(start, end):
    """start~end(양끝 포함) 날짜를 하루씩."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _accumulate(bucket, start_utc, end_utc):
    """한 번의 체류(입차~출차)를 KST 날짜별 분으로 쪼개 담는다."""
    s, e = _kst(start_utc), _kst(end_utc)
    if not s or not e:
        return
    # 초 단위는 버린다(models._minutes_between 과 같은 규칙).
    s = s.replace(second=0, microsecond=0)
    e = e.replace(second=0, microsecond=0)
    if e <= s:
        return
    cur, guard = s, 0
    while cur.date() < e.date() and guard < MAX_SPLIT_DAYS:
        nxt = datetime.combine(cur.date() + timedelta(days=1), time.min)
        bucket[cur.date()] += int((nxt - cur).total_seconds() // 60)
        cur, guard = nxt, guard + 1
    bucket[cur.date()] += int((e - cur).total_seconds() // 60)


def minutes_by_date(logs, now=None):
    """입출차 로그 → {KST 날짜: 주차한 분}.

    아직 출차 로그가 없는 마지막 입차는 now 까지를 잠정 체류로 본다.
    출차 없이 입차가 연달아 찍힌 구간(관제 중복 이벤트)은 먼저 들어온 입차를
    살려 하나의 연속 체류로 본다 — 짝 없는 입차를 버리면 실제 체류가 통째로
    사라져 한도가 헐거워지기 때문이다.
    """
    now = now or datetime.now(timezone.utc)
    bucket = defaultdict(int)
    open_in = None
    for lg in sorted(logs, key=lambda x: (x.event_time or datetime.min.replace(tzinfo=timezone.utc))):
        if not lg.event_time:
            continue
        if lg.is_in:
            if open_in is None:
                open_in = lg.event_time
        else:
            if open_in is not None:
                _accumulate(bucket, open_in, lg.event_time)
                open_in = None
            # 짝 없는 출차는 시작 시각을 알 수 없어 버린다.
    if open_in is not None:
        _accumulate(bucket, open_in, now)
    return dict(bucket)


def parked_days(logs, now=None, within=None):
    """실제로 주차한 날(회차 제외). within 을 주면 그 날짜집합으로 좁힌다."""
    return {d for d, m in minutes_by_date(logs, now=now).items()
            if m > TURNAROUND_MINUTES and (within is None or d in within)}


def reserved_dates(car_number, first, last, today):
    """활성 등록 중 '아직 오지 않은' 예약일(오늘 이후, 해당 월 안).

    지난 예약일은 실제로 왔으면 로그로 잡히고, 안 왔으면 소진하지 않는다.
    """
    out = set()
    for r in models.visits_active_by_car(car_number):
        s, e = _wall(r.entry_time), _wall(r.exit_time)
        if not (s and e):
            continue
        for d in daterange(s.date(), e.date()):
            if d >= today and first <= d <= last:
                out.add(d)
    return out


def car_quota(car_number, first, last, today=None, now=None):
    """한 차량의 해당 월 소진 현황.

    반환: {"limit", "used": 실주차일 집합, "reserved": 예약일 집합,
           "charged": 합집합(가변), "used_days": 최초 소진일수, "remaining"}
    """
    today = today or today_kst()
    # 전날 밤에 들어와 이 달 첫날 아침에 나간 체류를 놓치지 않도록 하루 앞에서 읽는다.
    logs = models.visit_logs_by_car(
        car_number,
        datetime.combine(first - timedelta(days=1), time.min),
        datetime.combine(last, time.min))
    month_days = set(daterange(first, last))
    used = parked_days(logs, now=now, within=month_days)
    reserved = reserved_dates(car_number, first, last, today)
    charged = used | reserved
    return {"limit": MONTHLY_LIMIT_DAYS, "used": used, "reserved": reserved,
            "charged": set(charged), "used_days": len(charged),
            "remaining": max(0, MONTHLY_LIMIT_DAYS - len(charged))}


def plan_registration(car_number, entry_date, exit_date, today=None, now=None):
    """신청 기간을 월별 잔여일수에 맞춰 자른다 → (허용 마지막 날짜|None, 월별 현황).

    잔여가 모자라면 되는 데까지만 허용한다(8일 쓴 뒤 3일 신청 → 2일).
    한 건이 달을 넘길 수 있으므로 달마다 따로 센다.

    이미 소진에 잡힌 날은 두 번 깎지 않는다. 다만 그것이 '한도를 다 쓴 차가
    자기가 주차했던 날로 신청을 시작하면 통과된다'는 구멍이 되면 안 되므로,
    잔여가 0이면 자기 기존 예약일이 아닌 한 거기서 끊는다.
    """
    today = today or today_kst()
    quotas, accepted = {}, []
    for d in daterange(entry_date, exit_date):
        if d < ENFORCE_FROM:
            accepted.append(d)      # 시행 전 날짜는 한도에 걸지 않는다
            continue
        key = (d.year, d.month)
        q = quotas.get(key)
        if q is None:
            first, last = month_bounds(d)
            q = quotas[key] = car_quota(car_number, first, last, today=today, now=now)
        if q["remaining"] <= 0 and d not in q["reserved"]:
            break
        if d not in q["charged"]:
            q["remaining"] -= 1
            q["charged"].add(d)
        accepted.append(d)
    return (accepted[-1] if accepted else None), quotas


# ------------------------------------------------------------------ 한도 초과 감시

def _stay_text(minutes):
    h, m = divmod(int(minutes), 60)
    return (f"{h}시간 " if h else "") + f"{m}분"


def scan_overuse(month=None, now=None):
    """이번 달(또는 지정한 달) 실주차일수가 한도를 넘은 차량.

    정기등록 차량(관제 DB 동기화본)은 상시 주차가 정상이므로 알림 대상에서 뺀다.
    다만 화면에서는 왜 빠졌는지 보이도록 따로 담아 돌려준다.
    """
    today = today_kst()
    base = parse_month(month) or today
    first, last = month_bounds(base)

    # 전달 밤을 넘긴 체류까지 담기게 하루 앞에서 읽는다.
    logs = models.visit_logs_filter(
        datetime.combine(first - timedelta(days=1), time.min),
        datetime.combine(last, time.min))
    by_car = defaultdict(list)
    for lg in logs:
        if lg.car_number:
            by_car[lg.car_number].append(lg)

    month_days = set(daterange(first, last))
    regular = models.regular_car_numbers(today)

    # 세대·신청자를 붙이기 위한 등록건. 지난달에 시작해 이 달까지 이어진 등록도
    # 잡히도록 조회 시작을 한 달 앞으로 당긴다.
    reg_by_car = defaultdict(set)
    try:
        for r in models.visits_filter_all(
                datetime.combine(first - timedelta(days=31), time.min),
                datetime.combine(last, time.min), with_user=True):
            if r.status != "cancelled" and r.car_number:
                reg_by_car[r.car_number].add(f"{r.dong}동 {r.ho}호")
    except Exception:
        pass

    rows, excluded = [], []
    for car, items in by_car.items():
        mins = minutes_by_date(items, now=now)
        days = sorted(d for d, m in mins.items()
                      if m > TURNAROUND_MINUTES and d in month_days)
        if len(days) <= MONTHLY_LIMIT_DAYS:
            continue
        total = sum(m for d, m in mins.items() if d in month_days)
        last_event = max((lg.event_time_kst for lg in items if lg.event_time_kst),
                         default=None)
        row = {
            "car_number": car,
            "days": len(days),
            "over": len(days) - MONTHLY_LIMIT_DAYS,
            "minutes": total,
            "stay_text": _stay_text(total),
            "first_day": days[0].isoformat(),
            "last_day": days[-1].isoformat(),
            "last_event": last_event,
            "households": ", ".join(sorted(reg_by_car.get(car, ()))) or "-",
            "is_regular": car in regular,
        }
        (excluded if row["is_regular"] else rows).append(row)

    rows.sort(key=lambda x: -x["days"])
    excluded.sort(key=lambda x: -x["days"])
    return {
        "period": f"{first.year}-{first.month:02d}",
        "first": first, "last": last,
        "rows": rows, "excluded": excluded,
        "limit": MONTHLY_LIMIT_DAYS, "turnaround": TURNAROUND_MINUTES,
        "enforced": is_enforced(), "enforce_from": ENFORCE_FROM,
        "regular_count": len(regular),
        "regular_synced_at": models.regular_cars_synced_at(),
        "stats": {"cars": len(by_car), "flagged": len(rows),
                  "excluded": len(excluded), "logs": len(logs)},
    }
