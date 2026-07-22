"""관리사무소(admin)용 화면 — 가입 승인 / 반려 및 전체 방문등록 조회."""
import csv
import io
import re
from datetime import datetime, timezone, timedelta
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
    if status not in ("pending", "approved", "rejected", "withdrawn"):
        status = None
    # role 로 거르지 않는다 — 그룹을 동대표/직원으로 바꾼 계정이 목록에서 사라지면 안 된다.
    # 관리사무소 계정만 제외한다(이 화면에서 다룰 대상이 아니다).
    user_list = [u for u in models.users_list(status=status) if not u.is_admin]
    return render_template("admin/users.html", users=user_list,
                           status=request.args.get("status", "pending"),
                           role_choices=[(r, models.role_label(r)) for r in models.ROLE_CHOICES])


@admin_bp.route("/users/<int:user_id>/role", methods=["POST"])
@admin_required
def change_role(user_id):
    """그룹(입주민/동대표/관리사무소 직원/관리사무소) 지정."""
    user = models.users_get_by_id(user_id)
    if not user:
        abort(404)

    role = (request.form.get("role") or "").strip()
    back = redirect(url_for("admin.users", status=request.form.get("status") or "approved"))
    if role not in models.ROLE_CHOICES:
        flash("알 수 없는 그룹입니다.", "danger")
        return back
    if str(user.id) == str(current_user.id):
        # 스스로 권한을 내려놓아 관리 화면에 못 들어가는 상황을 막는다.
        flash("본인 계정의 그룹은 바꿀 수 없습니다.", "danger")
        return back

    payload = {"role": role}
    if role == models.ROLE_ADMIN:
        # 관리사무소 계정은 카카오 로그인을 쓸 수 없다. 비밀번호가 없는 계정을 승격시키면
        # 그 순간 로그인 수단이 사라져 계정이 잠긴다.
        if not user.has_password:
            flash("카카오로만 로그인하는 계정은 관리사무소로 지정할 수 없습니다. "
                  "먼저 아이디·비밀번호를 설정하게 해주세요.", "danger")
            return back
        if user.provider_uid:
            # 남아 있어도 로그인 때 거부되지만, 상태가 헷갈리므로 연결을 정리한다.
            payload.update({"provider": "local", "provider_uid": None, "linked_at": None})

    models.users_update(user_id, payload)
    flash(f"{user.name} 님을 '{models.role_label(role)}' 그룹으로 지정했습니다.", "success")
    return back


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


@admin_bp.route("/users/<int:user_id>/withdraw", methods=["POST"])
@admin_required
def withdraw_user(user_id):
    user = models.users_get_by_id(user_id)
    if not user:
        abort(404)
    models.users_update(user_id, {"status": "withdrawn"})
    try:
        models.directory_delete_by_identity(user.dong, user.ho, user.name)
    except Exception:
        pass
    flash(f"{user.name}({user.household_label}) 이사·탈퇴 처리했습니다. 로그인 차단 + 명부에서 제거됩니다.", "info")
    return redirect(url_for("admin.users", status="approved"))


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
    logs_map = models.visit_logs_by_regs([r.id for r in regs])
    now_utc = datetime.now(timezone.utc)
    summaries = {r.id: models.summarize_logs(logs_map.get(r.id, []), now=now_utc) for r in regs}
    dispmap = models.visit_dispmap(regs, now=now_utc, logs_map=logs_map)
    return render_template(
        "admin/visits.html",
        registrations=regs,
        summaries=summaries,
        dispmap=dispmap,
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


@admin_bp.route("/popups")
@admin_required
def popups():
    return render_template("admin/popups.html", popups=models.popups_all())


@admin_bp.route("/popups/new", methods=["POST"])
@admin_required
def popup_new():
    title = (request.form.get("title") or "").strip()
    content = (request.form.get("content") or "").strip()
    start_date = (request.form.get("start_date") or "").strip() or None
    end_date = (request.form.get("end_date") or "").strip() or None
    if not title or not content:
        flash("제목과 내용을 입력하세요.", "danger")
        return redirect(url_for("admin.popups"))
    models.popups_create({"title": title, "content": content,
                          "start_date": start_date, "end_date": end_date, "is_active": True})
    flash("팝업을 등록했습니다.", "success")
    return redirect(url_for("admin.popups"))


@admin_bp.route("/popups/<int:pid>/toggle", methods=["POST"])
@admin_required
def popup_toggle(pid):
    p = models.popups_get(pid)
    if not p:
        abort(404)
    models.popups_update(pid, {"is_active": not p.is_active})
    return redirect(url_for("admin.popups"))


@admin_bp.route("/popups/<int:pid>/delete", methods=["POST"])
@admin_required
def popup_delete(pid):
    models.popups_delete(pid)
    flash("팝업을 삭제했습니다.", "info")
    return redirect(url_for("admin.popups"))


# ------------------------------------------------------------ 입주민 명부(자동승인 기준)

_DIR_HEADER_ALIASES = {
    "dong": {"동", "dong", "동수", "aptdong", "apt_dong"},
    "ho":   {"호", "호수", "호실", "ho", "aptho", "apt_ho"},
    "name": {"이름", "성명", "name", "세대주", "입주민", "신청자"},
}


def _resolve_columns(header):
    idx = {"dong": None, "ho": None, "name": None}
    for i, cell in enumerate(header):
        key = re.sub(r"\s", "", str(cell or "")).lower()
        for col, aliases in _DIR_HEADER_ALIASES.items():
            if idx[col] is None and key in {a.lower() for a in aliases}:
                idx[col] = i
    return idx


def _parse_directory_file(file):
    """업로드 파일(xlsx/csv) -> [(dong, ho, name), ...].
    첫 행에서 동/호/이름 헤더를 찾고, 못 찾으면 앞 3열을 동/호/이름으로 간주."""
    fname = (file.filename or "").lower()
    if fname.endswith(".csv"):
        data = file.read().decode("utf-8-sig", errors="replace")
        rows = [r for r in csv.reader(io.StringIO(data)) if any((c or "").strip() for c in r)]
    else:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file.read()), read_only=True, data_only=True)
        ws = wb.active
        rows = []
        for r in ws.iter_rows(values_only=True):
            cells = ["" if c is None else c for c in (r or [])]
            if any(str(c).strip() for c in cells):
                rows.append(cells)
    if not rows:
        return []
    idx = _resolve_columns(rows[0])
    if None not in idx.values():
        body, di, hi, ni = rows[1:], idx["dong"], idx["ho"], idx["name"]
    else:
        body, di, hi, ni = rows, 0, 1, 2
    out = []
    for r in body:
        if len(r) <= max(di, hi, ni):
            continue
        out.append((r[di], r[hi], r[ni]))
    return out


@admin_bp.route("/directory")
@admin_required
def directory():
    q = (request.args.get("q") or "").strip()
    dong = (request.args.get("dong") or "").strip()
    dong_summary = []
    try:
        total = models.directory_count()
        table_ready = True
        if q:
            entries = models.directory_list(query=q)
        elif dong:
            entries = models.directory_list(dong=dong)
        else:
            entries = []
            dong_summary = models.directory_dong_summary()
    except Exception:
        entries, total, table_ready = [], 0, False
    return render_template("admin/directory.html", entries=entries, q=q, dong=dong,
                           total=total, table_ready=table_ready, dong_summary=dong_summary)


@admin_bp.route("/directory/upload", methods=["POST"])
@admin_required
def directory_upload():
    file = request.files.get("file")
    if not file or not file.filename:
        flash("파일을 선택하세요.", "danger")
        return redirect(url_for("admin.directory"))
    try:
        raw_rows = _parse_directory_file(file)
    except Exception as e:
        flash(f"파일을 읽지 못했습니다: {e}", "danger")
        return redirect(url_for("admin.directory"))

    seen, entries, skipped_dup, invalid = set(), [], 0, 0
    for dong, ho, name in raw_rows:
        dong, ho, name = str(dong).strip(), str(ho).strip(), str(name).strip()
        if not dong or not ho or not name:
            invalid += 1
            continue
        key = models.directory_match_key(dong, ho, name)
        if key in seen:
            skipped_dup += 1
            continue
        seen.add(key)
        entries.append((dong, ho, name))

    if not entries:
        flash("유효한 행이 없습니다. (동/호/이름 컬럼을 확인하세요)", "danger")
        return redirect(url_for("admin.directory"))

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    try:
        added, skipped_exist = models.directory_merge(entries, batch_id)
    except Exception as e:
        flash(f"명부 저장 실패(1단계 DB SQL을 먼저 실행했는지 확인): {e}", "danger")
        return redirect(url_for("admin.directory"))

    msg = f"명부에 {added}명 추가했습니다."
    if skipped_exist:
        msg += f" 이미 있는 {skipped_exist}명 유지."
    if skipped_dup:
        msg += f" 파일 내 중복 {skipped_dup}건 제외."
    if invalid:
        msg += f" 누락행 {invalid}건 무시."
    flash(msg, "success")
    return redirect(url_for("admin.directory"))


@admin_bp.route("/directory/add", methods=["POST"])
@admin_required
def directory_add():
    dong = (request.form.get("dong") or "").strip()
    ho = (request.form.get("ho") or "").strip()
    name = (request.form.get("name") or "").strip()
    if not dong or not ho or not name:
        flash("동/호/이름을 모두 입력하세요.", "danger")
        return redirect(url_for("admin.directory"))
    try:
        models.directory_add(dong, ho, name, batch_id="manual")
        flash(f"{dong}동 {ho}호 {name} 명부에 추가했습니다.", "success")
    except Exception as e:
        flash(f"추가 실패(이미 있는 세대일 수 있음): {e}", "danger")
    return redirect(url_for("admin.directory"))


@admin_bp.route("/directory/<int:entry_id>/delete", methods=["POST"])
@admin_required
def directory_delete(entry_id):
    models.directory_delete(entry_id)
    flash("명부에서 삭제했습니다.", "info")
    return redirect(url_for("admin.directory"))
