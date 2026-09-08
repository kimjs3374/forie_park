import os

from dotenv import load_dotenv

load_dotenv()


def _bool_env(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me-in-production")

    # DB는 Supabase REST(PostgREST) 사용 — forie_kids 와 동일 프로젝트/방식.
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    # 세대별 월 무료 주차시간 (분) — 200시간 = 12000분. 차감 로직은 추후 구현.
    DEFAULT_MONTHLY_FREE_MINUTES = int(os.environ.get("DEFAULT_MONTHLY_FREE_MINUTES", 12000))

    # 관리사무소 알림용 텔레그램 (신규 가입 신청 등)
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

    # 통합 로그인(SSO) 서명키 — 세 앱이 동일한 값을 쓴다. SECRET_KEY 와는 다른 값이어야
    # 한다(세션 서명키를 공유하면 앱 간 세션 위조가 가능해진다).
    SSO_SECRET = os.environ.get("SSO_SECRET", "")

    # 카카오 로그인 (REST API 키 + 클라이언트 시크릿)
    # 시크릿은 카카오 콘솔에서 기본 활성화 상태로 발급된다. 켜져 있으면 토큰 요청에
    # 반드시 함께 보내야 하며, 빠뜨리면 KOE010(Bad client credentials)이 난다.
    KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "")
    KAKAO_CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "")

    # 경비실 차량조회 공용 PIN. 경비원에게는 계정이 없어 이 값 하나로 화면을 연다.
    # 비어 있으면 조회 화면 자체가 열리지 않는다(기본값으로 뚫려 있으면 안 되므로).
    LOOKUP_PIN = os.environ.get("LOOKUP_PIN", "")
    # PIN 을 한 번 넣으면 이 시간만큼 그 단말에서 다시 묻지 않는다(교대 근무 1회분).
    LOOKUP_SESSION_HOURS = int(os.environ.get("LOOKUP_SESSION_HOURS", 12))

    # 세션 쿠키 보안 (Cloudflare 뒤 HTTPS 전용)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = _bool_env("SESSION_COOKIE_SECURE", True)
