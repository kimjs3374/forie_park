"""데이터 모델 — Supabase REST 백엔드.

SQLAlchemy ORM 을 대체한다. 각 클래스는 REST 로 받은 dict 를 감싸
템플릿/뷰가 쓰던 속성(entry_time.strftime, duration_minutes, household_label 등)을
그대로 제공한다. DB 접근은 supabase_client(REST) 로만 한다.
"""
import re
from datetime import datetime, timezone, timedelta

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import login_manager
from . import supabase_client as sb

# 같은 Supabase 프로젝트를 forie_kids(놀이터)와 공유하므로 parking 전용 테이블은
# parking_ 프리픽스로 구분한다. 단 forie_users 는 main/kids/parking 이 공유하는
# 통합 계정 테이블이라 프리픽스가 없다.
T_USERS = "forie_users"
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
        # 소셜 전용 계정은 password_hash 가 비어 있다 → 로컬 로그인 불가
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw)

    @property
    def must_change_password(self):
        # 컬럼이 없으면(마이그레이션 전) None → False 로 안전 처리
        return bool(self._row.get("must_change_password"))

    @property
    def provider(self):
        """계정이 만들어진 경로. local | kakao | google"""
        return self._row.get("provider") or "local"

    @property
    def provider_uid(self):
        return self._row.get("provider_uid")

    @property
    def has_password(self):
        """로컬 로그인(아이디/비번)이 가능한 계정인지."""
        return bool(self.password_hash)

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


def users_household_active(dong, ho):
    """해당 세대(동/호)의 반려되지 않은 계정 목록(pending+approved). 세대당 인원/중복 검사용."""
    params = {
        "dong": f"eq.{(dong or '').strip()}",
        "ho": f"eq.{(ho or '').strip()}",
        "status": "in.(pending,approved)",
    }
    return [User(r) for r in sb.fetch_rows(T_USERS, params)]


HOUSEHOLD_OK = "ok"
HOUSEHOLD_DUPLICATE = "duplicate"   # 같은 세대에 같은 이름이 이미 있음
HOUSEHOLD_FULL = "full"             # 세대당 2계정 한도 초과


def household_check(dong, ho, name):
    """세대 가입 가능 여부 판정 → (상태, 기존계정|None).

    duplicate 는 차단 사유가 아니라 "본인일 가능성"이다. 소셜 가입에서는
    기존 계정에 소셜을 연결하도록 유도하는 분기로 쓴다.
    """
    household = users_household_active(dong, ho)
    target = (name or "").strip()
    for u in household:
        if (u.name or "").strip() == target:
            return HOUSEHOLD_DUPLICATE, u
    if len(household) >= 2:
        return HOUSEHOLD_FULL, None
    return HOUSEHOLD_OK, None


def users_get_by_provider(provider, provider_uid):
    """소셜 계정(provider + 고유번호)에 연결된 계정."""
    row = sb.fetch_one(T_USERS, {
        "provider": f"eq.{provider}",
        "provider_uid": f"eq.{provider_uid}",
    })
    return User(row) if row else None


def users_link_provider(user_id, provider, provider_uid):
    """기존 계정에 소셜 계정을 연결한다."""
    return users_update(user_id, {
        "provider": provider,
        "provider_uid": str(provider_uid),
        "linked_at": _utcnow_iso(),
    })


def make_social_username(provider, provider_uid):
    """소셜 전용 계정의 username.

    username 이 NOT NULL UNIQUE 라 값이 필요하다. 비밀번호가 없으므로
    이 아이디로 로컬 로그인은 되지 않는다(check_password 가 False).
    """
    return f"{provider}_{provider_uid}"


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
        """PostgREST 임베드(forie_users(name))로 함께 조회된 신청자 이름."""
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


# ---------------------------------------------------------- VisitLog(입출차 로그)
# 한 방문등록(registration)에서 발생한 개별 입/출차 이벤트를 시간순으로 누적한다.
# 넥스파 관제 에이전트가 관제 DB 이벤트를 감지할 때마다 이 테이블에 append(REST insert)한다.
# 앱은 읽어서 관리자 화면에 타임라인으로 표시한다.
T_LOGS = "parking_visit_logs"


class VisitLog:
    """개별 입/출차 이벤트. REST row(dict) 래퍼."""

    def __init__(self, row):
        self._row = row or {}

    @property
    def id(self):
        return self._row.get("id")

    @property
    def registration_id(self):
        return self._row.get("registration_id")

    @property
    def car_number(self):
        return self._row.get("car_number")

    @property
    def event_type(self):
        return self._row.get("event_type")  # in | out

    @property
    def is_in(self):
        return self._row.get("event_type") == "in"

    @property
    def event_time(self):
        return _parse_dt(self._row.get("event_time"))

    @property
    def source(self):
        return self._row.get("source") or "nexpa"

    @property
    def event_time_kst(self):
        """표시용 KST(naive) 변환. tz 정보 없으면 그대로."""
        t = self.event_time
        if not t:
            return None
        if t.tzinfo is None:
            return t
        return (t.astimezone(timezone.utc) + timedelta(hours=9)).replace(tzinfo=None)


def visit_logs_by_reg(reg_id):
    """한 등록의 입출차 로그(시간 오름차순). 테이블 미생성/조회실패 시 빈 리스트."""
    params = {"registration_id": f"eq.{int(reg_id)}", "order": "event_time.asc,id.asc"}
    try:
        return [VisitLog(r) for r in sb.fetch_rows(T_LOGS, params)]
    except Exception:
        return []


def visit_logs_by_regs(reg_ids):
    """여러 등록의 로그를 한 번에 조회 -> {registration_id: [VisitLog, ...]} (시간 오름차순)."""
    ids = [int(i) for i in reg_ids if i is not None]
    if not ids:
        return {}
    id_csv = ",".join(str(i) for i in ids)
    params = {"registration_id": f"in.({id_csv})",
              "order": "event_time.asc,id.asc"}
    out = {}
    try:
        rows = sb.fetch_rows(T_LOGS, params)
    except Exception:
        return {}  # 테이블 미생성 등 → 로그 없음으로 처리(화면 정상 유지)
    for r in rows:
        out.setdefault(r.get("registration_id"), []).append(VisitLog(r))
    return out


def visit_logs_create(data):
    return VisitLog(sb.insert_row(T_LOGS, data))


def summarize_logs(logs, now=None):
    """입/출 이벤트 리스트 -> 화면용 요약.

    in/out 을 순서대로 페어링하여 누적 주차시간(분)과 현재 주차중 여부를 계산한다.
    미완결 in(출차 로그가 아직 없음)은 now 까지를 잠정 체류로 본다.
    반환: {"logs": logs, "total_minutes": int, "parked_now": bool, "pairs": [(in, out|None), ...]}
    """
    pairs = []
    open_in = None
    for lg in logs:
        if lg.is_in:
            if open_in is not None:
                pairs.append((open_in, None))  # 짝 없는 입차(비정상) 그대로 노출
            open_in = lg
        else:  # out
            pairs.append((open_in, lg))  # open_in 이 None 이면 (None, out) = 짝 없는 출차
            open_in = None
    parked_now = open_in is not None
    if parked_now:
        pairs.append((open_in, None))

    total = 0
    for i_ev, o_ev in pairs:
        if not i_ev:
            continue
        end = o_ev.event_time if o_ev else now
        if i_ev.event_time and end:
            total += max(0, int((end - i_ev.event_time).total_seconds() // 60))
    ins = [lg.event_time for lg in logs if lg.is_in and lg.event_time]
    outs = [lg.event_time for lg in logs if (not lg.is_in) and lg.event_time]
    first_in = min(ins) if ins else None
    last_out = None if parked_now else (max(outs) if outs else None)
    latest_in = max(ins) if ins else None
    h, m = divmod(total, 60)
    total_text = (f"{h}시간 " if h else "") + f"{m}분"
    return {"logs": logs, "total_minutes": total, "total_text": total_text,
            "parked_now": parked_now, "pairs": pairs,
            "first_in": first_in, "last_out": last_out, "latest_in": latest_in}


# ---------------------------------------------------------- Popup(공지)
def visit_dispmap(regs, now=None, logs_map=None):
    """등록 리스트 -> {reg_id: (in_iso, out_iso)} 표시용 입/출차.
    parking_visit_logs 타임라인 있으면 '첫 입차 -> 마지막 출차'(주차중이면 out 비움),
    없으면 기존 단일필드(actual_in/out_time)로 폴백. 출차시간 기준 주차시간 계산용."""
    now = now or datetime.now(timezone.utc)
    if logs_map is None:
        logs_map = visit_logs_by_regs([r.id for r in regs])
    result = {}
    for r in regs:
        logs = logs_map.get(r.id, [])
        if logs:
            sm = summarize_logs(logs, now=now)
            fi, lo = sm["latest_in"], sm["last_out"]
            result[r.id] = (fi.isoformat() if fi else "", lo.isoformat() if lo else "")
        else:
            result[r.id] = (r.actual_in_time.isoformat() if r.actual_in_time else "",
                            r.actual_out_time.isoformat() if r.actual_out_time else "")
    return result


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


# ---------------------------------------------------------- Resident Directory(입주민 명부)
# 실내놀이터/방문차량 공용 세대 명부. 동/호/이름 3키로 자동승인 매칭.
import unicodedata

T_DIRECTORY = "resident_directory"


def directory_match_key(dong, ho, name):
    """SQL directory_match_key() 와 동일 규칙의 파이썬 구현.
    업로드 파일 내 중복제거용. 런타임 매칭의 진실원천은 SQL check_resident 이다."""
    d = re.sub(r"\D", "", str(dong or ""))
    h = re.sub(r"\D", "", str(ho or ""))
    n = re.sub(r"\s", "", unicodedata.normalize("NFC", str(name or ""))).lower()
    return f"{d}|{h}|{n}"


class ResidentEntry:
    def __init__(self, row):
        self._row = row or {}

    @property
    def id(self):
        return self._row.get("id")

    @property
    def dong(self):
        return self._row.get("dong")

    @property
    def ho(self):
        return self._row.get("ho")

    @property
    def name(self):
        return self._row.get("name")

    @property
    def batch_id(self):
        return self._row.get("batch_id")

    @property
    def created_at(self):
        return _parse_dt(self._row.get("created_at"))

    @property
    def household_label(self):
        return f"{self.dong}동 {self.ho}호"


def directory_count():
    return sb.count_rows(T_DIRECTORY, {"is_active": "eq.true"})


def directory_list(query=None, dong=None, limit=2000):
    params = [("is_active", "eq.true"),
              ("order", "dong.asc,ho.asc,name.asc"),
              ("limit", str(limit))]
    if dong:
        params.append(("dong", f"eq.{dong.strip()}"))
    if query:
        q = query.strip()
        params.append(("or", f"(dong.ilike.*{q}*,ho.ilike.*{q}*,name.ilike.*{q}*)"))
    return [ResidentEntry(r) for r in sb.fetch_rows(T_DIRECTORY, params)]


def directory_dong_summary():
    """동별 세대원 수 요약 → [[dong, count], ...] (동 숫자 오름차순)."""
    from collections import Counter
    rows = sb.fetch_rows(T_DIRECTORY, [("select", "dong"), ("is_active", "eq.true"), ("limit", "10000")])
    counter = Counter((r.get("dong") or "").strip() for r in rows if (r.get("dong") or "").strip())

    def _key(d):
        return (0, int(d)) if d.isdigit() else (1, d)

    return sorted(([d, n] for d, n in counter.items()), key=lambda x: _key(x[0]))


def directory_add(dong, ho, name, batch_id=None):
    data = {"dong": str(dong).strip(), "ho": str(ho).strip(),
            "name": str(name).strip(), "batch_id": batch_id}
    return ResidentEntry(sb.insert_row(T_DIRECTORY, data))


def directory_delete(entry_id):
    return sb.delete_rows(T_DIRECTORY, {"id": f"eq.{int(entry_id)}"})


def directory_replace_all(entries, batch_id):
    """명부 전체 교체: 기존 전부 삭제 후 새 목록 일괄 삽입.
    entries: [(dong, ho, name), ...] (파일 내 중복제거 완료 상태). 반환: 삽입행 수."""
    sb.delete_rows(T_DIRECTORY, {"id": "gt.0"})
    rows = [{"dong": d, "ho": h, "name": n, "batch_id": batch_id} for (d, h, n) in entries]
    inserted = 0
    for i in range(0, len(rows), 500):
        inserted += len(sb.insert_rows(T_DIRECTORY, rows[i:i + 500]))
    return inserted


def directory_delete_by_identity(dong, ho, name):
    """동/호/이름(정규화 match_key) 일치하는 명부 항목 삭제. 이사·탈퇴 연동용."""
    key = directory_match_key(dong, ho, name)
    return sb.delete_rows(T_DIRECTORY, {"match_key": f"eq.{key}"})


def directory_merge(entries, batch_id):
    """명부 보완(병합): 기존 유지, DB에 없는 세대원만 추가. 반환 (added, skipped_exist).
    entries: [(dong, ho, name), ...] (파일 내 중복제거 완료)."""
    existing = set()
    for r in sb.fetch_rows(T_DIRECTORY, [("select", "match_key"), ("is_active", "eq.true"), ("limit", "100000")]):
        k = r.get("match_key") or ""
        if k:
            existing.add(k)
    rows, skipped = [], 0
    for (d, h, n) in entries:
        if directory_match_key(d, h, n) in existing:
            skipped += 1
            continue
        rows.append({"dong": d, "ho": h, "name": n, "batch_id": batch_id})
    added = 0
    for i in range(0, len(rows), 500):
        added += len(sb.insert_rows(T_DIRECTORY, rows[i:i + 500]))
    return added, skipped


def check_resident_match(dong, ho, name):
    """SQL check_resident RPC 호출 → 명부 존재 여부(bool)."""
    return bool(sb.rpc("check_resident", {
        "p_dong": (dong or "").strip(),
        "p_ho": (ho or "").strip(),
        "p_name": (name or "").strip(),
    }))
