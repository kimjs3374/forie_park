"""Supabase REST(PostgREST) 얇은 클라이언트.

forie_kids 와 동일 패턴: service_role 키로 서버측에서만 호출(RLS 우회).
SQLAlchemy 를 대체하여 users / visit_registrations 테이블을 REST 로 다룬다.
"""
from concurrent.futures import ThreadPoolExecutor

import requests
from flask import current_app
from requests.adapters import HTTPAdapter


# 호출마다 requests.get 을 쓰면 그때마다 TLS 를 새로 맺는다. 로그 전량 조회처럼
# 1,000행씩 끊어 50번 왕복하는 곳에서는 핸드셰이크만으로 수 초가 나간다
# (실측: 로그 49,631건 조회 8.4초 → 연결 재사용 후 1초대).
# Session 하나를 프로세스 전체가 공유한다. 내부 커넥션 풀은 스레드 안전하므로
# gthread 워커에서도 그대로 쓸 수 있고, 풀 크기를 워커 스레드 수 이상으로 잡아
# 스레드끼리 연결을 뺏느라 기다리지 않게 한다.
_session = requests.Session()
_session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=16))
_session.mount("http://", HTTPAdapter(pool_connections=4, pool_maxsize=16))


def _base():
    return current_app.config["SUPABASE_URL"].rstrip("/") + "/rest/v1"


def _headers(extra=None):
    key = current_app.config["SUPABASE_SERVICE_ROLE_KEY"]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def fetch_rows(table, params=None):
    """params 는 dict 또는 (key, value) 튜플 리스트(동일 컬럼 다중 조건용)."""
    resp = _session.get(f"{_base()}/{table}", headers=_headers(), params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_all_rows(table, params=None, page=1000, workers=6):
    """PostgREST 기본 상한(1000행)을 넘겨 전량을 받아온다.

    fetch_rows 는 한 번만 요청하므로 행이 상한을 넘으면 조용히 잘린다.
    내보내기·통계처럼 '전부' 가 필요한 곳은 이 함수를 쓴다.
    params 에 limit/offset 이 이미 있으면 그 뜻을 존중해 한 번만 조회한다.

    Supabase 는 요청당 1,000행에서 끊으므로(limit 을 더 크게 줘도 1,000행) 로그
    5만 건이면 50번을 왕복해야 한다. 한 장씩 차례로 읽으면 왕복 지연만 쌓여
    6초가 걸린다 — 데이터가 아니라 기다림이 대부분이다. 그래서 총 행수를 먼저
    세고 나머지 장을 동시에 읽는다(실측: 로그 49,685건 6.1초 → 1초대).

    ⚠️ 작업 스레드에는 Flask 앱 컨텍스트가 없다. 주소와 헤더를 부르는 쪽에서
    미리 굳혀 넘겨야 하며, 그래서 여기서는 fetch_rows 대신 세션을 직접 쓴다.
    """
    base = list(params.items()) if isinstance(params, dict) else list(params or [])
    if any(k in ("limit", "offset") for k, _ in base):
        return fetch_rows(table, base)

    url = f"{_base()}/{table}"
    headers = _headers()

    def _page(offset):
        resp = _session.get(url, headers=headers,
                            params=base + [("limit", str(page)), ("offset", str(offset))],
                            timeout=30)
        resp.raise_for_status()
        return resp.json()

    first = _page(0)
    if len(first) < page:
        return first

    try:
        total = count_rows(table, base)
    except Exception:
        total = 0

    if total <= page:
        # 총 행수를 못 세면 예전처럼 한 장씩 이어 읽는다(결과는 같고 느릴 뿐이다).
        out, offset = list(first), page
        while True:
            rows = _page(offset)
            out.extend(rows)
            if len(rows) < page:
                return out
            offset += page

    out = list(first)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # map 은 넘긴 순서대로 결과를 돌려주므로 order 파라미터의 정렬이 유지된다.
        for rows in pool.map(_page, range(page, total, page)):
            out.extend(rows)
    return out


def fetch_one(table, params=None):
    p = dict(params or {}) if isinstance(params, dict) else list(params or [])
    if isinstance(p, dict):
        p["limit"] = "1"
    else:
        p.append(("limit", "1"))
    rows = fetch_rows(table, p)
    return rows[0] if rows else None


def insert_row(table, data):
    resp = _session.post(
        f"{_base()}/{table}",
        headers=_headers({"Prefer": "return=representation"}),
        json=data,
        timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def patch_rows(table, data, params):
    resp = _session.patch(
        f"{_base()}/{table}",
        headers=_headers({"Prefer": "return=representation"}),
        params=params,
        json=data,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def delete_rows(table, params):
    resp = _session.delete(f"{_base()}/{table}", headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    return True


def count_rows(table, params=None):
    """행 수만 센다. params 는 dict 또는 (key, value) 튜플 리스트.

    튜플 리스트를 받는 이유는 entry_time 처럼 한 컬럼에 하한·상한을 함께
    걸어야 하는 경우가 있어서다(dict 면 뒤 값이 앞 값을 덮어쓴다).
    """
    if isinstance(params, (list, tuple)):
        p = [kv for kv in params if kv[0] not in ("select", "order", "limit", "offset")]
        p.append(("select", "id"))
    else:
        p = dict(params or {})
        p.setdefault("select", "id")
    headers = _headers({"Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"})
    resp = _session.get(f"{_base()}/{table}", headers=headers, params=p, timeout=15)
    resp.raise_for_status()
    content_range = resp.headers.get("Content-Range", "")  # 예: "0-0/5" 또는 "*/5"
    if "/" in content_range:
        tail = content_range.rsplit("/", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return len(resp.json())


def rpc(fn, payload=None):
    """Postgres 함수 호출(POST /rest/v1/rpc/<fn>). 반환은 함수의 JSON 결과."""
    resp = _session.post(f"{_base()}/rpc/{fn}", headers=_headers(), json=payload or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def insert_rows(table, rows):
    """여러 행 일괄 insert. rows 는 dict 리스트."""
    if not rows:
        return []
    resp = _session.post(
        f"{_base()}/{table}",
        headers=_headers({"Prefer": "return=representation"}),
        json=rows,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()
