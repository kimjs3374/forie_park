# 넥스파(nexpa) 주차관제 연동 설계 + 현장 조사 런북

> 상태: **착수 전 (관제 PC 미조사)**. 아래 "현장 확인"은 관제 PC 앞에서 실측으로 채운다.
> 배경: 넥스파가 연동 규격을 안 주고 견적만 과하게 요구 → **직접 리버스로 개발**.

---

## 1. 목표

입주민이 웹(park.forie.kr)에서 등록한 **방문차량**을, 관리실 넥스파 관제 DB의 "허용차량"에 넣어서
**LPR(번호인식)→차단기**가 자동으로 통과시키게 한다. (대금청구 없음 = 정산/시간차감 로직 불필요)

## 2. 아키텍처 (확정)

```
[입주민] 방문차량 등록 (park.forie.kr)
   └─▶ [도쿄 VPS 앱] Supabase 저장 (parking_visit_registrations, nexpa_sync=pending)
            └─(웹훅 핑, tailnet 인바운드)─▶ [관리실 에이전트]
                                              │  ① Supabase REST 직접 조회 (pending)
                                              │  ② 넥스파 MariaDB INSERT (localhost)
                                              │  ③ Supabase PATCH nexpa_sync=synced
                                              ▼
                                       [넥스파 MariaDB] → LPR → 차단기
```

**핵심 원칙**
- 관제 DB는 **관리실 에이전트만** 만진다(로컬). 도쿄 VPS는 관제 DB 직접 접근 안 함.
- 위험/불안정한 결합(스키마 리버스·직접 INSERT)을 **에이전트 한 대에 격리**. 앱은 그대로.
- `app/nexpa_adapter.py` stub은 사실상 무의미 — 실제 어댑터 로직은 에이전트가 가짐.

**트리거 = 웹훅 핑 + pending 풀 + 안전망 폴링**
- 웹훅은 **데이터 없는 신호("새거 있으니 확인해")** 만. 데이터는 에이전트가 Supabase에서 pending으로 땡김.
- 핑 놓쳐도 pending으로 남아 다음 핑/안전망 폴링(수 분)이 회수.
- **pending 기준 처리 + 성공 시 synced 마킹 → 유실·중복 안전(멱등)**.

## 3. 네트워크 (확정)

- 관제 PC는 현재 **내부망만**, 외부 인터넷 없음.
- **무선(LTE) 동글로 아웃바운드만** 부여 → 파킹망은 인터넷에 노출 안 함.
- **Tailscale** 설치(아웃바운드로 mesh 형성, 인바운드 포트 개방 불필요). tailnet 안에선 VPS↔에이전트 양방향 → 웹훅 인바운드도 tailnet으로 가능.
- 그 PC에 Claude CLI 설치해 로컬/원격으로 조사.

## 4. 보안

- 에이전트에 **service_role(만능키) 두지 말 것** — RLS 우회 + forie_kids까지 전체 노출.
- `parking_visit_registrations` **읽기 + sync 컬럼 update만** 되는 제한키(RLS 정책/별도 롤)로.
- 관제 DB 계정도 최소권한(해당 테이블 접근만) 권장.

---

## 5. 현장 확인 — 경우의 수 (관제 PC 앞에서 채운다)

각 항목: **[확인할 것] → [방법] → [경우의 수 / 분기]**

### 5.1 OS · 환경
- **[OS/버전]** → `winver` / 시스템정보 → 경우: 윈도우 몇? (오래된 Win7/embedded면 파이썬·Tailscale 버전 제약)
- **[상시 가동/재부팅 정책]** → 경우: 24시간 켜져 있나? 에이전트를 **서비스로 등록** 가능한가(작업스케줄러/NSSM)?
- **[관리자 권한]** → 경우: 우리가 관리자 계정 있나? 없으면 획득 경로?

### 5.2 DB 엔진 · 접속
- **[엔진 확인]** → 서비스 목록(`services.msc`)에서 mariadb/mysql/mssql 확인, 설치 경로 → 경우: **MariaDB(추정)** / MySQL / MSSQL → 드라이버 결정(pymysql / pyodbc)
- **[포트/바인딩]** → `netstat -ano | findstr 3306`, `my.ini`의 `bind-address` → 경우:
  - localhost(127.0.0.1)만 → **에이전트를 이 PC에** 둬야 함 (별도 브리지 PC면 LAN서 못 붙음)
  - LAN(0.0.0.0) → 같은 랜의 다른 PC에서도 붙을 수 있음
- **[버전]** → `mysql --version` 또는 접속 후 `SELECT VERSION();`

### 5.3 DB 계정 확보 (순서대로)
- **[① 앱 config에서]** → 넥스파 설치폴더의 `*.ini/*.config/*.xml/*.exe.config`, 레지스트리에서 DB 접속문자열/비번 검색 (평문인 경우 많음) → 경우: 찾으면 끝
- **[② my.ini/데이터 위치]** → 데이터 디렉토리·설정 확인
- **[③ root 리셋]** (①② 실패 시) → 넥스파 서비스 중지 → `mysqld --skip-grant-tables`로 기동 → root 비번 재설정 → 원복 → 경우: **서비스 잠깐 멈춰야 하니 심야에**, AS계약 있으면 주의
- **[GUI 툴]** → HeidiSQL/DBeaver로 접속해 탐색

### 5.4 스키마 리버스 (킬러: general_log)
- **[전체 스키마 덤프]** → `mysqldump --no-data -u.. -p.. DB > schema.sql`, `SHOW TABLES;`, 테이블 코멘트
- **[general_log로 실제 쿼리 캡처]** ← 핵심
  ```sql
  SET GLOBAL general_log_file='C:/temp/nexpa_gen.log';
  SET GLOBAL general_log='ON';
  -- 넥스파 UI에서 방문차량 1건 등록 (+ 수정/삭제도 각각)
  SET GLOBAL general_log='OFF';
  ```
  → 경우: 앱이 어떤 테이블에 어떤 INSERT/UPDATE 하는지 그대로 나옴 → "방문 허용차량" 테이블·컬럼 100% 특정
- **[대안: before/after diff]** → 등록 전 `SELECT *` 스냅샷 → 등록 → 다시 스냅샷 → 비교

### 5.5 "방문 허용차량" 테이블 구조 (특정 후 채움)
- **[테이블명]** → ____
- **[차량번호 컬럼·저장형식]** → 경우: 하이픈 있음/없음? 공백? 지역명 포함(서울12가3456)? 대소문자? → **우리 저장값과 포맷 변환 필요할 수 있음**
- **[유효기간 처리]** → 경우: from/to 컬럼? 입차/출차 예정시간? 무기한? 날짜만/분단위? → 우리 entry_time/exit_time을 어떻게 매핑?
- **[동/호 컬럼]** → 있나? 필수인가?
- **[구분/플래그]** → 방문/정기/임시 구분 컬럼? 활성 플래그? 상태값?
- **[PK/자동증가·UNIQUE 제약]** → 경우: id auto? (차량번호+기간) UNIQUE? → 중복 INSERT 시 에러/무시 정책
- **[필수 NOT NULL·기본값]** → INSERT 시 반드시 채워야 하는 컬럼 목록
- **[FK/연관 테이블]** → 동호수 마스터·차주 테이블 등 선행 INSERT 필요한가?

### 5.6 반영 메커니즘 (제일 중요 · 실물 확인)
- **[INSERT만으로 차단기가 인식하나?]** → 테스트 차량 INSERT 후 실제 차 대보거나 LPR 로그 확인 → 경우:
  - (a) INSERT 즉시 반영 → 최상
  - (b) **캐시/메모리에 올려서** 주기적으로만 읽음 → 리로드 주기 파악 or 트리거 필요
  - (c) 앱/서비스가 특정 **프로시저·API·시그널**로 반영 → general_log에서 그 호출도 재현해야 함
  - (d) 별도 **인터페이스 테이블→운영 테이블** 이관 잡이 도는 구조 → 그 잡 확인
- **[반영 지연]** → 등록~인식까지 몇 초?

### 5.7 취소 / 수정
- **[등록 취소 시]** → general_log(삭제)로 확인 → 경우: DELETE? status 변경? 만료일 당김?
- **[수정 시]** → UPDATE 패턴

### 5.8 입출차 로그 (상태 회신용, 있으면 보너스)
- **[실제 입/출차 기록 테이블]** → 경우: 있으면 "이 방문차 실제로 들어왔다/나갔다"를 읽어 Supabase에 반영 가능(향후 상태표시·자동만료에 활용)

### 5.9 백업 · 안전
- **[전체 백업 가능?]** → `mysqldump` 전체 백업 확보(쓰기 전 필수)
- **[테스트 차량번호]** → 실차 아닌 더미 번호로 먼저 검증
- **[롤백 계획]** → 우리가 INSERT한 것만 지울 수 있게 식별(우리 등록에 표식/기간)
- **[AS 계약]** → 직접 DB 조작이 계약위반/AS거부 사유인지 확인

---

## 6. 에이전트 구현 스펙 (스키마 확정 후)

- **언어/드라이버**: Python + pymysql(MariaDB) + requests(Supabase REST)
- **루프**:
  ```
  on 웹훅핑  또는  매 N분(안전망):
     rows = Supabase GET parking_visit_registrations?status=active&nexpa_sync=eq.pending
     for r in rows:
        try:
           넥스파 MariaDB INSERT (5.5 규격대로, 5.6 반영 트리거 포함)
           Supabase PATCH r.id → nexpa_sync=synced, nexpa_synced_at=now
        except:
           Supabase PATCH r.id → nexpa_sync=failed (+ 로그)  # 다음 회차 재시도 대상
  취소분: status=cancelled & nexpa_sync=synced → 넥스파 취소(5.7) → 표식
  ```
- **멱등**: pending만 처리 + 성공 시 synced → 중복 핑/폴링에도 이중등록 없음
- **웹훅 수신**: tailnet에서 작은 HTTP 리스너(핑만 받고 즉시 위 루프 1회 실행). 인증(공유 토큰)
- **로깅/모니터링**: 성공·실패 카운트, 실패 알림(텔레그램 재사용 가능)
- **키**: Supabase 제한키(4번), 넥스파 DB 최소권한

## 7. VPS 앱 쪽 (경미)
- 등록/승인 시 에이전트 tailnet으로 **웹훅 핑** POST (실패 무시, 안전망 폴링이 커버)
- 그 외 앱은 이미 Supabase에 데이터 넣고 있으니 추가 작업 최소

## 8. 작업 순서 (런북)
1. 관제 PC에 LTE 동글 → 아웃바운드 확보
2. Tailscale 설치 → tailnet 편입 (+ key expiry off)
3. Claude CLI 설치
4. **5장 현장 확인 전부 실측** (특히 5.4 general_log, 5.5 테이블, 5.6 반영)
5. 백업 → 테스트 차량 INSERT → **차단기 실물 인식 확인**
6. 에이전트 작성·서비스 등록
7. Supabase 제한키 발급 + 웹훅 핑 연결
8. 실차량 파일럿 → 모니터링

---
> 이 문서는 현장 조사하며 5장 빈칸을 채워 확정한다. 빈칸이 다 차면 6~8이 바로 구현 가능.
