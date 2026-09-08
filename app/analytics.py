"""방문차량 부정사용(상시주차 우회) 의심 패턴 분석.

방문차량 등록은 '단순 방문' 전용이다. 상시·반복 주차는 관리사무소를 거쳐야 하므로,
같은 차량이 반복·연속으로 등록되거나 야간에 상주하는 흐름은 제도 취지에서 벗어난다.
여기서는 그 흐름을 네 축으로 계량해 세대별 점수를 매긴다.

    반복(rep)     같은 세대가 같은 차량을 며칠에 걸쳐 등록한 횟수
    연속(run)     그 차량이 며칠 연속으로 등록됐는지
    야간(night)   입차일과 출차일이 다른(밤을 넘긴) 체류 횟수
    체류(stay)    실제 입출차 기준 누적 체류시간

점수는 '살펴볼 순서'를 정하는 도구지 위반 판정이 아니다. 장기 간병·가족 돌봄처럼
정당한 사정이 얼마든지 있을 수 있으므로 화면에도 소명 절차를 안내한다.
"""
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from . import models

# 점수 가중치 — 반복/연속을 야간·체류보다 무겁게 본다(제도 취지에 정면으로 어긋나므로).
W_REPEAT = 3.0      # 동일차량 반복 1건당(면제 2건 초과분)
W_RUN = 4.0         # 연속일 1일당(면제 2일 초과분)
W_NIGHT = 2.0       # 야간 체류 1회당
W_STAY = 0.1        # 누적 체류 1시간당
W_MANY = 1.0        # 총 등록 과다(4건 초과분)
FREE_REPEAT = 2     # 이만큼은 정상 방문으로 봐준다
FREE_RUN = 2
FREE_MANY = 4

LEVEL_HIGH = 30.0
LEVEL_MID = 12.0
LEVELS = [(LEVEL_HIGH, "높음", "b-cancel"), (LEVEL_MID, "중간", "b-wait"), (0.0, "낮음", "b-ok")]

# 목록에 올리는 하한. 낮음(12점 미만)은 정상 방문과 잘 구분되지 않아 노출하지 않는다.
SCORE_MIN = LEVEL_MID


def scoring_rules():
    """화면·문서에 그대로 쓰는 점수 산출 설명. 상수를 바꾸면 설명도 같이 따라간다."""
    return {
        "items": [
            {"name": "반복", "weight": "%g점" % W_REPEAT,
             "unit": "동일 차량을 등록한 날 1일당",
             "free": "%d일까지 면제" % FREE_REPEAT,
             "why": "같은 차가 며칠씩 드나들면 방문이 아니라 상시 주차에 가깝다"},
            {"name": "연속", "weight": "%g점" % W_RUN,
             "unit": "연속 등록 1일당",
             "free": "%d일까지 면제" % FREE_RUN,
             "why": "날짜가 이어질수록 거주 차량일 가능성이 높다"},
            {"name": "야간", "weight": "%g점" % W_NIGHT,
             "unit": "밤을 넘긴 체류 1회당", "free": "면제 없음",
             "why": "방문객은 대개 당일에 나간다"},
            {"name": "체류", "weight": "%g점" % W_STAY,
             "unit": "누적 체류 1시간당", "free": "면제 없음",
             "why": "오래 머물수록 주차장 부담이 크다"},
            {"name": "건수", "weight": "%g점" % W_MANY,
             "unit": "총 등록 1건당",
             "free": "%d건까지 면제" % FREE_MANY,
             "why": "이용 빈도 자체가 높은 세대를 함께 본다"},
        ],
        "formula": ("(반복일−%d)×%g + (연속일−%d)×%g + 야간횟수×%g "
                    "+ 누적체류시간×%g + (등록건수−%d)×%g"
                    % (FREE_REPEAT, W_REPEAT, FREE_RUN, W_RUN, W_NIGHT, W_STAY,
                       FREE_MANY, W_MANY)),
        "levels": [
            {"label": "높음", "css": "b-cancel", "range": "%g점 이상" % LEVEL_HIGH},
            {"label": "중간", "css": "b-wait",
             "range": "%g점 이상 %g점 미만" % (LEVEL_MID, LEVEL_HIGH)},
        ],
        "min_score": SCORE_MIN,
        "hidden_note": "%g점 미만(위험도 낮음)은 정상 방문과 잘 구분되지 않아 목록에서 제외합니다."
                       % SCORE_MIN,
    }


def _kst(dt):
    """timestamptz(UTC) → KST naive. 실제 입출차·로그 이벤트용."""
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt
    return (dt.astimezone(timezone.utc) + timedelta(hours=9)).replace(tzinfo=None)


def _wall(dt):
    """신청 입/출차일 → 그대로.

    앱이 KST 벽시계 값을 그대로 저장하므로(2026-08-10T00:00:00+00:00 = 8월 10일)
    여기서 시간대를 한 번 더 옮기면 날짜가 하루 밀린다.
    """
    return dt.replace(tzinfo=None) if (dt and dt.tzinfo) else dt


def level_of(score):
    for cut, label, css in LEVELS:
        if score >= cut:
            return label, css
    return LEVELS[-1][1], LEVELS[-1][2]


def _max_run(days):
    """정렬된 날짜 집합에서 최장 연속일수."""
    days = sorted(set(days))
    if not days:
        return 0
    best = run = 1
    for i in range(1, len(days)):
        run = run + 1 if (days[i] - days[i - 1]).days == 1 else 1
        best = max(best, run)
    return best


def scan_cached():
    """기간을 안 좁힌 기본 집계만 묶는다 — 대시보드 배지와 의심세대 첫 화면이
    쓰는 바로 그 값이다. 기간을 좁혀 보는 조회와 내보내기는 scan 을 직접 부른다.
    자세한 취지는 app/cache.py 주석 참조.
    """
    from flask import current_app
    from . import cache
    return cache.cached("suspects", scan, logger=current_app.logger)


def scan(date_from=None, date_to=None):
    """기간 내 등록·로그를 훑어 세대별 의심 목록과 데이터 이상을 함께 돌려준다.

    반환: {"households": [...], "anomalies": [...], "stats": {...}}
    """
    regs = models.visits_filter_all(date_from, date_to, with_user=True)
    active = [r for r in regs if r.status != "cancelled"]
    logs = models.visit_logs_filter(date_from, date_to)

    # 차량번호로 로그를 묶는다(로그에는 동/호가 없어 등록을 통해서만 세대를 안다).
    logs_by_car = defaultdict(list)
    for lg in logs:
        logs_by_car[lg.car_number].append(lg)

    hh = defaultdict(lambda: {
        "dong": "", "ho": "", "users": set(), "regs": 0, "cars": set(), "days": set(),
        "night": 0, "stay": 0.0, "no_in": 0, "car_days": defaultdict(set),
        "reasons": set(), "first": None, "last": None,
    })
    anomalies = []

    for r in active:
        key = (r.dong, r.ho)
        a = hh[key]
        a["dong"], a["ho"] = r.dong, r.ho
        if r.user_name:
            a["users"].add(r.user_name)
        a["regs"] += 1
        a["cars"].add(r.car_number)
        day = _wall(r.entry_time)
        if day:
            a["days"].add(day.date())
            a["car_days"][r.car_number].add(day.date())
            if not a["first"] or day.date() < a["first"]:
                a["first"] = day.date()
            if not a["last"] or day.date() > a["last"]:
                a["last"] = day.date()
        reason = (r._row.get("visit_reason") or "").strip()
        if reason:
            a["reasons"].add(reason)

        ain, aout = _kst(r.actual_in_time), _kst(r.actual_out_time)
        if not ain:
            a["no_in"] += 1
        if ain and aout:
            hours = (aout - ain).total_seconds() / 3600.0
            if hours < 0:
                anomalies.append({
                    "kind": "출차가 입차보다 빠름",
                    "car_number": r.car_number, "household": f"{r.dong}동 {r.ho}호",
                    "user": r.user_name or "", "reg_id": r.id,
                    "detail": "입차 %s / 출차 %s (%.1f시간)" % (
                        ain.strftime("%m-%d %H:%M"), aout.strftime("%m-%d %H:%M"), hours),
                })
                continue
            a["stay"] += hours
            if ain.date() != aout.date():
                a["night"] += 1

    # 중복 이벤트(같은 차량·같은 종류가 1분 안에 두 번) — 관제 연동 이상 신호
    for car, items in logs_by_car.items():
        items = sorted(items, key=lambda x: (x.event_time or datetime.min.replace(tzinfo=timezone.utc)))
        for prev, cur in zip(items, items[1:]):
            if prev.event_type != cur.event_type or not prev.event_time or not cur.event_time:
                continue
            gap = abs((cur.event_time - prev.event_time).total_seconds())
            if gap <= 60:
                t = _kst(cur.event_time)
                anomalies.append({
                    "kind": "%s 이벤트 중복" % ("입차" if cur.is_in else "출차"),
                    "car_number": car, "household": "", "user": "", "reg_id": cur.registration_id,
                    "detail": "%s 에 %.0f초 간격으로 2회 기록" % (t.strftime("%m-%d %H:%M") if t else "?", gap),
                })

    # 동일 차량을 여러 세대가 등록 — 세대 간 돌려막기
    car_hh = defaultdict(set)
    for r in active:
        car_hh[r.car_number].add(f"{r.dong}동 {r.ho}호")
    shared_cars = {c: h for c, h in car_hh.items() if len(h) > 1}

    rows = []
    below = 0   # 하한 미만이라 숨긴 세대(있다는 사실은 알려 준다)
    for (dong, ho), a in hh.items():
        rep = max((len(d) for d in a["car_days"].values()), default=0)
        run = max((_max_run(d) for d in a["car_days"].values()), default=0)
        top_car, top_days = "", set()
        for c, d in a["car_days"].items():
            if len(d) > len(top_days):
                top_car, top_days = c, d
        score = (max(0, rep - FREE_REPEAT) * W_REPEAT
                 + max(0, run - FREE_RUN) * W_RUN
                 + a["night"] * W_NIGHT
                 + a["stay"] * W_STAY
                 + max(0, a["regs"] - FREE_MANY) * W_MANY)
        if score < SCORE_MIN:
            if score > 0:
                below += 1
            continue
        label, css = level_of(score)
        # 왜 걸렸는지 사람이 읽을 근거를 만들어 둔다.
        why = []
        if rep > FREE_REPEAT:
            why.append("동일차량 %s 를 %d일 등록" % (top_car, rep))
        if run > FREE_RUN:
            why.append("최장 %d일 연속" % run)
        if a["night"]:
            why.append("야간 체류 %d회" % a["night"])
        if a["stay"] >= 20:
            why.append("누적 체류 %.0f시간" % a["stay"])
        if a["regs"] > FREE_MANY:
            why.append("총 %d건 등록" % a["regs"])
        shared = sorted({c for c in a["cars"] if c in shared_cars})
        if shared:
            why.append("타 세대와 공유 차량 %s" % ", ".join(shared))
        rows.append({
            "dong": dong, "ho": ho, "household": "%s동 %s호" % (dong, ho),
            "users": ", ".join(sorted(a["users"])) or "(삭제된 계정)",
            "score": round(score, 1), "level": label, "level_css": css,
            "regs": a["regs"], "cars": len(a["cars"]), "days": len(a["days"]),
            "repeat": rep, "run": run, "night": a["night"],
            "stay_hours": round(a["stay"], 1), "no_in": a["no_in"],
            "top_car": top_car, "car_list": ", ".join(sorted(a["cars"])),
            "reasons": ", ".join(sorted(a["reasons"]))[:60],
            "period": ("%s ~ %s" % (a["first"], a["last"])) if a["first"] else "",
            "why": why,
        })
    rows.sort(key=lambda x: -x["score"])

    span = 0
    days_all = [_wall(r.entry_time).date() for r in regs if r.entry_time]
    if days_all:
        span = (max(days_all) - min(days_all)).days + 1
    stats = {
        "regs": len(regs), "active": len(active), "logs": len(logs),
        "households": len(hh), "span_days": span,
        "shared_cars": [{"car_number": c, "households": ", ".join(sorted(h))}
                        for c, h in sorted(shared_cars.items())],
        "flagged": len(rows),
        "high": sum(1 for r in rows if r["level"] == "높음"),
        "mid": sum(1 for r in rows if r["level"] == "중간"),
        "below_min": below,
    }
    return {"households": rows, "anomalies": anomalies, "stats": stats,
            "scoring": scoring_rules()}
