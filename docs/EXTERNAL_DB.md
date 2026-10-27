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

계정 보존 성공을 파일 보존 성공으로 넓혀 말하지 않습니다.

## 2. 서비스 선택과 무료 한도 (공식 문서 확인: Neon 무료 조건·Render 무료 웹 서비스 2026-10-25, 나머지 2026-10-23 — 생성·전환 직전에 다시 확인)

| 서비스 | 확인한 내용 | 출처 |
|---|---|---|
| Neon Free (기본안, 2026-10-25 확인) | $0/월, 카드 불필요, 기간 제한 없음(체험판 아님). 프로젝트 100개, 프로젝트당 분기 10개, 프로젝트당 컴퓨트 월 100 CU-시간, 자동 확장 최대 2 CU, 저장 프로젝트당 1 GB·모든 프로젝트 합계 20 GB(따로 적용), 공개 전송(egress) 프로젝트당 월 5 GB, 즉시 복원 이력 6시간(변경 1 GB 한도), 수동 스냅숏 1개(예약 백업 없음), 5분 무활동 뒤 컴퓨트 정지(scale to zero — 끌 수 없음). CU-시간·전송은 청구 기간(월)마다 새로 채워짐. **한도에 걸리면:** 저장 한도를 넘으면 쓰기 차단(INSERT·UPDATE·DELETE 실패), CU-시간이나 전송을 다 쓰면 다음 청구 기간까지 컴퓨트 정지 — 어느 한도도 자료를 지우지 않음 | https://neon.com/pricing · https://neon.com/docs/introduction/plans (문서마다 표기가 조금 달라 숫자는 plans 문서 기준) |
| Neon 연결 (2026-10-23) | 모든 연결 TLS 필수. 연결 풀러(`-pooler` 주소)는 PgBouncer 트랜잭션 모드 — SET·세션 단위 잠금 불가, 마이그레이션·이관은 직접 연결 | https://neon.com/docs/connect/connect-from-any-app · https://neon.com/docs/connect/connection-pooling |
| Neon 판 (2026-10-23) | PostgreSQL 14~18, 생성 때 고름 — **17을 고른다** (이 저장소 시험이 17에서 통과) | https://neon.com/docs/postgresql/postgres-version-policy |
| Render 무료 웹 서비스 (2026-10-25) | 운영용이 아님(문서). 15분 무트래픽 뒤 정지, 약 1분 뒤 기동, 언제든 재시작될 수 있음. 재배포·재시작·정지 때 로컬 파일(SQLite 포함) 소실, 영구 디스크 불가. **SSH·Dashboard Shell·one-off job 모두 지원 안 함**, 인스턴스 1개 | https://render.com/docs/free · https://render.com/docs/ssh |
| Render 무료 Postgres (쓰지 않음, 2026-10-23) | 생성 30일 뒤 만료, 14일 유예 뒤 자료 삭제 — 계정 장기 보존 수단이 아님 | https://render.com/docs/free |

'무료 영구 보존 보장'이 아닙니다. 사용량 한도·정책 변경·장애·계정 삭제는 별개이므로 7장의 따로 받는 백업을 둡니다.
유료 전환·결제 수단 등록·여러 무료 계정 만들기·휴면을 막는 반복 요청은 하지 않습니다.

### 시험 프로젝트 기본안

| 항목 | 기본안 |
|---|---|
| 프로젝트 | 소유자가 관리하는 `moajwo-test` 전용 프로젝트. 같은 목적의 기존 프로젝트가 있으면 먼저 확인 |
| DB 판 | PostgreSQL 17 |
| 무료 조건 확인일 | 2026-10-25 (위 표). 프로젝트를 만들기 직전에 다시 확인하고 그 날짜를 승인 묶음(8-5)에 적음 |
| 계정 | 앱 계정(`moajwo_app`, 데이터만)과 이관·마이그레이션 계정(소유 계정) 분리. 연결은 TLS 암호화(`sslmode=require` — 서버 인증서·이름 **검증은 하지 않음**). 인증서 검증(`sslmode=verify-full`+`sslrootcert`, 코드 변경 없이 주소로 가능)은 전환 승인 묶음(8-5)에서 함께 결정 — `docs/SECURITY-CA-20261026.md` 5장 |
| 비밀정보 | Render에는 소유자의 비밀정보 설정 경로(환경 변수의 비밀 값)로만, 사용자 PC에서는 서비스 파일·비밀번호 파일(3-1)로만 — 채팅·PR·로그·명령 인자·캡처에 남기지 않음 |
| 전환 경로 | 기존 계정 이관(A) 또는 새 계정 등록(B) — 소유자가 전환 전에 정함 (8-0) |
| 이관 원천 (A) | Render 시험 서비스에서 확보한 일관된 DB 백업 우선. 없으면 소유자가 지정한 검증된 백업 (8-1) |
| 로컬 운영 DB | 현재 PC 운영 계정·업무 자료를 외부로 복사하지 않음 |
| 백업 위치 | `C:\Users\juho\Documents\Moajwo\backups\external-db\` 아래 접근 제한 폴더 — Git·공개 링크 제외 |
| 세션 | 이관하지 않고 다시 로그인. 계정·비밀번호 해시·역할·상태·소유 관계는 유지 |

**이 작업에서는 실제 Neon에 연결하지 않았습니다.** 소유자 Neon 계정에 접근할 수단이 없어 프로젝트 생성·연결·풀러·휴면 동작은 모두 미검증입니다 (시험은 로컬 PostgreSQL 17.11). 실제 Neon 로그인·프로젝트 생성은 소유자가 합니다.

## 3. 설정 (환경 변수)

| 변수 | 값 | 설명 |
|---|---|---|
| `MOAJWO_DB_MODE` | `external` | 외부 DB 모드. 이 값이면 `DATABASE_URL`이 없거나 PostgreSQL이 아닐 때 **기동하지 않는다** (빈 SQLite로 대신 열지 않음) |
| `DATABASE_URL` | `postgresql://moajwo_app:…@ep-…-pooler…/neondb?sslmode=verify-full&sslrootcert=system&channel_binding=require` | **앱: 연결 풀러 주소 + 앱 계정**(데이터 읽기·쓰기만). `postgres://`·`postgresql://`는 psycopg 3 드라이버로 바뀐다 |
| `MOAJWO_MIGRATION_DATABASE_URL` | `postgresql://neondb_owner:…@ep-…(풀러 아님)…/neondb?sslmode=verify-full&sslrootcert=system&channel_binding=require` | **마이그레이션: 직접 연결 + 소유 계정**(스키마를 바꿀 권한). 이관 도구(`app.dbcopy`)도 같은 직접 연결·소유 계정을 쓴다. 비우면 `DATABASE_URL` |
| `MOAJWO_DB_POOL_SIZE` · `…_MAX_OVERFLOW` | 3 · 2 | 무료 인스턴스 1개 기준의 작은 풀 |
| `MOAJWO_DB_POOL_TIMEOUT_S` | 10 | 풀에서 연결을 기다리는 최대 시간 — 넘으면 503 '바쁨' |
| `MOAJWO_DB_POOL_RECYCLE_S` | 240 | Neon 휴면(5분)보다 짧게 연결을 새로 맺음 |
| `MOAJWO_DB_CONNECT_TIMEOUT_S` | 15 | 접속 제한 시간 (휴면에서 깨는 시간 포함). 마이그레이션 접속에도 같은 값 |
| `MOAJWO_MIGRATION_LOCK_TIMEOUT_S` | 60 | 기동 때 마이그레이션이 잠금(다른 인스턴스·이관 도구)과 표 잠금을 기다리는 최대 시간 — 넘으면 기동 실패 |

- 두 주소(풀러·앱 계정 / 직접 연결·소유 계정)가 **같은 DB**를 가리키는지 전환 전에 `python -m app.dbcheck`로 확인합니다 (8-3 5단계).
- 비밀번호에 `@ : / ? # %` 같은 문자가 있으면 주소 안에서 퍼센트 인코딩합니다 (`@` → `%40`). 시험에서 이런 문자를 넣은 비밀번호로 확인했습니다.
- 주소는 서버(Render 서비스)의 비밀 환경 변수에만 둡니다. 저장소·화면 번들·문서·캡처에 넣지 않습니다. 앱은 주소·비밀번호·SQL·매개변수를 응답·로그·오류 문구에 쓰지 않습니다 (엔진 `hide_parameters`, 오류 기록은 종류·SQLSTATE만).
- 파일 읽기 한도(`MOAJWO_MAX_…`)는 `docs/MEMORY.md`(PR #31)의 Render 값으로 낮춥니다.

### 3-1. 사용자 PC의 접속 정보 — 서비스 파일·비밀번호 파일 (권장)

마이그레이션·이관·확인·백업(alembic, `app.dbcopy`, `app.dbcheck`, pg_dump·pg_restore·psql)을 사용자 PC에서 돌릴 때 **비밀번호를 명령 인자에 넣지 않습니다** — 명령 인자는 셸 기록과 프로세스 목록에 남습니다. libpq 서비스 파일에 접속 정보를, 비밀번호 파일에 비밀번호를 두고 서비스 이름으로 접속합니다. `PGPASSWORD` 환경 변수는 PostgreSQL 문서가 보안상 권장하지 않으므로 쓰지 않습니다.

| 파일 | POSIX | Windows | libpq의 권한 검사 |
|---|---|---|---|
| 서비스 파일 | `~/.pg_service.conf` | `%APPDATA%\postgresql\.pg_service.conf` (점 있음) | **어느 OS에서도 하지 않음** → 비밀번호를 넣지 않음 |
| 비밀번호 파일 | `~/.pgpass` | `%APPDATA%\postgresql\pgpass.conf` (점 없음) | POSIX: 그룹·다른 사용자에게 권한이 있으면 그 파일을 **무시**(경고) → `chmod 0600 ~/.pgpass` / Windows: **하지 않음** (안전한 폴더에 있다고 가정) |

- `%APPDATA%`는 사용자 프로필의 `AppData\Roaming` 폴더입니다. 다른 위치는 `PGSERVICEFILE`·`PGPASSFILE` 환경 변수로 지정합니다. 경로를 직접 지정하는 쪽을 권합니다 — `PGSERVICEFILE`이 없는 파일을 가리키면 `service file "…" not found` 오류로 멈춥니다 (기본 위치의 파일이 없을 때는 조용히 건너뜀). `PGPASSFILE`의 파일이 없으면 오류 없이 비밀번호 없이 접속하다가 인증에서 실패합니다.
- **Windows는 권한을 검사하지 않으므로** 두 파일을 사용자 프로필 폴더(`%APPDATA%\postgresql`)에 두고 본인만 읽게 줄입니다 (서비스 파일 `.pg_service.conf`도 같게):
  - cmd: `icacls "%APPDATA%\postgresql\pgpass.conf" /inheritance:r /grant:r "%USERNAME%:F"`
  - PowerShell: `icacls "$env:APPDATA\postgresql\pgpass.conf" /inheritance:r /grant:r "${env:USERNAME}:F"` — PowerShell은 `%APPDATA%`를 펼치지 않아 cmd 형태를 그대로 쓰면 경로를 찾지 못합니다 (종료 3).
- 비밀번호 파일은 한 줄에 `호스트:포트:DB:계정:비밀번호` — 앞 네 칸은 `*`(아무 값)도 되고, 칸 안의 `:`와 `\`는 `\`로 이스케이프합니다. 맞는 첫 줄의 비밀번호를 씁니다.
- 서비스 파일은 `[이름]` 아래 `키=값` 줄입니다 (`=` 앞뒤에 공백 없이, 주석은 `#`로 시작하는 줄만). 연결 문자열에 직접 준 값(예: `postgresql:///다른DB?service=이름`의 DB 이름)이 서비스 파일 값보다 앞섭니다.

예 — 값의 모양만 적었습니다 (실제 호스트·계정은 소유자가 Neon 콘솔에서 확인):

```ini
# 마이그레이션·이관·백업: 직접 연결(풀러 아님) + 소유 계정
[moajwo-migrate]
host=ep-….neon.tech
port=5432
dbname=neondb
user=neondb_owner
sslmode=verify-full
sslrootcert=%APPDATA%\postgresql\isrgrootx1.pem
channel_binding=require
# 같은 DB 확인용: 연결 풀러 + 앱 계정 (앱 자체는 Render 환경 변수 DATABASE_URL을 씀)
[moajwo-app]
host=ep-…-pooler.….neon.tech
port=5432
dbname=neondb
user=moajwo_app
sslmode=verify-full
sslrootcert=%APPDATA%\postgresql\isrgrootx1.pem
channel_binding=require
```

```text
ep-….neon.tech:5432:neondb:neondb_owner:<소유 계정 비밀번호>
ep-…-pooler.….neon.tech:5432:neondb:moajwo_app:<앱 계정 비밀번호>
```

사용 (backend 폴더, PowerShell — POSIX sh는 `export PGSERVICEFILE=~/.pg_service.conf PGPASSFILE=~/.pgpass`):

```powershell
$env:PGSERVICEFILE = "$env:APPDATA\postgresql\.pg_service.conf"
$env:PGPASSFILE = "$env:APPDATA\postgresql\pgpass.conf"
$env:MOAJWO_DB_MODE = "external"; $env:DATABASE_URL = "postgresql:///?service=moajwo-migrate"
python -m alembic upgrade head          # 직접 연결·소유 계정으로 마이그레이션
Remove-Item Env:DATABASE_URL, Env:MOAJWO_DB_MODE
python -m app.dbcheck --app-service moajwo-app --migration-service moajwo-migrate
python -m app.dbcopy --source sqlite:///<원천 파일> --target "postgresql:///?service=moajwo-migrate"
psql --no-password "service=moajwo-migrate" -c "SELECT count(*) FROM users"
```

- `--no-password`(`-w`): 비밀번호 파일이 맞지 않으면 비밀번호를 묻지 않고 바로 실패합니다 (묻는 창에 비밀번호를 치지 않음, 스크립트가 묻는 창에서 멈추지 않음).
- **호환 — 권장하지 않음:** 비밀번호가 든 주소(`postgresql://계정:비밀번호@…`)도 동작은 합니다. `app.dbcopy --target`에 넣으면 stderr로 경고하고 보고에는 `target_secret_in_args: true`만 남깁니다 (주소는 남기지 않음). 주소를 꼭 써야 하면 `--target-env 변수 이름`처럼 환경 변수로 넘깁니다.
  - 그 환경 변수도 **손으로 치지 않습니다** — `$env:X = "postgresql://…"`·`export X=…` 같은 줄은 셸 기록(PowerShell PSReadLine 기록 파일, bash 기록)에 남습니다. 본인만 읽게 줄인 파일에서 읽어 넣고 끝나면 지웁니다:
    PowerShell `$env:MOAJWO_DBCOPY_TARGET = (Get-Content -Raw <접근 제한 파일>).Trim()` … `Remove-Item Env:MOAJWO_DBCOPY_TARGET` / sh `export MOAJWO_DBCOPY_TARGET="$(cat <접근 제한 파일>)"` … `unset MOAJWO_DBCOPY_TARGET`. 서비스 주소가 먼저입니다.
  - 이름 자리(`--target-env`·`app.dbcheck --app-env`·`--app-service` 등)에 주소를 넣는 실수(`--target-env "$MIGRATION_URL"`)는 받은 값을 출력·보고에 되풀이하지 않고 멈춥니다 (dbcopy는 그 인자에 비밀번호가 있었으면 경고하고 `target_secret_in_args: true`로 표시 — 시험).
- 로컬 확인 (2026-10-25, Windows, PostgreSQL 17.11 서버, psql 17·psycopg의 libpq 18, 합성 파일): 서비스 주소로 alembic 마이그레이션·`app.dbcopy` dry-run(경고 없음)·`app.dbcheck`·pg_dump(`PGSERVICE`)·pg_restore(`--dbname=service=…`)·psql이 비밀번호 파일의 비밀번호로 접속, 빈 비밀번호 파일 → `fe_sendauth: no password supplied`, 없는 `PGSERVICEFILE` → `service file "…" not found`. icacls는 위 두 형태를 그대로(cmd는 배치 파일, PowerShell은 PowerShell 5.1) 일회용 파일에 실행해 권한 항목이 본인 하나(`<PC>\<사용자>:(F)`)가 되는 것과, PowerShell에 `%APPDATA%` 형태를 쓰면 종료 3으로 실패하는 것을 확인했습니다 (`%APPDATA%` 자리는 일회용 폴더). POSIX의 0600 검사(그룹·다른 사용자 권한이 있으면 무시)는 이 PC(Windows)에서 시험하지 않았습니다 — PostgreSQL 문서 기준입니다.

### 3-2. TLS 검증 — `verify-full` 기본 (2026-10-27)

`sslmode=require`는 **암호화만** 보장하고 서버가 누구인지는 확인하지 않습니다 (libpq 문서 표: 도청 보호 예, 중간자 보호 아니오.
root 파일이 있으면 verify-ca처럼 검증하는 호환 동작이 있지만 문서가 "의존하지 말 것"이라 합니다). Neon도 "항상 verify-full"을
권장합니다. 그래서 외부 DB 주소는 모두 `sslmode=verify-full`을 기본으로 하고, 신뢰 저장소는 아래 표대로 둡니다.
(참조: https://www.postgresql.org/docs/17/libpq-ssl.html · https://www.postgresql.org/docs/17/libpq-connect.html ·
https://neon.com/docs/connect/connect-securely — 2026-10-27 확인)

| 어디서 | 설정 | 신뢰 저장소 | 비고 |
|---|---|---|---|
| Render의 backend(`DATABASE_URL`·`MOAJWO_MIGRATION_DATABASE_URL`) | `sslmode=verify-full&sslrootcert=system&channel_binding=require` | 이미지의 OS 저장소(Debian `ca-certificates` — Mozilla 묶음, ISRG Root X1 포함; PR #50이 빌드 때 보안 갱신) | `sslrootcert=system`은 libpq 16+ (psycopg-binary 3.3.6의 libpq 18). system이면 verify-full보다 약한 sslmode는 **오류**(libpq 문서) — 실수로 낮추지 못함 |
| 사용자 PC(Windows)의 psql·pg_dump·`app.dbcopy` (3-1 서비스 파일) | `sslmode=verify-full` + `sslrootcert=<파일>` | Windows에는 드라이버가 쓸 OS root 파일이 없음(Neon 문서) → Let's Encrypt의 ISRG Root X1(`https://letsencrypt.org/certs/isrgrootx1.pem`, 2030-06-04까지)을 받아 `%APPDATA%\postgresql\isrgrootx1.pem`에 둠 | 받은 파일은 `openssl x509 -in isrgrootx1.pem -noout -subject -enddate`로 `CN = ISRG Root X1`·`notAfter=Jun  4 … 2030`을 확인. Neon이 CA를 바꾸면 이 파일도 바꿔야 함 |
| 앱 코드 | 바꾸지 않음 — `config._database`는 주소의 연결 옵션을 그대로 전달(시험 `test_postgres_url_keeps_verify_full_and_system_root_options`) | | |

**Neon 직접 연결과 연결 풀러** (둘 다 TLS 필수, 둘 다 `verify-full`):

| 주소 | 쓰는 곳 | 이유 |
|---|---|---|
| 직접 `ep-….neon.tech` + 소유 계정 | 마이그레이션(`MOAJWO_MIGRATION_DATABASE_URL`)·`app.dbcopy`·백업·복원 | 세션 단위 잠금(advisory lock)·`SET`·`LOCK TABLE`을 쓴다 — PgBouncer 트랜잭션 모드 풀러는 이를 보장하지 않음 |
| 풀러 `ep-…-pooler.….neon.tech` + 앱 계정 | 앱(`DATABASE_URL`) | 무료 인스턴스의 짧은 연결을 풀러가 모음. 준비된 문장은 끈다(4장) |

`channel_binding=require`는 SCRAM 인증일 때 TLS 위에서 서버를 한 번 더 인증합니다(SCRAM-SHA-256-PLUS). 직접 연결에서는
확인됐고(격리 매트릭스), **풀러 주소에서 지원되는지는 실제 Neon에서 확인해야 합니다** — 전환 때 풀러 주소로 실패하면 풀러
주소에서만 `channel_binding=prefer`로 낮추고 그 사실을 승인 묶음(8-5 12번)에 적습니다. `verify-full`은 낮추지 않습니다.

**격리 매트릭스(실제 Neon 아님):** `diagnostics/ci/pg_tls_check.sh`(워크플로 `pg-tls (diagnostic)`, `ci/pg-tls-*` 브랜치)가 격리
러너 안의 일회용 PostgreSQL 17(자체 서명 CA, 이름 `localhost`, 암호화 연결·SCRAM만)에 psycopg로 붙어 다음을 보입니다 —
`require`는 모르는 CA도 통과(신원 확인 없음), `verify-ca`는 이름이 달라도 통과, `verify-full`은 이름이 다르면 거절,
`sslrootcert=system`은 사설 CA 거절(공인 CA는 OS 저장소에 있을 때 통과 — 여기서는 미검증), `system`+`require`는 오류,
`disable`은 서버가 거절, `channel_binding=require`는 SCRAM에서 통과. 결과 표는 워크플로 요약·아티팩트 `pg-tls`에 남습니다.
실행 기록: run 40446062371(`a5e3cff`, PostgreSQL 17.11·psycopg 3.3.6·libpq 18.0.6·OpenSSL 3.0.13) — 11가지 모두 기대와 같음, `verify-full`+host 불일치는 "server certificate for \"localhost\" … does not match host name"로, `system`+`require`는 "weak sslmode \"require\" may not be used with sslrootcert=system"으로 거절됐습니다 (사본 `backups/review-20261027/pg-tls/ci-a5e3cff/`).

**전환 때 실제 확인(승인 뒤, 8-3 단계 안에서):** 사용자 PC에서 서비스 파일로 `psql "service=moajwo-migrate" -c "SELECT ssl,
version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid()"` → `ssl = t`와 TLS 판을 적고, Render의 backend는 배포 뒤
`/health`(`database: ok`)와 로그의 접속 성공으로 확인합니다. 비밀번호·주소는 어디에도 적지 않습니다.

## 4. 연결 관리와 장애 때 동작

- 끊긴 연결(휴면·네트워크)은 풀에서 꺼낼 때 확인해 버리고 새로 맺습니다. 이 확인은 문장 실행 전이라 쓰기를 두 번 보내지 않습니다. **문장 단위 자동 재시도는 하지 않습니다** — 결과를 모르는 쓰기를 다시 실행하지 않게. 화면도 POST를 자동으로 다시 보내지 않습니다.
- 서버 쪽 준비된 문장(prepare)은 끕니다 — 풀러를 거쳐도 연결마다 상태가 남지 않게.
- 연결·자원 장애만 503으로 바꾸고, 데이터·코드·스키마 오류는 감추지 않습니다 (그대로 500):

| 종류 | 예 | 응답 |
|---|---|---|
| 닿지 못함 | 연결 실패·끊김, 인증 실패(SQLSTATE 28), DB 없음(3D), 서버 종료(57P01~03), SQLite 파일을 열 수 없음 | 503 "데이터베이스에 연결하지 못했습니다 — 잠시 뒤 다시 시도하세요." |
| 바쁨 | 연결 풀 대기 초과, 잠금 대기 초과(55P03 — 이관 중 등), 문장 시간 초과(57014), 자원 부족(53), 직렬화·교착(40001·40P01), SQLite `database is locked` | 503 "데이터베이스가 다른 작업으로 바빠 처리하지 못했습니다 — 잠시 뒤 다시 시도하세요." |
| 그 밖 | 값 범위 초과(22), 없는 표(42P01·`no such table`) 등 | 500 (연결 장애로 보이게 하지 않음) |

- **쓰기 요청(POST·PUT·PATCH·DELETE)의 503**에는 "이 요청이 반영됐는지는 확인하지 못했습니다 — 화면을 새로 고쳐 반영 여부를 확인한 뒤 필요하면 다시 하세요."를 덧붙입니다. 커밋 응답 전에 연결이 끊기면 반영 여부를 모르므로 '계정·자료는 그대로입니다'라고 단정하지 않습니다.
- 로그인은 '비밀번호가 틀림'으로, 상태 확인은 '최초 설정 필요'로, 가입·설정은 '이미 계정이 있음'으로 보이지 않습니다. `/health`도 503 (`database: error`). 화면은 처음 상태 확인이 실패하면 최초 설정 화면을 열지 않고 로그인 화면에 이유를 보입니다.
- 기동: 마이그레이션 접속은 `MOAJWO_DB_CONNECT_TIMEOUT_S`, 잠금 대기는 `MOAJWO_MIGRATION_LOCK_TIMEOUT_S` 뒤 실패해 프로세스가 끝납니다 (서버는 마이그레이션 뒤에 뜨므로, 멈춘 채 정상 기동처럼 보이지 않음 — 시험으로 확인).
- 최초 관리자 설정은 계정과 같은 트랜잭션에서 고정 키(`app_settings.owner_setup`)를 넣어, 동시에 여러 요청이 와도 소유자는 하나입니다 (PostgreSQL에서 8건 동시 요청 → 201 하나, 409 일곱, 계정 1개).

## 5. DB 계정과 스키마

- **계정 분리:** 앱 계정(`moajwo_app`)은 데이터 읽기·쓰기만, 스키마 변경·이관은 소유 계정(Neon 기본 `neondb_owner`)으로만 씁니다. 소유 계정으로 한 번 실행합니다:

```sql
CREATE ROLE moajwo_app LOGIN;
GRANT USAGE ON SCHEMA public TO moajwo_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO moajwo_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO moajwo_app;
-- 앱 계정의 서버 쪽 제한 시간: 잠금(이관 중 등)·긴 문장·버려진 트랜잭션에서 무한히 기다리지 않게 (503 '바쁨'으로 끝남)
ALTER ROLE moajwo_app SET lock_timeout = '5s';
ALTER ROLE moajwo_app SET statement_timeout = '60s';
ALTER ROLE moajwo_app SET idle_in_transaction_session_timeout = '600s';
-- 이미 만든 표가 있으면 (복원 뒤 등)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO moajwo_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO moajwo_app;
```

- **앱 계정 비밀번호는 평문 SQL로 보내지 않습니다.** `CREATE/ALTER ROLE … PASSWORD '평문'`은 서버 설정(`log_statement`, 실패한 문장 기록)에 따라 서버 로그에 평문이 남을 수 있습니다 (PostgreSQL `CREATE ROLE` 문서의 주의). psql에서 `\password moajwo_app`을 쓰면 psql이 클라이언트에서 암호화한 값(SCRAM 검증자)을 보냅니다 — 비밀번호는 묻는 창에 넣고 명령 인자·셸 기록에 넣지 않습니다. Neon 콘솔의 역할 관리로 만들고 Neon이 만든 비밀번호를 쓰는 방법도 있습니다 (그 화면의 동작은 전환 때 확인). 시험 스크립트·시험도 같은 방식(클라이언트에서 만든 검증자)으로 보냅니다.
- **`idle_in_transaction_session_timeout`은 600초:** 원본을 읽는 요청(미리보기·열 매핑 등)은 원본 기록을 읽은 트랜잭션을 연 채 읽기 차례를 기다리고(최대 `MOAJWO_PARSE_WAIT_S` 60초) 파일을 읽습니다 (`app/files.py`의 `load_parsed`). 이 제한을 60초로 두면 차례를 다 기다린 정상 요청도 서버가 끊어 'DB에 연결하지 못함'(503)이 됩니다 — Render 무료 인스턴스는 0.1 CPU라 큰 파일 읽기 자체도 러너(약 6초)보다 길 수 있습니다 (추정, Render에서 재지 않음). 이 값은 버려진 연결이 잠금을 쥐고 있는 시간의 상한이라 10분으로 둡니다.
- 앱 계정은 시퀀스를 바꿀 수 없습니다 (`ALTER SEQUENCE` 42501 — 확인). 이관 도구는 소유 계정으로 돌립니다.
- **스키마 초기화:** 빈 DB에 `alembic upgrade head`를 실제로 실행합니다 (기동 때 자동). 표를 맞추지 않고 `alembic stamp head`만 하지 않습니다.
  - 시험(`tests/test_migrations_postgres.py`): 빈 PostgreSQL → head, 마이그레이션 결과와 앱 모델의 차이 0건(열 타입 포함), 기본 용어사전 자료가 SQLite head와 같음.
  - 기존 SQLite 마이그레이션의 뜻은 바꾸지 않았습니다 (PostgreSQL 전용 초기화 경로 없음).
  - 여러 인스턴스가 함께 기동해도 마이그레이션은 하나씩 (PostgreSQL 잠금 — 직접 연결이어야 함). 이관 도구와도 같은 잠금을 씁니다.
- **시간:** PostgreSQL은 시간대를 붙여 저장·반환합니다. 앱은 시간대가 붙은 값을 그대로 비교하고, 시간대 없는 SQLite 값만 저장 규칙(UTC 또는 KST 벽시계, `app/timefmt.py`)으로 해석합니다. 저장 뒤 같은 순간인지 시험으로 확인했습니다 (9시간 밀림 없음).

## 6. 파일 보존 (이번 범위 밖)

- DB를 외부로 옮겨도 업로드 원본·결과 파일은 서버 디스크에 있습니다. Render 무료 서비스는 재배포·재시작·휴면 때 지워집니다.
- 기록은 있는데 파일이 없으면 **410**과 안내를 돌려줍니다 (500·무한 로딩 없음, 다른 파일로 잇지 않음, 계정·기록을 지우지 않음):
  - 원본: "원본 파일이 서버에 없습니다 — … 같은 원본을 다시 올리면 새 파일로 이어서 쓸 수 있습니다 (계정·검토·실행 기록은 그대로)."
  - 결과: "결과 파일이 서버에 없습니다 — … 실행 기록·변경 내역은 그대로이며, 원본을 다시 올려 다시 실행하면 새 결과 파일을 만듭니다."
- DB의 경로가 남은 것만으로 '파일 보존 성공'으로 보지 않습니다. 파일 보존은 별도 후속입니다: 무료 객체 저장소의 용량·전송·휴면·접근 정책 비교, 비공개 저장·소유자 권한 검사·내려받기 경로·백업·복구 포함. 유료 디스크나 DB에 파일을 통째로 넣는 방식은 쓰지 않습니다.

## 7. 백업·복원 (무료 플랜의 자동 복구에만 기대지 않음)

PostgreSQL 클라이언트 도구(서버와 같은 주 판 이상, 17)를 사용자 PC에 두고, **직접 연결·소유 계정**의 서비스(3-1의 `moajwo-migrate`)로 받습니다. 비밀번호는 비밀번호 파일에서만 읽고 명령 인자·셸 기록에 남기지 않습니다.

```sh
PGSERVICE=moajwo-migrate pg_dump --no-password --format=custom --no-owner --no-privileges --file=moajwo-YYYYMMDD.dump
sha256sum moajwo-YYYYMMDD.dump > moajwo-YYYYMMDD.dump.sha256
# 복원 확인 — 빈 시험 DB(운영·시연 DB 아님)를 가리키는 서비스(예: moajwo-restore-test)에 복원한 뒤 그 DB로 앱을 띄워 로그인·건수를 본다
pg_restore --no-password --no-owner --no-privileges --exit-on-error --dbname="service=moajwo-restore-test" moajwo-YYYYMMDD.dump
psql --no-password "service=moajwo-restore-test" -c "SELECT count(*) FROM users"
```

- PowerShell: `$env:PGSERVICE = "moajwo-migrate"` 뒤 같은 pg_dump 명령(끝나면 `Remove-Item Env:PGSERVICE`), 해시는 `Get-FileHash -Algorithm SHA256 moajwo-YYYYMMDD.dump`.
- **복구를 증명합니다** — 백업 파일이 있다는 것만으로 완료로 보지 않습니다: 다른 빈 DB로 복원 → 앱 계정 권한 부여(5장) → 그 DB로 띄운 앱에 같은 계정으로 로그인 → 역할·상태·소유 관계(남의 자료 404)·표별 건수·내용 해시가 원본과 같음 → 원본 DB는 그대로.
- 백업 파일에는 계정·비밀번호 해시·업무 기록이 들어 있습니다. 2장 백업 위치에 두고 Render 디스크·Git·공유 폴더에 두지 않습니다.
- 격리 확인 (2026-10-25, 로컬 PostgreSQL 17.11, 합성 계정): `PGSERVICE`로 백업(85KB) → `--dbname=service=…`로 다른 빈 DB에 복원 → psql(서비스)로 users 5행 → 그 DB로 띄운 앱에 같은 계정 로그인, 계정 수 원본과 같음, 원본 DB 그대로 (`diagnostics/scripts/external_db_check.py` 8단계).

## 8. 전환 — 기존 계정 이관(A) 또는 새 계정 등록(B)

### 8-0. 경로를 먼저 정한다 (소유자 결정, 전환 전)

| | (A) 기존 계정 이관 | (B) 새 계정 등록 |
|---|---|---|
| 조건 | 지금 Render SQLite의 **검증된 백업이 있을 때만** (8-1) | 백업이 없거나, 새로 시작하기로 정했을 때 |
| 준비 | 원천 확보(8-1) → `app.dbcopy` dry-run → 적용 → 7장 복원 확인 | 빈 DB에 마이그레이션만 (이관 없음) |
| 전환 직후 `GET /auth/status` | `setup_required: false` — 이관한 계정으로 로그인 | `setup_required: true` — 첫 소유자를 만들 때까지 이 값이 **정상** (실패가 아님) |
| 계정·기록 | 계정·비밀번호 해시·역할·상태·업무 기록을 그대로 옮김 (로그인 세션만 제외) | 새로 만든 계정만 있음 — 예전 비밀번호·업무 기록은 **돌아오지 않음** |

- 어느 경로든 전환 배포(재배포)가 지금 Render SQLite와 서버 파일을 지웁니다. (A)는 그 전에 원천을 확보해야 하고, (B)는 소유자가 그 손실을 알고 정합니다.
- (B)에서 계정을 새로 만드는 것은 예전 비밀번호나 업무 기록을 되살리는 것이 아닙니다. 결과 보고에 '새로 만든 계정'으로 적고 '복구'라고 하지 않습니다.
- 로컬 운영 DB의 사용자(9명)는 어느 경로에서도 동의 없이 클라우드로 복제하지 않습니다.

### 8-1. (A만) 원천을 먼저 확보한다

- **원천은 API 서비스(`moajwo-test-api`)가 쓰던 Render DB입니다** — 프런트(`moajwo-test`·`moajwo-test-web`)는 nginx라 DB가 없습니다. 계정 DB가 실제로 이 서비스의 것인지 전환 전에 확인합니다. 로컬 운영 SQLite 백업을 Render 계정의 백업으로 보지 않고, 두 DB를 합치지 않습니다.
- 지금 Render API 서비스의 SQLite는 **재시작·재배포·휴면(15분 무트래픽)으로 사라질 수 있습니다.** 백업을 확보하기 전에 서비스를 멈추거나 재배포하지 않습니다 (Render 환경 변수 저장의 'deploy' 선택지도 재배포 — 8-3. Auto-Deploy는 2026-10-25 조회 때 세 서비스 모두 Off였지만 당시 관측입니다 — 8-3 'Render 서비스').
- **무료 환경에서 쓸 수 있는 추출 수단:** Render Free는 SSH·Dashboard Shell·one-off job을 지원하지 않습니다. API 서비스의 연결 브랜치는 main이고(2026-10-25 조회 — 배포된 커밋은 대시보드의 마지막 배포로 확인), main의 앱에는 DB 내보내기 기능이 없습니다 (내려받기는 용어사전 CSV·통합·분석 결과 파일뿐이고, 관리자 화면은 계정 목록·이력만 보이며 비밀번호 해시를 내보내지 않음 — 코드 확인). 그래서 **현재 Render SQLite에서 계정을 그대로(해시 포함) 꺼낼 수 있는 안전한 무료 수단은 없습니다.** 내보내기 기능을 넣으려고 먼저 재배포하면 그 재배포가 SQLite를 지우므로 하지 않습니다.
- 이미 확보된 Render DB 백업은 이 저장소 기록에 없습니다. 소유자가 따로 가진 검증된 백업이 있으면 그것이 원천입니다 (경로 A). 없으면 **Render 계정은 이관할 수 없으므로 경로 B(새 계정 등록)** 입니다 — 결과 보고에서 '새로 만든 계정'과 '이관한 계정'을 나눠 적습니다. 이미 사라진 계정은 백업 없이 복구했다고 하지 않습니다.
- 원천이 정해지면: 원천 쓰기가 멈춘 시점(보호 시점)을 기록하고, 원천 파일의 sha256·크기·리비전·표별 건수·보관 위치를 남깁니다.

### 8-2. (A만) 이관 도구

backend 폴더에서 (대상은 **직접 연결·소유 계정**):

```sh
python -m app.dbcopy --source sqlite:///<원천 파일> --target "postgresql:///?service=moajwo-migrate" [--apply] [--report 파일]
python -m app.dbcopy --source sqlite:///<원천 파일> --target-env <주소가 든 환경 변수 이름> [--apply] [--report 파일]
```

- **대상 주소 입력 — 비밀번호를 명령 인자에 두지 않음:** 서비스 주소(3-1의 서비스 파일·비밀번호 파일) 또는 `--target-env`(환경 변수에서 읽음), 둘 중 하나만. 비밀번호가 든 `--target` 주소도 호환으로 동작하지만 DB에 닿기 전에 stderr로 경고하고, 보고에는 주소 없이 `target_input`과 `target_secret_in_args`(참·거짓)만 남깁니다.
- 기본은 확인만 합니다 (dry-run — 쓰지 않음): 두 DB가 최신 리비전인지, 원천 스냅숏 무결성, 표별 건수, 대상이 비었는지, 시간대 출처 확인이 필요한 실행 기록 후보, 대상 열 길이를 넘는 원천 값, NUL(0x00)이 든 원천 글자.
- **원천 값 검사 (확인만·적용 모두 같은 '확인' 단계 — 쓰기 전):** 대상 열 길이(varchar n)를 넘는 값(보고 `too_long_values` — 표·열·한도·건수·가장 긴 글자 수·정수 행 번호)이나 NUL(0x00)이 든 글자(보고 `nul_values` — 표·열·건수·정수 행 번호, 길이 제한이 없는 Text 열 포함)가 하나라도 있으면 멈춥니다 (종료 1, 대상 표·시퀀스 그대로). NUL이 든 값은 글자 수를 셀 수 없어, 길이 제한이 있는 열은 한도(`limit`)와 바이트 수가 한도를 넘는 행 수(`length_unverified` — NUL을 지운 뒤에도 길이를 넘을 수 있는 행)를 함께 남깁니다 (UTF-8에서 글자 수 ≤ 바이트 수라 나머지 행은 한도 안). SQLite는 길이를 강제하지 않고 NUL도 저장하지만 PostgreSQL은 둘 다 저장하지 못합니다. SQLite의 `length()`는 NUL 앞까지만 세므로(NUL 뒤가 긴 값도 짧게 보임) NUL은 바이트로 따로 찾습니다. 값을 자르거나 NUL을 지워서 옮기지 않습니다 — 원천 사본을 고칠지·스키마를 넓힐지는 소유자가 정합니다. 문자열 기본 키 표(`app_settings`)는 키가 원문이라 건수만 남깁니다. 시간대 출처 후보와 함께 있으면 모두 보고에 채운 뒤 한 번에 멈춥니다. 원천 SQLite의 글자 인코딩이 UTF-8이 아니면(`PRAGMA encoding`, 보고 `source_encoding`) 이 검사들 전에 그 이유로 멈춥니다 — 바이트로 찾는 NUL 검사가 UTF-16에서는 NUL이 없는 값도 잡기 때문입니다 (앱·alembic이 만든 DB는 UTF-8).
- **원천 보호:** 원천 파일은 읽기 전용으로만 엽니다. SQLite 백업 API로 임시 스냅숏(한 시점)을 만들고 무결성을 검사한 뒤 스냅숏에서 읽습니다. 스냅숏 해시와 원천 파일 해시(앞뒤 — 바뀌지 않았음)를 보고에 남깁니다. 읽기 전용 연결만으로 일관성이 보장된다고 보지 않습니다.
- **보고 파일:** 원천 DB·저널·WAL·SHM과 같은 파일(상대 경로·대소문자·하드링크·심볼릭 링크 포함)이면 DB를 열기 전에 거절합니다. 이미 있는 파일은 이 도구의 보고서일 때만 바꾸고(백업 자료 등은 거절), 같은 폴더의 임시 파일에 다 쓴 뒤 교체합니다. 보고 위치에 쓸 수 없으면 DB에 닿기 전에 멈춥니다.
- `--apply`는 트랜잭션 하나: 이관 잠금(이 도구·마이그레이션끼리) → 모든 표 ACCESS EXCLUSIVE 잠금(앱 요청은 잠금 대기 제한 뒤 503 '바쁨') → **잠금 안에서** 리비전·대상이 비었는지 다시 확인 → 복사 → 시퀀스 `ALTER SEQUENCE … RESTART`(트랜잭션과 함께 되돌려짐 — `setval`은 rollback으로 되돌아가지 않아 쓰지 않음) → 표별 건수·내용 해시·시퀀스 비교 → 이관 표시(`app_settings.dbcopy_applied`) → 커밋 → 새 연결로 다시 읽어 확인.
- 커밋 전 어느 단계에서 실패해도 표·시퀀스 모두 그대로입니다 (시험: 표 복사 뒤·첫 시퀀스 뒤·모든 시퀀스 뒤·내용 비교 실패·커밋 직전 연결 끊김 — 실패 전후 표 내용·시퀀스·다음 INSERT 번호 비교).
- **커밋 응답을 받지 못하면** 되돌렸다고 단정하지 않고 새 연결로 이관 표시·내용 해시를 읽어 판정합니다.

| 종료 코드 | 뜻 | 다음 행동 |
|---|---|---|
| 0 | 확인만 성공, 또는 적용하고 보고 저장 | — |
| 1 | 적용하지 않음 (확인됨 — 커밋 전 실패, 또는 커밋되지 않았음을 새 연결로 확인) | 원인을 고친 뒤 다시 실행해도 됨 |
| 3 | **적용했으나 보고 파일만 저장 실패** | 다시 실행하지 않음. 화면에 찍힌 JSON이 보고 |
| 4 | 적용 여부를 확인하지 못함, 또는 커밋 뒤 다시 읽기 실패 | 다시 실행하지 말고 대상의 `app_settings.dbcopy_applied`와 건수를 조회 |

- 대상에 업무 자료가 있거나 이관 표시가 있으면 덮어쓰지 않고 멈춥니다 (같은 원천 스냅숏인지 함께 알림). 동시에 두 번 실행해도 한 번만 적용됩니다 (시험).
- 계정 번호·아이디·비밀번호 해시·역할·상태·인증 버전·최초 설정 표시와 업무 기록의 참조를 그대로 옮깁니다. 비밀번호를 초기화하지 않습니다. **로그인 세션은 옮기지 않습니다** — 이관 뒤 다시 로그인합니다.
- 시간: SQLite의 시간대 없는 값에 저장 규칙대로 시간대를 붙여 옮깁니다 (통합 실행 시작·끝·자동화 수정 시각은 KST, 나머지는 UTC).
- **시간대 출처 확인이 필요한 실행 기록 후보:** 시작 시각 = 생성 시각인 실행 기록입니다. 0009(2026-10-06)가 그 이전 기록의 시작·종료 시각을 생성 시각(UTC)으로 채웠을 수 있는데, 앱은 시작·종료를 KST 벽시계로 읽습니다. 후보일 뿐 출처를 단정할 수 없으므로 후보가 하나라도 있으면 확인만·적용 모두 멈춥니다 (적용하지 않음). 9시간을 추정해서 고치지 않습니다 — 그 기록의 시각 출처는 소유자가 확인해야 하며 이 도구로는 이관하지 않습니다. 보고의 `tz_unverified_runs`가 후보 수입니다. Render 시험 서비스는 2026-10-21 이후 새 DB라 해당하지 않을 것으로 보이며, 실제 원천의 dry-run(`tz_unverified_runs: 0`)으로 확인합니다.
- 보고·오류에는 건수·해시·표·열 이름·정수 행 번호·오류 종류(SQLSTATE·제약 이름)만 남습니다 (원천 값·문자열 기본 키 원문 없음).
- 이관 중에는 대상 DB를 쓰는 앱을 외부에 열지 않습니다 (전환 전이라 Render 앱은 아직 SQLite를 씀). 잠금은 이 경우에도 앱의 쓰기·읽기를 섞이지 않게 막는 마지막 방어입니다.

### 8-3. 전환 순서 — 준비 → 승인 묶음 한 번 → 전환 (이 PR은 실제 Render 연결을 바꾸지 않음)

준비 단계는 지금 Render 서비스(SQLite)를 건드리지 않습니다. 소유자는 준비 결과로 만든 승인 묶음(8-5)을 검토하고 **한 번** 승인합니다. 승인 전에는 Render 환경 변수를 배포와 함께 저장하거나, 배포하거나, 연결 브랜치에 병합하지 않습니다. 아래는 절차이며, 이 저장소 작업에서 실행한 단계가 아닙니다 (Neon·Render 접근 없음).

**준비** (소유자 PC·Neon — Render는 그대로):

1. 경로 결정: (A) 기존 계정 이관 / (B) 새 계정 등록 (8-0).
2. Neon `moajwo-test` 프로젝트(PostgreSQL 17)와 앱 계정(5장). 그날 무료 조건(2장)을 다시 확인하고 날짜를 적습니다.
3. 사용자 PC에 서비스 파일·비밀번호 파일(3-1): `moajwo-migrate`(직접 연결·소유 계정), `moajwo-app`(연결 풀러·앱 계정).
4. 직접 연결·소유 계정으로 마이그레이션: `DATABASE_URL=postgresql:///?service=moajwo-migrate`, `MOAJWO_DB_MODE=external`로 `python -m alembic upgrade head` (3-1).
5. **같은 DB 확인:** `python -m app.dbcheck --app-service moajwo-app --migration-service moajwo-migrate` → 종료 0, `"result": "same"`. 종료 1(다름)이면 전환하지 않고 두 주소(프로젝트·분기·DB 이름)를 다시 확인합니다. Render에 넣을 `DATABASE_URL`·`MOAJWO_MIGRATION_DATABASE_URL`과 같은 값(호스트·계정·DB)인지도 확인합니다.
6. (A) 원천 확보(8-1) → `app.dbcopy` dry-run(`too_long_values`·`nul_values`가 빈 목록, `tz_unverified_runs: 0`, `source_encoding: UTF-8` 확인) → `--apply --report backups/external-db/<시각>/dbcopy-report.json` → 7장 백업을 받아 다른 빈 DB로 **복원 확인**.
   (B) 이관하지 않습니다 — 빈 DB 그대로 (`setup_required: true`가 정상).
7. **Render 설정 읽기 확인 (바꾸지 않음):** 아래 'Render 서비스' 표의 현재값(2026-10-25 조회)이 그대로인지, 프런트(`moajwo-test`)·API(`moajwo-test-api`) 서비스마다 Settings의 Auto-Deploy 방식, 연결 브랜치, Dockerfile 경로·빌드 문맥, health 경로를 대시보드에서 다시 읽어 기록합니다. 이 값으로 '승인된 배포 방법'(병합 / 수동 배포)을 정합니다. Auto-Deploy가 Off라는 사실은 수동 배포나 환경 변수 적용의 승인을 대신하지 않습니다:
   - **On Commit**(새 서비스 기본): 연결 브랜치에 병합·푸시하는 순간 재배포됩니다 (그 재배포도 SQLite를 지움).
   - **After CI Checks Pass**: 연결 브랜치의 새 커밋에서 CI 체크가 모두 통과하면 재배포됩니다. 체크가 0개면 자동 배포하지 않습니다 — 지금 이 저장소의 main·후보에는 GitHub Actions가 없어(2026-10-25 확인) 다른 체크가 붙지 않는 한 수동 배포가 됩니다.
   - **Off**: 수동 배포만.
8. 승인 묶음(8-5)을 만들어 소유자가 한 번 승인합니다.

**전환** (승인 뒤, 한 번에):

9. 7단계에서 기록한 Render 설정이 그대로인지 다시 봅니다 (바뀌었으면 멈추고 묶음을 고쳐 다시 승인).
10. Render 환경 변수는 **"Save only"** 로 저장합니다: 3장 변수(`MOAJWO_DB_MODE=external`, `DATABASE_URL` = 풀러·앱 계정, `MOAJWO_MIGRATION_DATABASE_URL` = 직접 연결·소유 계정).
11. 승인된 배포 한 번: PostgreSQL을 지원하는 앱 커밋(이 PR 이후 코드)을 배포합니다. 저장한 변수는 이 배포부터 쓰입니다. 기동 때 마이그레이션은 직접 연결로 돕니다 (통합 후보는 `0021_run_request_key`까지 — 0020 담당·생존 신호 + 0021 요청 번호).
    - **프런트(`moajwo-test`)를 먼저, API(`moajwo-test-api`)를 다음에:** 이전 화면은 완료가 아닌 실행을 모두 '실패: …'로 그려, 새 API가 정할 근거가 없어 오래 두는 '실행 중'과 새 상태 '결과 확인 필요'가 실패로 보입니다. 새 화면은 이전 API의 상태값(완료·실패·실행 중·검토 대기)을 그대로 처리하고, 이전 API에 없는 업로드 한도 조회가 실패하면 크기 안내만 빼고 보입니다 (코드 확인, 실행 안 함). 2026-10-25 조회에서는 세 서비스 모두 Auto-Deploy Off라 병합만으로는 재배포되지 않았습니다. 직전 확인(9단계)에서 두 서비스가 같은 브랜치에 On Commit으로 바뀌어 있으면 병합 한 번에 함께 재배포되어 순서를 정할 수 없습니다 — 화면 표시만 잠깐 섞이고 자료는 바뀌지 않습니다.
    - 프런트를 이 저장소의 `frontend/Dockerfile.render`로 바꾸는 것은 연결 브랜치·Dockerfile 경로·health 경로를 함께 바꾸는 설정 변경입니다 (아래 'Render 서비스'의 제안값) — 승인 묶음에 넣어 한 번에 적용합니다.
12. 점검 (쓰기 없는 것만): `/health`, `/auth/status` — (A) `setup_required: false`, 이관한 계정 로그인 → **로그인부터 쓰기입니다** (세션·최근 로그인 시각·실패 횟수) / (B) `setup_required: true`가 정상 → 최초 소유자 설정 → 계정을 새로 등록. 점검에 쓴 계정·시각·생긴 세션 수를 기록합니다.
13. 쓰기 재개 시점을 기록하고, 전환 뒤 첫 백업(7장)을 받습니다.

Render 환경 변수 저장 선택지 (https://render.com/docs/configure-environment-variables · https://render.com/docs/deploys, 2026-10-25 확인):

| 선택 | 동작 | 이 전환에서 |
|---|---|---|
| Save, rebuild, and deploy | 새로 빌드하고 새 변수로 배포 | 저장과 동시에 재배포가 시작됨 — 재배포되면 지금 SQLite·파일이 지워짐 |
| Save and deploy | 지금 빌드를 새 변수로 다시 배포 (다시 빌드하지 않음) | 저장과 동시에 지금 빌드로 재배포 — 재배포되면 지금 SQLite·파일이 지워짐. 지금 빌드가 PostgreSQL을 지원하지 않는 코드(main: psycopg 드라이버 없음)면 새 변수로는 기동하지 못할 것으로 보임 (코드 확인, 실행 안 함) |
| Save only | 저장만 — 배포하지 않음. 다음 배포부터 새 값을 씀 | **권장** — 저장과 배포를 나눠, 승인된 배포 한 번(11단계)만 재배포 |

- **(A)에서는 원천 확보(8-1) 전에 앞의 두 선택지를 고르지 않습니다** — 저장과 함께 재배포되어 지금 Render SQLite와 파일이 지워집니다.
- **Restart는 저장만 한 변수를 반영하지 않습니다** — 지금 배포된 커밋을 그때의 변수 값으로 다시 띄웁니다. 전환은 배포로만 합니다.
- Docker 서비스는 환경 변수를 빌드 인자로도 넘깁니다 — 빌드가 그 값을 쓰면 다시 빌드("Save, rebuild, and deploy" 또는 새 커밋 배포)해야 반영됩니다. 이 저장소의 `backend/Dockerfile`에는 `ARG`가 없어 빌드가 DB 변수를 쓰지 않지만, Render API 서비스가 어느 Dockerfile·브랜치로 빌드하는지는 2026-10-25 조회 기록(main · `backend/Dockerfile` · 문맥 `backend`)뿐이라 준비 7단계에서 대시보드로 다시 확인합니다.
- 환경 그룹을 고치면 자동 배포가 켜진 연결 서비스가 모두 재배포됩니다 — 이 변수들은 서비스 자체의 환경 변수로 둡니다.
- **같은 DB 확인(`app.dbcheck`)이 보는 것:** 두 주소 각각으로 읽기 전용 트랜잭션에서 `current_database()`·DB OID·`pg_postmaster_start_time()`·`server_version_num`·`alembic_version`·Neon 식별자(`neon.tenant_id`·`neon.timeline_id` — 설정이 있을 때)를 읽어 비교합니다. 한쪽이 읽지 못한 항목은 비교에서 빼고 `not_compared`에 적습니다. 출력(JSON)에 주소·호스트·비밀번호가 없습니다 (계정 이름은 있음). 종료 0 같음 · 1 다름 · 2 확인 못 함(주소 없음·연결 실패). 서버 시작 시각만 다르면 그 사이 서버가 다시 시작됐을 수 있으니 다시 실행합니다.
  - 로컬 PostgreSQL 17.11에서 시험: 같은 DB를 서로 다른 두 계정으로(하나는 서비스 주소) → 같음, 같은 서버의 다른 DB → 다름, 닿지 않는 주소 → 2.
  - **연결 풀러(PgBouncer·Neon 풀러)를 거친 동작과 실제 Neon 식별자 값은 확인하지 못했습니다** — 로컬에는 풀러와 Neon이 없습니다.

### Render 서비스 — 현재값(2026-10-25 조회)과 제안값

**현재값:** 2026-10-25 Render 관리 API의 읽기 조회(moajwo 워크스페이스 — 재배포·설정 변경·앱 로그인·DB 연결 없음)입니다. 이 저장소 작업에서는 Render에 접근하지 않았습니다. 조회 시점의 사실이며, 병합·전환 직전에 다시 확인합니다 (준비 7단계·전환 9단계).

| 서비스 | 역할 | 연결 브랜치 | Auto-Deploy | Dockerfile / 빌드 문맥 | HTTP health 경로 | plan |
|---|---|---|---|---|---|---|
| `moajwo-test` | 프런트 — 사용자가 써 온 주소 https://moajwo-test.onrender.com/ | `test/render-20261021` | Off | `frontend/Dockerfile.render-test` / `frontend` | 비어 있음 | free |
| `moajwo-test-web` | 프런트 (같은 API로 프록시) | `main` | Off | `frontend/Dockerfile.render` / `frontend` | 비어 있음 | free |
| `moajwo-test-api` | API — 계정 DB(SQLite)의 원천 후보 | `main` | Off | `backend/Dockerfile` / `backend` | 비어 있음 | free |

- **Auto-Deploy Off:** Render 문서는 Off를 자동 배포를 하지 않는 설정으로 설명합니다 (https://render.com/docs/deploys). 그래서 이 조회 기준으로는 main 병합만으로 재배포되지 않습니다. 다만 수동 배포·배포 훅 등 다른 트리거가 없다고 단정하지 않고, 병합 직전에 다시 확인합니다. Off는 수동 배포나 환경 변수 적용의 승인이 아닙니다.
- **지금 `moajwo-test`의 이미지:** `test/render-20261021`(60bcae8 — 앱 코드는 main 703fdcb)의 `frontend/Dockerfile.render-test`입니다. nginx 1.29(root, 포트 80)에 로컬용 `nginx.conf`를 빌드 때 고쳐 `/api/`를 `https://moajwo-test-api.onrender.com/`으로 보냅니다 (Host 헤더·SNI). 보안 응답 헤더는 없습니다. 이 파일은 통합 후보(#42) 트리에 **없습니다** — 후보 코드를 이 서비스에 배포하려면 연결 브랜치·Dockerfile 경로·빌드 문맥을 함께 바꿉니다. 파일 경로를 그대로 둔 채 후보 SHA로 배포하지 않습니다.
- **health 경로가 비어 있으면** Render는 포트에 TCP로 붙어 보기만 합니다. 포트 연결은 업무 준비(DB 연결)와 같지 않습니다. 이 저장소의 `/health`는 DB에 `SELECT 1`을 보내 실패하면 503을 돌려줍니다.
- **Render health 확인의 동작** (https://render.com/docs/health-checks, 2026-10-26 확인): 실행 중인 인스턴스에 몇 초마다 보내고, 검사마다 5초 안에 2xx·3xx가 와야 성공입니다. 15초 연속 실패하면 그 인스턴스로 트래픽을 보내지 않고, 60초 연속이면 다시 시작합니다. 새 배포는 확인을 통과해야 트래픽을 받고, 15분 안에 통과하지 못하면 취소되어 이전 인스턴스가 계속 받습니다.
- **무료 웹 서비스** (https://render.com/docs/free, 2026-10-26 확인): 15분 동안 들어오는 요청(HTTP·WebSocket)이 없으면 멈추고 다시 깨는 데 약 1분 걸립니다. 작업 공간마다 한 달 750시간이며, 다 쓰면 그달 남은 기간 무료 웹 서비스가 정지됩니다. Render의 health 확인 자체가 '들어오는 요청'으로 세어지는지는 문서에 없습니다 (**미확인**).
- 계정 보존은 API 서비스의 `DATABASE_URL` 등(3장)으로만 이뤄집니다. 프런트 서비스에 DB 환경 변수를 넣어도 계정이 보존되지 않습니다.

**제안값 (승인 묶음 8-5에 넣어 한 번에 적용 — 이번 작업에서는 저장하지 않음):**

| 서비스 | 항목 | 현재 | 제안 | 영향 |
|---|---|---|---|---|
| `moajwo-test` | 연결 브랜치 | `test/render-20261021` | 승인된 릴리스(main 병합 뒤 main) | 이 서비스가 승인된 코드를 따름. Auto-Deploy Off 그대로면 수동 배포만 |
| `moajwo-test` | Dockerfile / 문맥 | `frontend/Dockerfile.render-test` / `frontend` | `frontend/Dockerfile.render` / `frontend` (Docker Command는 비움 — 이미지 CMD) | nginx 1.30(brotli-libs 수정판)·비루트(uid 101)·컨테이너 포트 80 → 8080(Render가 열린 포트를 찾음 — 못 찾으면 `PORT=8080`)·보안 헤더 4종·접근 기록에 쿼리 값 없음. 공개 주소는 그대로 |
| `moajwo-test` | HTTP health 경로 | 비어 있음 | `/api/health` (nginx → API `/health`) — 또는 `/`(소유자 결정) | `/api/health`: 새 프런트가 API·DB 응답을 받아야 트래픽을 받음(API가 깨는 약 1분은 배포 대기). 대신 프런트 가용성이 API에 묶임 — API가 15초 넘게 답하지 못하면 프런트(정적 화면 포함)로도 트래픽이 끊기고 60초면 프런트가 다시 시작됨. 프런트가 떠 있는 동안 확인마다 API 공개 주소로 실제 요청이 가 API도 멈추지 않음. `/`: 프런트만 확인(정적 화면은 API와 무관) |
| `moajwo-test-api` | 연결 브랜치 · Dockerfile / 문맥 | `main` · `backend/Dockerfile` / `backend` | 그대로 (승인된 릴리스) | — |
| `moajwo-test-api` | HTTP health 경로 | 비어 있음 | `/health` (DB `SELECT 1`, 실패하면 503) | DB에 닿지 못하면 새 배포가 트래픽을 받지 않음(이전 인스턴스 유지). 실행 중에 DB가 15초 넘게 답하지 못하면 API로 트래픽이 끊김. Neon 휴면 복귀 시간과 검사마다 5초 제한의 관계는 실제 연결 시험에서 확인 (**미검증**) |
| `moajwo-test-api` | 환경 변수 | SQLite (외부 DB 변수 없음) | 3장 변수를 "Save only"로 (전환 10단계) | 다음 배포부터 외부 DB |
| `moajwo-test-web` | 모두 | `main` · `frontend/Dockerfile.render` | **바꾸지 않음** — 중복으로 보인다는 이유만으로 삭제·중지하지 않음 | 사용 여부·정리는 별도 과제 |

- **health 경로를 넣은 뒤 확인:** 요청 없이 15~30분 둔 뒤 프런트·API가 멈추는지(대시보드 이벤트)와 무료 시간 사용량을 봅니다. 멈추지 않으면(확인이 요청으로 세어지는 경우 — 세 서비스가 늘 떠 있으면 750시간을 넘음) health 경로를 비워 되돌립니다. 상시 기동을 위한 요청은 넣지 않습니다.
- 제안 프런트 이미지(`frontend/Dockerfile.render`)는 격리 러너에서 실제 Render 대신 같은 이름(`moajwo-test-api.onrender.com`)의 HTTPS 대역 API를 두고 확인합니다: nginx 설정 검사, 비루트·8080, `/api/`가 그 이름으로 TLS 연결해 backend까지 가는지, 프록시를 거친 로그인·쿠키 속성·업로드·내려받기, `/`·`/api/health`의 보안 헤더, 상류 인증서 검증 (`diagnostics/ci/image_check.sh`). 대역 API의 인증서는 시험 CA(뿌리 → 중간 → 서버)가 서명하고 앞단에는 그 뿌리만 신뢰 묶음으로 주어, 신뢰하지 않는 CA·만료·이름 불일치·중간 누락이 502로 끝나고 정상 체인으로 복구되는지까지 봅니다. 실제 Render 주소·인증서 체인으로는 확인하지 않았습니다.
- **API 쪽 인증서 검증:** 제안 프런트(`nginx.render.conf`)는 2026-10-26부터 `proxy_ssl_verify on`(OS CA 묶음, 검증 이름 = API 호스트, 깊이 3, TLS 1.2/1.3)으로 API 인증서를 검증합니다 — 검증에 실패하면 요청을 전달하지 않고 502로 끝내며 검증을 끄는 우회는 없습니다 (`docs/SECURITY-CA-20261026.md`). 지금 사용자용 서비스(`moajwo-test`, 다른 분기의 `Dockerfile.render-test`)는 여전히 검증하지 않으므로, 전환 승인 뒤 이 이미지를 배포해야 효력이 생깁니다. 그때 API 체인이 깊이 3을 넘으면 `/api/health`가 502가 되므로 첫 배포에서 확인합니다.

### 8-4. 되돌리기 경계

- **복구 대상도 PostgreSQL을 지원해야 합니다.** 기본 복구는 검증된 외부 DB를 유지한 채 PostgreSQL을 지원하는 호환 앱 이미지로 되돌리는 것입니다. 초기 SQLite 전용 이미지(이 PR 이전)로 돌아갈 수 있다고 가정하지 않습니다.
- **환경 변수만 이전 값으로 돌리고 재배포하는 SQLite 복구는 쓰지 않습니다.** Render 재배포는 SQLite 파일을 지우므로 원천 파일이 남는다는 보장이 없습니다. SQLite로 돌아가려면 실제 백업 복원 위치·명령·파일 생존 조건이 검증된 별도 복원 절차가 있어야 하며, 지금은 없습니다 (Render Free는 셸·디스크가 없음) — 복구안으로 제시하지 않습니다.
- Render 롤백 배포는 대상 배포 때의 설정(환경 변수 포함)으로 **그 롤백 배포만** 돌립니다. 서비스에 저장된 현재 설정은 바뀌지 않고, 다음 일반 배포는 현재 저장값(외부 DB)을 다시 씁니다. 대시보드에서 롤백하면 자동 배포가 꺼지므로 나중에 Settings에서 다시 켜야 합니다 (API로 롤백하면 끄지 않음) (https://render.com/docs/rollbacks, 2026-10-25 확인). 전환 전 배포로 롤백하면 그 롤백 배포는 외부 DB 설정 없이 빈 SQLite로 떠 최초 설정 화면이 다시 열릴 것으로 보이므로 (코드 기준 판단, 실행 안 함), 전환 전 배포로의 롤백을 복구 수단으로 쓰지 않습니다.
- **통합 후보는 외부 DB를 `0021_run_request_key`로 올립니다**(0020 담당·생존 신호 + 0021 요청 번호, `docs/DEPLOY.md` 9·10장). 0020을 모르는 이미지(이 후보 이전 — Render에 PostgreSQL 이미지로 배포된 적 없음)로는 되돌릴 수 없습니다 (기동 때 `alembic upgrade head`가 모르는 리비전에서 멈춤). 되돌릴 대상은 0020을 아는 이미지입니다. DB를 0019로 내리는 절차(로컬은 `docs/DEPLOY.md` 9장 — 서버 정지 → `recover` → downgrade)는 Render·Neon에서 실행·검증하지 않았습니다: Render Free에는 셸·one-off 작업이 없어 사용자 PC에서 직접 연결로 해야 하고, 그 PC는 서버의 결과 폴더를 볼 수 없어 서버가 남긴 '실행 중' 행을 근거로 정하지 못합니다 ('결과 확인 필요'까지만).
- 외부 DB 자체가 손상되면 7장 백업으로 **다른 빈 DB에** 복원해 확인한 뒤 주소를 바꿉니다.
- **쓰기 재개 후**: 전환 뒤 생긴 계정·기록이 있으므로, 예전 백업으로 되돌려 새 계정·자료를 잃는 복구를 자동으로 하지 않습니다. 필요하면 그 시점 백업을 먼저 받고, 잃는 범위를 알린 뒤 승인받습니다.

### 8-5. 최종 전환 승인 묶음 (소유자가 한 번 검토·승인)

아래를 한 묶음으로 만들어 소유자가 한 번 검토·승인한 뒤 8-3의 전환 단계(9~13)를 진행합니다. 묶음에 비밀번호·주소를 넣지 않습니다. 이 저장소 작업에서는 이 묶음을 만들거나 그 안의 단계를 실행하지 않았습니다.

1. 경로 결정 — (A) 기존 계정 이관 / (B) 새 계정 등록, 그 이유
2. Neon 프로젝트(`moajwo-test`)·지역·PostgreSQL 판(17), 무료 조건을 확인한 날짜(2장)
3. (A) 원천 스냅숏 식별자·sha256·보호 시점(원천 쓰기를 멈춘 시각)·보관 위치
4. (A) `app.dbcopy` 보고 — dry-run과 적용(`dbcopy-report.json`: 표별 건수·내용 해시·`too_long_values`·`nul_values`·`tz_unverified_runs`·`source_encoding`·`target_secret_in_args`)
5. (A) 7장 복원 확인 결과 — 다른 빈 DB, 로그인·건수·내용 해시, 원본 DB 그대로
6. 같은 DB 확인(`app.dbcheck`) 출력 — `result: same`, 앱 쪽·마이그레이션 쪽 계정 이름
7. 최종 앱 SHA·이미지, Render 환경 변수 저장 방식("Save only")과 승인된 배포 방법 — 준비 7단계의 Render 설정 읽기 확인 결과(프런트 `moajwo-test`·API `moajwo-test-api`의 Auto-Deploy 방식·연결 브랜치·Dockerfile 경로·health 경로)와 적용할 제안값('Render 서비스' 표). `moajwo-test-web`은 바꾸지 않음
8. 중단 계획 — (A)는 8-1의 보호 시점(원천 쓰기를 멈춘 시각)부터 전환까지 지금 Render 서비스가 받는 쓰기는 **옮겨지지 않고** 전환 배포 때 지워집니다: 그 사이 쓰기를 멈추거나(사용자 안내) 잃는 범위를 소유자가 받아들임을 적습니다. 그다음 전환 배포 → 점검 → 쓰기 재개 (예정 시각)
9. 되돌리기 경계 (8-4)
10. 전환 뒤 백업 계획 — 7장 백업 주기·보관 위치
11. 계정 — 이관한 계정 수(A, 이관 보고)와 새로 만든·만들 계정(B, 또는 전환 뒤 새 가입)을 나눠 적음
12. TLS 확인 — 두 주소(직접·풀러) 모두 `sslmode=verify-full`이고 3-2의 확인 명령 출력(`ssl = t`, TLS 판·암호 모음), `channel_binding`이 풀러 주소에서 통했는지(안 됐으면 `prefer`로 낮춘 사실)

## 9. 시험 (SQLite·PostgreSQL)

- backend 시험: 환경 변수 `MOAJWO_TEST_DATABASE_URL`(빈 시험 DB 주소)을 두고 `python -m pytest` — 비우면 지금처럼 SQLite. 일회용 시험 서버의 합성 값만 넣고, 비밀번호가 든 주소를 명령줄에 직접 치지 않습니다 (파일·비밀 저장소에서 읽어 환경 변수로).
  - 파일을 통째로 빼지 않습니다. SQLite 자체만 확인하는 시험에만 이유와 함께 `@pytest.mark.sqlite_only("이유")`를 붙이고, PostgreSQL 실행은 그 시험만 건너뜁니다 (지금은 SQLite 파일에 마이그레이션을 실행해 읽는 `test_migrations.py` 13개 — 대응은 `test_migrations_postgres.py`).
  - DB 내용 조회는 앱과 같은 DB에 붙는 공통 도우미(`tests/conftest.py`의 `db_rows`·`db_connect`·`db_dump`)로 합니다.
  - `MOAJWO_TEST_PG_ADMIN_URL=<일회용 시험 서버 관리자 주소>`가 있으면 시험마다 빈 DB를 새로 만드는 시험(이관 도구 실패·동시성·대상 주소 입력, 같은 DB 확인, 마이그레이션, 여러 프로세스의 최초 설정)도 돕니다. 없으면 그 시험만 건너뜁니다. 운영·시연 DB 주소를 넣지 않습니다.
  - 대상 주소 입력(`tests/test_dbcopy.py`): 비밀번호가 든 `--target`의 경고·`target_secret_in_args`, `--target-env`, 둘 중 하나만, PostgreSQL에서 합성 서비스·비밀번호 파일로 서비스 주소 접속(빈 비밀번호 파일 → 인증 실패, 없는 `PGSERVICEFILE` → 오류). 같은 DB 확인: `tests/test_dbcheck.py`.
- 계정 보존 확인: `python diagnostics/scripts/external_db_check.py --admin-service <서비스 이름> --backend backend --out <폴더> --pg-bin <pg_dump·pg_restore·psql 폴더> [--role-prefix <이름>]` — 관리자 비밀번호는 `PGSERVICEFILE`·`PGPASSFILE`의 파일에서 읽습니다 (또는 `--admin-env <주소가 든 환경 변수 이름>`). 실제 프로세스로 1~9단계 (스크립트 머리말).
  - 하위 프로세스에는 비밀번호를 명령 인자로 넘기지 않습니다: 앱은 환경 변수, 이관 도구는 `--target-env`·서비스 주소, pg_dump·psql은 `PGSERVICE`, pg_restore는 `--dbname=service=…` (모두 임시 서비스·비밀번호 파일 — 본인만 접근, 끝나면 지움. 권한을 줄이지 못하면 비밀번호를 쓰지 않고 멈춤).
  - 9단계는 이 스크립트가 `subprocess`로 띄운 하위 프로세스(`subprocess.run` 포함 — 목록 인자만 허용하고 셸 확장은 거절, `os.system` 같은 다른 실행 경로는 쓰지 않음)의 명령 인자와 스크립트 자신의 인자, 결과 파일에 생성한 비밀번호가 없는지, 임시 파일 권한·삭제를 결과 행으로 남깁니다.
  - `--role-prefix`로 계정 이름을 바꾸면 같은 시험 서버의 다른 계정 비밀번호를 건드리지 않습니다.
  - 2026-10-25 로컬 실행 (Windows, PostgreSQL 17.11, 합성 값): 결과 46행 모두 통과 — `argv_no_secrets`(하위 프로세스 30개), `outputs_no_secrets`, `secret_files_private`·`secret_files_removed` 포함. 검토 후속(이름 자리 검사·검증자 전송·실패 시 멈춤·같은 DB 판정에 결과 값까지 확인) 뒤 다시 실행해 같은 46행 (시간 값만 다름).
- **하지 않은 것:** 실제 Neon 프로젝트 연결(계정 생성은 소유자), Neon 휴면에서 깨는 실제 시간, 연결 풀러(PgBouncer·Neon 풀러)를 거친 연결과 `app.dbcheck` — 이것들은 소유자의 준비 단계(8-3 2~5단계)입니다. Render 환경 변수 저장·실제 재배포·전환은 승인 뒤의 단계입니다.
