# 실주차일수(숙박) 월 한도 — 구현 정리 · 관제PC 작업 인수인계

> 작성 2026-09-08 · **시행일 2026-10-01** (그 전까지는 집계·미리보기만, 등록 차단 없음)
> 근거: 변경된 주차관리 관리규약
> VPS 앱(도쿄)은 **구현·배포 완료**. 남은 것은 **관제PC 에이전트 작업**과 **Supabase SQL 실행**.

---

## 0. 요구사항 → 구현 대응

| 규약 | 내용 | 구현 | 상태 |
|---|---|---|---|
| 1 | 차량별 실주차일수 10일 초과 등록 불가 | 등록 POST에서 차단 (`main.visit_new`) | ✅ VPS |
| 1 | 초과 예상 시 최대 등록일자 제한 (8일 소모 → 2일만) | `usage.plan_registration()` 이 출차일을 잔여만큼 축소 | ✅ VPS |
| 2 | 20시~익일 07시 30분 초과 시 부과 | `usage.py` 야간창 집계 | ✅ VPS |
| 2 | 30분 이내 출차는 회차차량 (중고거래·배달) | `TURNAROUND_MINUTES = 30`, 야간창 내 누적 판정 | ✅ VPS |
| 3 | 아이돌봄·병간호·학습지는 관리사무소 별도 등록 | 앱은 안내 문구만. 실제 등록은 관제에서 → 정기등록 명단으로 알림 제외 | ✅ VPS |
| 4 | 주간 전용 주차는 관리사무소 별도 등록 / 1회 최대 3일은 유지 | 주간 주차는 애초에 0일 부과. 폼 3일 한도 그대로 | ✅ VPS |
| 주의 | 모든 입출차는 관제로 기록 | 기존 `parking_visit_logs` | ✅ |
| 주의 | **세대호출·경비실 호출 입차도 한도 동일** | 미등록 입출차 로그도 집계 | ⚠️ **스키마 + 관제PC 필요** |
| 주의 | 출차 미관제 시 별도 확인 | 계속 계산하되 **[미출차]** 로 분리 표시 | ✅ VPS |
| — | 10일 초과 차량 알림 (다차량 주차비 부과 근거) | 관리자 화면 + 텔레그램 배치 | ⚠️ **정기등록 명단 대기** |

---

## 1. 집계 규칙 (관리규약 기준)

> **실주차일수** = 방문차량이 **숙박**을 위해 주차한 일수.
> 오후 8시 ~ 다음날 오전 7시 사이에 **30분을 초과**하여 주차한 차량에게 부과.

- **진실원천은 `parking_visit_logs`** — 신청 기간이 아니라 관제가 기록한 **실제 입/출차**로 센다.
- 세는 단위는 달력 날짜가 아니라 **밤(야간창)** 이다. `d일 20:00 ~ (d+1)일 07:00` 이 한 밤이고,
  부과된 1일은 **밤이 시작된 날짜 d** 에 귀속한다 → 9/30 밤 ~ 10/1 아침 = **9월분**.
- **주간(07:00~20:00)에만 주차한 차량은 부과되지 않는다.** 아무리 오래 있어도 0일.
- 30분은 **그 밤의 누적**으로 본다. 20:10~20:35(25분) 나갔다가 06:00~06:20(20분) 다시 들어오면
  합쳐 45분이라 부과된다. 짧게 나갔다 들어오는 우회를 막기 위해서다.
- 정확히 30분은 미부과, **31분부터 부과**.
- 출차 로그가 아직 없는 마지막 입차는 **현재 시각까지** 주차중으로 본다.
  관제 누락일 수 있어 관리자 화면·알림에 **[미출차]** 로 따로 표시한다.
- 출차 없이 입차가 연달아 찍힌 구간(관제 중복 이벤트)은 먼저 들어온 입차를 살려 하나의 연속 체류로 본다.
- **시스템 등록 없이 세대호출·경비실 호출로 입차한 차량도 같은 한도를 적용한다.**
  등록이 없으면 세대를 알 수 없어 **[미등록]** 으로 표시한다.

### 검증한 예시 (실제 코드로 확인 완료)

| 입/출차 | 부과 |
|---|---|
| 8/1 22:00 → 8/2 09:00 | **1박** (야간창 8/1 에 540분) |
| 8/1 22:00 → 8/3 09:00 | **2박** |
| 8/1 10:00 → 8/1 18:00 (주간만) | **0** |
| 20:10 → 20:35 (25분) | **0** — 회차차량 |
| 20:10~20:35 + 06:00~06:20 (같은 밤 누적 45분) | **1박** |
| 19:00 → 20:31 (야간 31분) | **1박** |
| 19:00 → 20:30 (야간 정확히 30분) | **0** |
| 06:40 → 07:30 (야간 20분) | **0** — 전날 밤에 귀속 |

## 2. 한도 규칙

- 한도는 **세대가 아니라 차량번호 기준**, **매월 10일**, **매월 1일 리셋**.
- 소진량 = `실제 숙박한 밤(과거·오늘)` **∪** `아직 오지 않은 활성 등록의 예약 밤(오늘 이후)` 의 **합집합**.
  - 예약한 밤이 나중에 실제 숙박이 되어도 **중복 차감되지 않는다**.
  - 등록만 해 두고 오지 않은 **지난 밤은 저절로 풀린다**(실주차일수 기준이므로).
- **등록이 소모하는 밤 = 등록 기간에 20:00 이 들어오는 횟수.**

  | 폼 선택 | 소모 |
  |---|---|
  | 당일 (00:00~23:59) | 1일 |
  | +1일 | 2일 |
  | +2일 | 3일 |
  | 주간만 (예: 09:00~18:00) | **0일** — 한도와 무관 |

  → 규약 예시 "8일 소모 시 최대 2일 등록"이 그대로 성립한다.
- 잔여가 모자란 신청은 거부 대신 **되는 데까지만** 허용하고 출차일을 줄인다.
  잔여 0이면 등록을 막고 관리사무소 별도 등록을 안내한다.
- 등록 건이 달을 넘기면 **달마다 따로** 센다.
- **구멍 방지**: 한도를 다 쓴 차가 "자기가 묵었던 밤"으로 신청을 시작해도 통과되지 않는다.
  (자기 기존 활성 등록의 예약 밤만 예외)

### 알려진 한계 — 아침 출차

폼의 출차시각은 `23:59` 라, 밤에 들어와 **다음날 아침에 나가는** 손님은 `+1일`을 골라야 게이트가 열린다.
그러면 예약 소모는 2박이지만 실제로는 1박이므로, 방문이 끝나면 **자동으로 1박으로 정산된다**
(지난 예약 밤은 실주차일수 기준으로 다시 계산되므로). 일시적으로 잔여가 1일 적게 보일 뿐이다.

## 3. 시행일 처리

`app/usage.py` 의 `ENFORCE_FROM = date(2026, 10, 1)` 한 줄이 스위치다.

| | 2026-09-30까지 | 2026-10-01부터 |
|---|---|---|
| 입주민 등록 차단·축소 | **안 함** | 함 |
| 등록 폼 안내 문구 | "10월 1일부터 적용됩니다" | "이번 달 남은 주차일수 N일" |
| 방문기간 버튼 제한 | 안 함 | 잔여일수만큼만 선택 가능 |
| 관리자 화면 | 미리보기(노란 배너) | 정상 |
| 텔레그램 알림 배치 | 보류(로그만 남김) | 발송 |

시행일을 바꾸려면 `ENFORCE_FROM` 만 고치고 `systemctl restart parking`.

---

## 4. VPS 앱 — 구현 완료 내역

서버 `forie-vps:/web/parking` · 브랜치 `feat/sso`

### 새 파일
| 파일 | 내용 |
|---|---|
| `app/usage.py` | 야간창 집계·한도 엔진 전부. 상수(`MONTHLY_LIMIT_DAYS`, `NIGHT_START/END`, `TURNAROUND_MINUTES`, `ENFORCE_FROM`)도 여기 |
| `app/templates/admin/overuse.html` | 초과 차량 관리자 화면 |
| `scripts/notify_overuse.py` | 텔레그램 알림 배치(크론용) |
| `scripts/parking_usage_limit.sql` | **아직 실행 안 됨** — 5장 참조 |

### 수정 파일
| 파일 | 내용 |
|---|---|
| `app/models.py` | `visit_logs_by_car()`(차량번호 완전일치), `RegularCar`/`regular_cars_active()`/`regular_car_numbers()`/`regular_cars_synced_at()`, `overuse_alert_keys()`/`overuse_alert_add()` |
| `app/main.py` | 등록 POST 한도 검사·출차일 축소, `/visits/quota` JSON API, 템플릿 전역변수 주입 |
| `app/admin.py` | `/admin/overuse`, `/admin/overuse/export`(CSV), 대시보드 뱃지 |
| `app/notify.py` | `send_overuse_alert()` — [미등록]/[미출차] 태그 포함 |
| `app/templates/main/visit_new.html` | 숙박 기준 안내 박스, 잔여일수 실시간 표시, 방문기간 버튼 자동 제한 |
| `app/templates/admin/dashboard.html` | "실주차일수 초과 차량" 링크 + 뱃지 |

### 핵심 API
- `GET /visits/quota?car=<차량번호>` → `{ok, month, limit, used, remaining, enforced, from}`
  - 로그인 필수. **날짜·세대·시각은 돌려주지 않는다** — 남의 차량번호로 이용 이력을 캐낼 수 없게 숫자만 준다.
- `GET /admin/overuse?month=YYYY-MM` / `GET /admin/overuse/export?month=YYYY-MM`

### 실데이터 검증 (2026-08)

이용차량 279대 / 로그 1089건 → **10일 초과 2대**

```
65오8506    13박   야간누적 125시간 48분   304동 2704호
390너5265   12박   야간누적 129시간 38분   305동 301호
```

> 참고: 규약 변경 전(하루 전체 합산) 기준으로는 5대였다. 야간 기준으로 바뀌면서
> 낮 시간대 위주로 오래 세워둔 3대(`800루3546`·`57서7833`·`113버5929`)가 빠졌다.
> 이들은 규약 4항의 **주간 주차**에 해당하므로 관리사무소 별도 등록 대상이다.

---

## 5. 남은 작업 ① — Supabase SQL 실행 (사람이 1회)

Supabase 대시보드 → SQL Editor 에 `/web/parking/scripts/parking_usage_limit.sql` 붙여넣고 Run.

하는 일:
1. **`parking_visit_logs.registration_id` 를 nullable 로** — 세대호출·경비실 호출 입차를 기록하려면 필요.
2. `parking_regular_cars` — 관제 DB 정기(월주차) 차량 거울. **알림에서 제외할 명단.**
3. `parking_overuse_alerts` — 알림 발송 이력. `(car_number, period)` UNIQUE.
4. 에이전트(anon 키)용 GRANT + RLS 정책.

> 실행 전에도 앱은 정상 동작한다(테이블 없으면 정기등록 0대로 처리).
> 다만 **크론을 걸기 전에는 반드시 실행**할 것 — 알림 이력 테이블이 없으면 같은 차량을 매일 다시 알린다.

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

상시 원격관리를 원하면 OpenSSH 서버를 켜 두는 것을 권한다.

```powershell
# 관리자 PowerShell — 한 번만
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd; Set-Service -Name sshd -StartupType Automatic
# tailnet 안에서만 열리게: Windows 방화벽 규칙을 100.64.0.0/10 으로 제한 권장
```

---

### 6.2 작업 A — 미등록 입차 로그 기록 ★ 신규 (규약 주의사항)

**왜**: "시스템을 통해 등록하여 입차하는 차량 외 **세대호출 또는 경비실 호출을 통해 입차하는 차량 모두
숙박 10일 한도는 동일**합니다." 지금 에이전트는 방문등록에 매칭되지 않는 입출차 이벤트를 **버리고 있다**
(`NEXPA_INTEGRATION.md` 9장: "못 찾으면 로그 스킵"). 그러면 호출 입차로 한도를 통째로 우회할 수 있다.

**무엇을**: 등록 매칭에 실패해도 `registration_id` 를 비운 채 로그를 남긴다.

```python
def push_log(event):
    reg_id = find_active_registration(event.car_number, event.time)   # 기존 로직
    body = {
        "registration_id": reg_id,          # ★ 못 찾으면 None 그대로 (기존엔 여기서 return)
        "car_number": norm_car(event.car_number),
        "event_type": "in" if event.is_in else "out",
        "event_time": event.time_utc.isoformat(),
        "source": "nexpa",
        "raw": event.raw,
    }
    requests.post(LOGS_URL, headers=HEAD_MINIMAL, json=body, timeout=15).raise_for_status()
```

- `HEAD_MINIMAL` 은 기존과 동일하게 **`Prefer: return=minimal`** 필수(9장 함정).
- **차량번호 정규화(6.4)를 반드시 통과시킬 것.** 미등록 차량은 대조할 등록건이 없어
  포맷이 틀려도 아무도 눈치채지 못한 채 한도만 새 나간다.
- 사전에 5장 SQL(`registration_id drop not null`)이 실행돼 있어야 한다. 안 되어 있으면 이 INSERT 는 실패한다.

---

### 6.3 작업 B — 정기등록 차량 동기화

**왜**: 규약 3·4항으로 관리사무소가 별도 등록한 차량(아이돌봄·병간호·학습지·주간전용)은
상시 주차가 정상이라 10일 초과 알림에서 빼야 한다. 이 명단이 없으면 그 차들이 매달 알림에 섞여
알림이 무용지물이 된다. **현재는 명단이 비어 있어 모든 초과 차량이 알림 대상이다.**

#### ① 관제 DB에서 정기차량 뽑기 — 먼저 스키마 확인 필요

`NEXPA_INTEGRATION.md` 5.5의 "구분/플래그" 항목이 아직 미확정이다.
general_log 로 넥스파 UI의 **정기차량 등록** 동작을 캡처해 테이블·컬럼을 특정할 것.

```sql
SET GLOBAL general_log_file='C:/temp/nexpa_regular.log';
SET GLOBAL general_log='ON';
-- 넥스파 UI에서 정기차량 1건 등록 / 수정 / 삭제
SET GLOBAL general_log='OFF';
```

확인할 것: 테이블명, 차량번호 컬럼과 **저장 포맷**, 유효기간 컬럼(from/to), 동/호 컬럼, 해지 표현.

#### ② Supabase 업로드

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
    #   return=minimal              : anon 은 SELECT 권한이 없어 되읽기하면 401 (9장 함정과 동일)
    #   resolution=merge-duplicates : car_number PK 기준 upsert
    "Prefer": "return=minimal, resolution=merge-duplicates",
}

def sync_regular_cars(rows):
    """rows: 관제 DB에서 읽은 정기차량 [(차량번호, 이름, 동, 호, 시작일, 종료일), ...]"""
    now = datetime.now(timezone.utc).isoformat()
    payload, seen = [], set()
    for car, name, dong, ho, vfrom, vto in rows:
        c = norm_car(car)
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

#### ③ 에이전트 루프에 끼우기

```
매일 1회(또는 기존 안전망 폴링 중 하루 한 번만):
    rows = 관제 MariaDB SELECT (정기차량 전량)
    sync_regular_cars(rows)
```

기존 방문차량 pending 처리 루프와는 독립이다. 실패해도 방문차량 연동에 영향이 없도록 예외를 삼키고 로그만 남길 것.

---

### 6.4 차량번호 포맷 정규화 — **가장 중요한 함정** (작업 A·B 공통)

앱이 저장하는 차량번호는 **공백·기호가 전혀 없는 형태**다(`12가3456`, `서울12가3456`).
관제 DB가 `12-가-3456` 이나 `12 가 3456` 으로 저장한다면 **그대로 올리면 한 건도 매칭되지 않는다.**

```python
import re
def norm_car(v):
    """한글·영숫자만 남긴다. 앱의 models.normalize_car_query 와 동일 규칙."""
    return re.sub(r'[^0-9A-Za-z가-힣]', '', str(v or ''))
```

---

### 6.5 검증

관제PC에서 1회 실행한 뒤 VPS에서:

```bash
ssh root@forie-vps
cd /web/parking && ./venv/bin/python -c "
from app import create_app, models, usage
with create_app().app_context():
    cars = models.regular_cars_active()
    print('정기등록', len(cars), '대 / 최근 동기화', models.regular_cars_synced_at())
    for c in cars[:10]: print(' ', c.car_number, c.household_label, c.valid_to)
    r = usage.scan_overuse()
    print('초과', r['stats']['flagged'], '대 / 정기제외', r['stats']['excluded'],
          '대 / 미등록', r['stats']['unregistered'], '대')
"
```

관리자 화면 `park.forie.kr/admin/overuse` 에서 확인할 것:
- 상단 "정기등록(제외)" 숫자가 올라갔는지
- 하단 "정기등록이라 제외한 차량 N대" 접이식에 실제로 담기는지
- 맨 아래 "마지막 동기화"가 **"기록 없음 — 에이전트 동기화 미설정"** 에서 시각으로 바뀌었는지
- 작업 A 후: 등록 없이 입차한 차량이 **[미등록]** 뱃지로 뜨는지

---

## 7. 시행 전 체크리스트 (2026-10-01 이전)

- [ ] Supabase SQL 실행 (`scripts/parking_usage_limit.sql`) — `registration_id` nullable 포함
- [ ] 관제PC **작업 A** (미등록 입차 로그 기록) + 검증
- [ ] 관제PC **작업 B** (정기차량 동기화) + 검증
- [ ] 규약 3·4항 대상 차량(아이돌봄·병간호·학습지·주간전용)을 관제에 정기등록 → 동기화로 제외 확인
- [ ] 크론 등록 (`0 0 * * *`)
- [ ] 텔레그램 알림 리허설 — `ENFORCE_FROM` 을 잠깐 과거로 바꿔 1회 발송 확인 후 되돌리기
- [ ] **입주민 사전 공지** — 관리자 페이지 > 팝업(공지) 관리에 변경 규약 게시
- [ ] 9월 실데이터 미리보기로 시행 시 누가 막히는지 파악 → 관리사무소 선제 안내

## 8. 운영 중 조정 지점

전부 `app/usage.py` 상단 상수다. 고친 뒤 `systemctl restart parking`.

| 상수 | 현재 | 뜻 |
|---|---|---|
| `MONTHLY_LIMIT_DAYS` | 10 | 차량당 월 숙박 가능일수 |
| `NIGHT_START` / `NIGHT_END` | 20:00 / 07:00 | 야간창 |
| `TURNAROUND_MINUTES` | 30 | 야간창 내 누적이 이 시간 이하면 회차로 보고 미부과 |
| `ENFORCE_FROM` | 2026-10-01 | 실제 차단·알림 시작일 |
| `models.REGULAR_STALE_DAYS` | 2 | 동기화가 이만큼 뒤처진 정기등록 행은 빠진 것으로 봄 |

정기등록 명단은 관제 DB가 진실원천이므로 앱에서 직접 고치지 않는다. 예외 차량을 임시로 넣어야 하면
Supabase 대시보드에서 `parking_regular_cars` 에 직접 행을 추가하되, **다음 동기화 때 빠진다**는 점에
주의할 것(관제 DB에 넣는 것이 정석).
