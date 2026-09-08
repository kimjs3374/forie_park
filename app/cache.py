"""관리 화면 집계용 짧은 캐시.

의심세대·실주차일수 집계는 관제 로그 수천~수만 건을 훑는다. 대시보드는 배지
숫자 하나를 얻으려고 그 집계를 통째로 돌리므로, 캐시가 없으면 관리 화면에
들어갈 때마다 몇 초를 기다린다.

그런데 단순 TTL 캐시는 만료되는 순간 걸린 사람이 그 몇 초를 그대로 뒤집어쓴다.
관리자는 하루에 몇 번 들어오지 않으므로 **거의 매번 그 사람이 당첨된다** —
캐시가 있으나 마나다. 그래서 오래된 값을 그대로 내주면서 뒤에서 새로 계산한다
(stale-while-revalidate). 집계 대상이 관제 로그라 분 단위로 뒤집힐 값이 아니고,
화면의 성격도 '지금 이 순간의 정확한 수'가 아니라 추세 감시다.

이 캐시는 화면 전용이다. 알림 배치와 내보내기는 원본 함수를 직접 부른다 —
보낼지 말지, 무엇을 내보낼지는 항상 그 순간의 값으로 판단해야 한다.
"""
import threading
import time

# 이 시간 안이면 그대로 쓴다.
FRESH_SECONDS = 180
# 이 시간까지는 옛 값을 내주면서 뒤에서 갱신한다. 넘으면 계산이 끝날 때까지 기다린다
# — 너무 오래된 수를 아무 말 없이 보여 주면 그건 캐시가 아니라 오답이다.
STALE_SECONDS = 1800

_lock = threading.Lock()
_entries = {}          # key -> {"at": 계산시각, "value": 값, "refreshing": bool}


def _refresh(key, compute, logger=None):
    try:
        value = compute()
    except Exception:
        if logger:
            logger.exception("캐시 갱신 실패: %s", key)
        with _lock:
            entry = _entries.get(key)
            if entry:
                entry["refreshing"] = False
        return
    with _lock:
        _entries[key] = {"at": time.monotonic(), "value": value, "refreshing": False}


def cached(key, compute, fresh=FRESH_SECONDS, stale=STALE_SECONDS, logger=None):
    """key 로 묶어 compute() 결과를 재사용한다.

    compute 는 인자 없는 호출가능 객체여야 한다(호출부에서 부분적용해 넘길 것).
    """
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        if entry:
            age = now - entry["at"]
            if age < fresh:
                return entry["value"]
            if age < stale:
                # 옛 값을 그대로 내주고, 갱신은 뒤에서 한 번만 돌린다.
                start = not entry["refreshing"]
                entry["refreshing"] = True
                value = entry["value"]
            else:
                start, value = False, None
        else:
            start, value = False, None

    if value is not None:
        if start:
            threading.Thread(target=_refresh, args=(key, compute, logger),
                             daemon=True).start()
        return value

    # 처음이거나 너무 오래됐다 — 계산이 끝날 때까지 기다린다.
    value = compute()
    with _lock:
        _entries[key] = {"at": time.monotonic(), "value": value, "refreshing": False}
    return value


def clear(key=None):
    """시험용. key 를 주면 그것만, 없으면 전부 버린다."""
    with _lock:
        if key is None:
            _entries.clear()
        else:
            _entries.pop(key, None)
