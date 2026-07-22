"""Flask 확장 인스턴스. 순환 import 방지를 위해 별도 모듈로 분리.

DB는 Supabase REST(supabase_client)로 대체되어 SQLAlchemy/Migrate는 제거됨.
"""
from flask_login import LoginManager

login_manager = LoginManager()
# 미인증 처리는 forie_auth.init_sso() 의 unauthorized_handler 가 맡는다(IdP 로 리다이렉트).
# login_view 를 두면 그쪽이 먼저 잡히므로 설정하지 않는다.
