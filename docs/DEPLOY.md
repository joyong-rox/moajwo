# 운영 반영 · 백업 · 롤백

운영은 사용자 PC의 Docker Compose입니다 (원격 배포 시스템 없음). 아래 명령은 **Git Bash 기준**이며, 운영 볼륨을 복제한 격리
프로젝트에서 같은 형태로 리허설했습니다 (결과는 맨 아래).

| 항목 | 값 |
| --- | --- |
| Compose 프로젝트 | `moajwo` (`docker-compose.yml`) |
| 서비스 | `backend` (127.0.0.1:8000, `moajwo-backend:latest`, 실행 사용자 `app` uid 1000) · `frontend` (127.0.0.1:8080, `moajwo-frontend:latest`) |
| 데이터 볼륨 | `moajwo_moajwo-data` → `/app/data`: `moajwo.db`(SQLite, journal_mode=delete), `uploads/`(업로드 원본), `outputs/`(통합 결과) — 모두 uid 1000 소유 |
| 마이그레이션 | backend 기동 명령이 `alembic upgrade head` 후 서버를 띄움 (이미지에 ENTRYPOINT 없음, CMD만) |

**하지 않는 것**: 운영 볼륨 삭제, `docker compose down -v`, 자동 DB downgrade. backend는 일반 사용자(`app`)로 실행합니다
(원본·결과 파일의 읽기 전용 보호는 root로 실행하면 효과가 없음).

## 1. 준비 (서비스 중단 없음)

```bash
cd /c/Users/juho/Documents/Moajwo
export MSYS_NO_PATHCONV=1
git fetch origin && git checkout main && git pull --ff-only
APPROVED=<승인받은 커밋 SHA>
test "$(git rev-parse HEAD)" = "$APPROVED" && test -z "$(git status --porcelain)" && echo "소스 일치·미커밋 변경 없음"

# 지금 실행 중인 컨테이너의 실제 이미지 ID로 롤백 태그를 만든다 (latest를 추측하지 않음). backend·frontend를 한 배포 단위로 묶는다
PREV=$(docker compose exec -T backend alembic current | grep -o '^[0-9a-z_]*')        # 반영 전 리비전
OLD_B=$(docker inspect moajwo-backend-1 --format '{{.Image}}')
OLD_F=$(docker inspect moajwo-frontend-1 --format '{{.Image}}')
TAG=rollback-$(date +%Y%m%d-%H%M)
docker tag "$OLD_B" moajwo-backend:$TAG && docker tag "$OLD_F" moajwo-frontend:$TAG

# 새 이미지 빌드: 서비스는 그대로 동작한다 (빌드가 실패하면 여기서 멈추고 기존 서비스 유지)
docker compose build
NEW_B=$(docker image inspect moajwo-backend:latest --format '{{.Id}}')
NEW_F=$(docker image inspect moajwo-frontend:latest --format '{{.Id}}')
```

반영 기록에 남길 것: 시각, `$APPROVED`, `$OLD_B`/`$OLD_F`, `$NEW_B`/`$NEW_F`, `$PREV`, 백업 파일 이름·sha256·무결성 결과.

## 2. 쓰기 중단 → 일관된 백업

- 사용자에게 반영 시각을 알리고, 진행 중인 업로드·검토·통합이 없는지 확인합니다 (통합은 요청 안에서 끝나는 동기 작업).
- **frontend만 멈추면 안 됩니다**: backend는 127.0.0.1:8000으로 직접 요청을 받습니다 (리허설에서 frontend 정지 후 backend `/health` 200 확인). backend까지 멈춰야 쓰기가 멈춥니다.
- `stop -t 30`: 진행 중인 요청이 끝나기를 최대 30초 기다린 뒤 멈춥니다.

```bash
docker compose stop -t 30 frontend backend          # 여기부터 서비스 중단
mkdir -p backups && TS=$(date +%Y%m%d-%H%M%S)
# 멈춘 상태에서 one-off 컨테이너(서버를 띄우지 않음)로 볼륨 전체를 한 시점에 백업하고 DB 상태를 기록한다
docker run --rm --entrypoint sh -v moajwo_moajwo-data:/data:ro -v "$(pwd -W)/backups:/backup" "$OLD_B" -c \
  "tar czf /backup/moajwo-data-$TS.tgz -C /data . && python -c \"import sqlite3,hashlib; c=sqlite3.connect('file:/data/moajwo.db?mode=ro', uri=True); print('revision', c.execute('select version_num from alembic_version').fetchone()[0], '| integrity', c.execute('pragma integrity_check').fetchone()[0], '| journal', c.execute('pragma journal_mode').fetchone()[0]); print('sha256', hashlib.sha256(open('/backup/moajwo-data-$TS.tgz','rb').read()).hexdigest())\"" \
  | tee backups/moajwo-data-$TS.txt
```

- DB·업로드 원본·결과 파일을 한 tar에 같은 시점으로 담습니다 (서비스가 멈춘 상태라 SQLite 파일도 일관됨). `integrity ok`가 아니면 반영을 멈추고 `docker compose start backend frontend`로 되돌립니다.

## 3. 전환과 점검

```bash
docker compose up -d --wait                         # 새 이미지로 재생성(마이그레이션) → healthy → 서비스 재개
docker compose exec -T backend alembic current      # 새 리비전 (head)
docker inspect moajwo-backend-1 --format '{{.Image}}'   # = $NEW_B
```

점검:
1. `curl -s http://127.0.0.1:8000/health` → `{"status":"ok","database":"ok"}`
2. 로그인 → 새로고침 후 로그인 유지 → 로그아웃
3. 기존 파일의 열 매핑 분석(저장된 원본을 다시 읽음) · 기존 실행의 결과 다운로드
4. 새 파일 업로드 → 매핑 → 사전 검토 → 검토 완료 → 통합 결과 만들기 → 내려받은 엑셀 열기
5. 일반 사용자: 설정·사전 관리 메뉴가 없고 `/settings` 직접 접근이 403
6. `docker compose logs backend --since 10m`에 오류 없음

**중단 시간 (리허설 실측, DB 48MB·원본 소량 기준)**: 쓰기 중단 1.6초 + 백업 5.9초 + 전환(재생성·마이그레이션·healthy) 8.1초 = **15.6초**, 반영 후 기본 점검까지 18.3초. 빌드(10.3초)는 중단 전에 끝냅니다. 백업 시간은 볼륨 크기에 비례하고, 사람이 하는 점검 시간은 들어 있지 않습니다.

## 4. 롤백

롤백 전에 **항상 backend·frontend를 먼저 멈춥니다** (DB를 쓰는 프로세스가 없어야 복원·downgrade가 안전). 이전 코드는 새 리비전을 모르므로
(그대로 두면 이전 backend가 `alembic upgrade head`에서 실패) DB도 이전 리비전으로 맞춥니다.

### A. 배포 전 백업으로 복원 (기본)

```bash
docker compose stop -t 30 frontend backend
# 현재 데이터는 지우지 않고 _pre_rollback_<시각>/ 으로 옮긴 뒤 백업을 푼다 (uid 1000으로 실행 → 소유권 유지).
# moajwo.db-journal(또는 WAL 모드라면 -wal·-shm)이 있으면 DB와 함께 옮겨지고 지워지지 않는다
docker run --rm --entrypoint sh -v moajwo_moajwo-data:/data -v "$(pwd -W)/backups:/backup:ro" "$OLD_B" -c \
  "set -e; d=/data/_pre_rollback_$TS; mkdir -p \$d; for f in /data/* /data/.[!.]*; do [ -e \"\$f\" ] || continue; case \"\$f\" in /data/_pre_rollback_*) ;; *) mv \"\$f\" \$d/;; esac; done; tar xzf /backup/moajwo-data-$TS.tgz -C /data; ls -ln /data"
docker tag moajwo-backend:$TAG moajwo-backend:latest && docker tag moajwo-frontend:$TAG moajwo-frontend:latest
docker compose up -d --no-build --wait
docker compose exec -T backend alembic current      # = $PREV
```

- **유실되는 것**: 백업 이후 반영된 버전에서 생긴 모든 변경(새 업로드 원본, 통합 결과, 매핑 확정·학습, 검토·실행 기록, 계정·설정·용어사전 변경). 지워지지 않고 볼륨의 `_pre_rollback_<시각>/`에 남으므로 필요하면 수동으로 옮겨옵니다.

### B. 반영 후 생긴 데이터를 지키며 되돌리기 (승인 후 수동)

```bash
docker compose stop -t 30 frontend backend
docker compose ps --status running --services       # 아무것도 없어야 함
# 새 리비전을 아는 '새' 이미지로 downgrade만 실행 (CMD를 바꾸므로 서버는 뜨지 않는다 — 리허설에서 확인)
docker compose run --rm --no-deps backend alembic downgrade "$PREV"
docker tag moajwo-backend:$TAG moajwo-backend:latest && docker tag moajwo-frontend:$TAG moajwo-frontend:latest
docker compose up -d --no-build --wait
docker compose exec -T backend alembic current      # = $PREV
```

- 실행 전에 2번의 백업을 먼저 만듭니다. 각 downgrade는 그 리비전이 넣은 데이터만 지우고 사용자 별칭이 가리키는 기본 항목은 남깁니다 (0015는 `owner_setup` 표시 한 행만).

## 리허설 결과 (2026-10-11, 운영 볼륨 복제본, 격리 프로젝트 `moajwo-rehearsal`)

| 단계 | 결과 |
| --- | --- |
| 준비 | 실행 중 컨테이너 이미지 ID로 태그(backend `aa598ff2e6a5`, frontend `43a9a0a5b353`), 새 소스 `24d2fc4`(미커밋 0) 빌드 10.3초 |
| 복제 | 운영 볼륨을 읽기 전용으로 마운트해 복제, DB는 SQLite 백업 API로 복사, 모두 1000:1000 |
| 이전 버전 | revision 0014 · 로그인 · 파일 51개 · 원본 다시 읽기 · 기존 결과 다운로드(시트 3개) |
| frontend만 정지 | backend 직접 `/health` 200 → backend까지 정지 필요 확인 |
| 백업 | revision 0014 · integrity ok · journal delete · sha256 기록 |
| 새 버전 | revision 0015 · 로그인 · 원본 · 다운로드 · 새 파일 업로드 |
| 롤백 A | 12.6초 · revision 0014 · 로그인·원본·다운로드 정상 · 반영 후 업로드 파일은 활성 데이터에 없음(`_pre_rollback_`에 보존) · 1000:1000 |
| 롤백 B | 14.4초 · downgrade one-off 동안 실행 중 서비스 없음 · revision 0014 · 반영 후 업로드 파일 유지 · DB 1000:1000 |
| 태그 교체 방식 | rollback 태그 → latest → `up --no-build`로 이전 이미지(`aa598ff2e6a5`) 기동 확인 |
