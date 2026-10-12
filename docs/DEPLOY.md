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

## 1·2. 반영 실행: `scripts/deploy.sh` (준비 → 빌드 → 쓰기 중단 → 백업·검증 → 전환)

### 승인 대상과 배포 대상의 구분

- 승인은 **PR HEAD 커밋**(예: PR #1의 마지막 커밋)으로 받습니다. 병합 방식(merge commit·squash·rebase)에 따라 **병합 후 main의 SHA는
  달라질 수 있으므로**, 스크립트는 SHA가 아니라 **트리(파일 내용)**가 같은지로 "승인한 변경 = 배포할 내용"을 확인하고 두 SHA를 모두 기록합니다.
- 병합 후 main에 다른 변경이 섞여 있으면 트리가 달라 스크립트가 멈춥니다 (그때는 새로 승인받습니다).

```bash
cd /c/Users/juho/Documents/Moajwo
git fetch origin && git checkout main && git pull --ff-only
bash scripts/deploy.sh <승인한 PR HEAD 커밋 SHA>
```

### 스크립트가 하는 일과 멈추는 조건

| 단계 | 하는 일 | 멈추는 조건 (실패 시 상태) |
| --- | --- | --- |
| 1 | 승인 커밋 존재·트리 일치, 미커밋 변경 확인 | 승인 커밋 없음 · 트리 불일치 · 추적 파일 변경 · backend/frontend의 추적하지 않는 파일 (**서비스 그대로**) |
| 2 | 실행 중 컨테이너의 실제 이미지 ID로 롤백 태그(`rollback-<시각>`), 반영 전 리비전 기록 | 컨테이너·리비전을 읽을 수 없음 (서비스 그대로) |
| 3 | `docker compose build` | 빌드 실패 (**서비스 그대로**) |
| 4 | `stop -t 30 frontend backend` (진행 중 요청은 최대 30초 대기) 후 실행 중 서비스 0개, 데이터 볼륨을 쓰는 컨테이너 0개 확인 (두 조회 모두 성공해야 함) | 정지 실패 · 조회 실패 · 남은 서비스 · 볼륨을 쓰는 다른 컨테이너 → **마이그레이션 미시작(리비전 그대로)을 확인할 수 있으면 기존 컨테이너 재시작, 없으면 수동 복구 안내** |
| 5 | 서버를 띄우지 않는 one-off 컨테이너로 볼륨 전체를 한 시점에 tar, `gzip -t`·`moajwo.db` 포함 확인, DB `integrity_check`·리비전·저널 모드·sha256 기록 (`backups/moajwo-data-<시각>.txt`) | 백업 명령 실패 · 파일 없음/비어 있음 · 압축 손상 · DB 무결성 실패 · 리비전이 반영 전과 다름 → **같은 방식으로 기존 컨테이너 재시작** |
| 6 | 백업 확인 후에만 `up -d --wait` (새 이미지로 재생성·마이그레이션) → 리비전 `(head)`·컨테이너·이미지 조회 | 전환 실패 · 전환 후 조회 실패 → '전환 완료' 없이 멈춤, 자동 롤백하지 않음 (4장 절차, 백업·롤백 태그는 로그에 있음) |

- 모든 단계는 `backups/deploy-<시각>.log`에 남고, 마지막 줄에 **배포 단위**(승인 SHA·배포 SHA·이전 이미지 ID·롤백 태그·백업 파일·리비전 전후)를 적습니다. 롤백에 이 값들을 씁니다.
- `set -uo pipefail`을 쓰지만 판단에 쓰는 **모든 조회는 종료 코드와 필수 결과를 함께** 검사합니다: 백업 경로 변환, git 커밋·트리·상태, 컨테이너·이미지 ID, 반영 전후 리비전, 실행 중 서비스(`compose ps --status running --services`), 볼륨 사용 컨테이너(`docker ps --filter volume=`). 조회가 **성공했고 결과가 비어 있을 때만** '없음'으로 판단하고, 조회 자체가 실패하면 '확인할 수 없음'으로 기록한 뒤 백업·전환을 하지 않습니다. 백업 결과는 파이프 없이 파일로 받아 종료 코드를 보존합니다.
- 정지 이후 실패하면 볼륨의 DB 리비전 조회가 **성공하고** 반영 전 리비전과 같을 때만 기존 컨테이너를 `start`로 재개합니다. 리비전을 확인할 수 없거나 다르면 재개하지 않고, 로그에 현재 상태 확인·수동 복구 명령을 남깁니다.
- 전환 후 리비전·컨테이너·이미지 조회 중 하나라도 실패하면 '전환 완료'를 출력하지 않고 실패(종료 코드 1)로 끝냅니다.
- 실패 처리 시험: `bash scripts/test_deploy.sh` (실제 Docker를 쓰지 않는 명령 대역 시험, 아래 3차 결과)
- `backups/`에는 운영 데이터가 들어 있어 git에서 제외합니다 (`.gitignore`).
- 사용자에게 반영 시각을 미리 알리고, 진행 중인 업로드·검토·통합이 없는지 확인한 뒤 실행합니다 (통합은 요청 안에서 끝나는 동기 작업).
- frontend만 멈추면 backend가 127.0.0.1:8000으로 계속 요청을 받으므로 스크립트는 둘 다 멈춥니다.

## 3. 전환과 점검

```bash
# 스크립트 6단계가 실행한 내용 (확인용)
docker compose exec -T backend alembic current      # 새 리비전 (head)
docker inspect moajwo-backend-1 --format '{{.Image}}'   # 로그의 새 backend 이미지
```

점검:
1. `curl -s http://127.0.0.1:8000/health` → `{"status":"ok","database":"ok"}`
2. 로그인 → 새로고침 후 로그인 유지 → 로그아웃
3. 기존 파일의 열 매핑 분석(저장된 원본을 다시 읽음) · 기존 실행의 결과 다운로드
4. 새 파일 업로드 → 매핑 → 사전 검토 → 검토 완료 → 통합 결과 만들기 → 내려받은 엑셀 열기
5. 일반 사용자: 설정·사전 관리 메뉴가 없고 `/settings` 직접 접근이 403
6. `docker compose logs backend --since 10m`에 오류 없음

**중단 시간**: 빌드는 중단 전에 끝납니다. 중단 구간은 쓰기 중단·백업·전환입니다. 측정값은 **이 데이터 규모(DB 48MB, 원본 소량)에서의 값**입니다 — 1차 리허설(수동 명령) 15.6초(쓰기 중단 1.6 + 백업 5.9 + 전환 8.1), 스크립트 시험 24초(검증 단계 포함). 반영 후 기본 점검은 별도이고, 백업 시간은 볼륨 크기에 비례하며 사람이 하는 점검 시간은 들어 있지 않습니다.

## 4. 롤백

롤백 전에 **항상 backend·frontend를 먼저 멈춥니다** (DB를 쓰는 프로세스가 없어야 복원·downgrade가 안전). 이전 코드는 새 리비전을 모르므로
(그대로 두면 이전 backend가 `alembic upgrade head`에서 실패) DB도 이전 리비전으로 맞춥니다.

- 아래의 `$TS`(백업 시각), `$OLD_B`(이전 backend 이미지 ID), `$TAG`(롤백 태그), `$PREV`(반영 전 리비전)는 반영 로그 마지막의 **배포 단위** 줄에 있습니다. 이전 이미지와 백업은 한 배포 단위로 함께 씁니다.
- **반영 후 새 데이터가 생겼다면** A는 그 데이터를 활성 데이터에서 빼므로, 빠질 범위를 사용자에게 알리고 **별도 승인**을 받은 뒤 실행합니다. 데이터를 지켜야 하면 B를 씁니다.
- `down -v`, 자동 downgrade, 운영 데이터 삭제는 하지 않습니다.

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

### B. 반영 후 생긴 데이터를 지키며 되돌리기 (승인 후 수동, 새 이미지로만 downgrade)

```bash
docker compose stop -t 30 frontend backend
docker compose ps --status running --services       # 아무것도 없어야 함
# 새 리비전을 아는 '새' 이미지로 downgrade만 실행 (CMD를 바꾸므로 서버는 뜨지 않는다 — 리허설에서 확인)
docker compose run --rm --no-deps backend alembic downgrade "$PREV"
docker tag moajwo-backend:$TAG moajwo-backend:latest && docker tag moajwo-frontend:$TAG moajwo-frontend:latest
docker compose up -d --no-build --wait
docker compose exec -T backend alembic current      # = $PREV
```

- 실행 전에 그 시점의 백업을 먼저 만듭니다 (스크립트 5단계와 같은 one-off 명령). 각 downgrade는 그 리비전이 넣은 데이터만 지우고 사용자 별칭이 가리키는 기본 항목은 남깁니다 (0015는 `owner_setup` 표시 한 행만).

## 리허설 결과

### 1차 — 수동 명령 (2026-10-11, 운영 볼륨 복제본, 격리 프로젝트 `moajwo-rehearsal`, 소스 `24d2fc4`)

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

### 2차 — `scripts/deploy.sh` 중단 조건 시험 (2026-10-11, 같은 방식의 격리 환경, 소스 `b02ddee`)

| 시험 | 결과 |
| --- | --- |
| 승인 SHA 불일치 (이전 PR HEAD `49e561f` 전달) | 1단계에서 중단(종료 코드 1). 서비스·이미지(`aa598ff2e6a5`)·리비전 0014 그대로, 백업 0개 |
| backend에 추적하지 않는 파일 | 1단계에서 중단. 서비스 그대로 |
| 백업 실패 (백업 one-off 이미지 없음, 정지 이후) | 백업 명령 실패(125) → 리비전 0014 그대로 확인 → 기존 컨테이너 재시작(`aa598ff2e6a5`), 새 이미지는 기동하지 않음 |
| 데이터 볼륨을 쓰는 다른 컨테이너 잔존 | 4단계에서 중단 → 리비전 그대로 확인 → 기존 컨테이너 재시작, 백업 만들지 않음 |
| 정상 반영 | 트리 일치 → 빌드 → 정지 → 백업(sha256·integrity ok) → 전환, 리비전 0015 (head), 로그인·원본·다운로드 정상, 중단 24초 |

시험 뒤 리허설 볼륨·백업 사본(운영 데이터 포함)·시험이 만든 롤백 태그는 삭제했습니다.

### 3차 — 조회 실패 처리 (2026-10-11)

명령 대역 시험 `scripts/test_deploy.sh`: 임시 git 저장소 + 가짜 docker로 모든 호출을 기록 (실제 Docker 사용 안 함).

| 시험 | 현재 (`9a4a887`) | 이전 (`84233d2`) |
| --- | --- | --- |
| 승인 SHA 불일치 | 중단 · 호출 없음 | 중단 |
| Compose 실행 중 서비스 조회 실패(빈 출력) | 중단 · 백업·전환 없음 · 리비전 확인 후 재개 | **백업·전환 진행, 종료 코드 0** |
| 위 + 리비전 확인도 실패 | 중단 · 재개하지 않고 수동 복구 안내 | **백업·전환 진행** |
| 볼륨 사용 컨테이너 조회 실패(빈 출력) | 중단 · 백업·전환 없음 · 재개 | **백업·전환 진행, 종료 코드 0** |
| 정지 후 서비스가 남음 | 중단 · 재개 | 중단 |
| 두 조회 정상 + 빈 결과 | 정상 반영 (백업 → 전환 → 전환 완료) | 정상 반영 |
| 백업 실패 | 중단 · 전환 없음 · 재개 | 중단 |
| 전환 후 이미지 조회 실패 | '전환 완료' 없이 실패 | **'전환 완료' 출력, 종료 코드 0** |
| 전환 후 리비전 조회 실패 | '전환 완료' 없이 실패 | 실패(다른 사유로 판정) |
| 전환 후 컨테이너 조회 실패 | '전환 완료' 없이 실패 | **'전환 완료' 출력, 종료 코드 0** |

같은 날 격리 실제 Docker(운영 볼륨 복제본)에서 `9a4a887`로 승인 SHA 불일치(중단)·백업 실패(리비전 0014 확인 후 기존 컨테이너 재개)·정상 반영(0015, 로그인·원본·다운로드 정상)을 다시 확인했고, 시험 뒤 리허설 볼륨·백업 사본·롤백 태그를 삭제했습니다.

## 알려진 제한

- 의미가 불명확한 월별 실적 열이 시점 값 항목(차량·인구 등)으로 매핑되고 합계가 가장 늦은 시점 값과 같으면 누적 예외로 오판될 수 있습니다.
- 주민등록번호가 탐지된 파일의 과거 기록에서는 같은 형태(13자리, 생년월일·성별 자리 유효)의 정상 숫자도 마스킹될 수 있습니다.
- 배포 명령과 스크립트는 Git Bash에서 검증했습니다 (PowerShell 미검증).
- 운영 반영과 운영 검증은 아직 수행하지 않았습니다.
