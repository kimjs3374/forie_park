# 실주차일수 월 한도 (차량당 10일) — 구현 정리 · 관제PC 작업 인수인계

> 작성 2026-09-08 · **시행일 2026-10-01** (그 전까지는 집계·미리보기만, 등록 차단 없음)
> VPS 앱(도쿄)은 **구현·배포 완료**. 남은 것은 **관제PC 에이전트 작업**과 **Supabase SQL 실행** 두 가지.

---

## 0. 요구사항 → 구현 대응

| # | 요구사항 | 구현 | 상태 |
|---|---|---|---|
| 1 | 각 차량별 실주차일수 카운트 | `app/usage.py` — 관제 입출차 로그를 KST 날짜별로 쪼개 합산 | ✅ VPS |
| 2 | 실주차일수 10일 초과 등록 불가 | 등록 POST에서 차단 (`main.visit_new`) | ✅ VPS |
| 3 | 정기등록 차량 외 10일 초과 차량 알림 | 관리자 화면 `/admin/overuse` + 텔레그램 배치 | ⚠️ **관제PC 동기화 필요** |
| 4 | 1일 30분 이내는 회차차량 → 미카운트 | `TURNAROUND_MINUTES = 30`, 날짜별 합계로 판정 | ✅ VPS |
| 5 | 초과 예상 시 최대 등록일자 제한 | `usage.plan_registration()` 이 출차일을 잔여일수만큼 축소 | ✅ VPS |

---

## 1. 집계 규칙 (확정)

- **진실원천은 `parking_visit_logs`** — 신청 기간(`entry_time`/`exit_time`)이 아니라 관제가 기록한 **실제 입/출차**로 센다.
- 입/출차를 짝지어 체류시간을 구하고 **KST 날짜별로 쪼개** 합산한다.
  - 예) 8/1 22:00 입차 → 8/2 09:00 출차 = 8/1 120분 + 8/2 540분 → **2일**
- **하루 합계가 30분 이하인 날은 회차차량**으로 보고 세지 않는다.
  - 20분씩 두 번(합 40분)은 **카운트됨**. 하루 총합 기준이지 1회 기준이 아니다.
  - 정확히 30분은 미카운트, 31분부터 카운트.
- 출차 로그가 아직 없는 마지막 입차는 **현재 시각까지** 잠정 체류로 본다(주차중).
- 출차 없이 입차가 연달아 찍힌 구간(관제 중복 이벤트)은 먼저 들어온 입차를 살려 하나의 연속 체류로 본다.

## 2. 한도 규칙 (확정)

- 한도는 **세대가 아니라 차량번호 기준**, **매월 10일**, **매월 1일 리셋**.
- 소진량 = `실제 주차한 날(과거·오늘)` **∪** `아직 오지 않은 활성 등록의 예약일(오늘 이후)` 의 **날짜 합집합**.
  - 예약일이 나중에 실주차일이 되어도 **중복 차감되지 않는다**.
  - 등록만 해 두고 오지 않은 **지난 날은 저절로 풀린다**(실주차일수 기준이므로).
- 등록 신청이 잔여일수를 넘으면 **되는 데까지만** 허용하고 출차일을 줄인다.
  - 예) 8일 사용 + 3일 신청 → **2일만 등록**, 출차일을 자동 조정하고 경고 문구를 띄운다.
  - 잔여 0일이면 등록 자체를 막는다(모달 안내).
- 등록 건이 달을 넘기면 **달마다 따로** 센다(9/30~10/2 → 9월분·10월분 각각).
- **구멍 방지**: 한도를 다 쓴 차가 "자기가 이미 주차했던 날"로 신청을 시작해도 통과되지 않는다. (자기 기존 활성 등록의 예약일만 예외)

## 3. 시행일 처리

`app/usage.py` 의 `ENFORCE_FROM = date(2026, 10, 1)` 한 줄이 스위치다.

| | 2026-09-30까지 | 2026-10-01부터 |
|---|---|---|
| 입주민 등록 차단·축소 | **안 함** | 함 |
| 등록 폼 안내 문구 | "10월 1일부터 적용됩니다" | "이번 달 남은 주차일수 N일" |
| 방문기간 버튼 제한 | 안 함 | 잔여일수만큼만 선택 가능 |
| 관리자 화면 | 미리보기(노란 배너) | 정상 |
| 텔레그램 알림 배치 | 보류(로그만 남김) | 발송 |

시행일을 바꾸려면 `ENFORCE_FROM` 만 고치고 `systemctl restart parking` 하면 된다.

---

## 4. VPS 앱 — 구현 완료 내역

서버 `forie-vps:/web/parking` · 브랜치 `feat/sso`

### 새 파일
| 파일 | 내용 |
|---|---|
| `app/usage.py` | 집계·한도 엔진 전부. 상수(`MONTHLY_LIMIT_DAYS`, `TURNAROUND_MINUTES`, `ENFORCE_FROM`)도 여기 |
| `app/templates/admin/overuse.html` | 초과 차량 관리자 화면 |
| `scripts/notify_overuse.py` | 텔레그램 알림 배치(크론용) |
| `scripts/parking_usage_limit.sql` | **아직 실행 안 됨** — 5장 참조 |

### 수정 파일
| 파일 | 내용 |
|---|---|
| `app/models.py` | `visit_logs_by_car()`(차량번호 완전일치 조회), `RegularCar`/`regular_cars_active()`/`regular_car_numbers()`/`regular_cars_synced_at()`, `overuse_alert_keys()`/`overuse_alert_add()` 추가 |
| `app/main.py` | 등록 POST에 한도 검사·출차일 축소, `/visits/quota` JSON API, 템플릿 전역변수 주입 |
| `app/admin.py` | `/admin/overuse`, `/admin/overuse/export`(CSV), 대시보드 뱃지 집계 |
| `app/notify.py` | `send_overuse_alert()` |
| `app/templates/main/visit_new.html` | 차량번호 입력 시 잔여일수 실시간 표시, 방문기간 버튼 자동 제한 |
| `app/templates/admin/dashboard.html` | "실주차일수 초과 차량" 링크 + 뱃지 |

### 새 화면·API
- `GET /visits/quota?car=<차량번호>` → `{ok, month, limit, used, remaining, enforced, from}`
  - 로그인 필수. **날짜·세대·시각은 돌려주지 않는다** — 남의 차량번호로 이용 이력을 캐낼 수 없게 숫자만 준다.
- `GET /admin/overuse?month=YYYY-MM` — 초과 차량 목록, 달 선택, 정기등록 제외분 접이식 표시
- `GET /admin/overuse/export?month=YYYY-MM` — CSV

### 실데이터 검증 결과 (2026-08 기준)
```
이용 차량 273대 / 로그 1052건 → 10일 초과 5대
  390너5265  13일 (234시간 12분)  305동 301호
  65오8506   13일 (255시간 37분)  304동 2704호
  800루3546  12일 (61시간 28분)   305동 2201호
  57서7833   12일 (131시간 21분)  302동 1901호
  113버5929  11일 (220시간 45분)  304동 804호
```

---

## 5. 남은 작업 ① — Supabase SQL 실행 (VPS 쪽, 사람이 1회)

Supabase 대시보드 → SQL Editor 에 `/web/parking/scripts/parking_usage_limit.sql` 을 붙여넣고 Run.

만드는 것:
- `parking_regular_cars` — 관제 DB 정기(월주차) 차량 거울. **알림에서 제외할 명단.**
- `parking_overuse_alerts` — 알림 발송 이력. `(car_number, period)` UNIQUE 로 같은 차를 매일 다시 알리지 않는다.
- 에이전트(anon 키)용 GRANT + RLS 정책 (`parking_regular_cars` 에 INSERT/UPDATE 만, SELECT 는 안 엶)

> 실행 전에도 앱은 정상 동작한다(테이블 없으면 정기등록 0대로 처리). 다만 **크론을 걸기 전에는 반드시 실행**할 것 — 알림 이력 테이블이 없으면 같은 차량을 매일 다시 알린다.

### 크론 등록 (SQL 실행 후)
```
# 실주차일수 10일 초과 차량 알림 (서버시계 UTC / KST 09:00)
0 0 * * * cd /web/parking && ./venv/bin/python scripts/notify_overuse.py >> /tmp/parking_overuse.log 2>&1
```

---

## 6. 남은 작업 ② — 관제PC 에이전트 (여기가 본 작업)

### 6.1 접근 현황 (2026-09-08 실측)

| 항목 | 상태 |
|---|---|
| 노드 | `forie-park-server` = **100.77.55.114** (Windows), tailnet 연결 정상 |
| 에이전트 | 살아 있음 — `http://100.77.55.114:42150/` → `{"ok":true,"msg":"alive"}` (Python 3.12.10 BaseHTTP) |
| SSH(22) / RDP(3389) / WinRM(5985) | **전부 닫힘** → VPS에서 원격으로 코드 투입 불가 |
| 에이전트 소스 | VPS에 사본 없음 = **관제PC에만 존재** |

관제PC에서 한 번은 사람이 조작해야 한다. 이후 상시 원격관리를 원하면 OpenSSH 서버를 켜 두는 것을 권한다.

```powershell
# 관리자 PowerShell — 한 번만
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd; Set-Service -Name sshd -StartupType Automatic
# tailnet 안에서만 열리게: Windows 방화벽 규칙을 100.64.0.0/10 으로 제한 권장
```

### 6.2 해야 할 일 — 정기등록 차량 동기화 추가

에이전트 루프에 **정기(월주차) 차량 스냅샷 업로드**를 추가한다. 하루 1회면 충분하다.

**왜 필요한가**: 정기등록 차량은 상시 주차가 정상이라 10일 초과 알림에서 빼야 한다.
이 명단이 없으면 정기차량이 매달 알림에 섞여 들어와 알림이 무용지물이 된다.
(현재는 명단이 비어 있어 **모든 초과 차량이 알림 대상**이다.)

#### ① 관제 DB에서 정기차량 뽑기 — 먼저 스키마 확인 필요

`NEXPA_INTEGRATION.md` 5.5의 "구분/플래그" 항목이 아직 미확정이다. general_log 로 넥스파 UI의 **정기차량 등록** 동작을 캡처해 테이블·컬럼을 특정할 것.

```sql
SET GLOBAL general_log_file='C:/temp/nexpa_regular.log';
SET GLOBAL general_log='ON';
-- 넥스파 UI에서 정기차량 1건 등록 / 수정 / 삭제
SET GLOBAL general_log='OFF';
```

확인할 것: 테이블명, 차량번호 컬럼과 **저장 포맷**(하이픈·공백·지역명 유무), 유효기간 컬럼(from/to), 동/호 컬럼, 해지 표현(행 삭제 vs 상태값).

#### ② 차량번호 포맷 정규화 — **가장 중요한 함정**

앱이 저장하는 차량번호는 **공백·기호가 전혀 없는 형태**다(`12가3456`, `서울12가3456`).
관제 DB가 `12-가-3456` 이나 `12 가 3456` 으로 저장한다면 **그대로 올리면 한 건도 매칭되지 않는다.**
업로드 전에 반드시 정규화할 것:

```python
import re
def norm_car(v):
    """한글·영숫자만 남긴다. 앱의 models.normalize_car_query 와 동일 규칙."""
    return re.sub(r'[^0-9A-Za-z가-힣]', '', str(v or ''))
```

#### ③ Supabase 업로드

```python
import os, re, requests
from datetime import datetime, timezone

SUPABASE_URL = os.environ['SUPABASE_URL']          # https://xxxx.supabase.co
ANON_KEY     = os.environ['SUPABASE_ANON_KEY']     # sb_publishable_... (service_role 절대 금지)
T = f"{SUPABASE_URL.rstrip('/')}/rest/v1/parking_regular_cars"

HEAD = {
    "apikey": ANON_KEY,
    "Authorization": f"Bearer {ANON_KEY}",
    "Content-Type": "application/json",
    # ★ 필수 두 가지
    #   return=minimal          : anon 은 SELECT 권한이 없어 되읽기하면 401 이 난다(9장 함정과 동일)
    #   resolution=merge-duplicates : car_number PK 기준 upsert
    "Prefer": "return=minimal, resolution=merge-duplicates",
}

def sync_regular_cars(rows):
    """rows: 관제 DB에서 읽은 정기차량 [(차량번호, 이름, 동, 호, 시작일, 종료일), ...]"""
    now = datetime.now(timezone.utc).isoformat()
    payload, seen = [], set()
    for car, name, dong, ho, vfrom, vto in rows:
        c = re.sub(r'[^0-9A-Za-z가-힣]', '', str(car or ''))
        if not c or c in seen:      # PK 충돌 방지: 한 요청에 같은 차량번호가 두 번 오면 안 된다
            continue
        seen.add(c)
        payload.append({
            "car_number": c, "owner_name": name, "dong": dong, "ho": ho,
            "valid_from": vfrom, "valid_to": vto,      # 무기한이면 None
            "is_active": True, "source": "nexpa", "synced_at": now,
        })

    for i in range(0, len(payload), 500):             # 대량이면 나눠 보낸다
        r = requests.post(T, headers=HEAD, json=payload[i:i+500], timeout=30)
        r.raise_for_status()
    return len(payload)
```

> **관제에서 빠진 차량은 지우지 않아도 된다.** 매 회차 **전량**을 올려 `synced_at` 만 갱신하면,
> 앱이 "마지막 동기화보다 2일 이상 뒤처진 행"을 빠진 것으로 본다(`models.REGULAR_STALE_DAYS`).
> 에이전트에 DELETE 나 조건부 UPDATE 를 시키지 않는 이유는, 조건부 UPDATE 가 WHERE 절 컬럼에
> SELECT 권한을 요구해서 **차량번호 테이블을 anon 에 열어야 하기 때문**이다.
> 기준이 '마지막 동기화 시각'이라 에이전트가 멈춰도 명단이 통째로 무효화되지는 않는다.
> 그러므로 **반드시 전량 스냅샷을 올릴 것** — 증분만 올리면 나머지가 2일 뒤 정기등록에서 빠진다.

#### ④ 에이전트 루프에 끼우기

```
매일 1회(또는 기존 안전망 폴링 중 하루 한 번만):
    rows = 관제 MariaDB SELECT (정기차량 전량)
    sync_regular_cars(rows)
```

기존 방문차량 pending 처리 루프와는 독립이다. 실패해도 방문차량 연동에 영향이 없도록 예외를 삼키고 로그만 남길 것.

#### ⑤ 검증

관제PC에서 1회 실행한 뒤 VPS에서 확인:

```bash
ssh root@forie-vps
cd /web/parking && ./venv/bin/python -c "
from app import create_app, models
with create_app().app_context():
    cars = models.regular_cars_active()
    print('정기등록', len(cars), '대 / 최근 동기화', models.regular_cars_synced_at())
    for c in cars[:10]: print(' ', c.car_number, c.household_label, c.valid_to)
"
```

그리고 관리자 화면 `park.forie.kr/admin/overuse` 에서
- 상단 "정기등록(제외)" 숫자가 올라갔는지
- 하단 "정기등록이라 제외한 차량 N대" 접이식에 실제로 담기는지
- 맨 아래 "마지막 동기화" 가 **"기록 없음 — 에이전트 동기화 미설정"** 에서 시각으로 바뀌었는지

를 확인한다.

---

## 7. 시행 전 체크리스트 (2026-10-01 이전)

- [ ] Supabase SQL 실행 (`scripts/parking_usage_limit.sql`)
- [ ] 관제PC 에이전트에 정기차량 동기화 추가 + 1회 실행 검증
- [ ] 크론 등록 (`0 0 * * *`)
- [ ] 텔레그램 알림 리허설 — `ENFORCE_FROM` 을 잠깐 과거로 바꿔 1회 발송 확인 후 되돌리기
- [ ] **입주민 사전 공지** — 관리자 페이지 > 팝업(공지) 관리에 "10월 1일부터 차량당 월 10일 한도" 게시
- [ ] 9월 실데이터로 미리보기 확인 → 시행 시 누가 막히는지 파악하고 필요하면 관리사무소가 선제 안내

## 8. 운영 중 조정 지점

전부 `app/usage.py` 상단 상수 하나로 끝난다. 고친 뒤 `systemctl restart parking`.

| 상수 | 현재 | 뜻 |
|---|---|---|
| `MONTHLY_LIMIT_DAYS` | 10 | 차량당 월 주차 가능일수 |
| `TURNAROUND_MINUTES` | 30 | 이 시간 이하 체류는 회차로 보고 미카운트 |
| `ENFORCE_FROM` | 2026-10-01 | 실제 차단·알림 시작일 |

정기등록 명단은 관제 DB가 진실원천이므로 앱에서 직접 고치지 않는다. 예외 차량을 임시로 넣어야 하면 Supabase 대시보드에서 `parking_regular_cars` 에 직접 행을 추가하되, **다음 동기화 때 `is_active=false` 로 내려간다**는 점에 주의할 것(관제 DB에 넣는 것이 정석).
