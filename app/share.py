"""임시 방문차량 등록권한 공유링크.

입주민이 방문객의 차량번호를 몰라 등록을 못 하는 일이 잦다. 전화로 불러
받아 적다 보면 오입력이 나고, 그 차는 미등록으로 단속 대상이 된다.
그래서 **등록 권한 자체를 잠깐 넘기는** 링크를 만든다.

  입주민이 정하는 것 : 방문사유 · 차량종류 · 방문기간(세대 월 한도를 쓰는 값)
  방문자가 넣는 것   : 차량번호 · 연락처

링크는 만든 지 10분 뒤 자동 만료되고, 1건이 등록되면 그 즉시 소진된다.
카톡방에 남은 링크가 나중에 다시 통해서는 안 되기 때문이다. 같은 이유로
새 링크를 만들면 그 계정이 앞서 만든 살아 있는 링크는 모두 무효가 된다.

한도·중복 검사는 입주민이 직접 등록할 때(main.visit_new)와 **똑같이** 건다.
권한을 넘겼다고 규약이 느슨해지면 이 링크가 한도 우회 수단이 된다.
"""
import re
import secrets
from datetime import datetime, time, timedelta, timezone

from flask import (Blueprint, flash, render_template, request, url_for)
from flask_login import current_user, login_required

from . import models
from . import usage
from .main import CAR_NUMBER_RE, MAX_VISIT_HOURS
from .nexpa_adapter import send_to_nexpa

share_bp = Blueprint("share", __name__, url_prefix="/share")

# 입주민이 고를 수 있는 방문기간 (당일 / +1일 / +2일) — 등록 폼과 같은 규칙.
MAX_EXTRA_DAYS = 2


@share_bp.app_context_processor
def _inject_share_ttl():
    """유효시간은 화면 곳곳에 문구로 나온다. 상수를 한 곳에서만 고치도록 넘긴다."""
    return {"share_ttl": models.SHARE_TTL_MINUTES}


def _kst_now():
    return (datetime.now(timezone.utc) + timedelta(hours=9)).replace(tzinfo=None)


@share_bp.route("/", methods=["GET", "POST"])
@login_required
def new():
    today = _kst_now().date()

    if request.method == "POST":
        visit_reason = (request.form.get("visit_reason") or "").strip()
        car_type = (request.form.get("car_type") or "").strip()
        entry_date_raw = (request.form.get("entry_date") or "").strip()
        try:
            extra_days = int(request.form.get("extra_days") or 0)
        except ValueError:
            extra_days = -1

        errors = []
        if not visit_reason:
            errors.append("방문사유를 입력하세요.")
        elif len(visit_reason) > 100:
            errors.append("방문사유는 100자 이내로 입력하세요.")
        if car_type and len(car_type) > 30:
            errors.append("차량종류는 30자 이내로 입력하세요.")
        if not (0 <= extra_days <= MAX_EXTRA_DAYS):
            errors.append("방문기간을 다시 선택하세요.")
        try:
            entry_date = datetime.strptime(entry_date_raw, "%Y-%m-%d").date()
        except ValueError:
            entry_date = None
            errors.append("입차 날짜를 올바르게 선택하세요.")
        if entry_date and entry_date < today:
            errors.append("지난 날짜는 선택할 수 없습니다.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("share/new.html", form=request.form, today=today)

        entry_time = datetime.combine(entry_date, time(0, 0))
        exit_time = datetime.combine(entry_date + timedelta(days=extra_days), time(23, 59))

        # 카톡방에 옛 링크가 남아 다시 통하는 일이 없도록 항상 마지막 한 장만 살린다.
        models.share_revoke_live(current_user.id)

        raw = secrets.token_urlsafe(24)
        token = models.share_create({
            "token_hash": models.share_token_hash(raw),
            "user_id": current_user.id,
            "dong": current_user.dong,
            "ho": current_user.ho,
            "registrant_name": current_user.name,
            "visit_reason": visit_reason,
            "car_type": car_type or None,
            "entry_time": entry_time.isoformat(),
            "exit_time": exit_time.isoformat(),
            "expires_at": (datetime.now(timezone.utc)
                           + timedelta(minutes=models.SHARE_TTL_MINUTES)).isoformat(),
        })
        # 원문 토큰은 이 화면에서만 볼 수 있다(DB 에는 해시만 남는다).
        return render_template("share/link.html", token=token, raw=raw,
                               url=url_for("share.visit", token=raw, _external=True))

    return render_template(
        "share/new.html", form={}, today=today,
        recent=models.share_recent_by_household(current_user.dong, current_user.ho, limit=5))


@share_bp.route("/v/<token>", methods=["GET", "POST"])
def visit(token):
    """방문자용 화면. 로그인하지 않는다 — 링크 자체가 권한이다."""
    share = models.share_get_by_token(token)
    if not share:
        return render_template("share/invalid.html", reason="주소가 올바르지 않습니다."), 404
    if share.is_used:
        return render_template("share/invalid.html",
                               reason="이미 등록이 완료되어 사용할 수 없는 링크입니다."), 410
    if share.is_revoked:
        return render_template("share/invalid.html",
                               reason="세대에서 새 링크를 발급해 이 링크는 무효가 되었습니다."), 410
    if share.is_expired:
        return render_template(
            "share/invalid.html",
            reason="링크 유효시간(%d분)이 지났습니다. 세대에 다시 요청해 주세요."
                   % models.SHARE_TTL_MINUTES), 410

    if request.method == "GET":
        return render_template("share/visit.html", share=share, form={})

    car_number = (request.form.get("car_number") or "").strip().replace(" ", "")
    visitor_phone = (request.form.get("visitor_phone") or "").strip()

    errors = []
    if not car_number:
        errors.append("차량번호를 입력하세요.")
    elif not CAR_NUMBER_RE.match(car_number):
        errors.append("차량번호 형식이 올바르지 않습니다. 예) 12가3456, 123가4567, 서울12가3456")
    if not visitor_phone:
        errors.append("연락처를 입력하세요.")
    elif not (9 <= len(re.sub(r"\D", "", visitor_phone)) <= 13):
        errors.append("연락처 형식이 올바르지 않습니다.")

    if errors:
        for e in errors:
            flash(e, "danger")
        return render_template("share/visit.html", share=share, form=request.form)

    entry_time, exit_time = share.entry_time, share.exit_time
    if not entry_time or not exit_time or exit_time <= entry_time:
        return render_template(
            "share/invalid.html",
            reason="링크의 방문기간 정보가 올바르지 않습니다. 세대에 다시 요청해 주세요."), 410
    if (exit_time - entry_time) > timedelta(hours=MAX_VISIT_HOURS):
        return render_template(
            "share/invalid.html",
            reason="링크의 방문기간이 허용 범위를 넘습니다. 세대에 다시 요청해 주세요."), 410

    # 아래 두 검사는 main.visit_new 와 같은 규칙이다. 링크가 우회로가 되면 안 된다.
    now_kst = _kst_now()
    for ex in models.visits_active_by_car(car_number):
        ex_out = ex.exit_time.replace(tzinfo=None) if ex.exit_time else None
        if ex_out and ex_out >= now_kst:
            msg = ("이 차량(%s)은 이미 %s까지 등록되어 있습니다. "
                   "추가 등록 없이 그대로 입차하시면 됩니다."
                   % (car_number, ex_out.strftime("%Y-%m-%d")))
            return render_template("share/visit.html", share=share,
                                   form=request.form, block_msg=msg)

    plan = usage.plan_registration(car_number, entry_time, exit_time)
    nights, allowed = plan["nights"], plan["allowed"]
    notice = None
    if nights and not allowed:
        first_night = nights[0]
        q = plan["quotas"].get((first_night.year, first_night.month), {})
        msg = ("이 차량(%s)은 %d월 주차 가능일수 %d일을 모두 사용했습니다(사용 %d일). "
               "방문하실 세대를 통해 관리사무소로 문의해 주세요."
               % (car_number, first_night.month, usage.MONTHLY_LIMIT_DAYS,
                  q.get("used_days", usage.MONTHLY_LIMIT_DAYS)))
        return render_template("share/visit.html", share=share,
                               form=request.form, block_msg=msg)
    if allowed and allowed[-1] < nights[-1]:
        # 말없이 줄이면 방문객이 못 나가는 사고가 난다. 반드시 알린다.
        exit_time = datetime.combine(allowed[-1], time(23, 59))
        notice = ("이 차량의 이번 달 남은 주차일수에 맞춰 출차일을 %s로 조정했습니다."
                  % allowed[-1].strftime("%m월 %d일"))

    # 등록을 만들기 직전에 링크를 선점한다. 검증에서 막힌 시도로 링크가 타면
    # 방문객이 다시 요청해야 하므로, 통과가 확정된 이 지점에서만 태운다.
    if not models.share_claim(share.id):
        return render_template("share/invalid.html",
                               reason="이미 등록이 완료되어 사용할 수 없는 링크입니다."), 410

    try:
        reg = models.visits_create({
            "user_id": share.user_id,
            "dong": share.dong,
            "ho": share.ho,
            "registrant_name": share.registrant_name,
            "car_number": car_number,
            "visitor_phone": visitor_phone,
            "visit_reason": share.visit_reason,
            "car_type": share.car_type,
            "entry_time": entry_time.isoformat(),
            "exit_time": exit_time.isoformat(),
            "status": "active",
            "nexpa_sync_status": "pending",
        })
    except Exception:
        models.share_release(share.id)
        raise
    models.share_attach_visit(share.id, reg.id)

    send_to_nexpa(reg)
    try:
        from .notify import send_visit_alert
        send_visit_alert("%s (공유링크·방문자 직접등록)" % share.registrant_name,
                         share.dong, share.ho, car_number, visitor_phone,
                         share.visit_reason, entry_time.strftime("%Y-%m-%d"),
                         exit_time.strftime("%Y-%m-%d"))
    except Exception:
        pass

    return render_template("share/done.html", share=share, reg=reg,
                           car_number=car_number, entry_time=entry_time,
                           exit_time=exit_time, notice=notice)
