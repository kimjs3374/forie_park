import threading

from app import create_app

app = create_app()


def _prewarm():
    """관리 화면 집계를 워커가 뜨자마자 한 번 데워 둔다.

    캐시가 비어 있으면 그날 처음 들어온 관리자가 집계를 통째로 기다린다.
    관리자는 하루에 몇 번 들어오지 않아 거의 매번 그 사람이 당첨되므로,
    사람이 아니라 기동 직후의 유휴 시간에 치르게 한다.

    배치 스크립트는 app 패키지를 직접 임포트하므로 이 파일을 타지 않는다 —
    크론이 돌 때마다 쓸데없이 두 집계를 더 돌리는 일은 없다.
    """
    from app import analytics, usage
    with app.app_context():
        for name, fn in (("의심세대", analytics.scan_cached),
                         ("실주차일수", usage.scan_overuse_cached)):
            try:
                fn()
            except Exception:
                app.logger.exception("기동 시 %s 집계 준비 실패", name)


# 요청을 받기 전에 백그라운드로 시작한다. 실패해도 화면은 그때 계산해서 뜬다.
threading.Thread(target=_prewarm, name="prewarm", daemon=True).start()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
