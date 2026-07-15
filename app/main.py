"""방문차량 등록 / 조회 / 취소 (입주민용)."""
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from . import models
from .nexpa_adapter import send_to_nexpa, cancel_on_nexpa

main_bp = Blueprint("main", __name__)

# 1회 등록당 최대 체류시간
MAX_VISIT_HOURS = 72

# 차량번호 4가지 양식 통합:
#   12가3456 / 123가4567 / 서울12가3456 / 경기123가4567
#   = (지역 한글2자)? + 숫자2~3 + 한글1 + 숫자4
CAR_NUMBER_RE = re.compile(r"^(?:[가-힣]{2})?\d{2,3}[가-힣]\d{4}$")


def _parse_dt(value):
    """HTML datetime-local 입력값(YYYY-MM-DDTHH:MM)을 datetime으로 변환."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@main_bp.route("/")
@login_required
def index():
    regs = models.visits_by_user(current_user.id)
    now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).replace(tzinfo=None)
    overdue = [r for r in regs if r.status == "active" and r.visit_state == "entered"
               and r.exit_time and r.exit_time.replace(tzinfo=None) < now_kst]
    popups = models.popups_active_now(now_kst.date())
    return render_template("main/index.html", registrations=regs[:10],
                           overdue=overdue, popups=popups)


@main_bp.route("/visits")
@login_required
def visit_list():
    regs = models.visits_by_user(current_user.id)
    return render_template("main/visit_list.html", registrations=regs)


@main_bp.route("/visits/new", methods=["GET", "POST"])
@login_required
def visit_new():
    if request.method == "POST":
        car_number = (request.form.get("car_number") or "").strip().replace(" ", "")
        visitor_phone = (request.form.get("visitor_phone") or "").strip()
        visit_reason = (request.form.get("visit_reason") or "").strip()
        entry_time = _parse_dt(request.form.get("entry_time"))
        exit_time = _parse_dt(request.form.get("exit_time"))

        errors = []
        if not car_number:
            errors.append("차량번호를 입력하세요.")
        elif not CAR_NUMBER_RE.match(car_number):
            errors.append("차량번호 형식이 올바르지 않습니다. 예) 12가3456, 123가4567, 서울12가3456")
        if not visit_reason:
            errors.append("방문사유를 입력하세요.")
        elif len(visit_reason) > 100:
            errors.append("방문사유는 100자 이내로 입력하세요.")
        if not visitor_phone:
            errors.append("방문자 연락처를 입력하세요.")
        else:
            _digits = re.sub(r"\D", "", visitor_phone)
            if not (9 <= len(_digits) <= 13):
                errors.append("방문자 연락처 형식이 올바르지 않습니다.")
        if not entry_time:
            errors.append("입차시간을 올바르게 입력하세요.")
        if not exit_time:
            errors.append("출차시간을 올바르게 입력하세요.")
        if entry_time and exit_time and exit_time <= entry_time:
            errors.append("출차시간은 입차시간보다 뒤여야 합니다.")
        if entry_time and exit_time and (exit_time - entry_time) > timedelta(hours=MAX_VISIT_HOURS):
            errors.append(f"1회 등록은 최대 {MAX_VISIT_HOURS}시간까지 가능합니다.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("main/visit_new.html", form=request.form)

        # 중복 방지: 같은 차량번호에 아직 끝나지 않은 활성 등록이 있으면 차단 (차량당 1건, 연장 방지)
        now_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).replace(tzinfo=None)
        for ex in models.visits_active_by_car(car_number):
            ex_out = ex.exit_time.replace(tzinfo=None) if ex.exit_time else None
            if ex_out and ex_out >= now_kst:
                block_msg = (f"이 차량({car_number})은 이미 {ex_out.strftime('%Y-%m-%d')}까지 "
                             f"등록되어 있습니다. 기존 등록이 끝나거나 취소된 뒤에 다시 등록할 수 있습니다.")
                return render_template("main/visit_new.html", form=request.form, block_msg=block_msg)

        reg = models.visits_create({
            "user_id": current_user.id,
            "dong": current_user.dong,
            "ho": current_user.ho,
            "registrant_name": current_user.name,
            "car_number": car_number,
            "visitor_phone": visitor_phone or None,
            "visit_reason": visit_reason,
            "entry_time": entry_time.isoformat(),
            "exit_time": exit_time.isoformat(),
            "status": "active",
            "nexpa_sync_status": "pending",
        })

        # nexpa 연동(현재 stub) — 규격 확정 전까지는 전송대기 상태로 보관
        send_to_nexpa(reg)
        try:
            from .notify import send_visit_alert
            send_visit_alert(current_user.name, current_user.dong, current_user.ho,
                             car_number, visitor_phone, visit_reason,
                             entry_time.strftime("%Y-%m-%d"), exit_time.strftime("%Y-%m-%d"))
        except Exception:
            pass

        return redirect(url_for("main.visit_list", registered=1))

    return render_template("main/visit_new.html", form={})


@main_bp.route("/visits/<int:reg_id>/cancel", methods=["POST"])
@login_required
def visit_cancel(reg_id):
    reg = models.visits_get(reg_id)
    if not reg:
        abort(404)
    if reg.user_id != current_user.id:
        abort(403)

    if reg.status == "cancelled":
        flash("이미 취소된 등록입니다.", "info")
    elif reg.visit_state:
        flash("이미 입차한 차량은 취소할 수 없습니다.", "danger")
    else:
        models.visits_update(reg_id, {"status": "cancelled"})
        cancel_on_nexpa(reg)  # nexpa 취소 연동
        flash("등록이 취소되었습니다.", "info")

    return redirect(url_for("main.visit_list"))
