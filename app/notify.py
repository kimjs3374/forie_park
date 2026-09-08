"""관리사무소 알림 (텔레그램).

전송 실패가 본래 기능(가입 등)을 막지 않도록 항상 예외를 삼킨다.
"""
import requests
from flask import current_app


def _send(text):
    token = (current_app.config.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (current_app.config.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return bool(resp.ok)
    except Exception:
        current_app.logger.exception("텔레그램 알림 전송 실패")
        return False


def send_signup_alert(name, dong, ho, phone, username, verified=None):
    """입주민 신규 가입 신청을 관리사무소에 알린다.
    verified: True=명부일치 자동승인, False=명부불일치 수동확인, None=대조 안함."""
    if verified is True:
        head = "🅿️ 주차 신규 가입 (명부 일치 → 자동승인 ✅)\n\n"
        foot = "\n\n명부와 일치하여 자동 승인되었습니다."
    elif verified is False:
        head = "🅿️ 주차 신규 가입 (⚠️ 명부 불일치 — 수동확인 필요)\n\n"
        foot = "\n\n명부에 없는 세대입니다. 관리자 페이지에서 확인 후 승인해주세요."
    else:
        head = "🅿️ 주차 신규 가입 신청\n\n"
        foot = "\n\n관리자 페이지에서 승인해주세요."
    text = (
        head +
        f"· 이름: {name}\n"
        f"· 동/호: {dong}동 {ho}호\n"
        f"· 연락처: {phone or '-'}\n"
        f"· 아이디: {username}" + foot
    )
    return _send(text)


def send_visit_alert(name, dong, ho, car_number, phone, reason, book_start, book_end):
    """방문차량 등록을 관리사무소에 알린다."""
    text = (
        "🚗 방문차량 등록\n\n"
        f"· 신청자: {name} ({dong}동 {ho}호)\n"
        f"· 차량번호: {car_number}\n"
        f"· 방문자 연락처: {phone or '-'}\n"
        f"· 방문사유: {reason or '-'}\n"
        f"· 방문기간: {book_start} ~ {book_end}"
    )
    return _send(text)


def send_overuse_alert(period, rows, limit):
    """실주차일수 한도를 넘은 차량을 관리사무소에 알린다.

    rows 는 usage.scan_overuse()["rows"] 중 아직 알리지 않은 것만 걸러 넘긴다
    (정기등록 차량은 이미 빠져 있다).
    """
    if not rows:
        return False
    lines = ["⚠️ 실주차일수(숙박) %d일 초과 (%s)" % (limit, period), ""]
    for r in rows:
        tag = ""
        if not r.get("registered"):
            tag += " [미등록]"       # 세대호출·경비실 호출로 입차
        if r.get("open_in"):
            tag += " [미출차]"       # 관제 누락 가능 — 확인 필요
        lines.append("· %s — %d박 (한도 +%d) / %s%s"
                     % (r["car_number"], r["days"], r["over"], r["households"], tag))
    lines.append("")
    lines.append("밤 8시~다음날 오전 7시 사이 30분 초과 주차를 1일로 셉니다.")
    lines.append("정기등록 차량은 제외한 목록입니다.")
    lines.append("[미출차]는 출차 로그가 없어 주차중으로 계산된 차량이라 확인이 필요합니다.")
    lines.append("관리자 페이지 > 실주차일수 초과 차량 에서 상세를 확인하세요.")
    return _send("\n".join(lines))
