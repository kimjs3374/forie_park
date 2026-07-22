-- parking_users → forie_users 로 rename
--
-- 목적: 방문차량 전용이던 회원 테이블을 forie 통합 계정(main/kids/parking 공용)으로 승격.
-- 실행 위치: Supabase 대시보드 → SQL Editor
--
-- ⚠️ 실행 직후 parking 앱을 재시작해야 한다. 코드(app/models.py)가 forie_users 를 참조하도록
--    이미 바뀌어 있고, gunicorn 은 --reload 가 아니라 restart 전까지 옛 코드로 동작한다.
--    즉 "SQL 실행 → systemctl restart parking" 이 한 묶음이다.

alter table public.parking_users rename to forie_users;

-- FK(parking_visit_registrations.user_id), 제약, RLS 정책은 rename 을 자동으로 따라간다.
-- 인덱스는 이름만 옛 것이 남으므로 함께 정리한다(동작에는 영향 없음).
alter index if exists public.idx_parking_users_username rename to idx_forie_users_username;

-- PostgREST 스키마 캐시 갱신 (필수)
-- 이걸 빠뜨리면 REST API 가 새 테이블 이름을 인식하지 못해 404 가 난다.
notify pgrst, 'reload schema';


-- ---------------------------------------------------------------------------
-- 롤백이 필요하면 아래를 실행한다 (주석 해제)
-- ---------------------------------------------------------------------------
-- alter table public.forie_users rename to parking_users;
-- alter index if exists public.idx_forie_users_username rename to idx_parking_users_username;
-- notify pgrst, 'reload schema';
