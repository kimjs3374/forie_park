-- 경비실 차량조회 로그 + 입주민 임시 등록권한 공유링크
-- Supabase 대시보드 → SQL Editor 에 붙여넣고 Run.
-- 앱은 service_role 키로만 접근한다. RLS 는 anon/authenticated 직접 노출 차단용.

-- ── 1. 경비실 차량조회 로그 ────────────────────────────────────────────────
-- 공용 PIN 방식이라 "누가" 조회했는지는 계정으로 남지 않는다. 대신 조회 시각을
-- 남겨 두고 근무자 배치표와 대조해 조회자를 특정한다. 그래서 시각·검색어·결과수는
-- 반드시 남아야 하며, 실패한 PIN 시도도 같은 표에 기록한다(무단 접근 흔적).
create table if not exists public.parking_lookup_logs (
    id           bigint generated always as identity primary key,
    query        varchar(30),                                  -- 정규화된 검색어(뒤 4자리 등)
    result_count integer     not null default 0,
    kind         varchar(20) not null default 'search',        -- search | pin_ok | pin_fail
    ip           varchar(64),
    created_at   timestamptz not null default now()
);
create index if not exists idx_parking_lookup_created
    on public.parking_lookup_logs(created_at desc);

-- ── 2. 임시 방문차량 등록권한 공유링크 ─────────────────────────────────────
-- 입주민이 방문사유·기간을 정해 링크를 만들고, 방문자가 그 링크에서 차량번호와
-- 연락처만 넣어 등록을 마친다. 링크는 10분 뒤 자동 만료되고 1건 등록되면 즉시
-- 소진된다. 원문 토큰은 저장하지 않는다 — DB 가 새도 링크를 되살릴 수 없어야 한다.
create table if not exists public.parking_share_tokens (
    id              bigint generated always as identity primary key,
    token_hash      varchar(64) not null unique,               -- sha256(원문)
    user_id         bigint      not null,
    dong            varchar(10) not null,
    ho              varchar(10) not null,
    registrant_name varchar(50),
    visit_reason    varchar(100) not null,
    car_type        varchar(30),
    -- 방문 기간은 앱 전역 규약대로 KST 벽시계 값을 그대로 담는다(시간대 미부착).
    entry_time      timestamp   not null,
    exit_time       timestamp   not null,
    expires_at      timestamptz not null,
    used_at         timestamptz,
    visit_id        bigint,                                    -- 등록된 방문차량 id
    revoked_at      timestamptz,                               -- 새 링크 발급 시 이전 링크 무효화
    created_at      timestamptz not null default now()
);
create index if not exists idx_parking_share_user
    on public.parking_share_tokens(user_id, created_at desc);

alter table public.parking_lookup_logs  enable row level security;
alter table public.parking_share_tokens enable row level security;
