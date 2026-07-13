"""회원가입 / 로그인 / 로그아웃 / 아이디·비번 찾기 / 비번 변경."""
import secrets
import string

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user

from . import models

auth_bp = Blueprint("auth", __name__)


def _gen_temp_password(length=8):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        password2 = request.form.get("password2") or ""
        name = (request.form.get("name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        dong = (request.form.get("dong") or "").strip()
        ho = (request.form.get("ho") or "").strip()

        errors = []
        if not username.isalnum() or len(username) < 4:
            errors.append("아이디는 영문/숫자 4자 이상으로 입력하세요.")
        if len(password) < 8:
            errors.append("비밀번호는 8자 이상이어야 합니다.")
        if password != password2:
            errors.append("비밀번호 확인이 일치하지 않습니다.")
        if not name:
            errors.append("이름을 입력하세요.")
        if not dong or not ho:
            errors.append("동/호수를 입력하세요.")
        if not errors and models.users_get_by_username(username):
            errors.append("이미 사용 중인 아이디입니다.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("auth/register.html", form=request.form)

        models.users_create({
            "username": username,
            "password_hash": models.make_password_hash(password),
            "name": name,
            "phone": phone,
            "dong": dong,
            "ho": ho,
            "role": "resident",
            "status": "pending",
        })

        flash("가입 신청이 완료되었습니다. 관리사무소 승인 후 로그인할 수 있습니다.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html", form={})


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""

        user = models.users_get_by_username(username)
        if not user or not user.check_password(password):
            flash("아이디 또는 비밀번호가 올바르지 않습니다.", "danger")
            return render_template("auth/login.html", username=username)

        if user.status == "pending":
            flash("아직 관리사무소 승인 대기 중입니다.", "warning")
            return render_template("auth/login.html", username=username)
        if user.status == "rejected":
            flash("가입이 반려되었습니다. 관리사무소에 문의하세요.", "danger")
            return render_template("auth/login.html", username=username)

        login_user(user)
        next_page = request.args.get("next")
        return redirect(next_page or url_for("main.index"))

    return render_template("auth/login.html", username="")


@auth_bp.route("/find-id", methods=["GET", "POST"])
def find_id():
    """아이디 찾기 — 동/호/이름/연락처 일치 시 아이디 표시."""
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    found = None
    if request.method == "POST":
        dong = (request.form.get("dong") or "").strip()
        ho = (request.form.get("ho") or "").strip()
        name = (request.form.get("name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        if not (dong and ho and name and phone):
            flash("동/호/이름/연락처를 모두 입력하세요.", "danger")
        else:
            matches = models.users_find_by_identity(dong, ho, name, phone=phone)
            if matches:
                found = [u.username for u in matches]
            else:
                flash("일치하는 계정을 찾을 수 없습니다. 입력 정보를 확인하세요.", "danger")
    return render_template("auth/find_id.html", form=request.form, found=found)


@auth_bp.route("/find-password", methods=["GET", "POST"])
def find_password():
    """비밀번호 찾기 — 동/호/이름/연락처/아이디 일치 시 임시 비번 발급."""
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    temp_password = None
    if request.method == "POST":
        dong = (request.form.get("dong") or "").strip()
        ho = (request.form.get("ho") or "").strip()
        name = (request.form.get("name") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        username = (request.form.get("username") or "").strip().lower()
        if not (dong and ho and name and phone and username):
            flash("동/호/이름/연락처/아이디를 모두 입력하세요.", "danger")
        else:
            matches = models.users_find_by_identity(dong, ho, name, phone=phone, username=username)
            if len(matches) == 1:
                user = matches[0]
                temp_password = _gen_temp_password()
                models.users_update(user.id, {
                    "password_hash": models.make_password_hash(temp_password),
                    "must_change_password": True,
                })
            else:
                flash("일치하는 계정을 찾을 수 없습니다. 입력 정보를 확인하세요.", "danger")
    return render_template("auth/find_password.html", form=request.form, temp_password=temp_password)


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """비밀번호 변경. 임시 비번 로그인(must_change_password) 시 강제 진입."""
    forced = current_user.must_change_password
    if request.method == "POST":
        new1 = request.form.get("new_password") or ""
        new2 = request.form.get("new_password2") or ""
        errors = []
        if len(new1) < 8:
            errors.append("비밀번호는 8자 이상이어야 합니다.")
        if new1 != new2:
            errors.append("비밀번호 확인이 일치하지 않습니다.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("auth/change_password.html", forced=forced)

        models.users_update(current_user.id, {
            "password_hash": models.make_password_hash(new1),
            "must_change_password": False,
        })
        flash("비밀번호가 변경되었습니다.", "success")
        return redirect(url_for("main.index"))

    return render_template("auth/change_password.html", forced=forced)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("로그아웃되었습니다.", "info")
    return redirect(url_for("auth.login"))
