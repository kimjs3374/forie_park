"""관리사무소(admin)용 화면 — 가입 승인 / 반려 및 전체 방문등록 조회."""
import csv
import io
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, Response
from flask_login import login_required, current_user

from . import models

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


@admin_bp.route("/")
@admin_required
def dashboard():
    pending_count = models.users_count(status="pending")
    resident_count = models.users_count(role="resident", status="approved")
    visit_count = models.visits_count(status="active")
    return render_template(
        "admin/dashboard.html",
        pending_count=pending_count,
        resident_count=resident_count,
        visit_count=visit_count,
    )


@admin_bp.route("/users")
@admin_required
def users():
    status = request.args.get("status", "pending")
    if status not in ("pending", "approved", "rejected"):
        status = None
    user_list = models.users_list(role="resident", status=status)
    return render_template("admin/users.html", users=user_list, status=request.args.get("status", "pending"))


@admin_bp.route("/users/<int:user_id>/approve", methods=["POST"])
@admin_required
def approve_user(user_id):
    user = models.users_get_by_id(user_id)
    if not user:
        abort(404)
    models.users_update(user_id, {
        "status": "approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": current_user.id,
    })
    flash(f"{user.name}({user.household_label}) 가입을 승인했습니다.", "success")
    return redirect(url_for("admin.users", status="pending"))


@admin_bp.route("/users/<int:user_id>/reject", methods=["POST"])
@admin_required
def reject_user(user_id):
    user = models.users_get_by_id(user_id)
    if not user:
        abort(404)
    models.users_update(user_id, {"status": "rejected"})
    flash(f"{user.name}({user.household_label}) 가입을 반려했습니다.", "warning")
    return redirect(url_for("admin.users", status="pending"))


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


@admin_bp.route("/visits")
@admin_required
def visits():
    date_from = _parse_date(request.args.get("from"))
    date_to = _parse_date(request.args.get("to"))
    regs = models.visits_filter(date_from, date_to, limit=500, with_user=True)
    return render_template(
        "admin/visits.html",
        registrations=regs,
        date_from=request.args.get("from", ""),
        date_to=request.args.get("to", ""),
    )


# 등록 상태/연동 상태 한글 라벨
_STATUS_KO = {"active": "유효", "cancelled": "취소"}
_SYNC_KO = {"pending": "전송대기", "synced": "연동완료", "failed": "실패"}


@admin_bp.route("/visits/export")
@admin_required
def export_visits():
    date_from = _parse_date(request.args.get("from"))
    date_to = _parse_date(request.args.get("to"))
    regs = models.visits_filter(date_from, date_to, with_user=True)

    buf = io.StringIO()
    buf.write("﻿")  # Excel 한글 깨짐 방지용 UTF-8 BOM
    writer = csv.writer(buf)
    writer.writerow([
        "등록일시", "동", "호", "차량번호",
        "입차시간", "출차시간", "체류(시간)",
        "신청자", "등록상태", "연동상태",
    ])
    for r in regs:
        writer.writerow([
            r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            r.dong, r.ho, r.car_number,
            r.entry_time.strftime("%Y-%m-%d %H:%M") if r.entry_time else "",
            r.exit_time.strftime("%Y-%m-%d %H:%M") if r.exit_time else "",
            round(r.duration_minutes / 60, 1),
            r.user_name or "",
            _STATUS_KO.get(r.status, r.status),
            _SYNC_KO.get(r.nexpa_sync_status, r.nexpa_sync_status),
        ])

    today = datetime.now().strftime("%Y%m%d")
    filename = f"visit_registrations_{today}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
