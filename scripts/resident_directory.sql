-- 입주민 세대 명부 마스터 + 자동승인 매칭 (실내놀이터/방문차량 공용)
-- Supabase SQL Editor에서 1회 실행. 순수 추가 마이그레이션(기존 테이블 무영향).

-- 1) 정규화 매칭키 함수 (단일 진실원천: 명부 저장/조회 양쪽이 동일 로직 사용)
create or replace function directory_match_key(p_dong text, p_ho text, p_name text)
returns text language sql immutable as $$
  select regexp_replace(coalesce(p_dong,''), '[^0-9]', '', 'g') || '|' ||
         regexp_replace(coalesce(p_ho,''),   '[^0-9]', '', 'g') || '|' ||
         lower(regexp_replace(normalize(coalesce(p_name,''), NFC), '\s', '', 'g'));
$$;

-- 2) 명부 테이블
create table if not exists resident_directory (
  id         bigint generated always as identity primary key,
  dong       text not null,
  ho         text not null,
  name       text not null,
  match_key  text generated always as (directory_match_key(dong, ho, name)) stored,
  is_active  boolean not null default true,
  batch_id   text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists uq_resident_directory_active_key
  on resident_directory (match_key) where is_active;

-- 3) 매칭 판정 함수 (양앱이 RPC로 호출)
create or replace function check_resident(p_dong text, p_ho text, p_name text)
returns boolean language sql stable as $$
  select exists(
    select 1 from resident_directory
    where is_active
      and match_key = directory_match_key(p_dong, p_ho, p_name)
  );
$$;

-- 4) 개인정보 보호: RLS on, 정책 없음 = anon/authenticated 차단. 백엔드 service_role만 접근.
alter table resident_directory enable row level security;
