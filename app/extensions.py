"""Flask 확장 인스턴스. 순환 import 방지를 위해 별도 모듈로 분리.

DB는 Supabase REST(supabase_client)로 대체되어 SQLAlchemy/Migrate는 제거됨.
"""
from flask_login import LoginManager

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "로그인이 필요합니다."
login_manager.login_message_category = "warning"
