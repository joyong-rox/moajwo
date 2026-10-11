# 운영 반영 · 백업 · 롤백

운영은 사용자 PC의 Docker Compose입니다 (원격 배포 시스템 없음).

| 항목 | 값 |
| --- | --- |
| Compose 프로젝트 | `moajwo` (`docker-compose.yml`) |
| 서비스 | `backend` (127.0.0.1:8000, 이미지 `moajwo-backend:latest`, 실행 사용자 `app` uid 1000) · `frontend` (127.0.0.1:8080, `moajwo-frontend:latest`) |
| 데이터 볼륨 | `moajwo_moajwo-data` → 컨테이너 `/app/data` (SQLite `moajwo.db`, 업로드 원본, 통합 결과 파일) |
| DB 마이그레이션 | backend가 기동할 때마다 `alembic upgrade head` 실행 |

- 운영 볼륨 삭제·`docker compose down -v`·자동 DB downgrade는 배포·롤백 절차에 넣지 않습니다.
- backend는 일반 사용자(`app`)로 실행해야 합니다. 원본·결과 파일의 읽기 전용 보호(chmod)는 root로 실행하면 효과가 없습니다.

## 1. 반영 전 준비

```powershell
cd C:\Users\juho\Documents\Moajwo
# 지금 운영 이미지를 롤백용으로 표시해 둔다 (이미지 ID는 반영 기록에 남긴다)
docker image inspect moajwo-backend:latest moajwo-frontend:latest --format "{{index .RepoTags 0}} {{.Id}}"
docker tag moajwo-backend:latest moajwo-backend:rollback
docker tag moajwo-frontend:latest moajwo-frontend:rollback
docker compose exec backend alembic current     # 반영 전 리비전 기록
```

### DB 백업 (일관된 사본)

SQLite 파일을 그대로 복사하면 쓰는 중인 내용이 섞일 수 있어, 실행 중인 backend에서 SQLite 온라인 백업 API로 사본을 만든 뒤 꺼냅니다.

```powershell
mkdir backups -Force
docker compose exec -T backend python -c "import sqlite3; s=sqlite3.connect('/app/data/moajwo.db'); d=sqlite3.connect('/tmp/moajwo-backup.db'); s.backup(d); d.close(); s.close()"
docker compose cp backend:/tmp/moajwo-backup.db backups/moajwo-YYYYMMDD-HHMM.db
python -c "import sqlite3; print(sqlite3.connect('backups/moajwo-YYYYMMDD-HHMM.db').execute('pragma integrity_check').fetchone())"   # ('ok',)
```

### 업로드 원본·결과 파일 백업 (볼륨 전체)

```powershell
docker run --rm -v moajwo_moajwo-data:/data:ro -v "${PWD}\backups:/backup" --user 0 moajwo-backend:latest tar czf /backup/moajwo-data-YYYYMMDD-HHMM.tgz -C /data .
```

(볼륨 안의 `moajwo.db`도 함께 들어가지만, DB 복원에는 위의 일관된 사본을 씁니다.)

## 2. 반영

```powershell
cd C:\Users\juho\Documents\Moajwo
git fetch origin
git checkout main
git pull --ff-only
git rev-parse HEAD                       # 승인한 커밋과 같은지 확인
docker compose build                     # 서비스는 계속 동작 (중단 없음)
docker compose up -d --wait              # backend 재생성(마이그레이션) → frontend 재생성
docker compose exec backend alembic current
docker image inspect moajwo-backend:latest moajwo-frontend:latest --format "{{index .RepoTags 0}} {{.Id}}"
```

- 예상 중단: 컨테이너 재생성부터 healthy까지 약 10초 (격리 프로젝트 실측 9.3초, backend 재시작만은 4.0~4.3초). 빌드 시간은 중단에 들어가지 않습니다.

## 3. 반영 후 점검

1. `curl http://localhost:8000/health` → `{"status":"ok","database":"ok"}`
2. 로그인 → 새로고침 후 로그인 유지 → 로그아웃
3. 파일 업로드 → 미리보기·머리글 → 열 매핑 분석·확정 → 사전 검토 → 검토 완료 → 통합 결과 만들기 → 다운로드한 엑셀 열기
4. 권한: 일반 사용자에게 설정·사전 관리 메뉴가 보이지 않고 `/settings` 직접 접근이 403
5. 남의 파일 검토가 「조회 전용」으로 보이고 승인·거부 버튼이 꺼져 있음
6. `docker compose logs backend --since 10m`에 오류가 없는지

## 4. 롤백

이전 코드는 새 리비전(예: 0015)을 모르므로, 이전 이미지로 되돌리기 전에 **DB 리비전도 이전으로** 맞춰야 합니다 (그대로 두면 이전 backend가 기동할 때 `alembic upgrade head`에서 실패).

**A. 반영 직후 새 데이터가 없을 때 (권장): 백업 DB로 복원**

```powershell
docker compose stop backend frontend
docker compose cp backups/moajwo-YYYYMMDD-HHMM.db backend:/app/data/moajwo.db
docker tag moajwo-backend:rollback moajwo-backend:latest
docker tag moajwo-frontend:rollback moajwo-frontend:latest
docker compose up -d --no-build --wait
docker compose exec backend alembic current      # 반영 전 리비전
```

**B. 반영 후 생긴 데이터를 지킬 때 (승인 후 수동): 새 이미지로 해당 리비전만 되돌린 뒤 이전 이미지 기동**

```powershell
docker compose stop frontend
docker compose run --rm backend alembic downgrade <반영 전 리비전>
docker compose stop backend
docker tag moajwo-backend:rollback moajwo-backend:latest
docker tag moajwo-frontend:rollback moajwo-frontend:latest
docker compose up -d --no-build --wait
```

- 각 마이그레이션의 downgrade는 그 리비전이 넣은 데이터만 지우고, 사용자 별칭이 가리키는 기본 항목은 남깁니다. 실행 전에 백업을 먼저 만듭니다.
- 0015의 downgrade는 0015가 넣은 `owner_setup` 표시 한 행만 지웁니다 (운영 DB 사본에서 확인: 사용자·용어사전·별칭·매핑 확정·업로드 정보 변화 없음).
