# 외부 DB (PostgreSQL) — 인터넷 시연 환경의 계정 보존 (O-03)

이 문서는 Render 검토·시연 서비스가 재시작·재배포돼도 계정과 기록이 남도록 외부 PostgreSQL을 쓰는 방법입니다.
로컬 운영·폐쇄망 구성은 그대로 SQLite(파일)와 로컬 Ollama를 씁니다. 외부 DB에 접속하지 않습니다.

## 1. 보존하는 것과 보존하지 않는 것

| 구분 | 외부 DB로 보존 | 비고 |
|---|---|---|
| 계정 | 아이디·비밀번호 해시·역할·상태(이용 중·승인 대기·반려·이용 중지)·인증 버전·잠금 | 비밀번호 평문은 어디에도 없음 |
| 최초 설정 표시 | `app_settings.owner_setup` | 계정이 남으면 최초 관리자 설정이 다시 열리지 않음 |
| 업무 기록 | 파일 기록·열 매핑 확정·검토·실행 기록·변경 내역·분석 결과·자동화·용어사전·학습·승격 | 소유 관계(누구의 기록인지) 포함 |
| 로그인 세션 | 보존 (재시작 뒤에도 유효 기간 안이면 그대로) | 이관할 때는 옮기지 않음 → 이관 뒤 다시 로그인 |
| **업로드 원본·통합 결과·묶기 Excel 파일** | **보존하지 않음** | 파일은 서버 디스크에 있다. Render 무료 서비스는 재배포·재시작·휴면 때 지워진다 (6장) |

## 2. 서비스 선택과 무료 한도 (2026-10-23 공식 문서 확인 — 생성·전환 직전에 다시 확인)

| 서비스 | 확인한 내용 | 출처 |
|---|---|---|
| Neon Free (우선안) | $0, 카드 불필요, 기간 제한 없음. 프로젝트당 저장 1GB·컴퓨트 100 CU-시간·내보내기 전송 5GB. 5분 무활동 뒤 컴퓨트 정지(끌 수 없음). 되돌리기 이력 6시간, 수동 스냅숏 1개. 컴퓨트·전송을 다 쓰면 다음 기간까지 컴퓨트 정지, 저장을 넘으면 쓰기 차단 — 자료를 지우지는 않음 | https://neon.com/pricing |
| Neon 연결 | 모든 연결 TLS 필수. 연결 풀러(`-pooler` 주소)는 PgBouncer 트랜잭션 모드 — SET·세션 단위 잠금 불가, 마이그레이션은 직접 연결 권장. 0.25CU 직접 연결 최대 104(앱 97) | https://neon.com/docs/connect/connect-from-any-app · https://neon.com/docs/connect/connection-pooling |
| Neon 판 | PostgreSQL 14~18 지원, 생성 때 고름 — **17을 고른다** (이 저장소 시험이 17.11에서 통과) | https://neon.com/docs/postgresql/postgres-version-policy |
| Render 무료 웹 서비스 | 15분 무트래픽 뒤 정지, 약 1분 뒤 기동, 월 750시간. 재배포·재시작·정지 때 로컬 파일 소실, 영구 디스크 불가 | https://render.com/docs/free |
| Render 무료 Postgres (기본안 아님) | 생성 30일 뒤 만료, 14일 유예 뒤 자료 삭제 | https://render.com/docs/free |

'무료 영구 보존 보장'이 아닙니다. 사용량 한도·정책 변경·장애·계정 삭제는 별개이므로 7장의 따로 받는 백업을 둡니다.
유료 전환·결제 수단 등록·여러 무료 계정 만들기·휴면을 막는 반복 요청은 하지 않습니다.

## 3. 설정 (환경 변수)

| 변수 | 값 | 설명 |
|---|---|---|
| `MOAJWO_DB_MODE` | `external` | 외부 DB 모드. 이 값이면 `DATABASE_URL`이 없거나 PostgreSQL이 아닐 때 **기동하지 않는다** (빈 SQLite로 대신 열지 않음) |
| `DATABASE_URL` | `postgresql://moajwo_app:…@ep-…-pooler…/neondb?sslmode=require&channel_binding=require` | 앱 계정(데이터 읽기·쓰기만), 풀러 주소 가능. `postgres://`·`postgresql://`는 psycopg 3 드라이버로 바뀐다 |
| `MOAJWO_MIGRATION_DATABASE_URL` | `postgresql://neondb_owner:…@ep-…(풀러 아님)…/neondb?sslmode=require&channel_binding=require` | 마이그레이션 전용 — 스키마를 바꿀 권한이 있는 계정, **직접 연결**. 비우면 `DATABASE_URL` |
| `MOAJWO_DB_POOL_SIZE` · `…_MAX_OVERFLOW` | 3 · 2 | 무료 인스턴스 1개 기준의 작은 풀 |
| `MOAJWO_DB_POOL_TIMEOUT_S` | 10 | 풀에서 연결을 기다리는 최대 시간 |
| `MOAJWO_DB_POOL_RECYCLE_S` | 240 | Neon 휴면(5분)보다 짧게 연결을 새로 맺음 |
| `MOAJWO_DB_CONNECT_TIMEOUT_S` | 15 | 휴면에서 깨는 시간 포함 |

- 비밀번호에 `@ : / ? # %` 같은 문자가 있으면 주소 안에서 퍼센트 인코딩합니다 (`@` → `%40`). 시험에서 이런 문자를 넣은 비밀번호로 확인했습니다.
- 주소는 서버(Render 서비스)의 비밀 환경 변수에만 둡니다. 저장소·화면 번들·문서·캡처에 넣지 않습니다. 앱은 주소·비밀번호를 응답·로그·오류 문구에 쓰지 않습니다 (시험으로 확인).
- 파일 읽기 한도(`MOAJWO_MAX_…`)는 `docs/MEMORY.md`(PR #31)의 Render 값으로 낮춥니다.

## 4. 연결 관리와 장애 때 동작

- 끊긴 연결(휴면·네트워크)은 풀에서 꺼낼 때 확인해 버리고 새로 맺습니다. 이 확인은 문장 실행 전이라 쓰기를 두 번 보내지 않습니다. **문장 단위 자동 재시도는 하지 않습니다** — 결과를 모르는 쓰기를 다시 실행하지 않게.
- 서버 쪽 준비된 문장(prepare)은 끕니다 — 풀러를 거쳐도 연결마다 상태가 남지 않게.
- DB에 닿지 못하면(휴면에서 깨지 못함·인증 실패·연결 거절) 모든 요청이 **503 "데이터베이스에 연결하지 못했습니다 — 잠시 뒤 다시 시도하세요. 계정·자료는 그대로입니다."** 입니다.
  - 로그인은 '비밀번호가 틀림'으로, 상태 확인은 '최초 설정 필요'로, 가입·설정은 '이미 계정이 있음'으로 보이지 않습니다.
  - `/health`도 503 (`database: error`). 화면은 처음 상태 확인이 실패하면 최초 설정 화면을 열지 않고 로그인 화면에 이유를 보입니다.
- 최초 관리자 설정은 계정과 같은 트랜잭션에서 고정 키(`app_settings.owner_setup`)를 넣어, 동시에 여러 요청이 와도 소유자는 하나입니다 (PostgreSQL에서 8건 동시 요청 → 201 하나, 409 일곱, 계정 1개).

## 5. DB 계정과 스키마

- **계정 분리:** 앱 계정(`moajwo_app`)은 데이터 읽기·쓰기만, 스키마 변경은 소유 계정(Neon 기본 `neondb_owner`)으로 마이그레이션 때만 씁니다. Neon SQL 편집기에서 소유 계정으로 한 번 실행합니다 (비밀번호는 Neon이 만든 값 또는 충분히 긴 무작위 값):

```sql
CREATE ROLE moajwo_app LOGIN PASSWORD '…';
GRANT USAGE ON SCHEMA public TO moajwo_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO moajwo_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO moajwo_app;
-- 이미 만든 표가 있으면 (복원 뒤 등)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO moajwo_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO moajwo_app;
```

- **스키마 초기화:** 빈 DB에 `alembic upgrade head`를 실제로 실행합니다 (기동 때 자동). 표를 맞추지 않고 `alembic stamp head`만 하지 않습니다.
  - 확인 (PostgreSQL 17.11, TLS·channel_binding): 마이그레이션 19개가 빈 DB → `0019_analysis_runs`까지 통과, 마이그레이션 결과와 앱 모델의 차이 0건 (열 타입 비교 포함).
  - 기존 SQLite 마이그레이션의 뜻은 바꾸지 않았습니다 (PostgreSQL 전용 초기화 경로 없음).
  - 여러 인스턴스가 함께 기동해도 마이그레이션은 하나씩 (PostgreSQL 잠금 — 직접 연결이어야 함).
- **시간:** PostgreSQL은 시간대를 붙여 저장·반환합니다. 앱은 시간대가 붙은 값을 그대로 비교하고, 시간대 없는 SQLite 값만 저장 규칙(UTC 또는 KST 벽시계, `app/timefmt.py`)으로 해석합니다. 저장 뒤 같은 순간인지 시험으로 확인했습니다 (9시간 밀림 없음).

## 6. 파일 보존 (이번 범위 밖)

- DB를 외부로 옮겨도 업로드 원본·결과 파일은 서버 디스크에 있습니다. Render 무료 서비스는 재배포·재시작·휴면 때 지워집니다.
- 기록은 있는데 파일이 없으면 **410**과 안내를 돌려줍니다 (500·무한 로딩 없음, 다른 파일로 잇지 않음, 계정·기록을 지우지 않음):
  - 원본: "원본 파일이 서버에 없습니다 — … 같은 원본을 다시 올리면 새 파일로 이어서 쓸 수 있습니다 (계정·검토·실행 기록은 그대로)."
  - 결과: "결과 파일이 서버에 없습니다 — … 실행 기록·변경 내역은 그대로이며, 원본을 다시 올려 다시 실행하면 새 결과 파일을 만듭니다."
- DB의 경로가 남은 것만으로 '파일 보존 성공'으로 보지 않습니다. 파일 보존은 별도 후속입니다: 무료 객체 저장소의 용량·전송·휴면·접근 정책 비교, 비공개 저장·소유자 권한 검사·내려받기 경로·백업·복구 포함. 유료 디스크나 DB에 파일을 통째로 넣는 방식은 쓰지 않습니다.

## 7. 백업·복원 (무료 플랜의 자동 복구에만 기대지 않음)

PostgreSQL 클라이언트 도구(서버와 같은 주 판 이상, 17)를 사용자 PC에 두고, 직접 연결·소유 계정 주소로 받습니다:

```sh
pg_dump --format=custom --no-owner --no-privileges --file=moajwo-YYYYMMDD.dump "$MIGRATION_URL"
# 격리 복원 확인 — 빈 시험 DB(운영·시연 DB 아님)에 복원한 뒤 그 DB로 앱을 띄워 로그인·건수를 본다
pg_restore --no-owner --no-privileges --exit-on-error --dbname="$TEST_RESTORE_URL" moajwo-YYYYMMDD.dump
```

- 백업 파일에는 계정·비밀번호 해시·업무 기록이 들어 있습니다. 접근이 제한된, 사용자가 관리하는 저장소에 둡니다 (Render 디스크·Git·공유 폴더 아님).
- 로컬 확인: 백업(85KB) → 다른 빈 DB로 복원 → 그 DB로 띄운 앱에 같은 계정으로 로그인, 계정 수 원본과 같음, 원본 DB는 그대로.

## 8. 기존 계정 이관 (SQLite → PostgreSQL)

**먼저 원천을 정합니다.**
- 지금 Render 검토 서비스의 SQLite는 재배포 때 지워지므로, 이미 사라진 계정은 백업 없이 되살릴 수 없습니다 — 복원했다고 하지 않습니다.
- 로컬 운영 DB의 사용자(9명)를 동의 없이 클라우드로 복제하지 않습니다.
- 이관 대상이 정해지면 원천을 읽기 전용으로 검사하고, 이관 전 리비전·표별 건수·관계·백업 위치를 기록합니다.

**도구:** `python -m app.dbcopy --source sqlite:///<원천 파일> --target <직접 연결·소유 계정 주소> [--apply] [--report 파일]` (backend 폴더)
- 기본은 확인만 합니다 (dry-run — 쓰지 않음): 두 DB가 최신 리비전인지, 원천 무결성, 표별 건수, 대상이 비었는지.
- `--apply`: 한 트랜잭션으로 복사 → 시퀀스를 옮긴 최대 번호 다음으로 → 표별 건수·내용 해시 비교 → 모두 같을 때만 커밋. 중간에 실패하면 되돌려 대상은 그대로입니다.
- 대상에 업무 자료가 있으면 덮어쓰지 않고 멈춥니다 (같은 이관을 두 번 실행해도 여기서 막힘). 마이그레이션이 넣는 기본 자료(용어사전 기본 항목·설정 표시)만 원천 값으로 바꿉니다.
- 계정 번호·아이디·비밀번호 해시·역할·상태·인증 버전·최초 설정 표시와 업무 기록의 참조를 그대로 옮깁니다. 비밀번호를 초기화하지 않습니다. **로그인 세션은 옮기지 않습니다** — 이관 뒤 다시 로그인합니다 (계정을 다시 만들 필요 없음).
- 시간: SQLite의 시간대 없는 값에 저장 규칙대로 시간대를 붙여 옮깁니다 (통합 실행 시작·끝·자동화 수정 시각은 KST, 나머지는 UTC).
- 연습 결과 (격리 PostgreSQL 17.11, 합성 계정 4·업무 기록): 표 19개 434행 0.44초, 표별 해시 일치, 다시 적용 거절, 중간 실패 되돌림, 이관 뒤 같은 계정 번호·역할로 로그인, 남의 자료 404, 새 가입·새 업로드 번호 충돌 없음 (`diagnostics/scripts/external_db_check.py`).

**전환 순서 (각 단계 승인 필요 — 이 PR은 실제 Render 연결을 바꾸지 않음):**
1. 원천 쓰기 중지 (검토 서비스 공지·정지)
2. 원천 최종 백업과 읽기 전용 검사
3. Neon에 빈 DB·앱 계정(5장) → `alembic upgrade head` → `app.dbcopy` dry-run → `--apply` (보고서 보관)
4. 확인: 시험 계정 로그인·역할·건수, 보고서의 해시 일치
5. 연결 전환: Render API 서비스에 3장 변수 (`MOAJWO_DB_MODE=external`, 두 주소) → 배포
6. 점검: `/health`, 로그인, 기존 기록 조회, 새 업로드 1건
7. 쓰기 재개

**되돌리기 경계:**
- 쓰기 재개 **전**: Render 변수를 이전 값으로 돌리고 다시 배포 — 외부 DB에는 새 쓰기가 없어 잃는 것이 없습니다.
- 쓰기 재개 **후**: 연결 주소만 이전으로 돌리면 그사이 외부 DB에 쓴 계정·기록이 사라집니다. 이 경우는 외부 DB에 남기고 고쳐 나가거나, 7장 백업으로 복원합니다. 기존 '이미지만 되돌리면 충분'은 이 변경에 적용되지 않습니다.

## 9. 시험 (SQLite·PostgreSQL)

- backend 시험: `MOAJWO_TEST_DATABASE_URL=<빈 시험 DB 주소> python -m pytest` — SQLite 파일을 직접 여는 시험만 이유와 함께 건너뜁니다. 비우면 지금처럼 SQLite.
- 계정 보존 확인: `python diagnostics/scripts/external_db_check.py --admin-url <격리 시험 서버의 슈퍼유저 주소> --backend backend --out <폴더> --pg-bin <pg_dump 폴더>` — 실제 프로세스로 1~8단계 (스크립트 머리말).
- **하지 않은 것:** 실제 Neon 프로젝트 연결(계정 생성은 사용자), Neon 휴면에서 깨는 실제 시간, Render 실제 재배포·전환. 모두 승인 뒤의 단계입니다.
