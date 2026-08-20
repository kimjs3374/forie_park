"""parking 방문차량 세대 공유 검증(읽기만)."""
import sys

sys.path.insert(0, "/web/parking")
from app import create_app, models
from app.forie_auth import COOKIE_NAME, issue_token
from app import supabase_client as sb

app = create_app()

with app.app_context():
    users = [u for u in sb.fetch_rows("forie_users", {
        "select": "id,name,dong,ho,status", "limit": "5000"}) if u["status"] == "approved"]
    by_unit = {}
    for u in users:
        by_unit.setdefault((u["dong"], u["ho"]), []).append(u)
    visits = sb.fetch_rows("parking_visit_registrations",
                           {"select": "id,user_id,dong,ho", "limit": "2000"})
    units_with_visits = {(v["dong"], v["ho"]) for v in visits}

    target = next((k for k in units_with_visits if len(by_unit.get(k, [])) > 1), None)
    if not target:
        target = next(iter(sorted(units_with_visits))) if units_with_visits else None
        print("(계정 2개인 세대에 방문차량 기록이 없어 단일 계정 세대로 확인합니다)")
    dong, ho = target
    members = by_unit.get((dong, ho), [])
    print(f"검증 세대: {dong}동 {ho}호 / 계정 {[m['name'] for m in members]}")

    regs = models.visits_by_household(dong, ho)
    print(f"  세대 조회 결과: {len(regs)}건",
          [(r.car_number, r.user_name) for r in regs[:5]])

    # 계정별 기존 방식(user_id) 과 비교
    for m in members:
        own = models.visits_by_user(m["id"])
        print(f"  {m['name']:10s} 본인 등록분 {len(own)}건 → 세대 공유 후 {len(regs)}건")

    other = next(((d, h) for (d, h) in units_with_visits if (d, h) != (dong, ho)), None)
    if other:
        print(f"  다른 세대({other[0]}-{other[1]}) 조회는 별개인지:",
              len(models.visits_by_household(*other)), "건")
