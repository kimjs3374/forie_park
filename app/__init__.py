from flask import Flask, render_template, request
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from .extensions import login_manager

csrf = CSRFProtect()


def create_app(config_class=Config):
    app = Flask(__name__)
    # nginx(Cloudflare 뒤)가 넘겨주는 X-Forwarded-Proto/Host 를 신뢰한다.
    # 이게 없으면 request.url 이 http:// 로 만들어져 SSO 의 next 검증에 걸린다.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    app.config.from_object(config_class)

    # 확장 초기화 (DB는 Supabase REST 사용 → SQLAlchemy 없음)
    login_manager.init_app(app)
    csrf.init_app(app)

    # 모델 등록 (user_loader 바인딩)
    from . import models  # noqa: F401

    # 통합 로그인(SSO) — 이 앱은 SP 다. .forie.kr 공유 쿠키를 검증만 하고,
    # 발급과 로그인 화면은 IdP(forie.kr)가 맡는다. 미인증이면 IdP 로 보낸다.
    from .forie_auth import init_sso
    init_sso(app, models.users_get_by_id)

    # 블루프린트 등록
    from .auth import auth_bp
    from .main import main_bp
    from .admin import admin_bp
    from .lookup import lookup_bp
    from .share import share_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(lookup_bp)
    app.register_blueprint(share_bp)

    # 경비실 조회는 계정이 아니라 세션에 남긴 만료시각으로 유지된다. 그래서
    # 세션 쿠키가 브라우저를 닫아도 살아 있어야 하고(교대 중 화면 껐다 켬),
    # 수명은 그 만료시각보다 짧으면 안 된다. 로그인은 SSO 쿠키가 따로 맡으므로
    # 이 값을 줄여도 입주민 로그인에는 영향이 없다.
    from datetime import timedelta as _timedelta
    app.permanent_session_lifetime = _timedelta(
        hours=app.config.get("LOOKUP_SESSION_HOURS", 12))

    # CSRF 거부는 브라우저를 오래 열어 둬 토큰이 만료됐거나(입주민), 인앱
    # 브라우저가 Referer 를 지웠을 때 난다. 기본 "400 Bad Request" 로는 방문자가
    # 무엇을 해야 할지 알 수 없어 등록을 포기한다. 요청은 그대로 거부하되
    # 다시 시도할 길을 알려 준다.
    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def _csrf_failed(error):
        app.logger.info("CSRF 거부: %s %s (%s)", request.method, request.path, error.description)
        return render_template("csrf_error.html"), 400

    # 보안 응답 헤더 (Cloudflare 뒤 HTTPS)
    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    # CLI: 관리자 계정 생성
    from .cli import register_cli
    register_cli(app)

    return app
