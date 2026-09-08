-- 정기등록 차량 구분(입주민/택배/협력사…) 추가 · 2026-09-08
-- parking_usage_limit.sql 의 후속. 대시보드 SQL Editor 에서 Run.
--
-- 왜: 초과 알림 제외 목록에서 "이 차가 입주민인지 택배인지 협력사인지, 몇동 몇호인지"를
--     가릴 수 있어야 한다. 넥스파 season_car_group_id 를 에이전트가 라벨까지 붙여 올린다.
--     동/호는 원천에 있는 그룹만 채워진다(실측: 입주민 469/469 · 전기차 24/24 ·
--     협력사 103중 14 · 택배 88중 11 · 관리사무소 0). 없는 건 비워 둔다.

alter table public.parking_regular_cars
    add column if not exists group_id   smallint,
    add column if not exists group_name varchar(20);

comment on column public.parking_regular_cars.group_id   is '넥스파 season_car_group_id (1입주민/4전기차/8협력사·도우미/9·13관리사무소/10택배). 11방문차량은 애초에 올라오지 않는다';
comment on column public.parking_regular_cars.group_name is '위 id 의 한글 라벨 — 에이전트 SEASON_GROUP_NAMES';

-- 진단용으로 들어갔던 시험행이 남아 있으면 제거
delete from public.parking_regular_cars
 where car_number like '00테스트%' or car_number like '00진단%';

notify pgrst, 'reload schema';

select column_name, data_type
  from information_schema.columns
 where table_name = 'parking_regular_cars'
 order by ordinal_position;
