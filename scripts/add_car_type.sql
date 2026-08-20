-- parking: 방문차량 등록에 차량종류(car_type) 컬럼 추가 (2026-07-22)
-- 앱(service_role)이 INSERT, 에이전트(anon)가 select=* 로 읽음.
alter table public.parking_visit_registrations
  add column if not exists car_type varchar(60);

-- 에이전트(anon) 읽기 보장. 테이블 SELECT 권한이 이미 있으면 무해한 재확인.
grant select (car_type) on public.parking_visit_registrations to anon;
