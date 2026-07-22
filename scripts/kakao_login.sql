-- 소셜 로그인(카카오/구글) 연동 컬럼
--
-- 실행 위치: Supabase 대시보드 → SQL Editor
-- 선행: forie_users_rename.sql (parking_users → forie_users)
-- 적용일: 2026-07-22

alter table public.forie_users add column if not exists provider     text not null default 'local';
alter table public.forie_users add column if not exists provider_uid text;
alter table public.forie_users add column if not exists linked_at    timestamptz;

-- 소셜 전용 계정은 비밀번호가 없다.
-- (username 은 NOT NULL UNIQUE 라 값이 필요해서 앱이 'kakao_<회원번호>' 로 자동 생성한다.
--  비밀번호가 없으므로 그 아이디로 로컬 로그인은 되지 않는다 — User.check_password 참고)
alter table public.forie_users alter column password_hash drop not null;

-- 같은 소셜 계정이 두 입주민 계정에 중복 연결되지 않도록.
-- provider_uid 가 없는 로컬 계정들은 부분 인덱스에서 제외한다.
create unique index if not exists uq_forie_users_provider_uid
    on public.forie_users(provider, provider_uid)
    where provider_uid is not null;

notify pgrst, 'reload schema';


-- ---------------------------------------------------------------------------
-- 롤백 (주석 해제)
-- ---------------------------------------------------------------------------
-- drop index if exists public.uq_forie_users_provider_uid;
-- alter table public.forie_users drop column if exists linked_at;
-- alter table public.forie_users drop column if exists provider_uid;
-- alter table public.forie_users drop column if exists provider;
-- -- password_hash NOT NULL 복원은 소셜 계정을 먼저 정리한 뒤에만 가능하다:
-- -- delete from public.forie_users where password_hash is null;
-- -- alter table public.forie_users alter column password_hash set not null;
-- notify pgrst, 'reload schema';
