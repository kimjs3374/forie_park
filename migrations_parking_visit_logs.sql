-- 입출차 로그 테이블: 한 방문등록에서 발생한 개별 입/출차 이벤트를 시간순 누적.
-- 넥스파 관제 에이전트가 이벤트 감지 시마다 REST 로 append(insert). 앱은 읽어 관리자 화면에 타임라인 표시.
-- Supabase 대시보드 → SQL Editor 에 붙여넣고 Run.
create table if not exists public.parking_visit_logs (
    id              bigint generated always as identity primary key,
    registration_id bigint      not null references public.parking_visit_registrations(id) on delete cascade,
    car_number      varchar(20) not null,
    event_type      varchar(10) not null,                    -- in | out
    event_time      timestamptz not null,                    -- 실제 입/출차 발생 시각
    source          varchar(20) not null default 'nexpa', -- nexpa | manual
    raw             jsonb,                                    -- 관제 원시 이벤트(선택)
    created_at      timestamptz not null default now()
);
create index if not exists idx_parking_visit_logs_reg  on public.parking_visit_logs(registration_id);
create index if not exists idx_parking_visit_logs_car  on public.parking_visit_logs(car_number);
create index if not exists idx_parking_visit_logs_time on public.parking_visit_logs(event_time);

-- service_role(서버/에이전트)만 접근. anon/authenticated 직접 노출 차단.
alter table public.parking_visit_logs enable row level security;
