"""데이터 모델 — Supabase REST 백엔드.

SQLAlchemy ORM 을 대체한다. 각 클래스는 REST 로 받은 dict 를 감싸
템플릿/뷰가 쓰던 속성(entry_time.strftime, duration_minutes, household_label 등)을
그대로 제공한다. DB 접근은 supabase_client(REST) 로만 한다.
"""
import re
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import login_manager
from . import supabase_client as sb

# 같은 Supabase 프로젝트를 forie_kids(놀이터)와 공유하므로 parking_ 프리픽스로 구분.
T_USERS = "parking_users"
T_VISITS = "parking_visit_registrations"


def make_password_hash(raw):
    return generate_password_hash(raw)


def _parse_dt(value):
    """Supabase timestamptz 문자열 → datetime. 실패 시 None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- User

class User(UserMixin):
    """입주민 / 관리사무소 계정. REST row(dict) 래퍼."""

    def __init__(self, row):
        self._row = row or {}

    # Flask-Login
    def get_id(self):
        return str(self._row.get("id"))

    @property
    def id(self):
        return self._row.get("id")

    @property
    def username(self):
        return self._row.get("username")

    @property
    def password_hash(self):
        return self._row.get("password_hash") or ""

    @property
    def name(self):
        return self._row.get("name")

    @property
    def phone(self):
        return self._row.get("phone")

    @property
    def dong(self):
        return self._row.get("dong")

    @property
    def ho(self):
        return self._row.get("ho")

    @property
    def role(self):
        return self._row.get("role") or "resident"

    @property
    def status(self):
        return self._row.get("status") or "pending"

    @property
    def created_at(self):
        return _parse_dt(self._row.get("created_at"))

    @property
    def approved_at(self):
        return _parse_dt(self._row.get("approved_at"))

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def must_change_password(self):
        # 컬럼이 없으면(마이그레이션 전) None → False 로 안전 처리
        return bool(self._row.get("must_change_password"))

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_approved(self):
        return self.status == "approved"

    @property
    def household_label(self):
        return f"{self.dong}동 {self.ho}호"


def users_get_by_id(user_id):
    row = sb.fetch_one(T_USERS, {"id": f"eq.{int(user_id)}"})
    return User(row) if row else None


def users_get_by_username(username):
    row = sb.fetch_one(T_USERS, {"username": f"eq.{username}"})
    return User(row) if row else None


def users_create(data):
    return User(sb.insert_row(T_USERS, data))


def users_update(user_id, data):
    return sb.patch_rows(T_USERS, data, {"id": f"eq.{int(user_id)}"})


def users_list(role=None, status=None):
    params = {"order": "created_at.desc"}
    if role:
        params["role"] = f"eq.{role}"
    if status:
        params["status"] = f"eq.{status}"
    return [User(r) for r in sb.fetch_rows(T_USERS, params)]


def users_count(role=None, status=None):
    params = {}
    if role:
        params["role"] = f"eq.{role}"
    if status:
        params["status"] = f"eq.{status}"
    return sb.count_rows(T_USERS, params)


def _norm_phone(value):
    """연락처 비교용 정규화(숫자만). 저장 포맷 차이(하이픈 등) 흡수."""
    return re.sub(r"\D", "", str(value or ""))


def users_find_by_identity(dong, ho, name, phone=None, username=None):
    """동/호/이름(+선택적으로 연락처/아이디) 일치 계정 목록.

    아이디찾기: phone 포함 4키. 비번찾기: username 까지 5키.
    연락처는 정규화(숫자만) 비교로 포맷 차이를 흡수한다.
    """
    params = {
        "dong": f"eq.{(dong or '').strip()}",
        "ho": f"eq.{(ho or '').strip()}",
        "name": f"eq.{(name or '').strip()}",
    }
    if username:
        params["username"] = f"eq.{username.strip().lower()}"
    rows = sb.fetch_rows(T_USERS, params)
    if phone is not None:
        target = _norm_phone(phone)
        rows = [r for r in rows if _norm_phone(r.get("phone")) == target]
    return [User(r) for r in rows]


# ---------------------------------------------------------- VisitRegistration

class VisitRegistration:
    """방문차량 등록. REST row(dict) 래퍼."""

    def __init__(self, row):
        self._row = row or {}

    @property
    def id(self):
        return self._row.get("id")

    @property
    def user_id(self):
        return self._row.get("user_id")

    @property
    def dong(self):
        return self._row.get("dong")

    @property
    def ho(self):
        return self._row.get("ho")

    @property
    def car_number(self):
        return self._row.get("car_number")

    @property
    def entry_time(self):
        return _parse_dt(self._row.get("entry_time"))

    @property
    def exit_time(self):
        return _parse_dt(self._row.get("exit_time"))

    @property
    def used_minutes(self):
        return self._row.get("used_minutes") or 0

    @property
    def status(self):
        return self._row.get("status") or "active"

    @property
    def nexpa_sync_status(self):
        return self._row.get("nexpa_sync_status") or "pending"

    @property
    def created_at(self):
        return _parse_dt(self._row.get("created_at"))

    @property
    def actual_in_time(self):
        return _parse_dt(self._row.get("actual_in_time"))

    @property
    def actual_out_time(self):
        return _parse_dt(self._row.get("actual_out_time"))

    @property
    def visit_state(self):
        return self._row.get("visit_state")  # None | entered | exited

    @property
    def nexpa_registered(self):
        return bool(self._row.get("nexpa_registered"))

    @property
    def user_name(self):
        """PostgREST 임베드(parking_users(name))로 함께 조회된 신청자 이름."""
        u = self._row.get(T_USERS)
        if isinstance(u, dict):
            return u.get("name")
        return None

    @property
    def duration_minutes(self):
        entry, exit_ = self.entry_time, self.exit_time
        if not entry or not exit_:
            return 0
        return int((exit_ - entry).total_seconds() // 60)


def visits_by_user(user_id, limit=None):
    params = {"user_id": f"eq.{int(user_id)}", "order": "created_at.desc"}
    if limit:
        params["limit"] = str(limit)
    return [VisitRegistration(r) for r in sb.fetch_rows(T_VISITS, params)]


def visits_get(reg_id):
    row = sb.fetch_one(T_VISITS, {"id": f"eq.{int(reg_id)}"})
    return VisitRegistration(row) if row else None


def visits_active_by_car(car_number):
    """같은 차량번호의 활성 등록 목록 (중복/연장 방지 검사용)."""
    params = {"car_number": f"eq.{car_number}", "status": "eq.active"}
    return [VisitRegistration(r) for r in sb.fetch_rows(T_VISITS, params)]


def visits_create(data):
    return VisitRegistration(sb.insert_row(T_VISITS, data))


def visits_update(reg_id, data):
    return sb.patch_rows(T_VISITS, data, {"id": f"eq.{int(reg_id)}"})


def visits_count(status=None):
    params = {}
    if status:
        params["status"] = f"eq.{status}"
    return sb.count_rows(T_VISITS, params)


def visits_filter(date_from=None, date_to=None, limit=None, with_user=False):
    """entry_time 기준 기간 필터(내림차순). with_user=True 면 신청자 이름 임베드."""
    from datetime import timedelta

    params = [("select", f"*,{T_USERS}(name)" if with_user else "*"),
              ("order", "entry_time.desc")]
    if date_from:
        params.append(("entry_time", f"gte.{date_from.isoformat()}"))
    if date_to:
        params.append(("entry_time", f"lt.{(date_to + timedelta(days=1)).isoformat()}"))
    if limit:
        params.append(("limit", str(limit)))
    return [VisitRegistration(r) for r in sb.fetch_rows(T_VISITS, params)]


# ---------------------------------------------------------- Popup(공지)
T_POPUPS = "parking_popups"


class Popup:
    def __init__(self, row):
        self._row = row or {}
    @property
    def id(self): return self._row.get("id")
    @property
    def title(self): return self._row.get("title")
    @property
    def content(self): return self._row.get("content")
    @property
    def is_active(self): return bool(self._row.get("is_active"))
    @property
    def start_date(self): return self._row.get("start_date")
    @property
    def end_date(self): return self._row.get("end_date")
    @property
    def created_at(self): return _parse_dt(self._row.get("created_at"))


def popups_all():
    return [Popup(r) for r in sb.fetch_rows(T_POPUPS, {"order": "created_at.desc"})]


def popups_get(pid):
    row = sb.fetch_one(T_POPUPS, {"id": f"eq.{int(pid)}"})
    return Popup(row) if row else None


def popups_create(data):
    return Popup(sb.insert_row(T_POPUPS, data))


def popups_update(pid, data):
    return sb.patch_rows(T_POPUPS, data, {"id": f"eq.{int(pid)}"})


def popups_delete(pid):
    return sb.delete_rows(T_POPUPS, {"id": f"eq.{int(pid)}"})


def popups_active_now(today):
    today_s = today.isoformat() if hasattr(today, "isoformat") else str(today)
    rows = sb.fetch_rows(T_POPUPS, {"is_active": "eq.true", "order": "created_at.desc"})
    out = []
    for r in rows:
        sd, ed = r.get("start_date"), r.get("end_date")
        if sd and sd > today_s:
            continue
        if ed and ed < today_s:
            continue
        out.append(Popup(r))
    return out


@login_manager.user_loader
def load_user(user_id):
    try:
        return users_get_by_id(user_id)
    except Exception:
        return None
