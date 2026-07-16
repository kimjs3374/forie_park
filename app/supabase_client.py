"""Supabase REST(PostgREST) 얇은 클라이언트.

forie_kids 와 동일 패턴: service_role 키로 서버측에서만 호출(RLS 우회).
SQLAlchemy 를 대체하여 users / visit_registrations 테이블을 REST 로 다룬다.
"""
import requests
from flask import current_app


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
    resp = requests.get(f"{_base()}/{table}", headers=_headers(), params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_one(table, params=None):
    p = dict(params or {}) if isinstance(params, dict) else list(params or [])
    if isinstance(p, dict):
        p["limit"] = "1"
    else:
        p.append(("limit", "1"))
    rows = fetch_rows(table, p)
    return rows[0] if rows else None


def insert_row(table, data):
    resp = requests.post(
        f"{_base()}/{table}",
        headers=_headers({"Prefer": "return=representation"}),
        json=data,
        timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


def patch_rows(table, data, params):
    resp = requests.patch(
        f"{_base()}/{table}",
        headers=_headers({"Prefer": "return=representation"}),
        params=params,
        json=data,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def delete_rows(table, params):
    resp = requests.delete(f"{_base()}/{table}", headers=_headers(), params=params, timeout=15)
    resp.raise_for_status()
    return True


def count_rows(table, params=None):
    p = dict(params or {})
    p.setdefault("select", "id")
    headers = _headers({"Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"})
    resp = requests.get(f"{_base()}/{table}", headers=headers, params=p, timeout=15)
    resp.raise_for_status()
    content_range = resp.headers.get("Content-Range", "")  # 예: "0-0/5" 또는 "*/5"
    if "/" in content_range:
        tail = content_range.rsplit("/", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return len(resp.json())


def rpc(fn, payload=None):
    """Postgres 함수 호출(POST /rest/v1/rpc/<fn>). 반환은 함수의 JSON 결과."""
    resp = requests.post(f"{_base()}/rpc/{fn}", headers=_headers(), json=payload or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def insert_rows(table, rows):
    """여러 행 일괄 insert. rows 는 dict 리스트."""
    if not rows:
        return []
    resp = requests.post(
        f"{_base()}/{table}",
        headers=_headers({"Prefer": "return=representation"}),
        json=rows,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()
