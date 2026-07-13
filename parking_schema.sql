-- 주차예약시스템(parking) Supabase Postgres 스키마
-- forie_kids(놀이터)와 동일 프로젝트에 공존 → parking_ 프리픽스로 구분.
-- 앱은 service_role 키로 REST(PostgREST) 접근 → RLS 우회. RLS enable 은
-- anon/authenticated 키로의 직접 노출을 차단하는 안전장치(서버는 service_role 이라 영향 없음).
-- Supabase 대시보드 → SQL Editor 에 붙여넣고 Run.

create table if not exists public.parking_users (
    id            bigint generated always as identity primary key,
    username      varchar(50)  not null unique,
    password_hash varchar(255) not null,
    name          varchar(50)  not null,
    phone         varchar(20),
    dong          varchar(10)  not null,
    ho            varchar(10)  not null,
    role          varchar(20)  not null default 'resident',   -- resident | admin
    status        varchar(20)  not null default 'pending',    -- pending | approved | rejected
    created_at    timestamptz  not null default now(),
    approved_at   timestamptz,
    approved_by   bigint references public.parking_users(id),
    must_change_password boolean not null default false  -- 임시비번 발급 시 true → 첫 로그인 강제 변경
);
create index if not exists idx_parking_users_username on public.parking_users(username);

create table if not exists public.parking_households (
    id                   bigint generated always as identity primary key,
    dong                 varchar(10) not null,
    ho                   varchar(10) not null,
    monthly_free_minutes integer     not null default 12000,
    created_at           timestamptz not null default now(),
    constraint uq_parking_household_dong_ho unique (dong, ho)
);

create table if not exists public.parking_visit_registrations (
    id                bigint generated always as identity primary key,
    user_id           bigint      not null references public.parking_users(id),
    dong              varchar(10) not null,
    ho                varchar(10) not null,
    car_number        varchar(20) not null,
    entry_time        timestamptz not null,
    exit_time         timestamptz not null,
    used_minutes      integer     not null default 0,
    status            varchar(20) not null default 'active',   -- active | cancelled
    nexpa_sync_status varchar(20) not null default 'pending',  -- pending | synced | failed
    nexpa_synced_at   timestamptz,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);
create index if not exists idx_parking_visit_user on public.parking_visit_registrations(user_id);
create index if not exists idx_parking_visit_car  on public.parking_visit_registrations(car_number);

alter table public.parking_users               enable row level security;
alter table public.parking_households          enable row level security;
alter table public.parking_visit_registrations enable row level security;
-- 정책 없음 = anon/authenticated 접근 전면 차단, service_role(서버) 만 접근.
