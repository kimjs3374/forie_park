"""소셜 로그인(카카오) — 표준 OAuth2 authorization code flow.

Authlib 을 쓰지 않고 requests 로 직접 구현한다. 카카오는 code flow 하나뿐이라
의존성을 늘릴 이유가 없고, state 검증과 세션 취급을 눈에 보이게 두는 편이 안전하다.
구글(OIDC)을 붙일 때 Authlib 도입을 다시 판단한다.

동선(소셜 인증이 먼저, 세대 확인이 나중):
  /oauth/kakao/start → 카카오 동의 → /oauth/kakao/callback
      ├ 기존 연결 계정 있음 → 로그인 (승인 대기면 안내)
      ├ 로그인 상태에서 호출  → 현재 계정에 카카오 연결
      └ 신규                 → /oauth/complete (세대 확인)
                                  ├ 세대에 동명이인 = 기존 회원 → /oauth/link (계정 연결)
                                  └ 신규 생성 → 명부 일치면 자동승인, 아니면 pending
"""
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)
from flask_login import current_user, login_user

from . import models
from .notify import send_signup_alert

oauth_bp = Blueprint("oauth", __name__, url_prefix="/oauth")

KAKAO_AUTHORIZE = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN = "https://kauth.kakao.com/oauth/token"
KAKAO_ME = "https://kapi.kakao.com/v2/user/me"

PROVIDER_LABEL = {"kakao": "카카오"}

STATE_KEY = "oauth_state"
PENDING_KEY = "oauth_pending"      # 인증은 끝났지만 아직 계정이 없는 상태
LINK_HINT_KEY = "oauth_link_hint"
PENDING_TTL = 600                  # 세대 확인 화면에 머물 수 있는 시간(초)


# ------------------------------------------------------------------ 공통

def _redirect_uri():
    """Cloudflare 뒤라 request.scheme 이 http 로 잡힐 수 있어 https 를 강제한다.
    카카오에 등록한 Redirect URI 와 문자열이 정확히 일치해야 한다."""
    return url_for("oauth.kakao_callback", _external=True, _scheme="https")


def _get_pending():
    """인증 완료 대기 정보. 만료됐으면 지우고 None."""
    data = session.get(PENDING_KEY)
    if not isinstance(data, dict):
        return None
    if time.time() - data.get("ts", 0) > PENDING_TTL:
        session.pop(PENDING_KEY, None)
        session.pop(LINK_HINT_KEY, None)
        return None
    return data


def _clear_pending():
    session.pop(PENDING_KEY, None)
    session.pop(LINK_HINT_KEY, None)


def _login_or_notice(user):
    """승인된 계정이면 로그인, 아니면 사유를 안내하고 로그인 화면으로."""
    if user.status == "approved":
        login_user(user)
        _clear_pending()
        return redirect(url_for("main.index"))

    message = {
        "pending": "가입 신청이 접수되었습니다. 관리사무소 승인 후 이용할 수 있습니다.",
        "rejected": "가입이 반려된 계정입니다. 관리사무소에 문의하세요.",
        "withdrawn": "이사(퇴거) 처리된 계정입니다. 관리사무소에 문의하세요.",
    }.get(user.status, "로그인할 수 없는 계정입니다. 관리사무소에 문의하세요.")
    flash(message, "warning" if user.status == "pending" else "danger")
    _clear_pending()
    return redirect(url_for("auth.login"))


# ------------------------------------------------------------------ 카카오

def _kakao_fetch_uid(code):
    """인가 코드 → 액세스 토큰 → 카카오 회원번호(문자열). 실패 시 None.

    회원번호만 쓴다. 이메일·이름·전화번호는 비즈앱 전환이 필요한데다
    동/호/이름은 어차피 세대 확인 화면에서 직접 받으므로 필요가 없다.
    """
    cfg = current_app.config
    data = {
        "grant_type": "authorization_code",
        "client_id": cfg.get("KAKAO_REST_API_KEY", ""),
        "redirect_uri": _redirect_uri(),
        "code": code,
    }
    secret = cfg.get("KAKAO_CLIENT_SECRET", "")
    if secret:
        data["client_secret"] = secret

    try:
        resp = requests.post(KAKAO_TOKEN, data=data, timeout=10)
    except requests.RequestException:
        current_app.logger.exception("카카오 토큰 요청 실패")
        return None
    if resp.status_code != 200:
        current_app.logger.warning("카카오 토큰 교환 거부: %s %s", resp.status_code, resp.text[:300])
        return None

    access_token = (resp.json() or {}).get("access_token")
    if not access_token:
        return None

    try:
        me = requests.get(KAKAO_ME, headers={"Authorization": "Bearer " + access_token}, timeout=10)
    except requests.RequestException:
        current_app.logger.exception("카카오 사용자 조회 실패")
        return None
    if me.status_code != 200:
        current_app.logger.warning("카카오 사용자 조회 거부: %s %s", me.status_code, me.text[:300])
        return None

    uid = (me.json() or {}).get("id")
    return str(uid) if uid else None


@oauth_bp.route("/kakao/start")
def kakao_start():
    """카카오 동의 화면으로 보낸다. link=1 이면 로그인 상태에서의 계정 연결."""
    if not current_app.config.get("KAKAO_REST_API_KEY"):
        flash("카카오 로그인이 설정되지 않았습니다.", "danger")
        return redirect(url_for("auth.login"))

    state = secrets.token_urlsafe(24)
    session[STATE_KEY] = state
    params = {
        "client_id": current_app.config["KAKAO_REST_API_KEY"],
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "state": state,
    }
    return redirect(KAKAO_AUTHORIZE + "?" + urlencode(params))


@oauth_bp.route("/kakao/callback")
def kakao_callback():
    # 1) state 검증 — CSRF 방어. 세션에 담아둔 값과 일치해야 한다.
    expected = session.pop(STATE_KEY, None)
    given = request.args.get("state") or ""
    if not expected or not secrets.compare_digest(str(expected), given):
        flash("로그인 요청이 유효하지 않습니다. 처음부터 다시 시도해주세요.", "danger")
        return redirect(url_for("auth.login"))

    if request.args.get("error"):
        flash("카카오 로그인이 취소되었습니다.", "warning")
        return redirect(url_for("auth.login"))

    code = request.args.get("code")
    if not code:
        flash("카카오 인증에 실패했습니다. 다시 시도해주세요.", "danger")
        return redirect(url_for("auth.login"))

    uid = _kakao_fetch_uid(code)
    if not uid:
        flash("카카오 인증에 실패했습니다. 잠시 후 다시 시도해주세요.", "danger")
        return redirect(url_for("auth.login"))

    # 2) 로그인 상태에서의 호출 = 내 계정에 카카오 연결
    if current_user.is_authenticated:
        return _link_to_current_user("kakao", uid)

    # 3) 이미 연결된 계정이 있으면 곧바로 로그인
    user = models.users_get_by_provider("kakao", uid)
    if user:
        return _login_or_notice(user)

    # 4) 신규 → 세대 확인 화면. uid 는 세션에만 잠시 둔다(10분).
    session[PENDING_KEY] = {"provider": "kakao", "uid": uid, "ts": int(time.time())}
    return redirect(url_for("oauth.complete"))


def _link_to_current_user(provider, uid):
    """로그인한 계정에 소셜 계정을 연결한다."""
    if current_user.provider_uid and str(current_user.provider_uid) != str(uid):
        flash("이 계정에는 이미 다른 카카오 계정이 연결되어 있습니다.", "danger")
        return redirect(url_for("main.index"))

    other = models.users_get_by_provider(provider, uid)
    if other and str(other.id) != str(current_user.id):
        flash("이 카카오 계정은 이미 다른 입주민 계정에 연결되어 있습니다.", "danger")
        return redirect(url_for("main.index"))

    models.users_link_provider(current_user.id, provider, uid)
    flash("카카오 계정이 연결되었습니다. 다음부터는 카카오로 바로 로그인할 수 있습니다.", "success")
    return redirect(url_for("main.index"))


# ------------------------------------------------------------------ 세대 확인 / 계정 연결

@oauth_bp.route("/complete", methods=["GET", "POST"])
def complete():
    """소셜 인증을 마친 신규 사용자에게서 동/호/이름/연락처를 받아 계정을 만든다."""
    pending = _get_pending()
    if not pending:
        flash("인증 정보가 만료되었습니다. 다시 시도해주세요.", "warning")
        return redirect(url_for("auth.login"))

    provider = pending["provider"]
    label = PROVIDER_LABEL.get(provider, provider)

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        dong = (request.form.get("dong") or "").strip()
        ho = (request.form.get("ho") or "").strip()
        consent = request.form.get("consent_agreed")

        errors = []
        if not name:
            errors.append("이름을 입력하세요.")
        if not dong or not ho:
            errors.append("동/호수를 입력하세요.")
        if not consent:
            errors.append("개인정보 수집·이용에 동의해야 가입할 수 있습니다.")

        if not errors:
            state, existing = models.household_check(dong, ho, name)
            if state == models.HOUSEHOLD_DUPLICATE:
                # 이미 아이디/비번으로 가입한 본인일 가능성이 높다 → 차단이 아니라 계정 연결로 유도
                session[LINK_HINT_KEY] = {"name": existing.name,
                                          "household": existing.household_label}
                return redirect(url_for("oauth.link"))
            if state == models.HOUSEHOLD_FULL:
                errors.append("한 세대당 최대 2개까지만 가입할 수 있습니다. 관리사무소에 문의하세요.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("auth/social_signup.html", form=request.form, provider_label=label)

        # 입주민 명부 대조(동/호/이름 3키). 실패해도 가입은 진행 → 수동승인.
        try:
            verified = models.check_resident_match(dong, ho, name)
        except Exception:
            current_app.logger.exception("명부 대조 실패 → 수동승인(pending) 처리")
            verified = False

        now = datetime.now(timezone.utc).isoformat()
        user_data = {
            "username": models.make_social_username(provider, pending["uid"]),
            "password_hash": None,          # 소셜 전용 계정 → 로컬 로그인 불가
            "name": name,
            "phone": phone,
            "dong": dong,
            "ho": ho,
            "role": "resident",
            "status": "approved" if verified else "pending",
            "provider": provider,
            "provider_uid": pending["uid"],
            "linked_at": now,
            "consent_agreed": True,
            "consent_agreed_at": now,
        }
        if verified:
            user_data["approved_at"] = now

        try:
            user = models.users_create(user_data)
        except Exception:
            current_app.logger.exception("소셜 가입 저장 실패")
            flash("가입 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.", "danger")
            return render_template("auth/social_signup.html", form=request.form, provider_label=label)

        send_signup_alert(name, dong, ho, phone, user.username, verified=verified)

        if verified:
            flash("명부 확인이 완료되어 가입이 승인되었습니다.", "success")
        return _login_or_notice(user)

    return render_template("auth/social_signup.html", form={}, provider_label=label)


@oauth_bp.route("/link", methods=["GET", "POST"])
def link():
    """세대에 같은 이름이 이미 있을 때 — 기존 계정에 소셜을 연결한다."""
    pending = _get_pending()
    if not pending:
        flash("인증 정보가 만료되었습니다. 다시 시도해주세요.", "warning")
        return redirect(url_for("auth.login"))

    provider = pending["provider"]
    label = PROVIDER_LABEL.get(provider, provider)
    hint = session.get(LINK_HINT_KEY) or {}

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""

        user = models.users_get_by_username(username)
        if not user or not user.check_password(password):
            flash("아이디 또는 비밀번호가 올바르지 않습니다.", "danger")
            return render_template("auth/link_account.html", hint=hint, provider_label=label)

        if user.provider_uid and str(user.provider_uid) != str(pending["uid"]):
            flash("이 계정에는 이미 다른 카카오 계정이 연결되어 있습니다. 관리사무소에 문의하세요.", "danger")
            return render_template("auth/link_account.html", hint=hint, provider_label=label)

        models.users_link_provider(user.id, provider, pending["uid"])
        flash("카카오 계정이 연결되었습니다. 다음부터는 카카오 버튼만 누르면 로그인됩니다.", "success")
        return _login_or_notice(models.users_get_by_id(user.id))

    return render_template("auth/link_account.html", hint=hint, provider_label=label)
