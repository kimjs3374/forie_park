"""인증은 IdP(forie.kr)로 이관되었다. 여기 남은 것은 옛 경로를 IdP 로 넘기는 리다이렉트뿐이다.

가입·로그인·아이디/비번 찾기·비밀번호 변경은 모두 main 에서 처리한다. 계정 정보를 한 곳에
모아야 로그인 수단(비밀번호/소셜)의 상태를 일관되게 관리할 수 있기 때문이다.

엔드포인트 이름(auth.login, auth.logout ...)은 그대로 둔다. 템플릿의 url_for 와
주민들의 북마크가 깨지지 않게 하기 위해서다.
"""
from urllib.parse import quote

from flask import Blueprint, redirect, request

from .forie_auth import LOGIN_URL, LOGOUT_URL

auth_bp = Blueprint("auth", __name__)

IDP = "https://forie.kr"


@auth_bp.route("/login")
def login():
    """로그인은 IdP 로. 끝나면 원래 보려던 곳으로 돌아온다."""
    target = request.args.get("next") or request.url_root.rstrip("/")
    return redirect(LOGIN_URL + "?next=" + quote(target, safe=""))


@auth_bp.route("/logout")
def logout():
    """IdP 가 공유 쿠키를 지운다 → 세 앱이 한 번에 로그아웃된다."""
    return redirect(LOGOUT_URL)


@auth_bp.route("/register")
def register():
    return redirect(IDP + "/register")


@auth_bp.route("/find-id")
def find_id():
    return redirect(IDP + "/find-id")


@auth_bp.route("/find-password")
def find_password():
    return redirect(IDP + "/find-password")


@auth_bp.route("/change-password")
def change_password():
    return redirect(IDP + "/#account")
