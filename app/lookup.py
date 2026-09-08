"""경비실·관리사무소용 방문차량 조회.

경비원에게는 forie 계정이 없다. 교대 근무자마다 계정을 발급·회수하는 부담이
크고 야간 교대에서 로그인이 걸리면 업무가 멈춘다. 그래서 **공용 PIN** 으로
연다. 대신 신원이 계정으로 남지 않으므로, 조회 시각을 빠짐없이 기록해
근무자 배치표와 대조해 조회자를 특정한다(models.lookup_log_add).

경비실에는 QR(관리자 > 경비실 QR 발급)을 붙여 둔다. QR 이 나르는 것은 PIN 이
아니라 별개의 무작위 토큰(LOOKUP_QR_SECRET)이다 — PIN 을 주소에 실으면 형태를
어떻게 바꾸든 주소창을 보는 사람에게 그대로 읽힌다. 토큰은 주소의 **프래그먼트**
(`/lookup/#k=...`)에 실린다. 프래그먼트는 브라우저가 서버로 보내지 않으므로
nginx·Cloudflare 액세스 로그에도, Referer 에도 남지 않는다. 화면의 스크립트가
그 값을 읽어 대신 제출하고 주소에서 즉시 지운다. 스크립트가 막힌 단말에서는
인쇄물에 적힌 PIN 을 직접 입력하면 되므로 QR 이 안 통해도 업무는 막히지 않는다.

노출 정보는 **세대 · 방문기간 · 상태**까지다. 방문자 연락처·방문사유는 보여
주지 않는다 — 입차 허용 판단에 필요 없는데 경비실 화면에 상시 떠 있게 된다.
"""
import hmac
import json
import os
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


# 바뀐 PIN 은 instance/ 아래 파일에 둔다. .env 는 서비스 재시작이 있어야 반영되고
# 웹 프로세스가 고쳐 쓸 수도 없어서, 관리자가 화면에서 바로 바꾸려면 다른 자리가
# 필요하다. 파일이 있으면 그 값이, 없으면 .env 의 초기값이 쓰인다.
PIN_STORE = "lookup_pin.json"


def _store_path():
    return os.path.join(current_app.instance_path, PIN_STORE)


def read_pin_store():
    """{"pin", "updated_at", "updated_by"} 또는 None(아직 안 바꿈)."""
    try:
        with open(_store_path(), encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("pin") else None


def write_pin_store(pin, who):
    """임시 파일에 쓰고 통째로 갈아 끼운다.

    제자리에서 고쳐 쓰면 쓰는 도중에 읽힌 요청이 반쪽 파일을 만나 PIN 이
    통째로 사라진 것처럼 보이고, 그 순간 경비실 화면이 열리지 않는다.
    """
    path = _store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    payload = {"pin": str(pin), "updated_at": datetime.now(timezone.utc).isoformat(),
               "updated_by": who or ""}
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return payload


def _pin():
    """지금 통하는 PIN. 관리자가 바꿨으면 그 값, 아니면 .env 의 초기값."""
    data = read_pin_store()
    if data:
        return str(data["pin"]).strip()
    return (current_app.config.get("LOOKUP_PIN") or "").strip()


def _qr_secret():
    return (current_app.config.get("LOOKUP_QR_SECRET") or "").strip()


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
    # 이미 액세스 로그에 적힌 값으로 인증해 주는 것은 유출을 눈감아 주는 셈이다.
    if request.args.get("pin") is not None:
        return redirect(url_for("lookup.index"))

    if request.method == "POST" and not _authed():
        if _throttled(ip):
            models.lookup_log_add("pin_fail", ip=ip)
            return render_template("lookup/pin.html",
                                   error="입력 시도가 많습니다. 10분 뒤 다시 시도하세요."), 429
        # QR 이 실어 온 토큰(k)과 사람이 친 PIN(pin)은 다른 비밀이다. 둘 다 같은
        # 시도 제한을 받되, 어느 쪽으로 들어왔는지는 기록에 남겨 둔다.
        qr_given = (request.form.get("k") or "").strip()
        secret = _qr_secret()
        if qr_given:
            if secret and hmac.compare_digest(qr_given, secret):
                _grant()
                models.lookup_log_add("qr_ok", ip=ip)
                return redirect(url_for("lookup.index"))
            _record_fail(ip)
            models.lookup_log_add("qr_fail", ip=ip)
            return render_template("lookup/pin.html",
                                   error="QR 이 만료되었거나 올바르지 않습니다. "
                                         "PIN 을 직접 입력해 주세요."), 401

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
