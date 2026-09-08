"""경비실·관리사무소용 방문차량 조회.

경비원에게는 forie 계정이 없다. 교대 근무자마다 계정을 발급·회수하는 부담이
크고 야간 교대에서 로그인이 걸리면 업무가 멈춘다. 그래서 **공용 PIN** 으로
연다. 대신 신원이 계정으로 남지 않으므로, 조회 시각을 빠짐없이 기록해
근무자 배치표와 대조해 조회자를 특정한다(models.lookup_log_add).

경비실에는 PIN 이 박힌 QR(관리자 > 경비실 QR 발급)을 붙여 둔다. PIN 은
주소의 **프래그먼트**(`/lookup/#p=...`)에 실린다 — 쿼리스트링에 실으면 nginx 와
Cloudflare 액세스 로그에 요청줄 그대로 적혀 로그를 볼 수 있는 사람 모두에게
PIN 이 새기 때문이다. 프래그먼트는 브라우저가 서버로 보내지 않으므로 어떤
로그에도, Referer 에도 남지 않는다. 화면의 스크립트가 그 값을 읽어 PIN 폼을
대신 제출하고 주소에서 즉시 지운다. 스크립트가 막힌 단말에서는 인쇄물에 적힌
PIN 을 직접 입력하면 된다.

노출 정보는 **세대 · 방문기간 · 상태**까지다. 방문자 연락처·방문사유는 보여
주지 않는다 — 입차 허용 판단에 필요 없는데 경비실 화면에 상시 떠 있게 된다.
"""
import hmac
import time
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, current_app, redirect, render_template, request,
                   session, url_for)

from . import models

lookup_bp = Blueprint("lookup", __name__, url_prefix="/lookup")

# 세션에는 "언제까지 유효한가"만 남긴다. PIN 자체는 절대 세션에 넣지 않는다.
SESSION_KEY = "lookup_until"

# PIN 무차별 대입 방어. 프로세스 메모리라 워커 수만큼 느슨해지지만,
# 자동화 도구가 붙었을 때의 속도는 확실히 꺾인다.
FAIL_LIMIT = 10
FAIL_WINDOW = 600          # 10분
_fails = {}                # ip -> [실패 시각(epoch), ...]


def client_ip():
    """Cloudflare → nginx 를 거쳐 오므로 원본 IP 는 헤더에 있다."""
    cf = request.headers.get("CF-Connecting-IP")
    if cf:
        return cf.strip()
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or ""


def _pin():
    return (current_app.config.get("LOOKUP_PIN") or "").strip()


def _authed():
    until = session.get(SESSION_KEY)
    return bool(until) and float(until) > time.time()


def _grant():
    hours = current_app.config.get("LOOKUP_SESSION_HOURS", 12)
    session.permanent = True
    session[SESSION_KEY] = time.time() + hours * 3600


def _throttled(ip):
    now = time.time()
    hits = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
    _fails[ip] = hits
    return len(hits) >= FAIL_LIMIT


def _record_fail(ip):
    _fails.setdefault(ip, []).append(time.time())


@lookup_bp.route("/", methods=["GET", "POST"])
def index():
    ip = client_ip()

    if not _pin():
        return render_template("lookup/pin.html",
                               error="조회용 PIN 이 설정되지 않았습니다. 관리사무소에 문의하세요.",
                               locked=True), 503

    # 옛 QR(?pin=...)이 남아 있으면 값은 쓰지 않고 주소만 정리해 돌려보낸다.
    # 로그에 이미 적힌 뒤라 그 값으로 인증해 주는 것은 유출을 눈감아 주는 셈이다.
    if request.args.get("pin") is not None:
        return redirect(url_for("lookup.index"))

    if request.method == "POST" and not _authed():
        if _throttled(ip):
            models.lookup_log_add("pin_fail", ip=ip)
            return render_template("lookup/pin.html",
                                   error="입력 시도가 많습니다. 10분 뒤 다시 시도하세요."), 429
        given = (request.form.get("pin") or "").strip()
        if hmac.compare_digest(given, _pin()):
            _grant()
            models.lookup_log_add("pin_ok", ip=ip)
            return redirect(url_for("lookup.index"))
        _record_fail(ip)
        models.lookup_log_add("pin_fail", ip=ip)
        return render_template("lookup/pin.html", error="PIN 이 올바르지 않습니다."), 401

    if not _authed():
        return render_template("lookup/pin.html")

    raw = request.values.get("car", "")
    query = models.normalize_car_query(raw)
    results = regulars = None
    warn = None

    if raw.strip():
        if len(query) < models.LOOKUP_MIN_LEN:
            warn = f"차량번호를 {models.LOOKUP_MIN_LEN}자리 이상 입력하세요."
        else:
            results = models.visits_lookup_by_car(query)
            regulars = models.regular_cars_search(query)
            models.lookup_log_add("search", query=query,
                                  result_count=len(results) + len(regulars), ip=ip)

    now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).replace(tzinfo=None)
    return render_template("lookup/search.html", car=raw.strip(), query=query,
                           results=results, regulars=regulars, warn=warn,
                           now_kst=now_kst)


@lookup_bp.route("/exit", methods=["POST"])
def exit_session():
    """공용 단말에서 자리를 뜰 때 조회 권한을 즉시 내린다."""
    session.pop(SESSION_KEY, None)
    return redirect(url_for("lookup.index"))
