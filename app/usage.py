"""차량별 실주차일수(숙박일수) 집계와 월 한도.

관리규약(2026-10-01 시행)의 정의를 그대로 옮긴 것이다.

    실주차일수 = 방문차량이 **숙박**을 위해 주차한 일수.
                 오후 8시 ~ 다음날 오전 7시 사이에 30분을 초과해 주차하면 1일.

그러므로 세는 단위는 달력 날짜가 아니라 **밤(야간창)** 이다. d일자 밤은
`d 20:00 ~ (d+1) 07:00` 이고, 그 밤에 부과된 1일은 **시작일 d** 에 귀속시킨다
(9/30 밤 ~ 10/1 아침 = 9월분). 주간(07:00~20:00)에만 주차한 차량은 아무리
오래 있어도 부과되지 않는다 — 주간 주차는 관리사무소를 통해 따로 등록한다.

30분은 **그 밤의 누적**으로 본다. 20:10~20:35 로 나갔다가 06:00~06:20 다시
들어오면 합쳐 45분이라 부과된다. 짧게 나갔다 들어오는 우회를 막기 위해서다.

한도는 세대가 아니라 **차량번호** 기준이고 매월 1일에 리셋된다. 소진량은

    실제로 숙박한 밤(과거·오늘)  ∪  아직 오지 않은 활성 등록의 예약 밤(오늘 이후)

의 합집합이다. 예약한 밤이 나중에 실제 숙박이 되어도 중복 차감되지 않고,
등록만 해 두고 오지 않은 지난 밤은 저절로 풀린다.

집계 대상은 시스템 등록 차량만이 아니다. 세대호출·경비실 호출로 들어온 차량도
관제 로그에 남으면 같은 한도를 적용한다(규약 주의사항).
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from . import models

# 한 차량이 한 달에 쓸 수 있는 숙박일수
MONTHLY_LIMIT_DAYS = 10

# 야간창 — 이 시간대 밖의 주차는 부과 대상이 아니다.
NIGHT_START = time(20, 0)   # 당일 20:00
NIGHT_END = time(7, 0)      # 다음날 07:00

# 야간창 안 누적이 이 시간 이하면 회차차량(중고거래·배달 등)으로 보고 부과하지 않는다
TURNAROUND_MINUTES = 30

# 시행일. 이 날짜부터 등록 제한과 초과 알림이 실제로 걸린다.
# 그 전에는 집계만 해서 관리자 화면에 미리보기로 보여 주고 등록은 막지 않는다
# — 시행 사실을 모른 채 갑자기 등록이 거부되는 일이 없어야 하기 때문이다.
ENFORCE_FROM = date(2026, 10, 1)

# 체류를 밤마다 훑을 때의 안전장치. 짝 없는 입차 로그가 남아 있으면
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


def night_window(d):
    """d일자 밤의 (시작, 끝) = d 20:00 ~ (d+1) 07:00. KST naive."""
    return (datetime.combine(d, NIGHT_START),
            datetime.combine(d + timedelta(days=1), NIGHT_END))


def _overlap_minutes(a1, a2, b1, b2):
    lo, hi = max(a1, b1), min(a2, b2)
    return max(0, int((hi - lo).total_seconds() // 60))


def stays(logs, now=None):
    """입/출 로그를 짝지어 (시작, 끝) KST naive 체류구간 목록으로.

    아직 출차 로그가 없는 마지막 입차는 now 까지를 잠정 체류로 본다(주차중).
    출차 없이 입차가 연달아 찍힌 구간(관제 중복 이벤트)은 먼저 들어온 입차를
    살려 하나의 연속 체류로 본다 — 짝 없는 입차를 버리면 실제 숙박이 통째로
    사라져 한도가 헐거워지기 때문이다. 짝 없는 출차는 시작 시각을 알 수 없어 버린다.
    """
    now = now or datetime.now(timezone.utc)
    out, open_in = [], None
    for lg in sorted(logs, key=lambda x: (x.event_time
                                          or datetime.min.replace(tzinfo=timezone.utc))):
        if not lg.event_time:
            continue
        if lg.is_in:
            if open_in is None:
                open_in = lg.event_time
        elif open_in is not None:
            out.append((_kst(open_in), _kst(lg.event_time)))
            open_in = None
    if open_in is not None:
        out.append((_kst(open_in), _kst(now)))
    # 초 단위는 버린다(models._minutes_between 과 같은 규칙).
    return [(s.replace(second=0, microsecond=0), e.replace(second=0, microsecond=0))
            for s, e in out if s and e and e > s]


def minutes_by_night(logs, now=None):
    """입출차 로그 → {야간창 시작일: 그 밤에 주차한 분}.

    주간에만 있었던 차량은 어느 밤에도 겹치지 않아 빈 dict 가 나온다.
    """
    bucket = defaultdict(int)
    for s, e in stays(logs, now=now):
        # 체류가 시작한 날의 '전날 밤'(다음날 07시까지 이어짐)에도 걸릴 수 있다.
        d, guard = s.date() - timedelta(days=1), 0
        while d <= e.date() and guard < MAX_SPLIT_DAYS:
            ns, ne = night_window(d)
            if ns < e and ne > s:
                bucket[d] += _overlap_minutes(s, e, ns, ne)
            d += timedelta(days=1)
            guard += 1
    return {d: m for d, m in bucket.items() if m}


def parked_nights(logs, now=None, within=None):
    """실제로 숙박한 밤(회차 제외). within 을 주면 그 날짜집합으로 좁힌다."""
    return {d for d, m in minutes_by_night(logs, now=now).items()
            if m > TURNAROUND_MINUTES and (within is None or d in within)}


def nights_in_period(entry_dt, exit_dt):
    """등록 기간에 20:00 이 들어오는 밤들 = 그 등록이 소모할 수 있는 숙박일.

    폼이 '당일'(00:00~23:59)이면 그날 밤 1개, '+2일'이면 3개다.
    낮에만 등록한 기간(예: 10:00~18:00)은 빈 리스트 — 한도를 소모하지 않는다.
    """
    out, d, guard = [], entry_dt.date(), 0
    while d <= exit_dt.date() and guard < MAX_SPLIT_DAYS:
        if entry_dt <= datetime.combine(d, NIGHT_START) <= exit_dt:
            out.append(d)
        d += timedelta(days=1)
        guard += 1
    return out


def reserved_nights(car_number, first, last, today):
    """활성 등록 중 '아직 오지 않은' 예약 밤(오늘 이후, 해당 월 안).

    지난 밤은 실제로 묵었으면 로그로 잡히고, 안 왔으면 소진하지 않는다.
    """
    out = set()
    for r in models.visits_active_by_car(car_number):
        s, e = _wall(r.entry_time), _wall(r.exit_time)
        if not (s and e):
            continue
        for d in nights_in_period(s, e):
            if d >= today and first <= d <= last:
                out.add(d)
    return out


def car_quota(car_number, first, last, today=None, now=None):
    """한 차량의 해당 월 소진 현황.

    반환: {"limit", "used": 실제 숙박한 밤, "reserved": 예약 밤,
           "charged": 합집합(가변), "used_days": 최초 소진일수, "remaining"}
    """
    today = today or today_kst()
    # 말일 밤은 다음달 1일 아침까지 이어지고, 1일 새벽은 전달 말일 밤에 속한다.
    # 양쪽으로 하루씩 넓게 읽어야 경계의 밤을 놓치지 않는다.
    logs = models.visit_logs_by_car(
        car_number,
        datetime.combine(first - timedelta(days=1), time.min),
        datetime.combine(last + timedelta(days=1), time.min))
    month_nights = set(daterange(first, last))
    used = parked_nights(logs, now=now, within=month_nights)
    reserved = reserved_nights(car_number, first, last, today)
    charged = used | reserved
    return {"limit": MONTHLY_LIMIT_DAYS, "used": used, "reserved": reserved,
            "charged": set(charged), "used_days": len(charged),
            "remaining": max(0, MONTHLY_LIMIT_DAYS - len(charged))}


def plan_registration(car_number, entry_dt, exit_dt, today=None, now=None):
    """신청 기간이 소모할 밤을 월별 잔여일수에 맞춰 자른다.

    반환: {"nights": 신청이 포함한 밤 전부, "allowed": 허용된 밤, "quotas": 월별 현황}
      - nights 가 비었으면 주간 등록이라 한도와 무관하다.
      - nights 는 있는데 allowed 가 비었으면 잔여 0 → 등록 불가.
      - allowed 가 nights 보다 짧으면 그만큼 출차일을 줄여야 한다.

    잔여가 모자라면 되는 데까지만 허용한다(8일 쓴 뒤 3일 신청 → 2일).
    한 건이 달을 넘길 수 있으므로 달마다 따로 센다.

    이미 소진에 잡힌 밤은 두 번 깎지 않는다. 다만 그것이 '한도를 다 쓴 차가
    자기가 묵었던 밤으로 신청을 시작하면 통과된다'는 구멍이 되면 안 되므로,
    잔여가 0이면 자기 기존 예약 밤이 아닌 한 거기서 끊는다.
    """
    today = today or today_kst()
    nights = nights_in_period(entry_dt, exit_dt)
    quotas, allowed = {}, []
    for d in nights:
        if d < ENFORCE_FROM:
            allowed.append(d)       # 시행 전 밤은 한도에 걸지 않는다
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
        allowed.append(d)
    return {"nights": nights, "allowed": allowed, "quotas": quotas}


# ------------------------------------------------------------------ 한도 초과 감시

def _stay_text(minutes):
    h, m = divmod(int(minutes), 60)
    return (f"{h}시간 " if h else "") + f"{m}분"


def _is_open(logs):
    """마지막 이벤트가 입차 = 아직 출차 로그가 없음(주차중 또는 관제 누락)."""
    evs = sorted((lg for lg in logs if lg.event_time), key=lambda x: x.event_time)
    return bool(evs) and evs[-1].is_in


def _open_now(car_number, since, today):
    """지금 이 순간 출차 로그가 없는가 — '미출차' 판정.

    달 조회창의 마지막 이벤트로 판정하면 창 끝에 걸친 입차가 전부 미출차로 보인다
    (8/31 밤 입차, 9/1 출차 → 8월 창에서는 입차가 마지막). 오늘까지 다시 읽어 가른다.
    """
    logs = models.visit_logs_by_car(
        car_number,
        datetime.combine(since, time.min),
        datetime.combine(today + timedelta(days=1), time.min))
    return _is_open(logs)


def scan_overuse(month=None, now=None):
    """이번 달(또는 지정한 달) 숙박일수가 한도를 넘은 차량.

    시스템 등록 없이 세대호출·경비실 호출로 들어온 차량도 로그가 남으면 함께 센다
    (규약 주의사항). 등록이 없으면 세대를 알 수 없어 '미등록'으로 표시한다.

    정기등록 차량(관제 DB 동기화본)은 상시 주차가 정상이므로 알림 대상에서 뺀다.
    다만 화면에서는 왜 빠졌는지 보이도록 따로 담아 돌려준다.
    """
    today = today_kst()
    base = parse_month(month) or today
    first, last = month_bounds(base)

    # 경계의 밤을 놓치지 않도록 양쪽으로 하루씩 넓게 읽는다.
    logs = models.visit_logs_filter(
        datetime.combine(first - timedelta(days=1), time.min),
        datetime.combine(last + timedelta(days=1), time.min))
    by_car = defaultdict(list)
    for lg in logs:
        if lg.car_number:
            by_car[lg.car_number].append(lg)

    month_nights = set(daterange(first, last))
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
        mins = minutes_by_night(items, now=now)
        nights = sorted(d for d, m in mins.items()
                        if m > TURNAROUND_MINUTES and d in month_nights)
        if len(nights) <= MONTHLY_LIMIT_DAYS:
            continue
        total = sum(m for d, m in mins.items() if d in month_nights)
        last_event = max((lg.event_time_kst for lg in items if lg.event_time_kst),
                         default=None)
        households = sorted(reg_by_car.get(car, ()))
        row = {
            "car_number": car,
            "days": len(nights),
            "over": len(nights) - MONTHLY_LIMIT_DAYS,
            "minutes": total,
            "stay_text": _stay_text(total),
            "first_day": nights[0].isoformat(),
            "last_day": nights[-1].isoformat(),
            "last_event": last_event,
            "households": ", ".join(households) or "미등록(호출 입차)",
            "registered": bool(households),
            # 출차 로그 없음 → 관제 누락 확인 필요. 한도를 넘긴 차량만 다시 조회한다
            # (전체 차량에 대해 하면 조회가 차량 수만큼 늘어난다).
            "open_in": _open_now(car, first - timedelta(days=1), today),
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
        "night_start": NIGHT_START, "night_end": NIGHT_END,
        "enforced": is_enforced(), "enforce_from": ENFORCE_FROM,
        "regular_count": len(regular),
        "regular_synced_at": models.regular_cars_synced_at(),
        "stats": {"cars": len(by_car), "flagged": len(rows),
                  "excluded": len(excluded), "logs": len(logs),
                  "unregistered": sum(1 for r in rows if not r["registered"]),
                  "open_in": sum(1 for r in rows if r["open_in"])},
    }
