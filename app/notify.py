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


def send_signup_alert(name, dong, ho, phone, username):
    """입주민 신규 가입 신청을 관리사무소에 알린다."""
    text = (
        "🅿️ 주차 신규 가입 신청\n\n"
        f"· 이름: {name}\n"
        f"· 동/호: {dong}동 {ho}호\n"
        f"· 연락처: {phone or '-'}\n"
        f"· 아이디: {username}\n\n"
        "관리자 페이지에서 승인해주세요."
    )
    return _send(text)
