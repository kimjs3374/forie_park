"""nexpa(주차관제) 연동 어댑터 — 웹훅 핑 방식.

관리실 관제 PC의 동기화 에이전트에게 "새 변경 있으니 확인" 신호(핑)만 보낸다.
데이터는 에이전트가 Supabase 에서 직접 읽어간다(우리는 Supabase 를 쓰기만, 관제 DB 는
직접 건드리지 않는다). 핑이 실패해도 무시 — 에이전트의 안전망 폴링(수 분)이 회수하므로
등록은 유실되지 않는다(멱등).

설정(.env):
  NEXPA_AGENT_WEBHOOK = http://<관제PC tailnet IP>:42150/sync
  NEXPA_AGENT_TOKEN   = 공유 토큰
"""
import os
import logging

import requests

log = logging.getLogger(__name__)


def _ping(reason):
    """에이전트로 동기화 핑(fire-and-forget). 실패는 폴링이 커버하므로 삼킨다."""
    url = os.environ.get("NEXPA_AGENT_WEBHOOK", "")
    if not url:
        return False
    token = os.environ.get("NEXPA_AGENT_TOKEN", "")
    try:
        requests.post(url, headers=({"X-Token": token} if token else {}), timeout=3)
        return True
    except Exception as e:
        log.warning("nexpa 에이전트 핑 실패(%s) — 폴링이 회수: %s", reason, e)
        return False


def send_to_nexpa(registration):
    """방문차량 등록 → 에이전트에 즉시 반영 핑."""
    return _ping("register")


def cancel_on_nexpa(registration):
    """방문차량 취소 → 에이전트에 즉시 반영 핑."""
    return _ping("cancel")
