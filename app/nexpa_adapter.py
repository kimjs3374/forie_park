"""nexpa(주차관제) 연동 어댑터.

[현재 상태] nexpa 연동 규격 미확정 → stub. 실제 전송은 하지 않고
전송대기(pending) 상태로 보관만 한다.

[연동 확정 시 채울 부분]
- 방향 B(권장): 우리는 '승인된 방문차량'을 합의된 인터페이스 테이블에 적재만 하고,
  nexpa가 그것을 읽어 자기 운영 테이블에 반영한다.
- 코콤 월패드 연동 규격이 확보되면 그 규격에 맞춰 send_to_nexpa()만 교체하면 된다.
- 연동 상태 갱신이 필요하면 models.visits_update(reg.id, {...}) 로 처리한다.
"""


def send_to_nexpa(registration):
    """방문차량 등록 1건을 nexpa로 전송(예정).

    현재는 미구현 stub. 호출해도 상태를 바꾸지 않고 그대로 둔다.
    연동 규격 확정 후 이 함수 본문만 구현하면 된다.
    """
    # TODO: nexpa 연동 규격 확정 후 구현
    #   ... nexpa 인터페이스 테이블 INSERT 또는 API 호출 ...
    #   models.visits_update(registration.id, {"nexpa_sync_status": "synced",
    #                                          "nexpa_synced_at": datetime.now(timezone.utc).isoformat()})
    return False  # 아직 전송하지 않음


def cancel_on_nexpa(registration):
    """nexpa 측 등록 취소(예정). 중복/수정/취소 규격은 nexpa와 협의 후 구현."""
    # TODO: nexpa 연동 규격 확정 후 구현
    return False
