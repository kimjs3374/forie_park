#!/usr/bin/env python
"""실주차일수 한도 초과 차량 알림 배치.

크론이 하루 한 번 호출한다. 이번 달 실주차일수(관제 입출차 로그 기준)가 한도를
넘은 차량 중 정기등록 차량을 뺀 나머지를 관리사무소 텔레그램으로 알린다.
같은 차량을 매일 다시 알리지 않도록 (차량번호, 해당월)로 한 번만 보낸다.

전송에 실패하면 이력을 남기지 않아 다음 회차에 다시 시도한다.

사용:  cd /web/parking && ./venv/bin/python scripts/notify_overuse.py
크론:  0 0 * * * cd /web/parking && ./venv/bin/python scripts/notify_overuse.py >> /tmp/parking_overuse.log 2>&1
       (서버 시계가 UTC 라 UTC 00:00 = KST 09:00)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, models, usage          # noqa: E402
from app.notify import send_overuse_alert          # noqa: E402


def main():
    report = usage.scan_overuse()
    period, rows = report["period"], report["rows"]

    # 시행 전에는 집계만 하고 알리지 않는다(관리자 화면에서 미리보기로만 본다).
    if not report["enforced"]:
        print("[%s] 시행 전(%s 부터) — 알림 보류. 현재 초과 %d대"
              % (period, report["enforce_from"], len(rows)))
        return

    already = models.overuse_alert_keys(period)
    fresh = [r for r in rows if r["car_number"] not in already]

    if not fresh:
        print("[%s] 신규 초과 차량 없음 (초과 %d대 / 정기등록 제외 %d대)"
              % (period, len(rows), len(report["excluded"])))
        return

    if not send_overuse_alert(period, fresh, report["limit"]):
        print("[%s] 알림 전송 실패 — 이력 남기지 않음, 다음 회차 재시도 (%d대)"
              % (period, len(fresh)))
        return

    for r in fresh:
        try:
            models.overuse_alert_add(r["car_number"], period, r["days"])
        except Exception as e:      # 이력 기록 실패는 중복 알림일 뿐이라 멈추지 않는다
            print("  이력 기록 실패 %s: %s" % (r["car_number"], e))
    print("[%s] 알림 발송 %d대: %s"
          % (period, len(fresh), ", ".join(r["car_number"] for r in fresh)))


if __name__ == "__main__":
    with create_app().app_context():
        main()
