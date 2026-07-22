"""forie 통합 로그인(SSO) 공용 모듈.

⚠️  main / parking / forie_kids 세 앱이 **같은 파일을 복사해서** 쓴다.
    수정하면 반드시 세 곳 모두 동기화할 것. 버전이 어긋나면 앱 시작 로그에 경고가 남는다.

동작 원리
---------
세 앱이 모두 `*.forie.kr` 서브도메인이라는 점을 이용해, 부모 도메인(`.forie.kr`)
쿠키에 서명된 신원 토큰을 실어 로그인 상태를 공유한다. OAuth 식 리다이렉트 왕복이
없으므로 로그인 직후 다른 앱으로 이동해도 이미 로그인 상태다.

  IdP (main)  : 로그인/가입을 처리하고 토큰을 **발급**한다.
  SP (parking, kids) : 토큰을 **검증만** 한다. 미인증이면 main 으로 보낸다.

보안 원칙
---------
1. 토큰은 status == "approved" 계정에만 발급한다. SP 는 토큰을 받으면 "승인된
   주민"으로 전제하므로, 미승인 계정에 토큰이 나가면 승인 체계 전체가 무력화된다.
   그래서 issue_token() 안에서 검사하고 예외를 던진다 — 호출부가 빠뜨릴 수 없게.
2. 토큰은 "누구인지"만 나른다. 권한·상태의 진실은 항상 DB 다. 매 요청 user_loader
   로 계정을 다시 읽어 status 를 확인하므로, 탈퇴/반려 처리가 **즉시** 반영된다.
3. SSO_SECRET 은 각 앱의 SECRET_KEY 와 **다른 값**이어야 한다. 세션 서명키를
   공유하면 앱 간 세션 위조가 가능해진다.
"""
from urllib.parse import quote

from flask import current_app, redirect, request, session
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SSO_MODULE_VERSION = "1.0.0"

COOKIE_NAME = "forie_sso"
COOKIE_DOMAIN = ".forie.kr"
SALT = "forie-sso-v1"

MAX_AGE = 2 * 60 * 60        # 토큰 수명 2시간
RENEW_WINDOW = 30 * 60       # 잔여 30분 미만이면 갱신 발급(슬라이딩)

LOGIN_URL = "https://forie.kr/login"
LOGOUT_URL = "https://forie.kr/logout"


class NotApproved(Exception):
    """미승인 계정에 토큰을 발급하려 했을 때."""


# ------------------------------------------------------------------ 토큰

def _serializer():
    secret = current_app.config.get("SSO_SECRET") or ""
    if not secret:
        raise RuntimeError("SSO_SECRET 이 설정되지 않았습니다(.env 확인).")
    return URLSafeTimedSerializer(secret, salt=SALT)


def issue_token(user):
    """승인된 계정의 신원 토큰. 미승인이면 NotApproved."""
    if getattr(user, "status", None) != "approved":
        raise NotApproved("승인되지 않은 계정에는 SSO 토큰을 발급하지 않습니다.")
    return _serializer().dumps({
        "uid": str(user.id),
        "u": user.username,
        "n": user.name,
        "d": user.dong,
        "h": user.ho,
        "r": getattr(user, "role", "resident"),
        "mv": SSO_MODULE_VERSION,
    })


def read_token(req=None):
    """쿠키의 토큰 → (payload, age_seconds). 없거나 위조/만료면 None."""
    req = req or request
    raw = req.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        payload, issued_at = _serializer().loads(
            raw, max_age=MAX_AGE, return_timestamp=True)
    except (BadSignature, SignatureExpired):
        return None
    except Exception:
        current_app.logger.exception("SSO 토큰 해석 실패")
        return None

    from datetime import datetime, timezone
    age = (datetime.now(timezone.utc) - issued_at).total_seconds()
    return payload, age


# ------------------------------------------------------------------ 쿠키

def set_sso_cookie(response, token):
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=MAX_AGE,
        domain=COOKIE_DOMAIN,
        path="/",
        secure=True,          # 전 구간 HTTPS(Cloudflare Full Strict)
        httponly=True,        # JS 접근 차단
        samesite="Lax",
    )
    return response


def clear_sso_cookie(response):
    response.delete_cookie(COOKIE_NAME, domain=COOKIE_DOMAIN, path="/")
    return response


def login_redirect(next_url=None):
    """미인증 사용자를 IdP 로그인 화면으로. 로그인 후 원래 위치로 돌아온다."""
    target = next_url or request.url
    return redirect(LOGIN_URL + "?next=" + quote(target, safe=""))


# ------------------------------------------------------------------ 앱 배선

def init_sso(app, user_loader, issuer=False):
    """SP(그리고 IdP 자신)에 SSO 를 배선한다.

    선행 조건: flask_login 의 login_manager.init_app(app) 이 이미 호출되어 있을 것.

    user_loader(uid) -> User | None : DB 에서 계정을 읽어오는 함수(앱마다 다름).
    issuer : True 면 이 앱이 토큰 발급자(main). 갱신 발급을 여기서 한다.
    """
    login_manager = getattr(app, "login_manager", None)
    if login_manager is None:
        raise RuntimeError("login_manager.init_app(app) 을 먼저 호출해야 합니다.")

    app.config.setdefault("SSO_MODULE_VERSION", SSO_MODULE_VERSION)
    if not app.config.get("SSO_SECRET"):
        app.logger.warning("SSO_SECRET 미설정 — 통합 로그인이 동작하지 않습니다.")

    @app.before_request
    def _drop_stale_session_login():
        """SSO 도입 전 자체 로그인으로 남은 세션 잔재를 버린다.

        flask-login 은 세션의 _user_id 를 request_loader 보다 먼저 보기 때문에,
        이 잔재가 남아 있으면 공유 쿠키가 가리키는 계정과 다른 사람으로 인식된다.
        신원의 단일 진실원천은 SSO 쿠키 하나다.
        """
        for key in ("_user_id", "_fresh", "_id"):
            if key in session:
                session.pop(key, None)

    @login_manager.request_loader
    def _load_user_from_sso(req):
        data = read_token(req)
        if not data:
            return None
        payload, age = data

        # 토큰은 신원만 나른다. 승인 상태의 진실은 DB — 매 요청 확인해 즉시 반영한다.
        try:
            user = user_loader(payload.get("uid"))
        except Exception:
            current_app.logger.exception("SSO 사용자 조회 실패")
            return None
        if not user or getattr(user, "status", None) != "approved":
            return None

        # 잔여 수명이 짧으면 이번 응답에서 갱신 발급하도록 표시해 둔다.
        if MAX_AGE - age < RENEW_WINDOW:
            from flask import g
            g._sso_renew_user = user
        return user

    @app.after_request
    def _renew_sso_cookie(response):
        from flask import g
        user = getattr(g, "_sso_renew_user", None)
        if user is not None:
            try:
                set_sso_cookie(response, issue_token(user))
            except NotApproved:
                clear_sso_cookie(response)
            except Exception:
                current_app.logger.exception("SSO 쿠키 갱신 실패")
        return response

    @app.errorhandler(403)
    def _forbidden(_error):
        """권한 부족 안내. 기본 403 페이지로는 무엇이 문제인지 알 수 없다."""
        who = "로그인하지 않음"
        if getattr(current_user, "is_authenticated", False):
            who = "%s (%s)" % (current_user.name,
                               getattr(current_user, "role_label", None) or "입주민")
        html = (
            '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>접근 권한 없음</title><style>'
            'body{font-family:-apple-system,BlinkMacSystemFont,"Pretendard","Segoe UI",sans-serif;'
            'margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;'
            'padding:24px;background:#eef2fb;color:#161c28}'
            '@media(prefers-color-scheme:dark){body{background:#0b101b;color:#e9edf6}'
            '.box{background:#1a2131 !important;border-color:rgba(255,255,255,.08) !important}}'
            '.box{max-width:420px;width:100%;background:#fff;border:1px solid rgba(255,255,255,.9);'
            'border-radius:22px;padding:34px 28px;text-align:center;'
            'box-shadow:0 10px 30px rgba(30,42,70,.10)}'
            '.ico{font-size:44px;margin-bottom:14px}'
            'h1{font-size:20px;font-weight:800;margin:0 0 10px}'
            'p{color:#5c6579;font-size:14.5px;line-height:1.65;margin:0 0 8px}'
            '.who{display:inline-block;margin:14px 0 22px;padding:8px 14px;border-radius:999px;'
            'background:rgba(37,99,235,.10);color:#2563eb;font-size:13px;font-weight:700}'
            '.btn{display:block;padding:14px;border-radius:14px;text-decoration:none;'
            'font-weight:800;font-size:15px;margin-top:9px}'
            '.p{background:#2563eb;color:#fff}.g{background:rgba(100,116,139,.14);color:inherit}'
            '</style></head><body><div class="box">'
            '<div class="ico">🔒</div>'
            '<h1>접근 권한이 없습니다</h1>'
            '<p>이 화면은 <b>관리사무소 계정</b>으로만 볼 수 있습니다.</p>'
            '<div class="who">현재 로그인: ' + who + '</div>'
            '<a class="btn p" href="' + LOGOUT_URL + '">다른 계정으로 로그인</a>'
            '<a class="btn g" href="https://forie.kr/">홈으로</a>'
            '</div></body></html>'
        )
        return html, 403

    @login_manager.unauthorized_handler
    def _unauthorized():
        # API 요청은 리다이렉트 대신 401 (브라우저 폼이 아닌 호출자를 위해)
        if request.path.startswith("/api/") or request.is_json:
            return "", 401
        return login_redirect()

    return app
