-- 실주차일수 월 한도(차량당 10일) + 정기등록 차량 동기화 (2026-09-08)
-- Supabase 대시보드 → SQL Editor 에 붙여넣고 Run.
--
-- 실주차일수 자체는 이미 있는 parking_visit_logs 로 계산하므로 새 컬럼이 필요 없다.
-- 여기서 만드는 것은 두 가지뿐이다.
--   1) parking_regular_cars   — 넥스파 관제 DB의 정기(월주차) 차량 거울. 초과 알림에서 제외할 명단.
--   2) parking_overuse_alerts — 초과 알림 발송 이력. 같은 차량을 매일 다시 알리지 않기 위한 것.

-- ---------------------------------------------------------------- 정기등록 차량
create table if not exists public.parking_regular_cars (
    car_number  varchar(20)  primary key,          -- 앱 저장 포맷과 동일(공백·기호 없음)
    owner_name  varchar(50),
    dong        varchar(10),
    ho          varchar(10),
    valid_from  date,
    valid_to    date,                              -- null = 무기한
    is_active   boolean      not null default true, -- 관제에서 사라진 차량은 에이전트가 false 로
    source      varchar(20)  not null default 'nexpa',
    raw         jsonb,                             -- 관제 원본(디버그용)
    synced_at   timestamptz  not null default now()
);
create index if not exists idx_parking_regular_active on public.parking_regular_cars(is_active, valid_to);

-- ---------------------------------------------------------------- 초과 알림 이력
create table if not exists public.parking_overuse_alerts (
    id          bigint generated always as identity primary key,
    car_number  varchar(20) not null,
    period      varchar(7)  not null,              -- YYYY-MM
    days        integer     not null,
    notified_at timestamptz not null default now(),
    constraint uq_parking_overuse_car_period unique (car_number, period)
);

alter table public.parking_regular_cars   enable row level security;
alter table public.parking_overuse_alerts enable row level security;
-- 정책 없음 = anon/authenticated 전면 차단. 앱(service_role)만 접근.

-- ---------------------------------------------------------------- 관제 에이전트 권한
-- 에이전트는 anon(publishable) 키를 쓴다(4장 원칙: service_role 금지).
-- 정기등록 차량만 upsert 할 수 있게 열고, SELECT 는 열지 않는다(차량번호=개인정보).
-- → 에이전트는 반드시 `Prefer: return=minimal, resolution=merge-duplicates` 로 보낼 것.
--   return=representation 이면 되읽기 SELECT 단계에서 401 이 난다(9장 함정과 같은 이유).
--
-- 관제에서 빠진 차량은 에이전트가 지우지 않는다. 매 회차 전량을 upsert 해
-- synced_at 만 갱신하면 앱이 뒤처진 행을 빠진 것으로 본다(models.REGULAR_STALE_DAYS).
-- 조건부 UPDATE/DELETE 를 시키면 WHERE 절 컬럼에 SELECT 권한이 필요해져
-- 차량번호 테이블을 anon 에 열어야 하기 때문이다. INSERT/UPDATE 만으로 충분하다.
grant insert, update on public.parking_regular_cars to anon;

drop policy if exists agent_insert_regular on public.parking_regular_cars;
create policy agent_insert_regular on public.parking_regular_cars
    for insert to anon with check (true);

drop policy if exists agent_update_regular on public.parking_regular_cars;
create policy agent_update_regular on public.parking_regular_cars
    for update to anon using (true) with check (true);

-- PostgREST 스키마 캐시 갱신 (반영이 안 되면 이 줄만 다시 실행)
notify pgrst, 'reload schema';
