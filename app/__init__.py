from flask import Flask, redirect, request, url_for
from flask_login import current_user
from flask_wtf.csrf import CSRFProtect

from config import Config
from .extensions import login_manager

csrf = CSRFProtect()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # 확장 초기화 (DB는 Supabase REST 사용 → SQLAlchemy 없음)
    login_manager.init_app(app)
    csrf.init_app(app)

    # 모델 등록 (user_loader 바인딩)
    from . import models  # noqa: F401

    # 블루프린트 등록
    from .auth import auth_bp
    from .main import main_bp
    from .admin import admin_bp
    from .oauth import oauth_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(oauth_bp)

    # 임시 비밀번호 로그인 시 비번 변경 강제 (변경 전까지 다른 페이지 차단)
    @app.before_request
    def _force_password_change():
        if not current_user.is_authenticated:
            return None
        if not current_user.must_change_password:
            return None
        if request.endpoint in {"auth.change_password", "auth.logout", "static"}:
            return None
        return redirect(url_for("auth.change_password"))

    # 보안 응답 헤더 (Cloudflare 뒤 HTTPS)
    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    # CLI: 관리자 계정 생성
    from .cli import register_cli
    register_cli(app)

    return app
