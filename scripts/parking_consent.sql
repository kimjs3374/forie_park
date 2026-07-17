-- parking_users 개인정보 수집·이용 동의 기록 컬럼 (순수 추가)
alter table parking_users add column if not exists consent_agreed boolean not null default false;
alter table parking_users add column if not exists consent_agreed_at timestamptz;
