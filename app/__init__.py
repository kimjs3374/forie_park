from flask import Flask
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

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)

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
