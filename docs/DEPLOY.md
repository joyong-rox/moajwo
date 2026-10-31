# 운영 반영 · 백업 · 롤백

운영은 사용자 PC의 Docker Compose입니다 (원격 배포 시스템 없음). 아래 명령은 **Git Bash 기준**이며, 운영 볼륨을 복제한 격리
프로젝트에서 같은 형태로 리허설했습니다 (결과는 맨 아래).

| 항목 | 값 |
| --- | --- |
| Compose 프로젝트 | `moajwo` (`docker-compose.yml`) |
| 서비스 | `backend` (127.0.0.1:8000, `moajwo-backend:latest`, 실행 사용자 `app` uid 1000) · `frontend` (127.0.0.1:8080, `moajwo-frontend:latest` — 컨테이너 안 포트는 이번 반영부터 8080, 그 전 이미지는 80. 8장) |
| 데이터 볼륨 | `moajwo_moajwo-data` → `/app/data`: `moajwo.db`(SQLite, journal_mode=delete), `uploads/`(업로드 원본), `outputs/`(통합 결과) — 모두 uid 1000 소유 |
| 마이그레이션 | backend 기동 명령이 `alembic upgrade head` 후 서버를 띄움 (이미지에 ENTRYPOINT 없음, CMD만) |

**하지 않는 것**: 운영 볼륨 삭제, `docker compose down -v`, 자동 DB downgrade. backend는 일반 사용자(`app`)로 실행합니다
(원본·결과 파일의 읽기 전용 보호는 root로 실행하면 효과가 없음).

## 0. 현행 실행 기준 (2026-10-29 반영 뒤)

| 항목 | 값 |
|---|---|
| 지금 운영 | main `b12ba63` (PR #60 병합, 트리 `845f953a…`) · DB `0021_run_request_key` · backend `sha256:16b805ca3b3762c530e352bc6ef40c75e754e7712b2a2bfd19f76d260b9ce1ef` · frontend `sha256:bf25700f2d7423900b06bc34ac3e3ce450b228964350da7181ed67dde20ca593` (2026-10-29 20:51 반영, 기록 `backups/deploy-20261029-205119.log`; 그 전 2026-10-28 11:44 반영은 main `71f36eb`·backend `6874cec0…`·frontend `883f7fa5…`, 기록 `backups/deploy-20261028-114420.log`). 0022(관리자 발급·임시 비밀번호, 12장)는 main에 병합됐지만 **아직 운영에 반영하지 않았다** |
| 다음 이미지 교체 명령 | `bash scripts/deploy.sh <승인 SHA> 0021_run_request_key` — 두 번째 인자는 **지금 운영 리비전 0021**입니다. 2026-10-28에 쓴 `… 0019_analysis_runs`는 그 반영의 출발점이라 다시 쓰지 않습니다 (운영이 0021이라 2단계에서 멈춤) |
| 3단계 (이 판부터) | 자원 확인 → 후보 태그로 하나씩 빌드 → 완성 이미지 목록 대조 + 격리 기동 점검 + 통과 기록 확인 (11장) → 백업 확인 뒤 5f에서 통과한 ID에만 latest를 옮기고(실제 ID로 확인) 6단계에서 다시 빌드하지 않고 전환 |
| DB 변경이 없는 교체의 롤백 | 반영 직전에 실행 중이던 이미지(롤백 태그 — 지금 위의 backend·frontend) + 이전 포트 연결 + 리비전 0021 그대로. 4장 B 0단계 출력이 '리비전 0021_run_request_key → 0021_run_request_key'이면 3~5단계(정리·downgrade)를 건너뛰고 1·2·6·7단계만 합니다 |
| 2026-10-27 실행 기준 (PR #58, 0019 출발) | 이미 반영된 기록입니다 — 10장과 `backups/deploy-20261028/RECORD.md`. 8·9·10장의 '0019 출발'·'0020 출발' 설명은 그 반영에 대한 것입니다 |

## 1·2. 반영 실행: `scripts/deploy.sh` (준비 → 빌드 → 쓰기 중단 → 백업·검증 → 전환)

### 승인 대상과 배포 대상의 구분

- 승인은 **PR HEAD 커밋**(예: PR #1의 마지막 커밋)으로 받습니다. 병합 방식(merge commit·squash·rebase)에 따라 **병합 후 main의 SHA는
  달라질 수 있으므로**, 스크립트는 SHA가 아니라 **트리(파일 내용)**가 같은지로 "승인한 변경 = 배포할 내용"을 확인하고 두 SHA를 모두 기록합니다.
- 병합 후 main에 다른 변경이 섞여 있으면 트리가 달라 스크립트가 멈춥니다 (그때는 새로 승인받습니다).
- 여러 PR을 함께 반영할 때는 **모든 PR(보고서 PR 포함)을 담은 후보 브랜치의 HEAD**를 승인 대상으로 삼습니다. 병합을 마친 main의 트리가
  그 후보와 같아야 하며(`git diff <후보 SHA> main`이 비어야 함), 보고서 파일 하나만 달라도 스크립트는 멈춥니다. 승인 검사는 없애거나
  건너뛰지 않습니다.
- 롤백 단위는 **이미지(롤백 태그) + 이전 컨테이너의 포트 연결 + 백업**입니다. 스크립트 2단계가 실행 중 컨테이너의 실제 포트 연결을
  `backups/rollback-<시각>.compose.yml`(Compose 덧붙임, `ports: !override`)로 남기고 배포 단위 줄에 적습니다 (4장).

```bash
cd /c/Users/juho/Documents/Moajwo
git fetch origin && git checkout main && git pull --ff-only
bash scripts/deploy.sh <승인한 PR HEAD 커밋 SHA> <반영 전 리비전>
# 예 (2026-10-28 이후 다음 교체 — 지금 운영 리비전): bash scripts/deploy.sh <SHA> 0021_run_request_key
```

반영 전 리비전은 운영자가 미리 확인해 인자로 줍니다. 실행 중 backend의 리비전이 이 값과 다르면 **서비스를 멈추기 전에** 중단하고, 원인을
확인하기 전까지 전환하지 않습니다.

### 스크립트가 하는 일과 멈추는 조건

| 단계 | 하는 일 | 멈추는 조건 (실패 시 상태) |
| --- | --- | --- |
| 1 | 승인 커밋 존재·트리 일치, 미커밋·숨은 변경과 대체 설정 확인 (11장 '보호 범위') | 승인 커밋 없음 · 트리 불일치 · 추적 파일 변경 · 저장소의 추적하지 않는 파일 · 숨김 표시·`filter` 속성 · 작업 파일 내용이 승인 트리 blob과 다름 · `DEPLOY_*`(리허설 표지 없이) · 다른 이름·덧붙임 Compose 파일·`COMPOSE_FILE` · (시작할 때) 내보낸 셸 함수·`BASH_ENV`·`ENV` (**서비스 그대로**) |
| 2 | 실행 중 컨테이너의 실제 이미지 ID로 롤백 태그(`rollback-<시각>`), 반영 전 리비전 기록 · 인자로 받은 반영 전 리비전과 같은지 확인 | 컨테이너·리비전을 읽을 수 없음 · 리비전이 인자와 다름 (**서비스 그대로**, 정지하지 않음) |
| 3 | 자원 확인 → 후보 태그(`<이미지>:cand-<시각>`)로 하나씩 빌드(여유 메모리 기록) → `scripts/image_gate.sh`: 완성 이미지 목록이 검증한 목록(`deploy/expected-images/`)과 같은지 · 자원 확인 · 격리 기동 점검 → 통과 기록(`gate.result`의 두 이미지 ID) 확인 (11장). latest 태그는 옮기지 않음 | 자원 기준 미달·측정 실패 · Python 없음 · 빌드 실패 · 후보 이미지 ID 조회 실패 · 목록 다름·입력 오류·검증 목록의 커밋하지 않은 변경 · 격리 점검 실패·도구 오류 · 빌드·점검 감시 컨테이너 정리 확인 실패 · 통과 기록 없음·ID 다름 (**서비스·latest 태그 그대로**) |
| 4 | `stop -t 30 frontend backend` (진행 중 요청은 최대 30초 대기) 후 실행 중 서비스 0개, 데이터 볼륨을 쓰는 컨테이너 0개 확인 (두 조회 모두 성공해야 함) | 정지 실패 · 조회 실패 · 남은 서비스 · 볼륨을 쓰는 다른 컨테이너 → **마이그레이션 미시작(리비전 그대로)을 확인할 수 있으면 기존 컨테이너 재시작, 없으면 수동 복구 안내** |
| 5 | 백업과 검증 (아래 '5단계 백업 검증'): a 정지 상태 운영 볼륨 DB의 리비전·행 수 → b 볼륨 전체 백업·`gzip -t`·`moajwo.db` 포함·sha256 기록 → c **백업에서 꺼낸 DB**의 무결성·리비전·행 수 → d a와 c 비교 → e sha256 재확인 | 조회 실패 · 필수 결과 누락 · 백업 생성·압축 검사 실패 · 추출·열기 실패 · 백업 DB 무결성 실패 · 리비전·테이블·행 수 불일치 · sha256 불일치 → **4단계와 같은 방식으로 기존 컨테이너 재시작**, 이 백업은 요약에 `verified no` |
| 5f | 백업 확인 후에만 3단계에서 검증한 이미지 ID에 latest 태그 → 실제 ID로 확인 (6단계 머리줄 앞, 컨테이너는 멈춘 그대로) | 태그 실패 → 둘 다 반영 전 이미지로 되돌리고 되돌린 결과도 확인: 되돌림 명령 종료 0이고 두 ID가 반영 전이면 '되돌림 확인', 명령이 실패했는데 ID가 반영 전이면 그대로 적음, 아니면 '[주의] 되돌림 확인 안 됨'(어느 태그가 어느 이미지인지) → 4단계와 같은 방식으로 기존 컨테이너 재개 (재개 결과와 태그 상태를 따로 적음) |
| 6 | `up -d --no-build --wait` (다시 빌드하지 않고 재생성·마이그레이션) → 리비전 `(head)`·컨테이너·이미지 조회 → 실행 중 이미지 = 검증한 ID | 전환 실패 · 전환 후 조회 실패 · 실행 중 이미지가 검증한 ID와 다름 → '전환 완료' 없이 멈춤, 자동 롤백하지 않음 (4장 절차, 백업·롤백 태그는 로그에 있음) |

- 모든 단계는 `backups/deploy-<시각>.log`에 남고, 마지막 줄에 **배포 단위**(승인 SHA·배포 SHA·이전 이미지 ID·롤백 태그·백업 파일·리비전 전후)를 적습니다. 롤백에 이 값들을 씁니다.
- `set -uo pipefail`을 쓰지만 판단에 쓰는 **모든 조회는 종료 코드와 필수 결과를 함께** 검사합니다: 백업 경로 변환, git 커밋·트리·상태, 컨테이너·이미지 ID, 반영 전후 리비전, 실행 중 서비스(`compose ps --status running --services`), 볼륨 사용 컨테이너(`docker ps --filter volume=`). 조회가 **성공했고 결과가 비어 있을 때만** '없음'으로 판단하고, 조회 자체가 실패하면 '확인할 수 없음'으로 기록한 뒤 백업·전환을 하지 않습니다. 5단계의 DB 조회 결과는 파이프 없이 파일·변수로 받아 종료 코드를 보존하고, 마지막 줄(`end ok`)·리비전·행 수가 모두 있어야 '결과 있음'으로 봅니다.
- 정지 이후 실패하면 볼륨의 DB 리비전 조회가 **성공하고** 반영 전 리비전과 같을 때만 기존 컨테이너를 `start`로 재개합니다. 리비전을 확인할 수 없거나 다르면 재개하지 않고, 로그에 현재 상태 확인·수동 복구 명령을 남깁니다.
- 전환 후 리비전·컨테이너·이미지 조회 중 하나라도 실패하면 '전환 완료'를 출력하지 않고 실패(종료 코드 1)로 끝냅니다.
- 실패 처리 시험: `bash scripts/test_deploy.sh` (실제 Docker를 쓰지 않는 명령 대역 시험, 아래 3차·4차 결과. 2026-10-28 후보 빌드·검증·전환 6사례, 같은 날 PR #59 검토 보완 26사례를 더해 57사례 — 검증 도구 자체는 `scripts/test_image_tools.py` 74사례)
- `backups/`에는 운영 데이터가 들어 있어 git에서 제외합니다 (`.gitignore`).
- 사용자에게 반영 시각을 미리 알리고, 진행 중인 업로드·검토·통합이 없는지 확인한 뒤 실행합니다 (통합은 요청 안에서 끝나는 동기 작업).
- frontend만 멈추면 backend가 127.0.0.1:8000으로 계속 요청을 받으므로 스크립트는 둘 다 멈춥니다.

### 5단계 백업 검증 — 어디서 무엇을 확인하고 비교하는가

| 구분 | 확인 위치 | 확인하는 값 | 기록 |
| --- | --- | --- | --- |
| a. 운영 볼륨 값 | 서비스를 멈춘 상태의 운영 볼륨 DB (읽기 전용 마운트, 쓰기 없음) | 리비전 · 모든 테이블의 행 수 | `backups/moajwo-data-<시각>.source.txt`, 로그 `[운영 볼륨]` |
| b. 백업 파일 | 만든 압축 파일 | `gzip -t` · 목록에 `./moajwo.db` · sha256 (컨테이너에서 계산) | 로그 `[백업 파일]`, 요약의 `sha256` |
| c. 백업 DB 값 | 압축에서 꺼낸 DB — **운영 볼륨을 마운트하지 않은** one-off 컨테이너의 `/tmp`에 풀고 읽기 전용으로 엶 | `integrity_check` · 리비전 · 저널 모드 · 모든 테이블의 행 수 | `backups/moajwo-data-<시각>.backup.txt`, 로그 `[백업 DB]` |
| d. 비교 | a와 c | 리비전 같음(= 인자로 받은 반영 전 리비전) · 테이블 목록 같음 · 테이블마다 행 수 같음 | 로그에 테이블마다 `[일치]`/`[불일치]`, 마지막에 `비교 결과` |
| e. 재확인 | 호스트(Git Bash)에서 백업 파일 sha256 다시 계산 | b의 기록과 같음 | 요약 `backups/moajwo-data-<시각>.txt` (`verified yes`/`no`) |

- **비교 대상**: 테이블 목록을 고정하지 않고 운영 볼륨 DB에 있는 **모든 테이블**을 비교합니다. 문서에서 따로 보는 핵심 테이블(사용자 `users`, 관리 이력 `account_events`, 세션 `auth_sessions`, 매핑 확정 `mapping_decisions`, 원본 파일 정보 `source_files`)은 로그에 `(핵심)`으로 표시하고, 그 리비전의 DB에 없는 테이블(예: 0016 이전 DB의 `account_events`)은 `[없음]`으로 적고 넘어갑니다. 비교는 행 수까지이며 행 내용은 비교하지 않습니다 (백업 DB 손상은 `integrity_check`로 봅니다).
- **운영 볼륨 값과 백업 값을 섞지 않습니다**: 무결성 검사 결과는 백업에서 꺼낸 DB의 결과만 기록합니다 (운영 볼륨 DB에는 무결성 검사를 하지 않음). 로그·요약에 출처를 함께 적습니다.
- **전환을 허용하는 조건** (모두 만족해야 6단계로 감): 1~4단계 통과 · 반영 전 리비전 = 인자 · 운영 볼륨 조회 성공·결과 완전·리비전 = 반영 전 리비전 · 백업 생성·압축 검사·`moajwo.db` 포함 확인 · 백업 DB 추출·열기 성공·결과 완전 · 백업 DB 무결성 `ok` · 리비전·테이블 목록·행 수 모두 같음 · sha256 재계산 일치. 이때만 요약에 `verified yes`를 씁니다.
- **실패 시 재개 조건**: 정지 전(1~3단계, 2단계 리비전 불일치 포함) 실패는 서비스를 멈추지 않습니다. 정지 뒤(4·5단계) 실패는 **볼륨의 DB 리비전 조회가 성공하고 반영 전 리비전과 같을 때만** 기존 컨테이너를 `start`로 재개합니다(새 이미지·마이그레이션 실행 없음). 리비전을 확인할 수 없거나 다르면 재개·복원·downgrade하지 않고 로그에 상태 확인·수동 복구 명령을 남깁니다. 백업 검증에 실패한 백업은 요약에 `verified no`와 사유가 남고, 복원에 쓰지 않습니다.
- 백업 검증 중 운영 DB를 쓰거나 복원하지 않습니다: 운영 볼륨은 a·b에서 읽기 전용으로만 마운트하고, c 컨테이너에는 마운트하지 않습니다 (명령 대역 시험이 모든 사례에서 이를 확인).
- 비밀번호·쿠키·원문 개인정보는 기록하지 않습니다 (5단계 기록·요약에는 리비전·테이블 이름·행 수·sha256·파일 이름만 남깁니다).

## 3. 전환과 점검

```bash
# 스크립트 6단계가 실행한 내용 (확인용)
docker compose exec -T backend alembic current      # 새 리비전 (head)
docker inspect moajwo-backend-1 --format '{{.Image}}'   # 로그의 새 backend 이미지
```

점검:
1. `curl -s http://127.0.0.1:8080/api/health` → `{"status":"ok","database":"ok"}` (frontend 프록시 경유 — backend 호스트 포트를 닫은 B′ 상태에서도 같은 명령)
2. 로그인 → 새로고침 후 로그인 유지 → 로그아웃
3. 기존 파일의 열 매핑 분석(저장된 원본을 다시 읽음) · 기존 실행의 결과 다운로드
4. 새 파일 업로드 → 매핑 → 사전 검토 → 검토 완료 → 통합 결과 만들기 → 내려받은 엑셀 열기
5. 일반 사용자: 설정·사전 관리 메뉴가 없고 `/settings` 직접 접근이 403
6. `docker compose logs backend --since 10m`에 오류 없음

**중단 시간**: 빌드는 중단 전에 끝납니다. 중단 구간은 쓰기 중단·백업·전환입니다. 측정값은 **이 데이터 규모(DB 48MB, 원본 소량)에서의 값**입니다 — 1차 리허설(수동 명령) 15.6초(쓰기 중단 1.6 + 백업 5.9 + 전환 8.1), 스크립트 시험 24초(검증 단계 포함). 반영 후 기본 점검은 별도이고, 백업 시간은 볼륨 크기에 비례하며 사람이 하는 점검 시간은 들어 있지 않습니다.

## 4. 롤백

롤백 전에 **항상 backend·frontend를 먼저 멈춥니다** (DB를 쓰는 프로세스가 없어야 백업·복원·downgrade가 안전). 이전 코드는 새 리비전을 모르므로
(그대로 두면 이전 backend가 `alembic upgrade head`에서 실패) DB도 이전 리비전으로 맞춥니다.

- 롤백 단위는 되돌릴 반영의 로그(`backups/deploy-<시각>.log`)에 남은 **배포 단위** 줄입니다: 이전 이미지(롤백 태그 `rollback-<시각>`) + 이전 컨테이너의 포트 연결(`backups/rollback-<시각>.compose.yml`) + 반영 전 백업 + 반영 전 리비전. 전환(1·2장 6단계)에서 멈춘 반영은 배포 단위 줄 대신 `[중단] … docs/DEPLOY.md 4장 (백업 …, 롤백 태그 …, 포트 …, 반영 전 리비전 …)` 줄이 남습니다. 그 앞(3단계 검증·5f 전환 준비 포함)에서 멈춘 반영은 전환이 없어 컨테이너·DB는 되돌릴 것이 없습니다. 5f에서 멈췄고 그 로그의 'latest 태그:' 줄이 '[주의] 되돌림 확인 안 됨'이면 그 줄의 반영 전 ID로 latest 태그만 손으로 되돌립니다 — 0단계가 그렇게 안내합니다. 이전 이미지와 포트 연결은 **함께** 씁니다.
- **이미지만 되돌리지 않습니다.** 지금 저장소의 `docker-compose.yml`은 새 버전의 설정이라, 이전 이미지를 이 파일로 띄우면 설정이 맞지
  않을 수 있습니다 (예: 8장 — 이전 frontend는 컨테이너 80에서 듣는데 새 설정은 8080으로 연결). 덧붙임 파일을 `-f`로 붙여 이전 포트 연결로 띄우고,
  최종 설정과 실제 컨테이너의 포트 연결이 그 파일과 같은지 확인합니다 (덧붙임이 목록을 더하지 않고 바꾸도록 `!override` — Docker Compose 2.24.4 이상).
  이 파일이 없는 예전 반영은 `git show <이전 main SHA>:docker-compose.yml`로 이전 설정을 꺼내 씁니다.
- `down -v`, 자동 downgrade, 운영 데이터 삭제, '결과 확인 필요' 실행의 일괄 실패 처리는 하지 않습니다.
- 두 가지 길: **B(기본) 자료를 지키며 되돌리기** — 반영 뒤 생긴 업로드·실행 기록을 그대로 두고 이전 코드가 읽습니다. **A 배포 전 백업으로 복원** — 반영 뒤 생긴 자료가 활성 데이터에서 빠지므로(지우지 않고 옮겨 둠) 빠질 범위를 사용자에게 알리고 **별도 승인**을 받은 뒤 씁니다.
- **B′ — 구판으로 되돌린 동안 공개 가입 차단 (0022 이후 판의 롤백, 2026-10-31 소유자 결정 D2):** 구판(0021 이하) 앱은 공개 회원가입을 다시 연다(정책
  회귀). 되돌린 뒤 **P1 상자**로 덧붙임 설정(`deploy/bprime/`)만 적용해 `POST /api/auth/signup`을 nginx에서 410으로 끝내고 backend 호스트 포트 공개를
  없앤다 — 구판 이미지는 고치지 않는다. 해제는 **P0 상자**. 0022 이후 판의 B는 P1까지 해야 끝난다(12-4). 조건·중단 시간·판단은
  [RECOVERY_RUNBOOK.md](RECOVERY_RUNBOOK.md).

### B. 자료를 지키며 되돌리기 — 단계별 명령 (8장 통합 후보·9장 0020·10장 0021 롤백은 모두 이 절차)

저장소 폴더에서 Git Bash로 0단계부터 차례로 붙여 넣습니다. **각 단계는 앞 단계 끝에 `[N 완료]`가 찍힌 것을 본 뒤에만** 실행합니다.
1~7단계 상자는 `( … )` 안에서 돌아 실패해도 창이 닫히지 않고, 실패하면 `[중단] …`과 다음 행동을 알립니다 — 그때는 다음 단계(백업·정리·downgrade·이미지
전환)를 실행하지 않고 아래 '멈췄을 때' 표를 봅니다. 반영이 DB 리비전을 바꾸지 않았으면(0단계 출력의 '리비전 X → X') 3~5단계를 건너뜁니다.
되돌리기를 다시 시작할 때는 같은 창이라도 0단계부터 붙여 넣습니다 — 0단계가 로그와 값을 매번 새로 읽습니다.
0단계는 **가장 최근 반영 로그 하나만** 읽습니다 — 다른 로그의 배포 단위를 대신 고르지 않습니다. 그 앞 반영을 되돌리려면 0단계 바로 전에
`ROLLBACK_LOG=backups/deploy-<그 반영 시각>.log`를 입력합니다 (0단계가 한 번 쓰고 지우므로 다음 0단계는 다시 가장 최근 로그를 읽습니다).
1단계는 실행 중 backend가 `latest`이고 그 로그가 띄운 이미지인지, 이미 되돌린 상태(backend·frontend의 `latest`가 모두 롤백 태그 이미지)가
아닌지 확인한 뒤에만 멈춥니다.

<!-- rollback-step:0 -->
```bash
# 0) 되돌릴 배포 단위 읽기 (바꾸는 것 없음) — 가장 최근 반영 로그 하나만 (앞 반영은 바로 전에 ROLLBACK_LOG=backups/deploy-<시각>.log)
LOGF=${ROLLBACK_LOG:-$(ls backups/deploy-*.log 2>/dev/null | sort | tail -1)}; unset ROLLBACK_LOG   # 한 번만 쓰고 창에 남기지 않음
echo "반영 로그: ${LOGF:-(없음)}"; [ -z "$LOGF" ] || [ -f "$LOGF" ] || echo "[중단] 반영 로그 $LOGF 가 없음 — 파일 이름 확인"
HOSTDIR=$(pwd -W 2>/dev/null || pwd)   # Git Bash는 Windows 경로, 리눅스는 그대로
dbrev() {  # 데이터 볼륨 DB의 리비전 (읽기 전용 — 서버가 떠 있든 멈췄든)
  docker run --rm --entrypoint python -v moajwo_moajwo-data:/data:ro moajwo-backend:latest -c \
    'import sqlite3; print(sqlite3.connect("file:/data/moajwo.db?mode=ro", uri=True).execute("select version_num from alembic_version").fetchone()[0])'
}
UNIT=$(grep -h "배포 단위:" "$LOGF" 2>/dev/null | tail -1)
HINT=$(grep -h "\[중단\].*docs/DEPLOY.md 4장 (백업 " "$LOGF" 2>/dev/null | tail -1)
START=$(grep -h "· 롤백 태그 .*:rollback-[0-9-]*$" "$LOGF" 2>/dev/null | tail -1)   # deploy.sh 2단계 줄 (반영 전 리비전·롤백 태그)
NEWB=$(grep -h "전환 완료:" "$LOGF" 2>/dev/null | tail -1 | sed -n 's|.* backend \([0-9a-f]\{12\}\) .*|\1|p')
TS= PREV= NEWREV=
if [ -n "$UNIT" ]; then   # 전환을 마친 반영
  echo "$UNIT"
  TS=$(printf '%s\n' "$UNIT" | sed -n 's|.*backups/rollback-\([0-9-]*\)\.compose\.yml.*|\1|p')
  PREV=$(printf '%s\n' "$UNIT" | sed -n 's|.*리비전 \([0-9a-z_]*\) → .*|\1|p')
  NEWREV=$(printf '%s\n' "$UNIT" | sed -n 's|.*리비전 [0-9a-z_]* → \([0-9a-z_]*\).*|\1|p')
elif [ -n "$HINT" ] || { [ -n "$START" ] && grep -q "== 6\. 새 이미지로 전환" "$LOGF"; }; then
  # 전환에서 멈춘 반영(또는 전환 도중에 로그가 끊긴 반영) — 마이그레이션이 진행됐을 수 있어 반영 후 리비전은 지금 DB에서 읽는다
  if [ -n "$HINT" ]; then
    printf '%s\n' "$HINT" | sed 's/^\[[0-9:]*\] \[중단\] /반영 로그(전환에서 멈춤): /'
    TS=$(printf '%s\n' "$HINT" | sed -n 's|.*롤백 태그 rollback-\([0-9-]*\),.*|\1|p')
    PREV=$(printf '%s\n' "$HINT" | sed -n 's|.*반영 전 리비전 \([0-9a-z_]*\)).*|\1|p')
  else
    echo "[주의] 반영 로그가 전환(deploy.sh 6단계) 도중에 끝남 — 창 닫힘·재부팅 등으로 끊긴 반영으로 보고 전환에서 멈춘 반영처럼 다룸"
    TS=$(printf '%s\n' "$START" | sed -n 's|.*:rollback-\([0-9-]*\)$|\1|p')
    PREV=$(printf '%s\n' "$START" | sed -n 's|.*반영 전 리비전 \([0-9a-z_]*\) (예상과 같음).*|\1|p')
  fi
  if NEWREV=$(dbrev); then echo "전환에서 멈춘 반영 — 지금 DB 리비전 $NEWREV"
  else NEWREV=; echo "[중단] 지금 DB 리비전을 읽지 못함 — 데이터 볼륨·moajwo-backend:latest 이미지 확인 (1단계가 멈춤)"; fi
else
  echo "[안내] 이 로그로는 되돌릴 배포 단위를 정하지 않음 (배포 단위 줄·전환 실패 줄 없음) — 로그 끝을 보고 판단:"
  if [ -n "$LOGF" ] && ! grep -q "== 4\. 쓰기 중단" "$LOGF"; then   # 2026-10-28 이미지 검증(11장) 뒤의 deploy.sh 기준
    echo "  정지(4단계) 전에 멈춘 반영 (3단계 이미지 검증 실패 등) — 서비스·latest 태그는 그대로라 되돌릴 것이 없음"
  elif [ -n "$LOGF" ] && grep -q "== 5f\. 전환 준비" "$LOGF"; then
    echo "  전환 준비(5f, latest 태그 이동)에서 멈춘 반영 — 전환하지 않았음. 로그의 'latest 태그:' 줄이 '되돌림 확인'이면 태그도 반영 전 그대로,"
    echo "  '[주의] 되돌림 확인 안 됨'이면 그 줄의 반영 전 ID로 docker tag <반영 전 ID> <이름>:latest 를 하고 docker image inspect로 확인 (11장)"
  fi
  echo "  '기존 서비스 재개'가 있으면 반영 전 판이 다시 떠 있어 되돌릴 것이 없음 · '[수동 확인 필요]'가 있으면 그 아래 안내대로"
  echo "  둘 다 없으면 로그 이름을 확인 (앞 반영은 ROLLBACK_LOG를 지정해 0단계부터)"
fi
TAG="rollback-$TS"; RBF="backups/rollback-$TS.compose.yml"; RB="-f docker-compose.yml -f $RBF"
PORTS_PY='import json, sys
d = json.load(sys.stdin)["services"]
for s in ("backend", "frontend"):
    for p in d[s].get("ports", []):
        print(s, "%s:%s:%s/%s" % (p.get("host_ip", ""), p["published"], p["target"], p.get("protocol", "tcp")))'
VERIFY_PY='import os, sqlite3, tarfile, tempfile
def read(path):
    c = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    names = [r[0] for r in c.execute("select name from sqlite_master where type = ? and name not like ? order by name", ("table", "sqlite_%"))]
    return (c.execute("pragma integrity_check").fetchone()[0], c.execute("select version_num from alembic_version").fetchall(),
            {n: c.execute("select count(*) from [" + n + "]").fetchone()[0] for n in names})
src = read("/data/moajwo.db")
d = tempfile.mkdtemp()
with tarfile.open("/backup/" + os.environ["A"]) as t:
    for m in t.getmembers():
        if m.isfile() and m.name.startswith("./moajwo.db"):
            with open(os.path.join(d, os.path.basename(m.name)), "wb") as f:
                f.write(t.extractfile(m).read())
bk = read(os.path.join(d, "moajwo.db"))
assert bk[0] == "ok", "백업 DB 무결성 검사 실패: " + bk[0]
assert bk == src, "백업 DB와 정지 상태 볼륨 DB의 리비전·표·행 수가 다름"
print("[백업 검증] 무결성 ok · 리비전", src[1][0][0], "· 표", len(src[2]), "개의 행 수 같음")'
STATUS_PY='import json, sys
d = json.load(sys.stdin)
owned = [r["id"] for r in d["running"] if r["owner"] in ("me", "alive", "dead", "unknown")]
print("running(담당 있음)", owned, "· running(이전 판)", [r["id"] for r in d["running"] if r["id"] not in owned],
      "· check", [r["id"] for r in d["check"]])
sys.exit(1 if owned else 0)'
echo "되돌릴 배포 단위: 시각 ${TS:-(없음)} · 리비전 ${PREV:-(없음)} → ${NEWREV:-(없음)} · 이전 이미지 $TAG · 이전 포트 $RBF${NEWB:+ · 반영한 backend $NEWB}"
```

<!-- rollback-step:1 -->
```bash
# 1) 대상·이전 이미지·이전 포트 파일 확인(바꾸는 것 없음) → 쓰기 중지 → 쓰는 프로세스가 없는지 확인
( set -euo pipefail
  [ -n "$TS" ] && [ -n "$PREV" ] && [ -n "$NEWREV" ] \
    || { echo "[중단] 되돌릴 배포 단위(시각 ${TS:-?} · 반영 전 리비전 ${PREV:-?} · 반영 후 리비전 ${NEWREV:-?})를 다 정하지 못함 — 0단계 출력의 [안내]·[중단] 줄 확인 (아무것도 바꾸지 않음)"; exit 1; }
  [ "$(docker compose config | sed -n 's/^name: //p')" = moajwo ] || { echo "[중단] Compose 프로젝트가 moajwo가 아님"; exit 1; }
  docker volume inspect moajwo_moajwo-data --format '데이터 볼륨 {{.Name}}' \
    || { echo "[중단] 데이터 볼륨 moajwo_moajwo-data 없음 (아무것도 바꾸지 않음)"; exit 1; }
  [ -f "$RBF" ] || { echo "[중단] 이전 포트 파일 $RBF 없음 — 이미지만 되돌리지 않음 (아무것도 바꾸지 않음)"; exit 1; }
  docker image inspect "moajwo-backend:$TAG" "moajwo-frontend:$TAG" --format '{{.Id}}' > /dev/null \
    || { echo "[중단] 롤백 태그 $TAG 이미지 없음 — '멈췄을 때'의 R 상자 (아무것도 바꾸지 않음)"; exit 1; }
  lb=$(docker image inspect moajwo-backend:latest --format '{{.Id}}') && lf=$(docker image inspect moajwo-frontend:latest --format '{{.Id}}') \
    || { echo "[중단] latest 이미지를 조회하지 못함 (아무것도 바꾸지 않음)"; exit 1; }
  [ "$lb" != "$(docker image inspect "moajwo-backend:$TAG" --format '{{.Id}}')" ] \
    || [ "$lf" != "$(docker image inspect "moajwo-frontend:$TAG" --format '{{.Id}}')" ] \
    || { echo "[중단] latest가 backend·frontend 모두 롤백 태그 $TAG 이미지와 같음 — 이미 되돌렸거나 이 반영이 이미지를 바꾸지 않음. 되돌릴 것이 없어 멈추지 않음 (아무것도 바꾸지 않음)"; exit 1; }
  want=$(awk '/^  [a-z]+:$/ {s = $1; sub(":", "", s)} /^      - "/ {p = $2; gsub("\"", "", p); print s, p}' "$RBF" | sort)
  got=$(docker compose $RB config --format json | docker run --rm -i --entrypoint python "moajwo-backend:$TAG" -c "$PORTS_PY" | sort) \
    || { echo "[중단] 덧붙인 Compose 설정을 읽지 못함 (아무것도 바꾸지 않음)"; exit 1; }
  [ -n "$want" ] && [ "$want" = "$got" ] \
    || { echo "[중단] 덧붙인 설정의 포트 연결($got)이 이전 포트 파일($want)과 다름 (아무것도 바꾸지 않음)"; exit 1; }
  rev=$(dbrev) || { echo "[중단] DB 리비전을 읽지 못함 (아무것도 바꾸지 않음)"; exit 1; }
  [ "$rev" = "$NEWREV" ] \
    || { echo "[중단] 지금 DB 리비전($rev)이 배포 단위의 반영 후 리비전($NEWREV)과 다름 (아무것도 바꾸지 않음)"; exit 1; }
  run_b=$(docker inspect moajwo-backend-1 --format '{{.Image}}') \
    || { echo "[중단] backend 컨테이너를 찾지 못함 (아무것도 바꾸지 않음)"; exit 1; }
  [ "$run_b" = "$lb" ] \
    || { echo "[중단] backend 컨테이너 이미지(${run_b:7:12})가 latest가 아님 — 3~5단계가 다른 코드로 돌 수 있음 (아무것도 바꾸지 않음)"; exit 1; }
  [ -z "$NEWB" ] || [ "${run_b:7:12}" = "$NEWB" ] \
    || { echo "[중단] backend 컨테이너 이미지(${run_b:7:12})가 이 로그가 띄운 이미지($NEWB)가 아님 — 되돌릴 반영(ROLLBACK_LOG) 확인 (아무것도 바꾸지 않음)"; exit 1; }
  docker compose stop -t 30 frontend backend || { echo "[중단] 정지 명령 실패 — 1단계부터 다시 (백업·정리를 하지 않음)"; exit 1; }
  left=$(docker compose ps --status running --services) \
    || { echo "[중단] 정지 뒤 실행 중 서비스를 조회하지 못함 — 1단계부터 다시 (백업·정리를 하지 않음)"; exit 1; }
  [ -z "$left" ] || { echo "[중단] 정지 뒤에도 실행 중인 서비스: $(echo $left)"; exit 1; }
  w=$(docker ps --filter volume=moajwo_moajwo-data --format '{{.Names}}') \
    || { echo "[중단] 데이터 볼륨을 쓰는 컨테이너를 조회하지 못함 — 1단계부터 다시 (백업·정리를 하지 않음)"; exit 1; }
  [ -z "$w" ] || { echo "[중단] 데이터 볼륨을 쓰는 컨테이너가 남음: $(echo $w) — 백업·정리를 하지 않음"; exit 1; }
  echo "[1 완료] 쓰는 프로세스 없음 · DB 리비전 $rev · 이전 이미지 $TAG · 이전 포트 $(echo $want)"
)
```

<!-- rollback-step:2 -->
```bash
# 2) 정리(recover) 전에 지금 상태를 백업하고 검증 — 볼륨 전체(DB·원본·결과·담당 잠금 파일) 한 시점, 백업에서 꺼낸 DB를 정지 상태 볼륨 DB와 비교
( set -euo pipefail
  A="moajwo-data-before-rollback-$(date +%Y%m%d-%H%M%S).tgz"
  sha_c=$(docker run --rm --entrypoint sh -v moajwo_moajwo-data:/data:ro -v "$HOSTDIR/backups:/backup" moajwo-backend:latest -c \
    "tar czf /backup/$A -C /data . && gzip -t /backup/$A && tar tzf /backup/$A | grep -qx ./moajwo.db && sha256sum /backup/$A | cut -d' ' -f1") \
    || { echo "[중단] 백업 만들기·압축 검사 실패 — 정리·downgrade를 하지 않음 (만들다 만 backups/$A 는 쓰지 않음)"; exit 1; }
  docker run --rm --entrypoint python -e A="$A" -v moajwo_moajwo-data:/data:ro -v "$HOSTDIR/backups:/backup:ro" \
    moajwo-backend:latest -c "$VERIFY_PY" \
    || { echo "[중단] 백업 검증 실패 — 정리·downgrade를 하지 않음 (backups/$A 는 쓰지 않음)"; exit 1; }
  sha_h=$(sha256sum "backups/$A" | cut -d' ' -f1) && [ -n "$sha_h" ] && [ "$sha_h" = "$sha_c" ] \
    || { echo "[중단] 백업 sha256이 컨테이너(${sha_c:-없음})와 호스트(${sha_h:-없음})에서 다름 — 정리·downgrade를 하지 않음"; exit 1; }
  echo "$sha_h  backups/$A" | tee "backups/$A.sha256"   # 검증을 통과한 백업에만 생기는 표시
  docker compose run --rm --no-deps backend python -m app.recovery status > "backups/rollback-$TS.status-before.json" \
    || { echo "[중단] 정리 전 실행 상태를 남기지 못함 — 출력 확인 (정리를 하지 않음)"; exit 1; }
  echo "[2 완료] 백업 backups/$A 검증됨 · 정리 전 실행 상태 backups/rollback-$TS.status-before.json"
)
```

<!-- rollback-step:3 -->
```bash
# 3) 끝난 실행을 근거대로 정리 — 새 이미지의 one-off (서버는 뜨지 않음). 종료 0이 아니면 여기서 멈춤
( set -uo pipefail
  docker compose run --rm --no-deps backend python -m app.recovery recover > "backups/rollback-$TS.recover.json"; rc=$?
  cat "backups/rollback-$TS.recover.json"
  case $rc in
    0) echo "[3 완료] recover 종료 0 — 담당이 있는 실행 중 행 없음 (모든 결과가 확정됐다는 뜻은 아님 — 4단계)" ;;
    2) echo "[중단] recover 종료 2 — 담당이 있는 실행 중 행이 남음. next_s초 뒤 3단계만 다시 (downgrade·이전 이미지 기동 안 함)"; exit 2 ;;
    *) echo "[중단] recover 종료 $rc — 정리 오류. 출력을 보고 3단계를 다시 (downgrade·이전 이미지 기동 안 함)"; exit 1 ;;
  esac
)
```

<!-- rollback-step:4 -->
```bash
# 4) 정리 뒤 상태: running(담당 있음)·running(이전 판)·check를 따로 본다 — 담당이 있는 running이 남으면 멈춤
( set -euo pipefail
  docker compose run --rm --no-deps backend python -m app.recovery status > "backups/rollback-$TS.status.json" \
    || { echo "[중단] status 실패 — 출력 확인 (downgrade를 하지 않음)"; exit 1; }
  docker run --rm -i --entrypoint python moajwo-backend:latest -c "$STATUS_PY" < "backups/rollback-$TS.status.json" \
    || { echo "[중단] 담당이 있는 running이 남음 — 3단계부터 다시"; exit 1; }
  echo "[4 완료] 담당이 있는 running 없음 — check 행은 이유를 보고 하나씩 정한다: resolve <번호> --failed (일괄로 바꾸지 않음)"
)
```

check 행(이 판이 '결과 확인 필요'로 둔 실행)은 이전 판 화면에 상태 원문 `check`로 보이고 내려받을 수 없습니다. 실패로 정리할 실행만 번호를 골라 하나씩 바꿉니다 — 바꾸지 않아도 되돌리기는 이어집니다.

```bash
docker compose run --rm --no-deps backend python -m app.recovery resolve <실행 번호> --failed   # 그 실행이 check일 때만 바뀜
```

<!-- rollback-step:5 -->
```bash
# 5) 새 이미지(새 리비전을 아는 코드)로 DB를 반영 전 리비전으로 — one-off라 서버는 뜨지 않음. 실패하면 이전 이미지를 띄우지 않음
( set -euo pipefail
  docker compose run --rm --no-deps backend alembic downgrade "$PREV" \
    || { echo "[중단] downgrade 실패 — 이전 이미지로 바꾸지 않음. 리비전을 확인: 아래 '멈췄을 때' 5단계"; exit 1; }
  rev=$(dbrev) || { echo "[중단] downgrade 뒤 리비전을 읽지 못함 — 이전 이미지로 바꾸지 않음"; exit 1; }
  [ "$rev" = "$PREV" ] || { echo "[중단] downgrade 뒤 리비전이 $rev (기대 $PREV) — 이전 이미지로 바꾸지 않음"; exit 1; }
  echo "[5 완료] DB 리비전 $rev"
)
```

<!-- rollback-step:6 -->
```bash
# 6) 이전 backend·frontend 이미지와 이전 포트 연결을 함께 — 실제 컨테이너의 이미지·포트가 롤백 단위와 같은지 확인
( set -euo pipefail
  rev=$(dbrev) || { echo "[중단] DB 리비전을 읽지 못함 — 이전 이미지로 바꾸지 않음 (5단계 또는 A 확인)"; exit 1; }
  [ "$rev" = "$PREV" ] || { echo "[중단] DB 리비전이 $rev (기대 $PREV) — 5단계(또는 A)가 끝나지 않아 이전 이미지로 바꾸지 않음"; exit 1; }
  docker tag "moajwo-backend:$TAG" moajwo-backend:latest && docker tag "moajwo-frontend:$TAG" moajwo-frontend:latest \
    || { echo "[중단] 이전 이미지로 태그를 바꾸지 못함 — 서비스는 멈춘 그대로 ('멈췄을 때' 6단계)"; exit 1; }
  docker compose $RB up -d --no-build --wait \
    || { echo "[중단] 이전 판 기동 실패 — docker compose $RB ps·logs로 원인 확인 ('멈췄을 때' 6단계)"; exit 1; }
  want=$(awk '/^  [a-z]+:$/ {s = $1; sub(":", "", s)} /^      - "/ {p = $2; gsub("\"", "", p); print s, p}' "$RBF" | sort)
  got=$(for s in backend frontend; do
          docker inspect "moajwo-$s-1" --format '{{range $p, $b := .HostConfig.PortBindings}}{{range $b}}{{.HostIp}}:{{.HostPort}}:{{$p}} {{end}}{{end}}' \
            | tr ' ' '\n' | sed "/^$/d; s/^/$s /" || exit 1
        done | sort) || { echo "[중단] 실제 포트 연결을 조회하지 못함 — docker compose $RB ps 확인"; exit 1; }
  [ "$want" = "$got" ] || { echo "[중단] 실제 포트 연결($got)이 이전 포트 파일($want)과 다름"; exit 1; }
  for s in backend frontend; do
    [ "$(docker inspect "moajwo-$s-1" --format '{{.Image}}')" = "$(docker image inspect "moajwo-$s:$TAG" --format '{{.Id}}')" ] \
      || { echo "[중단] $s 컨테이너가 롤백 태그 이미지가 아님"; exit 1; }
  done
  echo "[6 완료] 이전 이미지($TAG) · 포트 $(echo $got)"
)
```

<!-- rollback-step:7 -->
```bash
# 7) 확인 — 리비전·health(frontend 프록시를 거쳐)·웹
( set -euo pipefail
  rev=$(docker compose $RB exec -T backend alembic current 2>/dev/null | tail -1) \
    || { echo "[중단] 리비전을 읽지 못함 (backend exec 실패) — docker compose $RB ps·logs 확인"; exit 1; }
  case "$rev" in "$PREV"*) ;; *) echo "[중단] 리비전 $rev (기대 $PREV)"; exit 1 ;; esac
  curl -sf http://127.0.0.1:8080/api/health | grep -q '"status":"ok"' || { echo "[중단] /api/health가 ok가 아님"; exit 1; }
  curl -sf -o /dev/null http://127.0.0.1:8080/ || { echo "[중단] 웹 화면이 열리지 않음"; exit 1; }
  echo "[7 완료] 자동 확인만 — 리비전 $rev · health ok · 웹 200"
  echo "사람이 확인한 뒤에 롤백 완료로 보고: 기존 계정 로그인 · 이전 결과 내려받기 · (있었다면) check 실행의 표시"
  echo "되돌린 상태에서는 모든 docker compose 명령에 '$RB'를 붙인다 (다시 반영은 scripts/deploy.sh)"
)
```

**멈췄을 때** (그 단계까지 바뀐 것과 할 일):

| 멈춘 곳 | 바뀐 것 | 할 일 |
| --- | --- | --- |
| 1단계, 정지 전 확인 (배포 단위·프로젝트·포트 파일·덧붙인 설정의 포트·DB 리비전) | 없음 — 서비스 그대로 | 이름은 반영 로그의 배포 단위 줄(또는 `[중단] … 4장 (…)` 줄)에 있음. 로그를 잘못 골랐으면 `ROLLBACK_LOG`를 입력하고 0단계부터. 포트 파일이 없는 예전 반영은 `git show <이전 main SHA>:docker-compose.yml`로 이전 설정을 꺼내 덧붙임 파일을 만든 뒤 (이미지만 되돌리지 않음) |
| 1단계 '이미 되돌렸거나 이미지를 바꾸지 않음' | 없음 — 서비스 그대로 | 되돌릴 것이 없음 — 다시 되돌리지 않음 (A도 하지 않음: 되돌린 뒤 생긴 자료가 빠짐). 다른 반영을 되돌리려던 것이면 `ROLLBACK_LOG`를 입력하고 0단계부터 |
| 1단계 '롤백 태그 이미지 없음' | 없음 — 서비스 그대로 | 아래 **R 상자**로 이전 커밋의 이미지를 다시 만들어 롤백 태그를 붙인 뒤 1단계부터. `scripts/deploy.sh`로 이전 커밋을 반영하지 않음 — 이전 코드는 새 리비전을 몰라 전환(기동 때 `alembic upgrade head`)에서 멈춤 |
| 1단계 'latest가 아님'·'backend 컨테이너를 찾지 못함'·'이 로그가 띄운 이미지가 아님' | 없음 | 나중 반영이 전환 준비(5f)에서 멈췄고 그 로그의 'latest 태그:' 줄이 '[주의] 되돌림 확인 안 됨'이면 `latest`가 검증한 후보를 가리킬 수 있음 (2026-10-28 이미지 검증 전의 deploy.sh는 빌드가 latest를 옮겨, 4·5단계에서 멈춘 반영도 그랬음) — 실행 중 이미지로 되돌린 뒤(`docker tag $(docker inspect moajwo-backend-1 --format '{{.Image}}') moajwo-backend:latest`, frontend도 같게) 0단계부터. 전환 실패 로그인데 backend 컨테이너가 롤백 태그 이미지이거나 없으면(전환이 컨테이너를 바꾸기 전에 멈춤) 2~5단계 없이 6·7단계 — 6단계가 DB 리비전이 반영 전인지 먼저 확인하고, 아니면 멈춤(그때는 이 표 밖 — 상태를 기록해 판단) |
| 1단계, 정지 뒤 확인 (실행 중 서비스·볼륨을 쓰는 컨테이너 — 조회 실패 포함) | 서비스만 멈춤 | 남은 컨테이너·조회 오류를 확인해 멈춘 뒤 1단계부터. 되돌리지 않으려면 `docker compose start backend frontend` (지금 판 재개) |
| 2단계 (백업·검증·정리 전 상태) | 서비스 멈춤. 검증을 통과한 백업에만 `.sha256` 파일이 생김 — `.sha256`이 없는 `moajwo-data-before-rollback-*.tgz`는 만들다 만 백업이라 쓰지 않음 | 원인(백업 폴더 권한·디스크)을 고친 뒤 2단계부터, 또는 지금 판 재개. 정리 전 `status`가 '리비전과 열이 맞지 않음'으로 멈추면(전환이 마이그레이션 도중 멈춘 경우 등) 지금 판도 뜨지 않을 수 있음 — A를 판단 |
| 3단계 종료 2 | 서비스 멈춤 (정리된 행이 있을 수 있음 — 출력) | `next_s`초 뒤 3단계만 다시. 계속 2면 같은 DB를 쓰는 다른 서버가 떠 있는지 확인 |
| 3단계 종료 1 | 서비스 멈춤 | 출력의 `errors`를 보고 3단계를 다시, 또는 지금 판 재개 |
| 4단계 | 서비스 멈춤 | 3단계부터 다시 |
| 5단계 | 리비전을 확인 (`dbrev` — 0단계에서 정의) | 리비전이 반영 후 그대로면: downgrade 거절(이 판에서 시작한 실행 중 행)은 3단계부터 다시, 그 밖의 실패(예: 남은 임시 표 `_alembic_tmp_*`로 시작하자마자 실패)는 출력을 보고 원인을 고친 뒤 5단계를 다시 — 어느 쪽이든 지금 판 재개(`docker compose start backend frontend`)도 됨. 리비전이 반영 후와 다르면(SQLite downgrade는 표를 다시 만드는 방식이라 실패하면 일부만 바뀔 수 있음) 지금 판을 재개하지 않고, 2단계 백업을 A 상자로 풀어(A 바로 전에 `RESTORE_BACKUP=moajwo-data-before-rollback-<시각>.tgz`) 반영 후 리비전으로 되돌린 뒤 3단계부터 다시. A 기본값(반영 직전 백업)은 반영 뒤 생긴 자료가 빠짐 |
| 6단계, 앞 확인(DB 리비전 ≠ 반영 전) | 없음 | 5단계(또는 A)를 끝낸 뒤 6단계 |
| 6단계, 태그 바꾸기·기동·확인 | 이미지 태그가 이전 판을 가리킴(태그 바꾸기에서 멈췄으면 하나만 바뀌었을 수 있음 — `docker image inspect moajwo-backend:latest moajwo-frontend:latest`를 롤백 태그와 비교), DB는 반영 전 리비전 | 태그를 마저 바꾸고 6단계부터, 또는 `docker compose $RB ps`·`logs`로 원인 확인. 새 판으로 돌아가려면 반영 로그 '전환 완료' 줄의 backend·frontend 이미지 ID로 `latest`를 다시 태그하고 `docker compose up -d --no-build --wait` (덧붙임 없이 — DB는 반영 전 리비전이라 새 backend가 기동 때 다시 올림). `scripts/deploy.sh`는 실행 중 컨테이너가 있어야 해서 이 상태에서는 쓰지 못함 |
| 7단계 | 이전 판이 떠 있음 | 실패한 확인 항목을 보고 (서비스는 그대로) |
| A, 서비스·볼륨 확인 (실행 중 서비스·볼륨을 쓰는 컨테이너 — 조회 실패 포함) | 없음 | B 0·1단계로 먼저 멈춘 뒤 A |
| A, 백업 확인 | 없음 | '이 반영의 백업이 아님'이면 `RESTORE_BACKUP`을 확인 (창에 남은 다른 반영의 값 등 — 반영 직전 백업이면 입력하지 않음). 그 밖에는 검증 기록(반영 직전 백업은 요약 `verified yes`·sha256, 2단계 백업은 `.sha256`)이 있는 백업으로 다시 |
| A, 옮긴 뒤 풀기 | 지금 자료는 볼륨의 `_pre_rollback_<시각>/`에 있음, `/data`는 비었거나 일부만 풀림 | 보존 폴더를 지우거나 A를 다시 돌리지 않음(보존 폴더가 있으면 A가 거절 — 덮지 않게). 원인을 고친 뒤 보존 폴더를 볼륨 안에서 다른 이름으로 옮기고 A를 다시, 또는 보존 폴더의 자료를 `/data`로 되돌려 놓고 지금 판 재개 |
| R | 없음 — 서비스·DB·`latest` 그대로 (빌드에서 멈췄으면 롤백 태그가 하나만 생겼을 수 있음) | 출력의 원인(커밋 없음 → `git fetch`, 꺼낼 폴더가 이미 있음 → 확인 뒤 옮김, 빌드 실패)을 고친 뒤 R을 다시. 하나만 생긴 태그는 R이 거절하지 않고 둘 다 다시 만듦 |
| P1 (B′ 적용), 기동 전 확인 (deploy/bprime 파일·DB 리비전·롤백 태그 이미지·설정 생성·`nginx -t`) | 없음 — 구판 서비스 그대로 (`backups/bprime-<시각>.conf*` 파일만 생겼을 수 있음) | 원인(0022 이후 판의 저장소가 아님 · B 6·7단계 미완 · 구판 설정에 `location /api/` 없음 · 설정 검사 실패)을 고친 뒤 P1을 다시 |
| P1, 기동·확인 (up 실패 · 가입 요청이 410이 아님 · status/health 실패 · backend 포트 남음) | frontend·backend 컨테이너가 B′ 설정으로 다시 만들어졌을 수 있음, DB·자료 그대로 | `docker compose $RB -f deploy/bprime/compose.bprime.yml ps`·`logs`로 원인 확인. 되돌리려면 P0 상자(덧붙임 없이 롤백 단위 그대로) |
| P0 (B′ 해제) | 컨테이너가 롤백 단위 설정으로 다시 만들어졌을 수 있음, DB·자료 그대로 | 포트·가입 응답이 기대와 다르면 `docker compose $RB ps`·`docker port moajwo-backend-1`로 확인한 뒤 P0을 다시 |

### R. 롤백 태그 이미지가 없을 때 — 이전 커밋으로 다시 만들기

1단계가 '롤백 태그 이미지 없음'으로 멈췄을 때만 씁니다 (정리 명령으로 태그가 지워진 경우 등). 이전 커밋을 다른 폴더에 꺼내 backend·frontend
이미지를 만들고 롤백 태그(`rollback-<시각>`)를 붙입니다 — 서비스·DB·`latest`는 바꾸지 않습니다. 이전 커밋은 반영 승인 문서의 '이전 main'
또는 앞 반영 로그의 배포 단위 줄 '대상'이며, R 상자 바로 전에 `REBUILD_FROM=<이전 main SHA>`를 입력합니다 (한 번만 쓰임). 다시 만든 이미지는
기반 이미지·패키지가 그사이 바뀌었으면 원래 이미지와 같지 않을 수 있습니다. 끝나면 1단계부터 합니다.

<!-- rollback-step:R -->
```bash
# R) 이전 커밋으로 이미지를 다시 만들어 롤백 태그를 붙인다 (서비스·DB·latest는 그대로) — 바로 전에 REBUILD_FROM=<이전 main SHA>
SRC=${REBUILD_FROM:-}; unset REBUILD_FROM   # 한 번만 쓰고 창에 남기지 않음
( set -euo pipefail
  [ -n "$TS" ] || { echo "[중단] 배포 단위를 읽지 못함 — 0단계 출력 확인 (아무것도 바꾸지 않음)"; exit 1; }
  [ -n "$SRC" ] || { echo "[중단] REBUILD_FROM=<이전 main SHA> 를 먼저 입력 (아무것도 바꾸지 않음)"; exit 1; }
  sha=$(git rev-parse --verify --quiet "$SRC^{commit}") || { echo "[중단] 커밋 $SRC 를 찾지 못함 — git fetch 뒤 다시 (아무것도 바꾸지 않음)"; exit 1; }
  if docker image inspect "moajwo-backend:$TAG" > /dev/null 2>&1 && docker image inspect "moajwo-frontend:$TAG" > /dev/null 2>&1; then
    echo "[중단] 롤백 태그 $TAG 이미지가 이미 있음 — 다시 만들지 않음 (1단계로)"; exit 1
  fi
  dir="../moajwo-rebuild-$TS"
  [ ! -e "$dir" ] || { echo "[중단] $dir 가 이미 있음 — 지우지 않고 멈춤 (확인 뒤 다른 곳으로 옮기고 다시)"; exit 1; }
  git worktree add --detach "$dir" "$sha" > /dev/null || { echo "[중단] 이전 커밋을 $dir 에 꺼내지 못함 (아무것도 바꾸지 않음)"; exit 1; }
  rc=0
  docker build -q -t "moajwo-backend:$TAG" "$dir/backend" && docker build -q -t "moajwo-frontend:$TAG" "$dir/frontend" || rc=$?
  git worktree remove "$dir" || echo "[주의] $dir 정리 실패 — 나중에 git worktree remove $dir"
  [ "$rc" -eq 0 ] || { echo "[중단] 이전 커밋 이미지 빌드 실패 — 롤백 태그가 하나만 생겼을 수 있음 (서비스·DB·latest는 그대로)"; exit 1; }
  got="$(docker image inspect "moajwo-backend:$TAG" --format '{{.Id}}' | cut -c8-19) $(docker image inspect "moajwo-frontend:$TAG" --format '{{.Id}}' | cut -c8-19)"
  orig=$(sed -n 's/^# 롤백 태그: .*:rollback-[0-9-]* \([0-9a-f]\{12\}\) · .*:rollback-[0-9-]* \([0-9a-f]\{12\}\)$/\1 \2/p' "$RBF" 2>/dev/null || true)
  if [ "$got" = "$orig" ]; then same="반영 전 이미지(이전 포트 파일의 기록)와 같음"
  else same="[주의] 반영 전 이미지(${orig:-기록 없음})와 다름 — REBUILD_FROM이 그 반영 전 커밋인지 확인 (기반 이미지·패키지가 바뀌어도 다를 수 있음)"; fi
  echo "[R 완료] 이전 커밋 ${sha:0:12}로 롤백 태그 $TAG 이미지를 만듦 · backend·frontend $got · $same — 다음: 1단계"
)
```

### A. 배포 전 백업으로 복원 (별도 승인)

B의 0·1단계(배포 단위 읽기·확인·정지)를 먼저 하고, 반영 직전 백업(`moajwo-data-$TS.tgz`)을 풉니다. 다른 백업(4장 B 2단계 백업 등)은 A 상자
바로 전에 `RESTORE_BACKUP=moajwo-data-before-rollback-<시각>.tgz`를 입력합니다 (한 번만 쓰임). A 상자는 쓸 백업 이름을 먼저 보여 주고,
서비스가 멈춰 있고 볼륨을 쓰는 컨테이너가 없을 때만 풉니다. 풀기 전에 검증 기록을 봅니다 —
반영 직전 백업은 반영 로그와 함께 남는 요약(`moajwo-data-$TS.txt`의 `verified yes`·`sha256`), 4장 B 2단계 백업은 `.sha256` 파일.

<!-- rollback-step:A -->
```bash
# A) 검증된 백업인지 확인 → 지금 자료를 지우지 않고 _pre_rollback_<시각>/ 으로 옮긴 뒤 백업을 푼다 (uid 1000 → 소유권 유지).
#    moajwo.db-journal(또는 WAL 모드라면 -wal·-shm)이 있으면 DB와 함께 옮겨지고 지워지지 않는다
BK=${RESTORE_BACKUP:-moajwo-data-$TS.tgz}; unset RESTORE_BACKUP   # 기본: 반영 직전 백업. 한 번만 쓰고 창에 남기지 않음
echo "풀 백업: backups/$BK"
( set -euo pipefail
  [ -n "$TS" ] && [ -n "$PREV" ] || { echo "[중단] 배포 단위를 읽지 못함 — 0단계 출력 확인 (아무것도 바꾸지 않음)"; exit 1; }
  left=$(docker compose ps --status running --services) \
    || { echo "[중단] 실행 중 서비스를 조회하지 못함 (아무것도 바꾸지 않음)"; exit 1; }
  [ -z "$left" ] || { echo "[중단] 실행 중인 서비스: $(echo $left) — B 0·1단계로 먼저 멈춤 (아무것도 바꾸지 않음)"; exit 1; }
  w=$(docker ps --filter volume=moajwo_moajwo-data --format '{{.Names}}') \
    || { echo "[중단] 데이터 볼륨을 쓰는 컨테이너를 조회하지 못함 (아무것도 바꾸지 않음)"; exit 1; }
  [ -z "$w" ] || { echo "[중단] 데이터 볼륨을 쓰는 컨테이너가 남음: $(echo $w) (아무것도 바꾸지 않음)"; exit 1; }
  bts=${BK#moajwo-data-before-rollback-}; bts=${bts%.tgz}   # 이 반영의 백업만: 반영 직전 백업, 또는 반영 뒤에 만든 2단계 백업
  [ "$BK" = "moajwo-data-$TS.tgz" ] || { [[ "$bts" =~ ^[0-9]{8}-[0-9]{6}$ ]] && [[ "$bts" > "$TS" ]]; } \
    || { echo "[중단] $BK 는 이 반영($TS)의 백업이 아님 (moajwo-data-$TS.tgz 또는 그 뒤의 moajwo-data-before-rollback-<시각>.tgz) — 풀지 않음 (아무것도 바꾸지 않음)"; exit 1; }
  sum="backups/${BK%.tgz}.txt"
  if [ -f "$sum" ]; then
    grep -qx "verified yes" "$sum" || { echo "[중단] $BK 는 검증되지 않은 백업 (요약에 verified yes 없음) — 풀지 않음 (아무것도 바꾸지 않음)"; exit 1; }
    want=$(sed -n 's/^sha256 //p' "$sum")
  else
    want=$(cut -d' ' -f1 "backups/$BK.sha256" 2>/dev/null || true)
  fi
  [ -n "$want" ] || { echo "[중단] $BK 의 검증 기록(sha256)이 없음 — 풀지 않음 (아무것도 바꾸지 않음)"; exit 1; }
  got=$(sha256sum "backups/$BK" | cut -d' ' -f1) || { echo "[중단] backups/$BK 를 읽지 못함 (아무것도 바꾸지 않음)"; exit 1; }
  [ "$got" = "$want" ] || { echo "[중단] $BK 의 sha256이 기록과 다름 — 풀지 않음 (아무것도 바꾸지 않음)"; exit 1; }
  docker run --rm --entrypoint sh -v moajwo_moajwo-data:/data -v "$HOSTDIR/backups:/backup:ro" "moajwo-backend:$TAG" -c \
    "set -e; gzip -t /backup/$BK; tar tzf /backup/$BK | grep -qx ./moajwo.db; d=/data/_pre_rollback_$TS; [ ! -e \$d ] || { echo '[중단] 보존 폴더가 이미 있음 — A를 다시 돌리지 않음 (옮겨 둔 자료를 덮지 않게)'; exit 1; }; mkdir \$d; for f in /data/* /data/.[!.]*; do [ -e \"\$f\" ] || continue; case \"\$f\" in /data/_pre_rollback_*) ;; *) mv \"\$f\" \$d/;; esac; done; tar xzf /backup/$BK -C /data; ls -ln /data" \
    || { echo "[중단] A 실패 — 출력 확인. 보존 폴더 _pre_rollback_$TS 를 지우거나 덮지 않음 ('멈췄을 때' A)"; exit 1; }
  if ! rev=$(dbrev); then next="리비전을 읽지 못함 — 6단계가 먼저 확인"
  elif [ "$rev" = "$PREV" ]; then next="B 6·7단계 (반영 전 리비전 $rev — 3~5단계 없음)"
  else next="B 3단계부터 (리비전 $rev — 반영 전 $PREV 가 아님: 2단계 백업 등 반영 후 백업)"; fi
  echo "[A 완료] $BK 를 풀었음 · 옮긴 자료는 볼륨의 _pre_rollback_$TS/ · 다음: $next"
)
```

그다음 `[A 완료]`가 알린 단계로 갑니다 — 반영 직전 백업은 반영 전 리비전이라 B 6·7단계(이전 이미지 + 이전 포트, 확인 — 3~5단계 없음), 2단계
백업처럼 반영 후 리비전인 백업은 B 3단계부터 (6단계가 DB 리비전을 먼저 확인하므로 순서를 건너뛰면 멈춤).

- **유실되는 것**: 백업 이후 반영된 버전에서 생긴 모든 변경(새 업로드 원본, 통합 결과, 매핑 확정·학습, 검토·실행 기록, 계정·설정·용어사전 변경). 지워지지 않고 볼륨의 `_pre_rollback_<시각>/`에 남으므로 필요하면 수동으로 옮겨옵니다.
- 각 downgrade는 그 리비전이 넣은 데이터만 지우고 사용자 별칭이 가리키는 기본 항목은 남깁니다 (0015는 `owner_setup` 표시 한 행만).

### B′. 구판으로 되돌린 동안 공개 가입 차단 — 덧붙임 설정만 (0022 이후 판의 롤백, B 7단계 뒤)

0022 이후 판에서 구판(0021 이하)으로 되돌리면 구판 앱은 공개 회원가입(`POST /auth/signup`)을 다시 받습니다(계정 정책 회귀, 12-4). 소유자 결정(2026-10-31, D2)에
따라 구판 복귀는 **B′** — B 0~7단계 뒤에 아래 **P1 상자**로 덧붙임 설정을 적용한 상태 — 로만 허용합니다. 구판 이미지는 고치지 않습니다.

- `deploy/bprime/compose.bprime.yml`: frontend에 **그 구판 이미지 자신의** nginx 설정에 차단 블록(`deploy/bprime/signup-410.location` — `location = /api/auth/signup`
  → 410, JSON `detail`)을 끼운 파일을 읽기 전용으로 덮어 씌우고, backend의 호스트 포트 공개(`127.0.0.1:8000`)를 없앱니다(`ports: !override []`) — 프록시를
  우회해 backend `/auth/signup`에 닿는 길을 막습니다. 컨테이너끼리의 `backend:8000`은 그대로라 프록시·health는 정상입니다.
- P1은 설정 파일을 그 이미지에서 꺼내 만들고(`backups/bprime-<시각>.conf`), 구판 이미지의 `nginx -t`로 검사한 뒤 올립니다. backend 컨테이너가 다시 만들어지므로
  수 초(격리 리허설 측정값은 RECOVERY_RUNBOOK.md) 중단됩니다. 세션은 DB에 있어 유지됩니다.
- 되돌린 동안 모든 `docker compose` 명령에 `$RB -f deploy/bprime/compose.bprime.yml`을 붙이고 `BPRIME_CONF=<호스트 절대 경로>/backups/bprime-<시각>.conf`를 함께 줍니다.
- B′ 상태에서 P0 없이 바로 `scripts/deploy.sh`로 복귀할 때, 2단계는 backend 호스트 포트가 **없음**인 것을 조회 실패와 구분해 빈 목록(`ports: !override []`)으로
  기록하고(그 반영의 롤백 단위는 B′의 포트 상태를 그대로 되돌림), 6단계 뒤 포트는 `docker-compose.yml`대로(8000·8080) 돌아옵니다 — 진단 CI 리허설 5절이 확인
  (2026-10-31 6차, 명령 대역 시험 사례 `B′상태_backend포트없음_진행`).
- 해제(**P0 상자**)는 덧붙임 없이 롤백 단위 그대로 다시 올립니다 — 되돌리기 쉬운 것이 조건입니다. 새 판으로 복귀할 때는 P0 없이 바로 `scripts/deploy.sh`
  (또는 '멈췄을 때' 6단계의 복귀 방법)로 갑니다.
- 검증: 진단 CI 리허설(`diagnostics/ci/image_gate_rehearsal.sh` 4절)이 이 두 상자를 문서에서 그대로 꺼내 실행하고, 운영 이미지 태그(구판)로 돌린 격리 기록은
  `backups/followup-20261030/round5/`.

<!-- rollback-step:P1 -->
```bash
# P1) B′ — 구판(반영 전 리비전)으로 되돌린 상태에서 공개 가입을 막는 덧붙임 설정 적용 (구판 이미지는 그대로). 해제는 P0 상자
( set -euo pipefail
  [ -n "$TS" ] && [ -n "$PREV" ] || { echo "[중단] 배포 단위를 읽지 못함 — 0단계 출력 확인 (아무것도 바꾸지 않음)"; exit 1; }
  [ -f deploy/bprime/compose.bprime.yml ] && [ -f deploy/bprime/signup-410.location ] \
    || { echo "[중단] deploy/bprime 파일이 없음 — B′는 이 파일이 있는 판(0022 이후 main)의 저장소에서만 (아무것도 바꾸지 않음)"; exit 1; }
  rev=$(dbrev) || { echo "[중단] DB 리비전을 읽지 못함 (아무것도 바꾸지 않음)"; exit 1; }
  [ "$rev" = "$PREV" ] || { echo "[중단] DB 리비전이 $rev (기대 $PREV) — B′는 B 7단계까지 끝난 구판 상태에서만 (아무것도 바꾸지 않음)"; exit 1; }
  for s in backend frontend; do
    [ "$(docker inspect "moajwo-$s-1" --format '{{.Image}}')" = "$(docker image inspect "moajwo-$s:$TAG" --format '{{.Id}}')" ] \
      || { echo "[중단] $s 컨테이너가 롤백 태그 $TAG 이미지가 아님 — B 6·7단계 확인 (아무것도 바꾸지 않음)"; exit 1; }
  done
  CONF="backups/bprime-$TS.conf"
  docker run --rm --entrypoint cat "moajwo-frontend:$TAG" /etc/nginx/conf.d/default.conf > "$CONF.orig" \
    || { echo "[중단] 구판 frontend 이미지의 nginx 설정을 읽지 못함 (아무것도 바꾸지 않음)"; exit 1; }
  awk -v blk="$(cat deploy/bprime/signup-410.location)" '!done && /^[[:space:]]*location \/api\/ \{/ { print blk; done = 1 } { print } END { exit done ? 0 : 1 }' "$CONF.orig" > "$CONF" \
    || { echo "[중단] 구판 nginx 설정에 'location /api/' 블록이 없어 차단 블록을 넣지 못함 — $CONF.orig 확인 (아무것도 바꾸지 않음)"; exit 1; }
  # 설정 검사는 단독 컨테이너라 compose 네트워크가 없다 — proxy_pass의 'backend' 이름 해석용으로만 주소를 준다(연결하지 않음)
  docker run --rm --add-host backend:127.0.0.1 -v "$HOSTDIR/$CONF:/etc/nginx/conf.d/default.conf:ro" --entrypoint nginx "moajwo-frontend:$TAG" -t > "$CONF.test" 2>&1 \
    || { cat "$CONF.test"; echo "[중단] 만든 nginx 설정이 구판 이미지의 검사(nginx -t)를 통과하지 못함 (아무것도 바꾸지 않음)"; exit 1; }
  BPRIME_CONF="$HOSTDIR/$CONF" docker compose $RB -f deploy/bprime/compose.bprime.yml up -d --no-build --wait \
    || { echo "[중단] B′ 적용 기동 실패 — docker compose $RB -f deploy/bprime/compose.bprime.yml ps·logs 확인. 해제는 P0 상자"; exit 1; }
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8080/api/auth/signup) || code=실패
  [ "$code" = 410 ] || { echo "[중단] 가입 요청이 410이 아님($code) — 차단 설정이 적용되지 않음. P0 상자로 해제하고 확인"; exit 1; }
  curl -sf http://127.0.0.1:8080/api/auth/status > /dev/null || { echo "[중단] /api/auth/status 실패 — P0 상자로 해제하고 확인"; exit 1; }
  curl -sf http://127.0.0.1:8080/api/health | grep -q '"status":"ok"' || { echo "[중단] /api/health가 ok가 아님 — P0 상자로 해제하고 확인"; exit 1; }
  direct=$(docker port moajwo-backend-1 | tr '\n' ' ')
  [ -z "$direct" ] || { echo "[중단] backend 호스트 포트가 아직 열려 있음($direct) — 프록시 우회 경로가 남음. P0 상자로 해제하고 확인"; exit 1; }
  echo "[P1 완료] 공개 가입 410(nginx, 구판 이미지 그대로) · backend 호스트 포트 없음 · /api/auth/status·health 정상 · 설정 $CONF"
  echo "되돌린 동안 모든 docker compose 명령에 '$RB -f deploy/bprime/compose.bprime.yml'을 붙이고 BPRIME_CONF=$HOSTDIR/$CONF 를 함께 준다. 해제는 P0 상자"
)
```

<!-- rollback-step:P0 -->
```bash
# P0) B′ 해제 — 덧붙임 설정 없이 롤백 단위 그대로(이전 이미지 + 이전 포트) 다시 올린다. DB·자료는 바꾸지 않음
( set -euo pipefail
  [ -n "$TS" ] && [ -n "$PREV" ] || { echo "[중단] 배포 단위를 읽지 못함 — 0단계 출력 확인 (아무것도 바꾸지 않음)"; exit 1; }
  rev=$(dbrev) || { echo "[중단] DB 리비전을 읽지 못함 (아무것도 바꾸지 않음)"; exit 1; }
  [ "$rev" = "$PREV" ] || { echo "[중단] DB 리비전이 $rev (기대 $PREV) — 이 상자는 구판 상태에서만 (아무것도 바꾸지 않음)"; exit 1; }
  docker compose $RB up -d --no-build --wait \
    || { echo "[중단] 해제 기동 실패 — docker compose $RB ps·logs 확인"; exit 1; }
  want=$(awk '/^  [a-z]+:$/ {s = $1; sub(":", "", s)} /^      - "/ {p = $2; gsub("\"", "", p); print s, p}' "$RBF" | sort)
  got=$(for s in backend frontend; do
          docker inspect "moajwo-$s-1" --format '{{range $p, $b := .HostConfig.PortBindings}}{{range $b}}{{.HostIp}}:{{.HostPort}}:{{$p}} {{end}}{{end}}' \
            | tr ' ' '\n' | sed "/^$/d; s/^/$s /" || exit 1
        done | sort) || { echo "[중단] 실제 포트 연결을 조회하지 못함 — docker compose $RB ps 확인"; exit 1; }
  [ "$want" = "$got" ] || { echo "[중단] 해제 뒤 포트 연결($got)이 이전 포트 파일($want)과 다름"; exit 1; }
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8080/api/auth/signup) || code=실패
  [ "$code" != 410 ] || { echo "[중단] 가입 요청이 아직 410 — frontend 컨테이너가 B′ 설정으로 남아 있음. docker compose $RB ps 확인"; exit 1; }
  curl -sf http://127.0.0.1:8080/api/health | grep -q '"status":"ok"' || { echo "[중단] /api/health가 ok가 아님"; exit 1; }
  echo "[P0 완료] B′ 해제 — 포트 $(echo $got) · 가입 요청 응답 $code(backend에 닿음 — 구판은 공개 가입이 열려 있음) · health ok"
)
```

## 5. 0016·0017 반영 (회원가입 승인·역할 단순화·인증 버전) — 추가 확인과 롤백 제한

반영 절차는 1~3장과 같습니다 (2026-10-14 반영 당시 명령 `bash scripts/deploy.sh <승인한 PR HEAD SHA>` — 지금 스크립트는 반영 전 리비전 인자도 받음, 배포 전 백업은 스크립트가 만듦). backend가 시작하면서
`0015_owner_setup_marker → 0016_account_status_roles → 0017_auth_version`을 적용합니다.
0017은 `users.auth_version`·`auth_sessions.auth_version`(기본 0)을 더합니다. 기존 사용자·세션이 모두 0이므로 반영 때 지금 로그인한
세션은 끊기지 않습니다 (0016이 예전 관리자 세션만 끝냄). 이후 비밀번호 초기화·변경·반려·이용 중지가 버전을 올립니다.

### 계정 전환 원칙 (0016 upgrade)

| 기존 계정 | 전환 후 |
| --- | --- |
| 소유자(`owner`, 활성) | 최고 관리자 · 이용 중 (그대로) |
| 활성 일반 사용자 | 일반 사용자 · 이용 중 (재승인 없음) |
| 관리자(`admin`) | 일반 사용자 (활성 여부 유지) · **기존 세션 종료** · 관리 이력에 `role_migrated` |
| 비활성 계정 (검증용 계정 포함) | 이용 중지 (다시 활성화하지 않음) |
| 소유자 여러 명 · 알 수 없는 역할 | 바꾸지 않음 · 관리 이력에 `migration_check`(반영 안 됨)로 남김 → 확인 후 처리안 결정 |

계정 ID, 파일 소유 관계, 실행·검토·검증 기록, 매핑 확정·학습 별칭·승인, 자동화는 바꾸지 않습니다.

### 반영 후 점검

**A. 조회만으로 확인 (운영 데이터·계정을 바꾸지 않음)**

1. `docker compose exec -T backend alembic current` → `0017_auth_version (head)`, `/health` 정상, 반영 후 로그에 오류 없음
2. rox로 로그인 → 새로고침 후 유지 → 사이드바 「관리자」 → 관리 현황·사용자·관리 이력이 열림 → 로그아웃
3. 사용자 목록의 상태가 위 전환 원칙과 같음 (2026-10-12 운영 기준 예상: rox 이용 중, 나머지 6개 계정 이용 중지,
   ft-admin·ops-admin-20261012는 일반 사용자, 관리 이력에 역할 전환 2건). 관리 현황 숫자가 DB 조회(읽기 전용)와 같음
4. 로그인 화면에 「회원가입」이 보이고, 최초 설정 화면은 열리지 않음
5. 기존 파일 목록·원본 다시 읽기(LLM 미사용)·기존 결과 내려받기 정상, 직접 URL·새로고침·화면 크기 확인

**B. 실제 가입·승인 흐름이 필요한 항목 (운영 검증용 계정 생성 — 별도 승인 후에만)**

- 가입 신청 → 승인 대기 로그인 안내 → 승인 → 로그인 → 업로드·통합 → 이용 중지(남은 쿠키 401) → 재개(이전 쿠키 401, 새 로그인)
  → 비밀번호 초기화(이전 쿠키·비밀번호 401) → 관리 이력 확인
- 대상(제안): 아이디 `ops-signup-<날짜>` 1개, 표시 이름 `[운영검증-<날짜>] 가입`, 업로드 자료는 이름에 `[운영검증-<날짜>]`를 붙인 합성 파일
- 정리 계획(제안): 시험 후 이용 중지(사유 '운영 검증 종료')로 남기고, 계정·파일·실행·관리 이력은 삭제 기능이 없어 보존.
  관리 현황·대시보드 숫자에 더해지는 몫(실행·업로드 건수)을 인수인계 문서에 기록
- 이 항목들은 승인 전까지 격리 환경 결과로만 갈음하며, 운영 보고에는 '미검증'으로 적습니다.

### 검증 결과 (격리 환경, 2026-10-12)

- 운영 배포 후 백업 복사본(`moajwo-data-postdeploy-20261012-154513.tgz` → 격리 볼륨 `moajwo-signup-copy`, 프로젝트 `moajwo-signup`, 127.0.0.1:8082)에서
  0016 적용: 리비전 0015 → 0016, 계정 7개 전환이 원칙과 일치(rox 이용 중 · 비활성 6개 이용 중지 · admin 2개 → user), 연결 데이터 건수
  (사용자별 파일·실행·검토·검증·자동화·학습 별칭·승인, 매핑 확정 770, 용어 190/360, 변경 기록 141,341, 검토 항목 4,250) 전후 동일.
- 빈 DB(`moajwo-signup-empty`, 127.0.0.1:8083): 최초 설정 화면 안내·버튼, 설정 전 가입 409, 최고 관리자 생성 후 회원가입 열림.
- 마이그레이션 테스트: 0014 → head(소유자 2명·알 수 없는 역할·관리자 세션·관리자 파일 포함) → 0015 downgrade(관리자 역할 복원).
  0016 → 0017: 기존 세션이 버전 0으로 그대로 유효, downgrade 시 두 열만 삭제.
- 같은 복사본에 0017까지 적용(0015 → 0016 → 0017): 계정 전환·연결 데이터·기존 세션 7개 그대로. 실제 쿠키로 초기화 후 기존 쿠키 401,
  이전 비밀번호 401, 중지 → 재개 후 이전 쿠키 401·새 로그인 200, 본인 변경 시 이 기기만 새 세션 유지.

### 롤백 제한

- 이전 이미지(0015까지 아는 코드)는 0016·0017 DB에서 `alembic upgrade head`가 실패해 backend가 뜨지 않습니다. 4장 A(배포 전 백업 복원) 또는
  B(**새** 이미지로 `alembic downgrade 0015_owner_setup_marker` — 0017 → 0016 → 0015 순서로 내려감 — 후 이전 이미지)로만 되돌립니다.
  둘 다 별도 승인 대상입니다.
- 0017 downgrade는 두 `auth_version` 열만 지웁니다. 그 뒤 세션은 이전 방식(활성 여부만 확인)으로 판정되므로, 반영 중 비밀번호
  초기화·이용 중지 때 이미 삭제된 세션 외에는 그대로 유효합니다 (경합 방어는 사라짐).
- **반영 후 가입·관리 데이터가 생긴 뒤**의 롤백:
  - A를 쓰면 반영 후 생긴 가입 신청·승인·이용 중지·비밀번호 초기화와 그 계정들의 업로드·실행 등 모든 변경이 활성 데이터에서 빠집니다
    (`_pre_rollback_<시각>/`에 보존).
  - B를 쓰면 `account_events`(관리 이력)와 `status`·사유 열이 삭제됩니다. downgrade 전에 백업을 만들어 이력을 보존하세요.
    반영 후 가입한 **승인 대기·반려 계정은 비활성 일반 사용자로 남고**, 이전 화면의 「다시 사용」으로 켜면 승인 절차 없이 이용할 수 있게
    되므로 롤백 후 운영자가 그 계정들을 확인해야 합니다. 이용 중지 계정은 비활성으로 남습니다. 예전 관리자는 `role_migrated` 이력으로
    `admin`이 복원됩니다 (반영 후 다시 바뀌지 않은 경우).
  - 롤백 전에 반영 후 생긴 계정 수·상태와 그 계정의 업로드·실행 수를 확인하고, 영향 범위를 알린 뒤 승인을 받습니다.

## 6. 0018 반영 (열 매핑 작업 화면) — 확인 항목과 롤백

반영 절차는 1~3장과 같습니다 (`bash scripts/deploy.sh <승인한 PR HEAD SHA> 0017_auth_version`). backend가 시작하면서 `0017_auth_version → 0018_mapping_analyses`를 적용합니다.

### 반영 전 필수 — 0017 상태 백업과 검증

- 보존 백업 현황 (인수인계 문서 11-3, 2026-10-14 기준): **0014 리비전 1개**(`moajwo-data-20261012-131832.tgz`, PR #1 반영 전), **0015 리비전 2개**(`moajwo-data-postdeploy-20261012-154513.tgz` — PR #1 반영 후 수동, `moajwo-data-20261014-150724.tgz` — PR #2 반영 전). **0017 적용 이후의 백업은 아직 없습니다** — 0016·0017 반영 뒤의 가입·승인·관리 이력, 계정 상태·인증 버전, 검증 계정 자료는 어떤 백업에도 없습니다.
- 0018 반영 때 `scripts/deploy.sh` 5단계가 만드는 배포 직전 백업이 **첫 0017 상태 백업**이 됩니다. 스크립트가 위 '5단계 백업 검증'을 모두 통과해야만 전환하므로, 운영자는 반영 로그에서 다음을 확인해 기록합니다.
  - `[운영 볼륨] 리비전 0017_auth_version` (정지 상태 운영 볼륨에서 읽은 값)
  - `[백업 DB] 무결성 ok · 리비전 0017_auth_version` (백업에서 꺼낸 DB에서 읽은 값)
  - 핵심 테이블 5개(`users`·`account_events`·`auth_sessions`·`mapping_decisions`·`source_files`)를 포함한 모든 테이블의 `[일치]`와 `비교 결과: 리비전 같음 (0017_auth_version) · 테이블 N개 행 수 모두 같음`
  - 요약 `backups/moajwo-data-<시각>.txt`의 `verified yes`·`sha256`, 백업 파일이 `backups/`에 있고 Git 추적 대상이 아님
- PR #4 작업(보완 포함)에서는 운영을 멈추거나 운영 백업을 만들거나 복원하지 않았습니다. 위 확인은 반영 승인 뒤 실제 반영 때 합니다.
- **중단 시간**: 5단계 검증(운영 볼륨 행 수, 백업 DB 추출·무결성·행 수, sha256 재계산)이 늘어 이전 반영(0015 → 0017, 2026-10-14 운영 27초)보다 길어질 수 있습니다. 이전의 '약 30초'는 확정값이 아닙니다. 격리 측정값은 아래 '격리 검증 기록'에 자료 크기·환경과 함께 적었고, **실제 운영 중단 시간은 반영 때 로그의 `중단 N초`(정지 → 전환 확인)로 따로 측정해 기록**합니다.

### 반영 내용과 점검

- 0018은 `mapping_analyses` 테이블만 새로 만듭니다 (파일·사용자마다 마지막 분석 결과 1건, 마스킹한 JSON). 계정·세션·관리 이력·매핑 확정(`mapping_decisions`)·학습 별칭·승격 근거는 바꾸지 않습니다.
- 반영 직후에는 분석 기록이 없습니다. 작업 화면은 기존 파일의 **저장된 확정값을 '사용자 확정'으로, 나머지 열을 '미분석'으로** 보여 주며 자동으로 분석하지 않습니다 (LLM 호출 없음). 「여러 파일 공통 검토」는 분석 기록이 없는 파일을 '제외'로 안내합니다.
- 반영 후 점검 (조회만): `alembic current` → `0018_mapping_analyses (head)`, 로그에 오류 추적 없음, 사이드바 「열 매핑」에서 기존 파일 미리보기(파일·시트 고르기·쪽 넘김)와 이미 확정한 열의 '사용자 확정' 표시, 화면을 연 뒤에도 `mapping_analyses` 0행·`mapping_decisions`·`learned_aliases` 행 수 그대로. 분석 실행·확정·일괄 확정은 운영 데이터를 바꾸므로 별도 승인 후에만 시험합니다.
- 메모리: 미리보기 보기 캐시는 최근 2개 파일, 파일당 300만 칸 이하만 보관합니다 (예: 10만 행 × 11열 CSV ≈ 110만 칸). 300만 칸을 넘는 파일은 쪽마다 다시 파싱합니다 (README '열 매핑 작업 화면').

### 롤백 B — 데이터를 지키며 0017로 되돌리기 (기본, 별도 승인 후 수동)

이전 이미지(0017까지 아는 코드)는 0018 DB에서 `alembic upgrade head`가 실패해 뜨지 않습니다. 그래서 **새 이미지로 downgrade한 뒤** 이전 이미지로 바꿉니다. `$TAG`·`$OLD_B`는 0018 반영 로그의 배포 단위 줄 값입니다.

```bash
# 1) 서비스 쓰기 중지 — DB를 쓰는 프로세스가 없어야 한다
docker compose stop -t 30 frontend backend
docker compose ps --status running --services                 # 출력 없어야 함
docker ps -q --filter volume=moajwo_moajwo-data               # 출력 없어야 함 (볼륨을 쓰는 다른 컨테이너 없음)

# 2) 롤백 직전 백업 (0018 상태) — 1·2장 '5단계 백업 검증'과 같은 방식: 정지 상태 볼륨의 리비전·행 수 기록 → 백업 →
#    백업에서 꺼낸 DB의 무결성·리비전(0018)·행 수 확인 → 두 값 비교 → sha256 재확인. 하나라도 다르면 진행하지 않는다

# 3) 새 이미지(지금 latest = 0018 코드)로 downgrade만 실행 — one-off 컨테이너라 서버는 뜨지 않는다
docker compose run --rm --no-deps backend alembic downgrade 0017_auth_version
#    로그: Running downgrade 0018_mapping_analyses -> 0017_auth_version
docker compose run --rm --no-deps backend alembic current     # 0017_auth_version

# 4) 이전 이미지로 전환
docker tag moajwo-backend:$TAG moajwo-backend:latest && docker tag moajwo-frontend:$TAG moajwo-frontend:latest
docker compose up -d --no-build --wait
docker compose exec -T backend alembic current                # 0017_auth_version (head)
docker inspect moajwo-backend-1 --format '{{.Image}}'          # = $OLD_B
```

**보존 범위**

| 구분 | 내용 |
| --- | --- |
| 그대로 남음 | 계정·상태·인증 버전·관리 이력, 로그인 세션(유효한 세션은 계속 유효, 무효 세션은 계속 401), 업로드 원본·통합 결과·실행·검토·검증 기록, 매핑 확정 — **0018에서 작업 화면·일괄 확정으로 저장한 확정 포함**, 학습 별칭, 승격 근거, 설정·용어사전 |
| 지워짐 | `mapping_analyses` (분석 기록) — 다시 분석하면 만들어지는 자료 |
| 화면에서 사라짐 | 열 매핑 작업 화면·공통 검토·일괄 확정 (0018 코드 기능). 이전 화면(업로드 카드의 열 매핑)은 남은 확정값을 그대로 씀 |

**확인** (조회만): `/api/health` 200, rox 로그인·새로고침 유지, 기존 사용자 세션 유지, 업로드 카드의 「열 매핑 분석」 결과에 0018에서 확정한 열이 '확정'으로 나옴, 파일 목록·기존 결과 내려받기, 관리 현황 숫자가 롤백 전 조회와 같음, 로그에 오류 추적 없음.
나중에 0018을 다시 반영하면 `mapping_analyses`가 빈 상태로 다시 만들어지고, 파일은 다시 분석할 때까지 '미분석'(확정값은 표시)으로 보입니다.

### 롤백 A — 반영 직전 백업(0017 상태) 복원

4장 A와 같습니다. 반영 뒤 생긴 **모든** 변경(0018에서 저장한 확정·학습, 가입·승인·관리 이력, 업로드·실행 등)이 활성 데이터에서 빠지고 `_pre_rollback_<시각>/`에 남습니다. 빠질 범위를 알리고 별도 승인 후에만 씁니다. 쓸 수 있는 백업은 요약에 `verified yes`가 있는 0018 반영 직전 백업뿐입니다. 그런 백업이 없으면 A는 쓸 수 없습니다 (0015 백업으로 복원하면 0016·0017 이후 계정·관리 이력까지 사라짐).

### 격리 검증 기록

- **이전 시험 (PR #4 초안, 2026-10-14)**: 백엔드 단위 마이그레이션 시험 1개와, 0015 백업 복사본에 PR 이미지가 0015 → 0016 → 0017 → 0018을 연속 적용한 화면 검증(49개) — 0016·0017 시기의 승인 대기·이용 중지·초기화·세션 데이터를 만들지 않은 상태라 0017 운영 상태에서의 전환 검증은 아님.
- **이번 시험 (PR #4 보완, 2026-10-14, 격리 프로젝트 `moajwo-signup`, 127.0.0.1:8082)**: 0015 복사본을 **운영과 같은 이미지**(backend `30e3602b78c4`·frontend `072c9d015e10`, main `b52e18e` — 별칭 태그로만 사용, 운영 태그·컨테이너 변경 없음)로 0017까지 올린 뒤 승인 대기 1·이용 중지 1·비밀번호 초기화 1(인증 버전 1)·유효 세션·초기화로 무효가 된 세션·관리 이력 11건·매핑 확정 771행을 만들어 시작.
  - 0017 → 0018 (PR 이미지): 기존 18개 테이블의 행 수·내용 해시 그대로, `mapping_analyses` 0행, 유효 세션 200·무효 세션 401·승인 대기/이용 중지 로그인 403 그대로, 분석 없는 파일은 확정값 표시·나머지 미분석(운영 복사본 파일 #9: 사용자 확정 35개), 화면을 열어도 기록 변화 없음.
  - 0018에서 만든 기록 구분: 분석 기록은 `mapping_analyses`(0018 전용 테이블)에만, 확정은 `decided_at` ≥ 전환 시각인 행으로 구분.
  - 롤백 B (새 이미지 one-off downgrade, 서비스 정지 상태): 0017, `mapping_analyses`만 삭제, 나머지 18개 테이블은 0018 사용 후 상태 그대로(0018에서 만든 확정 유지 → 772행).
  - 이전 이미지 기동: 0017 (head), 유효 세션 200·무효 세션 401, 이전 열 매핑 응답에 0017·0018 확정값 그대로, 업로드·목록 정상, 승인 대기 로그인 거부.
  - 다시 0018: 충돌 없음, 기존 데이터 그대로, 분석 기록 0행, 확정값 표시. 23개 확인 모두 통과.
- **배포 스크립트 백업 검증 (2026-10-14, 스크립트 커밋 `2d28225`)**: 아래 리허설 결과 '4차' 참고. 중단 시간 격리 측정 (Windows 11, Docker Desktop(WSL2)):
  - 합성 자료(DB 0.3MiB, 업로드 파일 24.4MiB, 백업 5.0MiB)로 실제 `deploy.sh` 정상 전환: **중단 26초**(정지 → 전환 확인), 그중 5단계 백업·검증 8초.
  - 운영 복사본 크기(데이터 192.1MiB, DB 46.5MiB, 테이블 20개, 백업 58.1MiB)에서 5단계 명령만 일회용 컨테이너 안에서 잰 값: 백업 생성·압축 검사·sha256 5.96초, 운영 볼륨 행 수 0.09초, 백업 DB 추출·무결성·행 수 0.84초, sha256 재계산 0.03초 (이전 방식에 있던 운영 볼륨 무결성 검사 1.16초는 빠짐). 명령 자체의 시간은 거의 늘지 않고, 검증용 일회용 컨테이너 2개를 띄우는 시간(합성 자료 실측 각 2~3초)이 더해집니다.
  - 측정용 백업은 컨테이너 내부 `/tmp`에 썼으므로, 백업을 Windows 폴더에 쓰는 실제 반영은 입출력 시간이 더 걸릴 수 있습니다. 운영 중단 시간은 실제 반영 때 따로 측정합니다.

## 7. 0019 반영 (데이터 분석 1차) — 확인 항목과 롤백

반영 절차는 1~3장과 같습니다 (`bash scripts/deploy.sh <승인한 PR HEAD SHA> 0018_mapping_analyses`). backend·frontend가 함께 바뀌고, backend가 시작하면서 `0018_mapping_analyses → 0019_analysis_runs`를 적용합니다.

- 0019는 `analysis_runs` 테이블만 새로 만듭니다. 기존 테이블(계정·세션·매핑 확정·학습 별칭·분석 기록·업로드·통합 실행)은 바꾸지 않습니다. 반영 직후 분석 결과는 0행이며, 화면을 열어도 아무것도 만들지 않습니다.
- 반영 전 백업: 2026-10-14 22:45 백업(`moajwo-data-20261014-224455.tgz`)은 **0017 상태**라 0018 반영 뒤의 자료가 없습니다. 0019 반영 때 스크립트 5단계가 만드는 백업이 첫 0018 상태 백업이 되며, '5단계 백업 검증'(리비전 `0018_mapping_analyses`)을 통과해야만 전환합니다.
- 반영 후 점검 (조회만): `alembic current` → `0019_analysis_runs (head)`, 사이드바 「데이터 분석」 직접 접근·새로고침, 항목·파일 목록이 열리고 `analysis_runs`·`mapping_decisions`·`learned_aliases`·`mapping_analyses` 행 수가 화면을 연 뒤에도 그대로. 대상 확인·실행은 운영 자료로 분석 결과를 만들므로 별도 승인 후에만 시험합니다.
- 중단 시간은 실제 반영 때 로그의 `중단 N초`와 웹 접속 측정으로 따로 잽니다.

**롤백 B (데이터 보존)**: 6장 롤백 B와 같은 순서 — 쓰기 중지 → 롤백 직전 백업·검증 → **새 이미지로** `docker compose run --rm --no-deps backend alembic downgrade 0018_mapping_analyses` → 이전 이미지로 전환 → 확인.

| 구분 | 내용 |
| --- | --- |
| 지워짐 | `analysis_runs` (분석 결과·조건) — 같은 조건으로 다시 실행하면 다시 만들 수 있음 (그 사이 매핑이 바뀌었으면 결과가 다를 수 있음) |
| 그대로 남음 | 계정·세션·관리 이력, 업로드 원본·통합 결과·실행 기록, 매핑 확정·학습 별칭·승격 근거, 열 매핑 분석 기록 |
| 화면에서 사라짐 | 「데이터 분석」 화면과 분석 진입 버튼 (0019 코드 기능) |

격리 검증 (2026-10-15, 합성 자료 `moajwo-feat` 볼륨): 0018 → 0019 기존 테이블 그대로 · 분석 결과가 있는 0019 → 새 이미지 one-off downgrade 0018: `analysis_runs`만 삭제, 나머지 19개 테이블 행 수·내용 해시 그대로 · 이전(0018) 코드 기동·로그인·확정값 정상, 분석 API 없음 · 다시 0019: 빈 `analysis_runs`, 다시 실행 가능.

## 8. 통합 후보 반영 (리비전 0019 → 0020 · frontend 컨테이너 포트 80 → 8080)

PR #22~#41을 담은 통합 후보(`rc/review-20261023`, 통합 PR)를 반영할 때의 절차입니다. **DB 마이그레이션이 하나 있습니다**: `0019_analysis_runs → 0020_run_worker_lease` (`runs`에 빈 값을 허용하는 열 두 개 — 9장). 2026-10-23 후보까지는 마이그레이션이 없었고, #41의 2026-10-25 보완이 0020을 더했습니다.

| 구분 | 반영 전 (main) | 반영 후 (후보) |
| --- | --- | --- |
| backend 이미지 | main 코드 | 후보 코드 (외부 DB 모드는 기본 꺼짐 — 로컬은 그대로 SQLite) |
| frontend 이미지 | nginx 1.29, root로 시작, **컨테이너 80** | nginx 1.30, 비루트(uid 101), **컨테이너 8080** |
| Compose 포트 | `127.0.0.1:8080:80` | `127.0.0.1:8080:8080` (호스트 주소·포트는 같음) |
| 데이터 볼륨 | `moajwo_moajwo-data` | 같음 (새 볼륨을 만들지 않음 — 프로젝트 이름 `moajwo`·서비스 이름 그대로) |
| DB 리비전 | `0019_analysis_runs` | `0020_run_worker_lease` (기동 때 자동 upgrade — 9장) |

**반영**: 모든 PR을 순서대로 병합한 main이 후보와 같은 트리인지 확인한 뒤(`git diff <후보 SHA> main` 비어 있음)

```bash
bash scripts/deploy.sh <후보 HEAD SHA> 0019_analysis_runs
```

스크립트가 이전 포트 연결(`127.0.0.1:8080:80/tcp`)을 `backups/rollback-<시각>.compose.yml`에 남기고, 전환 뒤 `포트 연결: frontend 127.0.0.1:8080:80/tcp → 127.0.0.1:8080:8080/tcp`를 기록합니다. 반영 전 리비전 인자는 지금 운영 리비전 `0019_analysis_runs`입니다 (반영 뒤 `0020_run_worker_lease (head)`).

**롤백 (이전 이미지 + 이전 포트 + 0020 → 0019 downgrade)** — 4장 B를 0~7단계 그대로 합니다 (이 반영은 리비전을 바꾸므로 3~5단계 포함). 이 반영에서 단계마다 기대하는 값: 0단계 '리비전 0019_analysis_runs → 0020_run_worker_lease', 1·6단계 포트 `backend 127.0.0.1:8000:8000/tcp`·`frontend 127.0.0.1:8080:80/tcp`(frontend 대상 80 하나 — 8080 대상이 남지 않음), 3단계 recover 종료 0, 5단계 'DB 리비전 0019_analysis_runs', 7단계 health ok. 이전 이미지는 0020을 모르므로(기동 때 `alembic upgrade head`가 멈춤) 새 이미지로 DB를 먼저 0019로 내립니다. 반영 뒤 생긴 자료(업로드·실행 등)는 그대로 남고 이전 코드가 읽습니다 — 지워지는 것은 0020이 더한 두 열뿐이고, '결과 확인 필요' 행은 이전 판 화면에 상태 원문으로 보입니다 (9장 표).

롤백 뒤 다시 후보로: 같은 `bash scripts/deploy.sh <후보 HEAD SHA> 0019_analysis_runs` (덧붙임 파일 없이 `docker-compose.yml`만 씀 → 8080:8080으로 다시 만들어지고 0020으로 다시 upgrade).

**보안 수정 전 이미지로 되돌릴 때의 위험**: 2026-10 인증서 사고 보완(`docs/SECURITY-CA-20261026.md` — pip·CA 묶음 갱신, Render 앞단의 상류 인증서 검증)이 든 이미지에서 롤백 태그(그 전 이미지)로 되돌리면 그 보완이 없는 상태로 돌아갑니다. 로컬 운영은 아웃바운드 TLS가 없어 운영 중 영향은 없지만, 되돌린 뒤 다시 빌드할 때는 수정판 pip·CA로 빌드되는 보완 포함 커밋을 쓰고, Render에는 보완 전 이미지를 다시 배포하지 않습니다.

**frontend 단독 반영 경로**(`docker compose up -d --no-deps frontend`)는 이번 반영에 쓰지 않습니다 — backend도 바뀌고, 포트 설정과 이미지가 함께 바뀌어야 하기 때문입니다. 이 경로(`docker compose build frontend` → `up -d --no-deps frontend`)는 빌드가 latest 태그를 바로 옮기고 11장의 완성 이미지 대조·격리 점검을 거치지 않으므로, 2026-10-28 이미지 검증(11장)을 넣은 뒤로는 **frontend만 바뀌어도 쓰지 않습니다** — `scripts/deploy.sh`로 반영합니다 (backend도 같은 검증을 거쳐 다시 만들어지고 리비전은 그대로). 이 반영 이전 frontend 이미지로 되돌릴 때는 이번 반영의 `rollback-<시각>.compose.yml`이 필요합니다.

**격리 확인**: 지금 절차(4장 B 단계 상자 그대로)는 아래 '리허설 결과' 7차. 5차(포트, 0019 그대로)·6차(통합 후보 — 0020 downgrade 포함)는 이 정리 전 8장 명령의 기록입니다. Windows Docker Desktop에서는 `ports: !override` 덧붙임과 `pwd -W` 경로 백업 마운트를 명령 수준으로만 확인했습니다 (6차 아래).

## 9. 0020 반영 (결과 실행의 담당 프로세스·생존 신호) — 확인 항목과 롤백

반영 절차는 1~3장과 같습니다 (`bash scripts/deploy.sh <승인한 PR HEAD SHA> 0019_analysis_runs`). backend가 시작하면서 `0019_analysis_runs → 0020_run_worker_lease`를 적용합니다.

- 0020은 `runs`에 빈 값을 허용하는 열 두 개만 더합니다: `worker`(그 실행을 만드는 서버 프로세스 — `<결과 폴더 식별자>-<프로세스 식별자>`), `heartbeat_at`(마지막 생존 신호, UTC). 기존 행은 두 열이 비어 있고 다른 표·값은 바꾸지 않습니다 (SQLite는 `ALTER TABLE ADD COLUMN` 두 번). PostgreSQL에서는 표 잠금을 5초까지만 기다리고(`lock_timeout`), 넘으면 마이그레이션이 실패해 서버가 뜨지 않습니다 — 트랜잭션 DDL이라 바뀐 것이 없으니 잠금을 쥔 연결을 정리한 뒤 다시 반영합니다.
- **멈춘 실행 정리가 바뀝니다** (`backend/app/recovery.py`): 시작 시각만으로 '실패'로 바꾸지 않습니다. 같은 결과 폴더를 쓰는 프로세스가 담당 프로세스의 운영체제 잠금(`outputs/.workers/<프로세스>.lock`)으로 끝났음을 확인했을 때만 파일을 보고 정합니다 — 저장 전 → 실패(그 실행의 임시 파일만 지움), 기록한 해시와 같은 최종·임시 파일 → 완료, 임시 파일 해시 다름 → 실패(그 임시 파일 삭제), 해시를 기록한 뒤 파일 없음 → 실패, 최종 파일 해시 다름·완료 기록 전인데 최종 파일 있음 → '결과 확인 필요'(지우지 않음). 기동 때 한 번과, 정할 근거가 생길 때만 도는 일회성 타이머로 돕니다.
- **새 실행 상태 '결과 확인 필요'(`check`)**: 담당이 끝났는지 이 서버가 확인할 수 없을 때(다른 디스크 — 생존 신호 10분 끊김, 이전 판 실행 — 기동 30분 뒤) 또는 최종 파일이 기록과 다를 때입니다. 실패로 바꾸지 않고 결과 파일을 지우지 않습니다. 화면에는 '결과 확인 필요 — 이유'로 보이고 내려받기는 막힙니다 (완료가 아님).
- **반영 전에 남아 있던 '실행 중' 행**(이전 판이 멈춘 실행 — 담당이 비어 있음): 새 판은 기동 30분 뒤(무중단 배포의 이전 서버가 아직 돌 수 있는 시간) '결과 확인 필요'로 표시합니다. 이전 판 임시 파일(`tmp*.xlsx.tmp`)·기록과 연결되지 않은 결과 파일은 자동으로 지우지 않습니다.
- 결과 만들기는 한 번에 하나입니다 (`MOAJWO_OUTPUT_SLOTS`, 기본 1 — `docs/MEMORY.md`). 다른 결과를 만드는 동안 온 요청은 기다리지 않고 503으로 거절되며 실행 기록·파일을 남기지 않습니다.
- 반영 후 점검 (실행 기록은 바꾸지 않음): `alembic current` → `0020_run_worker_lease (head)`, 아래 `status`로 실행 중·결과 확인 필요 건수, 실행 기록 화면이 열리고 기존 완료 실행의 결과를 내려받을 수 있음.

### 결과 확인 필요 실행 정리 — 별도 절차 (관리자)

```bash
docker compose exec -T backend python -m app.recovery status
docker compose exec -T backend python -m app.recovery resolve <실행 번호> --failed
```

- `status`: 실행 중·결과 확인 필요 실행(담당 상태 `me`·`alive`·`dead`·`unknown`·이전 판, 마지막 신호 뒤 지난 초, 결과 파일 이름, 확인 필요 이유)과, 기록과 연결되지 않은 결과 파일·이전 판 임시 파일의 이름을 보여 줍니다. 실행 기록·결과 파일은 바꾸지 않습니다 (담당 확인을 위해 그 명령 프로세스의 잠금 파일 `outputs/.workers/<프로세스>.lock` 하나와, 없으면 `outputs/.disk-id`가 생깁니다 — 잠금 파일은 다음 정리 때 지워짐).
- `resolve … --failed`: '결과 확인 필요'인 실행만 실패로 바꿉니다 (그 상태일 때만 — 그 사이 바뀌었으면 바꾸지 않음, 종료 1). 남은 파일은 지우지 않고 경로만 알립니다. 지울지는 관리자가 파일을 확인한 뒤 따로 정합니다.
- `recover`: 위 정리 판정을 한 번 (기동 정리와 같음). 서버를 멈춘 뒤 롤백 전에 씁니다 (아래). 살아 있는 담당은 그대로, 근거 없는 실행(다른 디스크·잠금 파일 없음)은 생존 신호가 10분 넘게 끊겼으면 '결과 확인 필요'로 바꾸고(파일은 건드리지 않음), 이전 판 실행은 그대로 둡니다 (막 뜬 명령 프로세스라 기동 30분 조건에 닿지 않음). 출력의 `blocking`은 정리 뒤에도 남은 담당 있는 실행 중 행 수, `legacy`는 이전 판 행 수입니다. 종료 **0** = `blocking` 0 (downgrade 가능), **2** = 남음 (살아 있는 담당이거나 근거를 기다리는 다른 디스크 실행 — `next_s`초 뒤 다시), **1** = 오류 (다시).
- 오래됐다는 이유만으로 `outputs/`의 파일을 지우지 않습니다.
- **세 명령 모두 먼저 이 DB가 어느 판인지 맞춰 봅니다** (읽기만 — `alembic_version`의 모든 행과 `runs` 표의 담당·생존 신호 열):

  | DB | `status` | `recover`·`resolve` |
  | --- | --- | --- |
  | `0020_run_worker_lease` + 두 열 | 위 동작 (`revision` 함께 표시) | 위 동작 |
  | `0019_analysis_runs` + 두 열 없음 (반영 전 운영 DB 등) | 실행 기록의 상태별 건수만 (종료 0) | 아무것도 바꾸지 않고 종료 1 |
  | 그 밖 — 리비전 없음·빈 값·여러 개·다루지 않는 리비전, 리비전과 열이 맞지 않음(전부·일부), `runs` 표 없음 | 진단만, 종료 1 | 진단만, 종료 1 |
  | SQLite DB 파일 없음 | 진단만, 종료 1 (파일을 만들지 않음) | 같음 |

  진단에는 리비전·열 이름과 오류 종류만 나옵니다 (주소·경로·SQL·값 없음). 그 밖의 경우는 마이그레이션이 끝나지 않았거나 다른 판·손으로 바뀐 DB일 수 있으니 백업한 뒤 `alembic current`와 `runs` 표 구조(`PRAGMA table_info(runs)` 등 읽기 전용)를 확인합니다. DB 파일 없음은 데이터 폴더(`MOAJWO_DATA_DIR`)나 one-off에 붙인 볼륨을 잘못 고른 경우입니다 (`DATABASE_URL`이 `sqlite:///file:<경로>?mode=ro&uri=true` 꼴이면 그 경로의 파일로 봅니다). 확인을 통과한 뒤의 실패(다른 연결의 쓰기 잠금 등 — 어떤 예외든)도 종류만 알리고 종료 1 — `status`로 상태를 다시 봅니다. 결과 폴더 식별자(`outputs/.disk-id`)가 깨져 있으면 그 원인 문구를 알립니다 (경로·값 없음).
  이 표와 문장은 통합 후보의 `backend/app/recovery.py`(#41 `fix/output-memory-budget`) 기준입니다 — 이 문서만 따로 main에 들어가면 맞지 않으므로 통합 PR(#42)로 함께 병합합니다.

### 롤백 B — 데이터를 지키며 0019로 되돌리기 (별도 승인 후 수동)

4장 B를 0~7단계 그대로 합니다. 0020에 해당하는 단계: 2단계 정리 전 백업(볼륨 전체 — 담당 잠금 파일·결과 포함)·검증(DB 무결성·리비전·표별 행 수, 컨테이너·호스트 sha256) → 3단계 `recover`(서버를 띄우지 않는 one-off, 종료 0일 때만 진행 — 2면 `next_s`초 뒤 다시) → 4단계 `status`(담당 있는 running 0이어야 함, check는 하나씩 `resolve … --failed` 여부를 정함 — 일괄로 바꾸지 않음) → 5단계 새 이미지로 `alembic downgrade 0019_analysis_runs` → 6단계 이전 이미지 + 이전 포트.

| 구분 | 내용 |
| --- | --- |
| 지워짐 | `runs.worker`·`runs.heartbeat_at` 두 열 (담당·생존 신호) |
| 그대로 남음 | 실행 기록·변경 내역·결과 파일, 계정·세션·업로드·매핑·분석 등 다른 모든 표 |
| 남지만 이전 판이 모르는 것 | '결과 확인 필요'(`check`) 행 — 이전 판 화면에는 상태 원문이 보이고 내려받을 수 없음. 되돌리기 전에 `resolve … --failed`로 정리할지 정합니다 |
| downgrade가 멈추는 경우 | 이 판에서 시작한 실행 중 행(담당 있음)이 있을 때 — "이 판에서 시작한 실행 중인 결과 실행이 N건 있어 0019로 내리지 않습니다". 4장 B 3단계(recover)부터 다시 합니다. 이 판 전에 시작한 실행 중 행(담당 없음)은 이전 판이 남긴 그대로라 막지 않습니다 |

- 이전 이미지는 기동 때 `alembic upgrade head`가 모르는 리비전(0020)에서 멈추므로 DB를 먼저 0019로 내립니다 (자동 downgrade는 하지 않음).
- 롤백 A(반영 직전 백업 복원)는 4장 A입니다 — 반영 뒤 생긴 실행·결과는 활성 데이터에서 빠집니다 (지우지 않고 `_pre_rollback_<시각>/`).
- 확인한 것 (SQLite): 마이그레이션 up → down → up, 이 판의 실행 중 행이 있으면 downgrade 거절·이전 판 행은 통과 (`tests/test_migrations.py`), 별도 프로세스의 `recover`가 끝난 담당은 판정하고 신호가 끊긴 다른 디스크 실행은 '결과 확인 필요'로, 신호가 새로운 실행·이전 판 실행은 그대로 두며 남은 행 수와 종료 2를 알리고, 정리 뒤 downgrade가 됨 (`tests/test_run_recovery.py`). PostgreSQL: 0019 → 0020 → 0019 → 0020·downgrade 거절·표 잠금 대기 5초·부분 DDL 되돌림·소유자·앱 계정 권한·PostgreSQL에서의 `recover`·`status` 뒤 downgrade (`tests/test_migration_0020_postgres.py`). 실제 Docker 볼륨으로 4장 B를 그대로 리허설한 기록은 아래 '리허설 결과' 7차입니다.

## 10. 0021 반영 (결과 실행의 요청 번호·내용 해시·고유 색인) — 확인 항목과 롤백

공통 key 연결(#52)의 생성 요청 번호를 DB에서 관리하는 판입니다. 반영 절차는 1~3장과 같습니다. **반영 전 리비전 인자는 실제 출발점**입니다 —
이번 실행(2026-10-27, PR #58)은 운영이 `0019_analysis_runs`이므로 `bash scripts/deploy.sh <승인 HEAD> 0019_analysis_runs`이고, backend가
시작하면서 `0019 → 0020 → 0021`을 연속 적용합니다. 0020이 이미 반영된 DB에서 시작할 때만 `0020_run_worker_lease`를 주고 `0020 → 0021`만
적용됩니다.

- 0021은 `runs`에 빈 값을 허용하는 열 세 개(`request_kind`·`request_id`·`request_hash`)와 고유 색인 `uq_runs_request(user_id, request_kind,
  request_id)`를 더합니다. 다른 표·값은 바꾸지 않습니다.
- **기존 행 채움**: 이 판 전에 만든 공통 key 연결 실행(요약 JSON의 `mode="keyjoin"`·`request_id`)은 `request_kind`·`request_id`만 채우고
  해시는 비워 둡니다 — 그 번호로 다시 보내면 내용을 비교할 수 없어 409 `KEYJOIN_REQUEST_LEGACY`로 거절합니다(새 번호로 새로 만들기). 묶기·통합
  실행은 비워 둡니다.
- **멈추는 조건(아무것도 바꾸지 않음)**: 같은 사용자·같은 요청 번호의 key 연결 실행이 둘 이상 / `keyjoin`이 든 요약을 JSON으로 읽을 수 없음 /
  key 연결 요약에 요청 번호가 없거나 모양이 다름. 이때 0021은 열을 만들기 전에 실행 번호만 알리고 멈춥니다 — **0021의 열·색인·채우기는 적용하지
  않고** 기존 행을 지우거나 덮지 않습니다. 다만 0019부터 연쇄 upgrade를 시작했다면 **SQLite에는 앞 단계 0020 변경이 남을 수 있습니다**(아래 항목 —
  두 DB를 하나로 말하지 않음). 거절 뒤 리비전과 스키마를 확인한 후 그 상태에 맞는 복구 절차를 적용합니다. 기동 때 `alembic upgrade head`가 멈추면
  backend가 뜨지 않으므로 deploy.sh의 전환 실패 경로(4장 B로 되돌리기)가 됩니다. 백업한 뒤 그 실행 기록을 확인하고 정리 방법(어느 행을 남길지)을
  정한 다음 다시 반영합니다 — 자동으로 고르지 않습니다.
- **멈춘 뒤의 상태는 DB마다 다르니 확인합니다(추정하지 않음)**: `alembic upgrade head`가 0019에서 출발해 0021에서 멈추면 **SQLite는
  0020까지 올라가 있고**(0020의 열·리비전 기록이 남음, 0021의 열·색인은 없음 — 마이그레이션마다 바로 반영) **PostgreSQL은 0019
  그대로**입니다(env.py가 한 트랜잭션으로 돌려 0020도 함께 되돌아감). 0020에서 출발하면 둘 다 0020 그대로입니다. 멈춘 뒤에는
  `alembic current`와 `runs` 열(worker·heartbeat_at / request_kind·request_id·request_hash)로 실제 상태를 보고 거기서부터 다시
  합니다(시험 `test_0021_refusal_in_the_chain_from_0019_…` SQLite·PostgreSQL).
- **멈추는 자료의 처리 원칙(2026-10-27 2차 지시 4-1)**: 자료를 임의로 고치지 않고 중단합니다 — 같은 요청 번호의 기존 실행을
  지우거나 합치거나, 요청 번호를 바꿔 색인을 억지로 만들지 않습니다. 반영 전에 **검증된 백업의 격리 복사본**(deploy.sh가 만든
  `backups/moajwo-data-<시각>.tgz`에서 `moajwo.db`만 꺼내 운영 볼륨 밖 임시 폴더로)에 0019→0020→0021을 미리 돌려 보고, 복사본의
  기준 시각(백업 시각)과 현재 운영 자료가 그 뒤 얼마나 달라졌을 수 있는지를 함께 적습니다. 멈추면 원문 개인정보 없이 실행 번호·
  사유·영향 수만 보고하고, **실제로 해당 행이 있을 때만** 정리안(어느 행을 남길지)을 소유자에게 올립니다. 대상이 0건이면 '별도
  정리 불필요'로 종결합니다. 복사본은 점검 뒤 지웁니다. 이 점검 결과는 **그 백업에 대한 결과**이며 현재 운영 내용 전체가 같다는 뜻이
  아닙니다 — 배포 때 deploy.sh가 만드는 새 백업과 현재 리비전 확인은 별도로 합니다(2026-10-27 점검: 2026-10-19 백업, key 연결 실행 0건).
- 표 잠금은 5초만 기다립니다(0020과 같음). 반영 뒤 점검: `alembic current` → `0021_run_request_key (head)`, `python -m app.recovery status`가
  `revision 0021_run_request_key`로 동작(0020과 같은 정리 동작), key 연결 화면에서 생성 → 같은 요청 번호 재전송이 200.
- 동작 변화(`docs/KEYJOIN.md` '중복 제출'): 같은 번호는 사용자별 실행 하나 — 내용이 같으면 그 실행(200, 상태 그대로), 다르면 409, 다른
  프로세스가 동시에 보내도 실행 행을 넣는 순간 하나만 남습니다. 결과 만들기 차례는 여전히 프로세스별 메모리 차례입니다(1 worker·1 instance).

### 롤백 B — 데이터를 지키며 반영 전 리비전으로 되돌리기 (별도 승인 후 수동)

4장 B를 0~7단계 그대로 합니다 — 0단계가 이 반영 로그의 **반영 전 리비전**을 읽어 downgrade 대상으로 정합니다. 이번 실행(0019 출발)에서는
`alembic downgrade 0019_analysis_runs`가 새 이미지로 `0021 → 0020 → 0019`를 차례로 내리고(두 단계 모두 '담당 있는 실행 중 행'이면 거절), 0020이
이미 반영된 DB에서 출발한 반영이었다면 0020까지만 내립니다. 0021의 downgrade는 고유 색인과 세 열만 지웁니다(요청 번호는 요약 JSON에도 남음).
**되돌아가는 이미지가 무엇인지가 중요합니다**: 이번 실행의 롤백 대상은 배포 직전 실제로 실행 중이던 2026-10-19/20 반영 이미지로, key 취합 모듈이
없습니다 — 되돌리면 key 취합은 쓸 수 없고, 0021 코드로 만든 key 결과(실행 기록·결과 파일)의 이전 화면 표시·내려받기는 확인하지 않았습니다(미검증).
key 취합이 있으면서 0021이 없는 판(통합 후보 `8d40c69` 계열 — '최근 50건 조회' 동작)은 운영에 반영된 적이 없으므로 그 설명을 이번 롤백에 적용하지
않습니다. 업무 자료(업로드·실행 기록·결과 파일)는 B에서 그대로 남으며, 배포 전 백업 복원 A와는 다릅니다(A는 별도 승인). **0021도 0020과 같은 조건(이 판에서 시작해 실행 중인 행 — 담당 있음)이면 내리지 않고 아무것도 바꾸지
않습니다** — `alembic downgrade 0019_analysis_runs`는 0021 → 0020 → 0019 순서라, 0021에서 멈춰야 리비전이 0021 그대로 남습니다(보호가
0020에만 있던 판은 리허설 10차 s6에서 0021→0020은 내려가고 0020→0019에서 거절돼 리비전이 0020에 남았음). 거절 문구의 안내대로 `recover`로
정리한 뒤 다시 합니다. 4장 B는 1단계에서 서비스를 멈춘 뒤에만 5단계 downgrade에 이르므로 새 판 코드가 지워진 열에 쓰는 일은 없습니다.
확인한 것: SQLite up·down·up과 채움·멈춤 조건(`tests/test_migrations.py`), PostgreSQL 같은 항목과 고유 색인·잠금 대기 5초
(`tests/test_migration_0021_postgres.py`), 롤백 리허설은 후보 트리의 alembic head를 읽어 같은 단계 상자로 돕니다(8차 뒤 9차).

## 11. 기반 이미지 고정과 반영 전 이미지 검증 (2026-10-28)

**왜 바꿨나**: 2026-10-28 반영에서 backend 기반 `python:3.11-slim`(digest 없는 태그)이 레지스트리를 묻지 않고 이 PC에 남아 있던
옛 이미지로 정해졌습니다. 그래서 CI가 검증한 이미지와 기반 판(Python 3.11.17 ↔ 3.11.18)과 OS 패키지 두 개가 다른 이미지가 운영에
들어갔습니다. 비교한 것은 운영 backend `6874cec0…`(PR #58 H `150f71d` 트리를 이 PC에서 빌드)와 그 전날 CI가 검사한 이미지
(`434d101`·`2a95036`에서 빌드)입니다. 이미지 입력 파일(Dockerfile 2개·`requirements.txt`·`requirements-linux-hashes.txt`·`package.json`·
`package-lock.json`·`nginx.conf`·`.dockerignore`)과 Python 의존성 27개의 이름·판은 같았고, 앱 소스는 같지 않았습니다(H와 `434d101`은
앱·화면·시험 8개 파일, `2a95036`은 여기에 0021 마이그레이션까지 9개 파일이 다름). 실행 환경은 기반에서 갈렸습니다 — Python 3.11.17 ↔
3.11.18, OS 패키지 liblzma5·tzdata. 그때 CI 자료에는 Python 내장 Expat·ssl의 OpenSSL·SQLite·psycopg 내장 라이브러리 값이 없어 그 넷은
비교하지 못했습니다(운영 Expat 2.8.5). `docker compose build`는 기반을
새로 받지 않고, 빌드 뒤 반영 전에 완성 이미지를 확인하는 단계도 없었습니다.

**고정한 기반** — 자동 갱신은 두지 않습니다. 바꿀 때는 이 표·Dockerfile·검증 목록을 같은 PR에서 바꾸고 CI 검증을 다시 합니다.

| 이미지 | 태그 | digest (멀티아키 색인) | linux/amd64 매니페스트 | 내용 |
|---|---|---|---|---|
| backend 기반 | `python:3.11.18-slim-trixie` | `sha256:82209229d3fda30d71e603ec95bb4bea0476635ba6df3df2e32284e036654f51` | `sha256:0451b9eeed3e5a9523d8958cc33262eb5843c7af3114c50f45d99f56e2b883ac` | Python 3.11.18 · Debian 13 trixie (2026-10-26 생성, 2026-10-27 CI 이미지 검사의 기반과 같은 매니페스트) |
| frontend 실행 기반 | `nginx:1.30-alpine` | `sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951` | `sha256:84ed8c0dd296c188bfc24ed506f46589e9285fb0b11cf3a04d91d524d3067e11` | nginx 1.30.6 · Alpine 3.24.3 (2026-10-28 반영과 CI가 실제로 쓴 색인) |
| frontend 빌드 단계 | `node:24.22.1-alpine` | `sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53` (그대로) | `sha256:1200d815de24f8dcca745fbf7651d166034a35adbcad8860a8538ffff44f1420` | 완성 이미지에 남지 않음 |

**기반을 고정해도 같아지지 않는 것**: backend의 `apt-get install --only-upgrade ca-certificates`와 frontend의 `apk upgrade …`는 빌드할
때 저장소에서 받습니다. pip 의존성은 해시 고정이라 같습니다. 그래서 반영 전에 **완성 이미지**를 다시 확인합니다.

**빌드 → 검증 → 반영의 연결** (`scripts/deploy.sh` 3·6단계):

1. **빌드 전 자원 확인** (`scripts/memory_guard.py check build`): 아래 '자원 기준'에 못 미치거나 재지 못하면 후보를 만들지 않고 멈춥니다.
2. **후보 빌드** — backend·frontend를 하나씩, 후보 태그로 빌드합니다 (`<이미지>:cand-<시각>`, 덧붙임 `backups/candidate-<시각>.compose.yml`).
   운영이 쓰는 latest 태그는 옮기지 않습니다. 빌드 동안의 시작·최저 여유 메모리를 기록합니다.
3. **검증** — `scripts/image_gate.sh <backend ID> <frontend ID> backups/image-gate-<시각>`. 하나라도 실패하면 서비스와 latest 태그를
   그대로 두고 멈춥니다.
   - **완성 이미지 목록** (`scripts/image_inventory.py`): 플랫폼, OS 판, OS 패키지 전체 판, Python 판·ssl의 OpenSSL·내장 Expat(xlsx
     XML 해석)·SQLite, Python 의존성 전체 판, CA 묶음 sha256·개수, psycopg 내장 라이브러리 해시, nginx 판이 검증한 목록
     `deploy/expected-images/{backend,frontend}.json`과 같아야 합니다. 후보 쪽 목록은 매번 후보 이미지에서 새로 모으고, 검증 목록은
     그 고정 경로의 파일 내용이 HEAD에 커밋된 것과 같아야 씁니다(커밋하지 않은 변경은 거절). 비교 전에 두 목록의 도구 판·종류와 종류별
     필수 항목·자료형을 검사합니다 — 항목이 빠졌거나 형식이 틀린 목록은 '같음'이 아니라 입력 오류입니다. 이미지 ID가 같다는 사실만으로는
     통과도 거절도 하지 않고 항목을 그대로 비교하며, ID가 같은데 실행 환경이 다르면 모순(입력 오류)입니다. 앱 코드·화면 파일 해시는
     기록만 합니다.
   - **격리 점검 전 자원 확인** (`memory_guard.py check smoke`) — 미달·측정 실패면 점검을 건너뛰지 않고 멈춥니다.
   - **격리 기동 점검** (`scripts/image_smoke.py`): 내부 네트워크(`--internal`)와 새 임시 볼륨에 띄우고 호스트 포트를 열지 않습니다.
     기동(healthy·alembic head), DB 연결(`/health`), CA·TLS(기본 신뢰 저장소, 점검 안에서 만든 시험 CA로 Python ssl과 psycopg
     `verify-full`의 성공·거절), frontend(첫 화면·보안 헤더·프록시), 핵심 취합(CSV·XLSX 업로드 → 공통 key 연결 → 내려받아 내용 확인,
     묶기 → 내려받기), 로그를 봅니다. 끝나면 이 실행이 만든 컨테이너(익명 볼륨 포함)·볼륨·네트워크만 지우고, 목록을 다시 조회해 남지 않았음을
     확인합니다.
   - **자원 감시의 정리** (`memory_guard.py watch`): 빌드·격리 점검을 감싸 여유 메모리를 재는 감시 컨테이너(`moajwo-memwatch-<시각>-<pid>-<무작위>`)도
     이 실행이 만든 이름 하나만 지우고 목록으로 남지 않았음을 확인합니다. 남았거나 목록 조회가 실패해 확인하지 못하면 감싼 명령이
     성공했어도 종료 3(도구 오류)이고, 감싼 명령의 종료 코드·삭제 결과·삭제 뒤 조회 결과·남은 이름은 기록에 따로 남습니다
     (`memory.tsv`의 summary 줄, 격리 점검은 `smoke.watch.json`). 표본을 얻지 못한 것(감시 컨테이너를 띄우지 못함·기록을 읽지 못함)은
     '미측정'일 뿐 판정을 바꾸지 않습니다. 앞 실행이나 다른 작업의 컨테이너는 지우지 않습니다.
4. 검증 스크립트는 종료 0만 믿지 않습니다. 목록 대조는 '같음' 요약 줄까지, 격리 점검은 결과 JSON이 이번 두 이미지 ID·필수 점검 22개
   전부 통과·도구 오류 0을 말해야 하고, 그 점검을 감싼 감시는 `smoke.watch.json`에서 점검의 종료 코드와 감시 컨테이너 정리 확인을
   따로 읽어 둘 다 정상이어야 통과입니다. 점검 실패와 정리 실패가 함께면 `gate.result`의 사유에 둘 다 적습니다. 통과하면 마지막에
   `gate.result`에 `status=pass`와 두 이미지 ID를 쓰고, `deploy.sh`는 종료 코드와 이 기록을 함께 확인합니다. 빌드를 감싼 감시의 정리
   실패도 `deploy.sh`가 후보 빌드 단계에서 멈추고(검증·정지·백업 전) 감시의 '판정' 줄(빌드 종료 코드·정리 결과)을 사유로 적습니다.
5. 백업 확인 뒤 전환 준비(로그 `== 5f.`)에서 **그 ID에만** latest 태그를 옮기고, 옮긴 결과를 실제 이미지 ID로 확인합니다. 그다음 6단계
   `up -d --no-build`로 전환하며, 실행 중 이미지 ID가 검증한 ID와 같아야 '전환 완료'입니다. 태그를 옮기지 못하면 전환하지 않고 둘 다 반영
   전 이미지로 되돌린 뒤, 되돌린 결과도 실제 ID로 확인해 '되돌림 확인'(되돌림 명령 종료 0이고 읽은 ID가 둘 다 반영 전), '읽은 latest는
   반영 전 이미지와 같음 — 되돌림 명령 실패', 또는 '[주의] 되돌림 확인 안 됨'(어느 태그가 어느 이미지에 남았는지)
   으로 적고 기존 컨테이너를 재개합니다. 컨테이너 재개 결과와 latest 태그 상태는 따로 적습니다 (`결과: … · latest 태그: …` 줄).
   이 실패는 `== 6.` 머리줄 앞이라 4장 B 0단계가 '전환 도중 끊긴 반영'으로 분류하지 않습니다 — 전환이 없었으므로 4장 B는 쓰지 않고,
   '[주의] 되돌림 확인 안 됨'이면 그 줄의 이미지 ID로 `docker tag <반영 전 ID> <이름>:latest`를 직접 하고 `docker image inspect`로 확인합니다.
6. 반영 로그에 '자원 | …'(단계마다 '시작 전'·'동안'·'판정'), '후보 이미지', '이미지 검증 통과', '실행 중 이미지 = 3단계에서 검증한 이미지', '이미지 연결' 줄이 남습니다.

**격리 점검의 통과 판정** — 점검마다 바뀌지 않는 id가 있고(`image_smoke.py`의 `REQUIRED`, 22개: 호스트 5 + 컨테이너 안 17), 통과는
22개가 각각 정확히 한 번, `ok`가 참거짓 값 `true`로 있을 때만입니다. 빈 결과·일부·중복·모르는 id·`ok`가 문자열·숫자·null인 결과,
`docker logs` 조회 실패(종료 코드가 0이 아님), 기동 표지가 없는 빈 로그, 자원 삭제 실패·삭제 뒤 목록 조회 실패는 통과가 아닙니다.
결과 JSON은 화면 출력 뒤에 원자적으로 쓰고, 출력이 실패하면 통과로 남기지 않습니다.
상태·응답 조회가 docker 쪽에서 실패한 채 끝난 점검(docker 명령 시간 초과·데몬 연결 실패)은 대상의 실패가 아니라 미측정(도구
오류)입니다. 컨테이너 안 한 단계가 예외로 끝나 하지 못한 점검은 사유(`not_run`)와 함께 실패로 남습니다 — 대부분 후보 쪽 원인
(응답 없음·모듈 없음·파일 손상)입니다. 컨테이너는 익명 볼륨과 함께 지우고(`rm -f -v`), 자원별 삭제 결과·오류 원문·남은 것은
결과 JSON의 `cleanup`에 남습니다. 측정한 실패와 도구 오류가 함께 있으면 정리 실패·확인 불가·출력 실패는 3(남은 자원·기록부터 봐야
함), 그 밖에는 4가 먼저입니다 — 실패한 id와 도구 오류는 어느 쪽이든 결과 JSON과 출력에 모두 남습니다.

| 도구 | 종료 0 | 4 | 3 | 1 | 2 |
|---|---|---|---|---|---|
| `image_inventory.py compare` | 같음 | 실행 환경이 다름 | 입력 오류 (도구 판·종류·필수 항목·형식·출처 기록, 같은 이미지 ID인데 실행 환경이 다름) | 예상치 못한 중단 | 사용법 |
| `image_smoke.py` | 통과 | 측정한 점검 실패 | 도구 오류 (빠짐·중복·형식, docker 명령 실패, 정리 실패·확인 불가, 출력 실패) | 예상치 못한 중단 | 사용법 |
| `memory_guard.py check` | 통과 | 기준 미달 | 측정 실패 | 예상치 못한 중단 | 사용법 |
| `memory_guard.py watch` | 감싼 명령 0 · 감시 정리 확인 | 감싼 명령의 종료 코드 그대로 (정리를 확인했을 때 — 4·1·2 등) | 감시 컨테이너가 남음·남았는지 확인 불가, 결과 파일 쓰기 실패 (감싼 명령의 결과보다 먼저 — 그 종료 코드는 기록에) | 〃 (또는 예상치 못한 중단) | 〃 (또는 사용법) |

**자원 기준 (공유 PC)** — 운영 서비스와 같은 PC·같은 Docker에서 후보를 빌드하고 격리 점검을 띄우므로, 각 단계를 시작하기 전에
여유 메모리를 재고 기준에 못 미치거나 재지 못하면 후보 준비 단계에서 멈춥니다 (운영은 멈추지 않고 검사도 건너뛰지 않음).
단위는 MiB(1024×1024 바이트)이고 두 곳을 따로 잽니다.

| 측정 위치 | 무엇 | 빌드 시작 기준 | 격리 점검 시작 기준 |
|---|---|---|---|
| host | Windows 사용 가능 물리 메모리 (`GlobalMemoryStatusEx.ullAvailPhys`, 작업 관리자의 '사용 가능'). 리눅스 러너는 `/proc/meminfo` MemAvailable | 870 | 560 |
| vm | Docker 엔진 VM(Docker Desktop은 WSL2 VM)의 `/proc/meminfo` MemAvailable — 네트워크·볼륨 없는 일회용 컨테이너로 읽음 | 1536 | 1536 |

근거가 된 측정 (2026-10-28, 이 PC — 원기록: 첫 줄 `backups/deploy-20261028/hostmem.tsv`, 둘째·셋째 줄
`backups/review-20261028/local-gate-20261028-145559/run.log`, 15:45 세 줄 `backups/review-20261028/memmeasure-20261028-154519/`,
18:35~18:39 `backups/review-20261028/local-gate-b7d9332-*`, 19:25 `backups/deploy-20261028/image-compare/prod-smoke-v2-memory.tsv`):

| 단계 | 직전 check (기준과 비교하는 값) | host 감시 시작 → 최저 | vm 시작 → 최저 | 측정 방법 | 결과 |
|---|---|---|---|---|---|
| 반영 전체 (backend·frontend 동시 빌드 ~ 전환, 11:44~11:46) | 없음 | 1940 → 364 (11:45:20 — 빌드 중인지 백업 중인지는 기록만으로 알 수 없음) | 미측정 | CIM `FreePhysicalMemory`, 약 6초 간격 | 끝까지 정상 (OOM·재시작 없음) |
| 미리 확인 빌드 backend (새 기반 받음) | 없음 | 739 → 330 (끝 시점) | 미측정 | CIM, 시작·끝만 | 정상 |
| 미리 확인 격리 점검 | 없음 | 1082 → 849 (끝 시점) | 미측정 | CIM, 시작·끝만 | 22/22 |
| 빌드 backend (층 캐시, 15:45) | 866 | 867 → 601 | 5462 → 5452 | `memory_guard.py` (ullAvailPhys 1초·VM 2초 간격) — 측정용 실행이라 그때 기준 미달에도 계속 | 정상 |
| 빌드 frontend (층 캐시, 15:45) | 없음 (backend 빌드에 이어 시작) | 557 → 543 | 5455 → 5455 | 〃 | 정상 |
| 격리 점검 (새 판, 15:45) | 552 | 479 → 326 | 5466 → 5351 | 〃 | 22/22 |
| (18:35 시도) | 빌드 551 | — | — | `memory_guard.py` | 빌드 전에 멈춤 (설계대로) |
| 빌드 backend (층 캐시, 18:38) | 1070 | 1031 → 737 | 5459 → 5432 | 〃 | 정상 |
| 빌드 frontend (층 캐시, 18:39) | 없음 (backend 빌드에 이어 시작) | 684 → 679 | 5453 → 5453 | 〃 | 정상 |
| 격리 점검 (18:39) | 671 | 667 → 449 | 5458 → 5318 | 〃 | 22/22 |
| 운영 이미지 사본 격리 점검 (19:25) | 928 | 901 → 715 | 5460 → 5304 | 〃 | 22/22 |

- 기준과 비교하는 값은 단계 직전의 check 값입니다(빌드는 backend 빌드 전 한 번, 격리 점검은 `image_gate.sh`가 점검 전에 한 번).
  host 기준은 그 check 값으로 단계가 OOM·재시작 없이 끝난 것을 확인한 가장 낮은 값(빌드 866, 격리 점검 552 — 15:45, ullAvailPhys)을
  10 MiB 단위로 올린 값(870·560)입니다. 감시 첫 표본(867·479)은 check 뒤 감시 컨테이너를 띄우고 잰 값이라 기준과 비교하는
  값이 아닙니다. 그보다 낮은 여유는 확인하지 않았으므로 시작하지 않습니다. frontend 빌드는 따로 확인하지 않고 backend 빌드에 이어 시작합니다(557·684에서 시작해 정상).
  보완한 도구(`b7d9332`)로 한 이 PC 실측(`local-gate-b7d9332-*`): 18:35에는 빌드 check 551로 빌드 전에 멈췄고(설계대로), 18:38에는
  빌드 1031→737, 격리 점검 667→449 MiB로 정상이었습니다. 이 측정들은 '끝까지 정상'의 관측이고 안전 여유를 증명하지는 않습니다 —
  여러 번 host가 330~364 MiB까지 내려갔습니다(Windows가 다른 프로그램의 메모리를 줄이기 시작하는 쪽). 그래서 이 기준은 정상 완료를
  관측한 범위 안에서만 단계를 시작하게 하는 조건이고, 메모리 부족(OOM)을 막는다는 보장이 아닙니다.
- CIM `FreePhysicalMemory`와 ullAvailPhys는 같은 순간에도 값이 다릅니다 (2026-10-28 19:24 세 번: CIM 838~854 대 ullAvailPhys
  912~922 MiB — `backups/review-20261028/pr59-fix/cim-vs-availphys.txt`). 앞 세 줄(CIM)은 원기록의 값('MB' 표기, 환산 방식
  미기록)을 그대로 옮겼고 기준 계산에 쓰지 않았습니다.
- vm 기준 1536 = 격리 점검 컨테이너 상한 합(backend `--memory 1g` + frontend `256m` = 1280) + 256입니다. 상한은 실측이 아니라 넘지 못하는
  선입니다. 빌드의 vm 기준도 같은 값으로 두었지만 근거는 약합니다 — 층 캐시 빌드에서 VM 여유는 10 MiB 남짓만 줄었고, 새 기반을 받는
  빌드의 VM 사용은 재지 않았습니다(미검증). 측정한 VM 여유는 5.3 GiB 이상이라 실제로 막는 쪽은 host입니다.
- 기준을 바꿀 때는 `scripts/memory_guard.py`의 값과 이 표를 같은 PR에서 바꿉니다. 미달로 멈추면 다른 프로그램을 닫는 등 여유를 만든
  뒤 다시 실행합니다 — 고의로 메모리를 채워 시험하지 않습니다.

**검증한 목록은 어디서 오나**: CI에서 같은 고정 입력으로 빌드한 완성 이미지에서 `image_inventory.py collect`로 만든 것입니다. 같은
CI 실행에서 그 이미지로 격리 기동 점검과 Trivy 검사를 합니다. 파일의 `verified` 항목에 CI 실행 번호·커밋이 있고(CI 이미지 ID는
`image` 항목), 대조할 때 `ci_run`·`commit`이 있는지 확인합니다 (나머지는 비교하지 않는 기록). 그 CI 워크플로는 기본 가지에서 저절로 돌지 않도록 저장소에는
`diagnostics/ci/workflows/`(`image-gate.yml`·`gate-rehearsal.yml`)에 사본으로 두고, 실행할 때만 진단 가지에 `.github/workflows/`로
넣어 올립니다 — `image-gate.yml`은 `ci/image-gate-*`, `gate-rehearsal.yml`은 `ci/gate-rehearsal-*` 가지에서만 돕니다.

**CI에서 검증한 이미지를 그대로 옮기지 않는 이유**: 옮기려면 레지스트리가 필요한데, 저장소가 공개라 이미지를 공개 레지스트리에 올리지
않습니다(사설 레지스트리 없음). 그래서 운영 PC에서 같은 고정 입력으로 다시 빌드하고, 그 완성 이미지가 검증한 이미지와 같은 실행
환경인지 목록으로 확인한 뒤, 같은 PC에서 격리 점검을 통과한 **그 ID**만 반영합니다. Docker Desktop(containerd 저장소)의 이미지 ID는
매니페스트 digest이고 CI docker(기존 저장소)의 ID는 설정 digest라, 같은 이미지라도 CI와 로컬의 ID 값은 다릅니다. CI와 로컬은
목록으로, 같은 PC 안의 빌드·검증·반영은 ID로 연결합니다.

**목록이 다르면**: 반영하지 않습니다. 보통 빌드 시점의 저장소 갱신(예: ca-certificates·curl 보안 판) 때문입니다. CI에서 같은 커밋으로
다시 빌드·검증해 `deploy/expected-images/`를 갱신하는 PR을 만들고 승인받은 뒤 반영합니다. 목록을 손으로 고쳐 통과값에 맞추지 않습니다.

**보호 범위 — 무엇을 막고 무엇은 막지 않나**:

- `deploy.sh`·`image_gate.sh`에는 검증을 바꾸거나 건너뛰는 환경 변수가 없습니다 (2026-10-28에 `DEPLOY_IMAGE_GATE`·`DEPLOY_EXPECTED_DIR`·
  `PYTHON`을 없앰). 검증 스크립트·검증 목록·자원 기준은 1단계에서 승인한 트리와 같다고 확인한 이 저장소의 파일이고, Python은 `python`·
  `python3` 가운데 무작위 응답을 돌려주는 3.10 이상만, 격리 모드(`-I` — `PYTHON*` 환경 변수·스크립트 폴더의 모듈을 쓰지 않음)로
  씁니다. 명령 대역 시험은 임시 저장소에 대역 파일을 커밋해 같은 경로로 부릅니다.
- 1단계는 추적 파일의 미커밋 변경 외에, 저장소 어디든 추적하지 않는 파일(`.gitignore` 대상인 `backups/` 등은 제외 — 빌드·검증 입력,
  Compose 덧붙임 파일, 그리고 Windows가 PATH보다 먼저 현재 폴더에서 찾는 `docker.exe` 같은 실행 파일), 변경을 숨기는 git 표시
  (assume-unchanged·skip-worktree)·`filter` 속성이 붙은 추적 파일이 있으면 멈춥니다. git 조회는 fsmonitor 훅·untracked 캐시 없이 합니다.
  `deploy.sh`의 Compose 호출(조회·후보 빌드·정지·전환)은 모두 `-f docker-compose.yml`을 직접 지정해 같은 설정을 읽습니다 — 기본
  파일 찾기·덧붙임 파일·`COMPOSE_FILE`(셸·`.env`·`COMPOSE_ENV_FILES`의 파일)이 전환에만 끼어들지 못합니다. 4장 롤백 같은 손 명령은
  기본 파일 찾기를 따르므로, 다른 이름·덧붙임 Compose 파일(추적 여부와 상관없이)이나 `COMPOSE_FILE`(셸·`.env`)이 있으면 멈춥니다. Compose 이미지 참조에 태그·digest가
  지정돼 있어도 멈춥니다 (이 스크립트가 옮기는 것은 `<이름>:latest`뿐).
- 검증 목록의 출처는 이미지 ID로 판단하지 않습니다. (가) 고정 경로 `deploy/expected-images/`의 파일 내용이 HEAD에 커밋된 blob과
  같아야 하고(`image_gate.sh` — 변환·필터 없이 대조, 커밋하지 않은 변경은 거절. `deploy.sh`는 1단계에서 HEAD = 승인한 트리를 확인),
  (나) CI 출처 기록(`verified.ci_run`·`commit`)이 있어야 합니다. 후보 쪽 목록은 매번 새로 모으고, 기대값 파일을 쓰는 도구는 없습니다.
  2026-10-28 판은 같은 이미지 ID를 '후보 자신의 목록'으로 거절했지만, ID가 같다는 것만으로는 출처가 잘못됐다고 할 수 없고(승인한 CI
  이미지 자체를 비교하거나 같은 결과를 다시 쓰는 경우) 2026-10-29에 그 규칙을 없앴습니다. 반대로 'ID가 같으면 통과'도 아닙니다 —
  ID가 같아도 필수 항목·자료형·종류·실행 환경을 그대로 비교하고, ID가 같은데 실행 환경이 다르면 모순(입력 오류)입니다. ID는
  `sha256:` + 소문자 16진수 64자리 한 가지 표기만 받습니다. 이 검사들은 형식·커밋 확인이라, 목록이 정말 CI에서 왔는지는 목록을
  바꾸는 PR의 검토(CI 실행 번호·산출물과 대조)로 확인합니다.
- 다른 `DEPLOY_*` 변수(Compose 인자·볼륨·백업 폴더·백업 이미지·롤백 태그 이름)는 격리 리허설용입니다. Compose 파일·볼륨을 바꿔
  승인하지 않은 설정으로 빌드·전환하게 할 수 있으므로, 운영 반영에서는 하나라도 있으면 1단계에서 멈춥니다 (격리 리허설은
  `DEPLOY_REHEARSAL=1`을 함께 지정 — 반영 로그 1단계 줄에 남음).
- 내보낸 셸 함수·`BASH_ENV`·`ENV`가 있거나 시작할 때 이미 정의된 함수가 있으면 `deploy.sh`·`image_gate.sh` 모두 멈춥니다 (셸 시작
  파일·함수가 `docker`·`git` 같은 명령을 바꿀 수 있음). Windows에서는 실행 파일을 현재 폴더에서 먼저 찾지 않게
  (`NoDefaultCurrentDirectoryInExePath=1`) 합니다.
- 1단계는 git 상태 외에 빌드·검증 입력(`backend`·`frontend`·`scripts`·`deploy`·`docker-compose.yml`)의 작업 파일 내용을 승인한 트리의
  blob과 직접 대조합니다 — git은 크기·수정 시각이 같으면 내용을 읽지 않아, 같은 크기로 고치고 수정 시각을 되돌린 파일을 놓칩니다.
- 서비스별 Compose 이미지 이름은 설정(JSON)의 `image`(없으면 `<프로젝트>-<서비스>`)로 정하고 Compose 자신의 이미지 목록과
  맞춰 봅니다 (`config --images <서비스>`는 depends_on 서비스까지 순서 없이 찍어 frontend가 backend 이름을 받을 수 있었음).
  두 이름이 같거나 후보 두 ID가 같으면 멈춥니다.
- 막지 못하는 것: 운영 PC의 `docker`·`python` 실행 파일 자체를 바꾸는 경우, `deploy.sh` 자체를 바꾸는 경우(실행하는 스크립트는
  자기를 검사할 수 없음 — 1단계의 내용 대조는 실수로 바뀐 것을 잡지만 바뀐 스크립트는 그 확인을 건너뛸 수 있음), 셸 시작 파일이
  스스로 흔적을 지우는 경우, 운영 반영에서 `DEPLOY_REHEARSAL=1`을 함께 지정한 경우(대체 변수가 그대로 쓰이고 1단계 로그 줄에만
  남음 — 검증 자체는 그대로 돎), `deploy.sh`를 거치지 않고 손으로 빌드·태그를 옮겨
  `docker compose up`을 하는 경우 (그냥 `docker compose build`는 latest 태그를 바로 옮깁니다 — 8장의 frontend 단독 반영 경로가 그
  예라 쓰지 않습니다). 이런 변경은 승인 절차 밖의 일입니다.

**미리 확인하기 (운영을 바꾸지 않음)** — 반영 창 전에 같은 PC에서 후보를 빌드해 검증만 해 볼 수 있습니다:

```bash
TS=$(date +%Y%m%d-%H%M%S); OLD_B=$(docker inspect "$(docker compose ps -q backend)" --format '{{.Image}}')
mkdir -p "backups/image-gate-pre-$TS"
printf 'services:\n  backend:\n    image: moajwo-backend:pre-%s\n  frontend:\n    image: moajwo-frontend:pre-%s\n' "$TS" "$TS" > "backups/pre-$TS.compose.yml"
python -X utf8 scripts/memory_guard.py check build "$OLD_B" "backups/image-gate-pre-$TS/memory.tsv" && for s in backend frontend; do
  python -X utf8 scripts/memory_guard.py watch "build-$s" "$OLD_B" "backups/image-gate-pre-$TS/memory.tsv" -- \
    docker compose -f docker-compose.yml -f "backups/pre-$TS.compose.yml" build "$s" || break      # latest 태그는 그대로
done
bash scripts/image_gate.sh "$(docker image inspect moajwo-backend:pre-$TS --format '{{.Id}}')" \
  "$(docker image inspect moajwo-frontend:pre-$TS --format '{{.Id}}')" "backups/image-gate-pre-$TS"
docker rmi "moajwo-backend:pre-$TS" "moajwo-frontend:pre-$TS"   # 끝나면 pre 태그만 지움 (반영 때는 deploy.sh가 다시 빌드·검증)
```

**하지 않는 것**: 기반 이미지 자동 갱신(Dependabot·Renovate 등), 운영 자동 재배포.

## 12. 0022 반영 (관리자 아이디 admin 고정·계정 발급·임시 비밀번호) — 확인 항목과 롤백

**바뀌는 것 (코드 + DB 0022):** 공개 최초 설정(`/auth/setup`)과 회원가입(`/auth/signup`)이 종료되고(410), 계정은 설치 담당자의 초기화 명령
`python -m app.adminctl init`(아이디 `admin`, 역할 owner)과 최고 관리자의 계정 발급(서버가 만든 임시 비밀번호, 발급 응답에서 한 번만 표시)으로만
생긴다. 임시 비밀번호로 로그인한 계정은 본인 비밀번호로 바꾸기 전까지 업무 API가 403이고, 유효 시간(기본 24시간, `MOAJWO_TEMP_PASSWORD_HOURS`)이
지나면 로그인하지 못한다(재발급). `users`에 `must_change_password`(기본 false)·`temp_password_expires_at`(NULL) 두 열이 생기고 **기존 계정의
비밀번호·상태는 그대로**다 (어떤 계정도 임시 상태로 바뀌지 않음). 스키마 변경은 계정을 개명하지 않는다 — 기존 소유자(`rox`)의 아이디 전환은 별도
명령(12-2)이다. DB 역할(`moajwo_app`)과 로그인 계정(`admin`)은 별개이며 앱 역할에 새 권한이 필요하지 않다(DML만).

### 12-1. 반영 (이미지 교체 + 0021 → 0022)

- 명령 형식: `bash scripts/deploy.sh <승인 SHA> 0021_run_request_key` — 두 번째 인자는 지금 운영 리비전(0021). 기동 때 `alembic upgrade head`가 0022를
  적용한다(열 두 개 추가, 자료 변환 없음 — PostgreSQL은 `lock_timeout 5s`).
- 반영 뒤 바로 달라지는 사용자 동작: 로그인 화면에서 「회원가입」이 사라지고 "계정은 관리자에게 요청" 안내가 보인다. 기존 계정(rox·일반 사용자)은
  **같은 아이디·비밀번호로 그대로 로그인**한다. 예전 공개 가입으로 남은 '승인 대기' 계정은 관리자가 승인·반려할 수 있다.
- 반영 뒤 '사용자 계정' 탭(옛 '사용자·가입 신청')에서 계정 발급·임시 비밀번호 재발급·이용 중지/재개를 한다. 예전 '계정 직접 추가(비밀번호 입력)'와
  '비밀번호 초기화(새 비밀번호 입력)'는 없어지고 둘 다 임시 비밀번호 발급으로 바뀐다.

### 12-2. 기존 소유자 → admin 전환 (반영 뒤, 소유자 합의한 시점에 한 번)

```sh
docker compose exec -T backend python -m app.adminctl transition            # 확인만 — 계획·보존 건수 출력, 바꾸지 않음
docker compose exec -T backend python -m app.adminctl transition --apply --yes   # 적용: 로그인 아이디만 admin으로, 그 관리자의 세션 종료
```

- 보존: 내부 번호(user id), 비밀번호 해시, 파일·검토·실행·분석 소유(FK), 관리 이력. 바뀌는 것: `users.username`(rox → admin), `app_settings.owner_setup`의
  owner 이름(이전 이름은 `renamed_from`으로), 그 관리자의 `auth_version`(+1 → 세션 종료, admin으로 다시 로그인), 전환 이력 1건(`owner_renamed`).
  다른 사용자의 세션·계정은 그대로다.
- 멈추는 조건(아무것도 바꾸지 않음, 종료 2): `admin` 아이디를 다른 계정이 이미 씀(상태와 무관 — 지우거나 합치거나 승격하지 않는다), owner가 없음,
  owner가 둘 이상(자동으로 고르지 않음).
- **호환 기간:** 전환 전까지 기존 아이디(rox)로 계속 로그인한다 — 반영이 기존 아이디를 잠그지 않는다. 전환 시점은 소유자가 정하고, 전환 뒤 관리자
  아이디는 `admin`으로 유지된다.

### 12-3. 반영 후 점검 (운영 자료를 바꾸지 않는 것부터)

1. `docker compose exec -T backend alembic current` → `0022_temp_passwords (head)`; `curl -s http://127.0.0.1:8080/api/health` 정상(frontend 경유 — B′ 상태에서도
   같은 명령); backend 로그에 오류 없음.
2. 로그인 전 화면: 「회원가입」 없음·"계정은 관리자에게 요청" 안내; `POST /api/auth/setup`·`/signup` → 410 (본문 반환 없음); `GET /api/auth/status` →
   `setup_required: false`.
3. 기존 소유자 로그인 → 「관리자 → 사용자 계정」에 발급 양식·'비밀번호' 열(기존 계정은 '본인 비밀번호'); 관리 이력의 새 작업 이름 표시.
4. 실제 발급·최초 변경·재발급 흐름은 **운영 검증용 계정 1개로 별도 승인 뒤에만** (관리 이력·대시보드 숫자에 더해지는 몫을 기록). 격리 환경 결과로
   갈음하고 운영 보고에는 '미검증'으로 적는다.
5. 소유자가 정한 시점에 12-2 전환 → admin으로 재로그인 → 파일 목록·기존 결과 내려받기 그대로.

### 12-4. 롤백 제한과 복구 정책 — 자료 복구와 계정 정책 복구는 다른 문제다 (소유자 결정 2026-10-31, D2·D3)

- **이전 이미지(0021까지 아는 코드)는 0022 DB에서 뜨지 않는다** — 기동 때 `alembic upgrade head`가 모르는 리비전(0022)에서 실패한다. 이미지만 이전 판으로
  바꾸는 것은 복구가 아니다.
- **0022 downgrade의 보호:** 임시 비밀번호 상태(`users.must_change_password`)인 계정이 **이용 중·이용 중지·만료와 관계없이** 하나라도 있으면 거절한다
  (아무것도 바꾸지 않음). 이용 중지·만료는 임시 상태를 풀지 않는다 — 구판에서 그 계정이 재개되면 임시 비밀번호가 일반 비밀번호처럼 쓰이기 때문이다.
  거절을 피하려고 DB에서 플래그를 지우거나 중지 계정을 검사에서 빼지 않는다. 실행 중인 결과 실행(담당 있음)이 있어도 0020·0021과 같은 이유로 거절한다
  (9·10장의 정리 절차 뒤 다시) — 어느 거절이든 리비전·새 열·계정·업무 자료는 그대로다.
- **임시 상태 계정이 남아 있을 때의 처리 순서 (D3):** ① **이용 중** 임시 계정 — 본인 비밀번호 변경을 기다린다. 폐기 대상이 아니고, downgrade를 통과시키려고
  중지시키거나 상태를 강제로 바꾸지 않는다. 기다릴 수 없으면 ②·③ 경로의 downgrade는 쓸 수 없다고 안내한다(①로). ② **이용 중지** 임시 계정 —
  관리자가 「임시 비밀번호 폐기」(12-6)를 한다: 비밀번호가 사용할 수 없는 값이 되고 임시 상태가 풀려 보호 조건 밖으로 나간다(플래그만 지우는 경로가
  아니라 비밀번호를 못 쓰게 바꾼 결과). 폐기된 계정은 구판에서도 로그인할 수 없고, 복귀 뒤 재개하려면 재발급한다.
- **복구 경로 (D2):**

| 경로 | 쓰는 때 | 자료 | 계정 정책(관리자 발급 전용) | 절차 |
|---|---|---|---|---|
| **① 0022를 유지한 수정판(fix-forward)** | **표준** — 장애 원인을 고친 후보를 평소 반영 경로(PR → 진단 CI → `bash scripts/deploy.sh <SHA> 0022_temp_passwords`)로 올린다 | 유지 | **유지** | [RECOVERY_RUNBOOK.md](RECOVERY_RUNBOOK.md) ① |
| **② B′ = B(0~7) + P1** | 비상시에만 — ①을 기다릴 수 없을 때. 임시 상태 계정 0(위 처리 순서) · 실행 중 실행 0 | 유지(두 열만 제거) | **유지** — 구판 이미지는 그대로 두고 덧붙임 설정(P1, `deploy/bprime/`)이 `POST /api/auth/signup`을 nginx 410으로 막고 backend 호스트 포트를 닫는다 | 4장 B 0~7 → P1, 해제 P0 |
| ② B (P1 없이) | **허용하지 않음** | — | 회귀(공개 가입이 다시 열림) | — |
| **③ A + P1** | 자료 손상으로 자료 복구가 필요할 때만 — 백업 이후 변경을 잃어도 된다는 결정 | **손실**(반영 뒤 발급 계정·변경·업무 자료가 활성 DB에서 빠짐 — 보존 폴더로) | **유지**(P1) | 4장 B 0·1 → A → 6·7 → P1 |

- 어느 경로도 자동으로 실행하지 않는다 — 소유자 승인 뒤 사람이 상자를 하나씩 붙여 넣는다. B′ 동안에는 가입 신청이 생기지 않는다(nginx가 410으로 끝내
  backend에 닿지 않음).
- 구판 화면의 안내 문구는 구판 것이다 — 로그인 화면에 「회원가입」 버튼이 보이지만 누르면 서버 응답 410의 문구("공개 회원가입은 종료되었습니다. 계정은
  관리자에게 요청해 주세요.")가 그 화면의 오류 자리에 보인다(격리 리허설에서 기록, `backups/followup-20261030/round5/`). 버튼을 숨기는 것이 차단 근거가 아니라
  nginx 410과 backend 포트 차단이 근거다.
- "안전한 롤백 준비" 판정과 각 경로의 선행 조건·예상 중단 시간·판단 기준·단계·중단점은 [RECOVERY_RUNBOOK.md](RECOVERY_RUNBOOK.md).
- 전환(12-2) 뒤의 롤백: 아이디는 `admin`으로 남는다(이전 코드도 그 이름으로 로그인됨 — 이전 판은 `admin`을 예약하지 않으므로 문제 없음). 전환 이력은 보존.
- 격리 리허설 결과: `backups/followup-20261030/round3/rollback/`·`round4/rollback/`(②의 동작 확인 — '가입 201'은 정책 회귀의 확인이지 성공이 아님),
  `round5/`(폐기 경계·B′, 12-5).

### 12-5. 격리 리허설 (합성 자료, 운영 분리 — 결과는 round3 폴더)

새 이미지로 빈 DB 기동 → `init` → 발급(임시 상태 1) → `downgrade 0021` 거절 확인 → 최초 변경 완료(임시 상태 0) → `downgrade 0021` 성공·두 열 제거 → 이전
이미지(main `b12ba63`, 0021)로 기동·같은 계정 로그인·**공개 가입이 다시 열림(정책 회귀)** 확인 → 새 이미지로 `upgrade head` → 계정·비밀번호 그대로. 실제
수치·시각은 round3 기록에. 중지된 임시 계정이 있을 때의 거절은 시험(`tests/test_migrations.py`·`test_migration_0022_postgres.py`)으로 확인한다.

5차(2026-10-31, `backups/followup-20261030/round5/`): 진단 CI 리허설(`diagnostics/ci/image_gate_rehearsal.sh`)에 폐기 경계 — 발급·중지한 임시 계정이
있으면 서버가 뜬 채 one-off downgrade 거절(리비전 그대로) → 「임시 비밀번호 폐기」 → 그 계정을 둔 채 4장 B 5단계 downgrade 통과 — 와 B′(P1 적용 →
가입 410·로그인·status·관리자 API·저장한 세션 유지·backend 직접 포트 도달 불가 → P0 해제 → 원상)를 넣었다. 운영 이미지 태그(구판) 자체로 B′를 돌린
격리 기록(화면 문구 포함)도 거기에 있다.

### 12-6. 임시 비밀번호 폐기 (관리자 작업, D3)

- 대상: **이용 중지** 상태이면서 임시 비밀번호 상태인 계정만. 「관리자 → 사용자 계정」의 '임시 비밀번호 폐기' 또는 `POST /users/{id}/password/revoke`
  (`expected_auth_version` 필수 — 재발급과 같은 계약: 없거나 정수가 아니면 422, 그 사이 재개·재발급·다른 폐기가 먼저 반영됐으면 409, 화면은 자동으로
  다시 시도하지 않고 최신 목록을 다시 불러온다).
- 동작(한 UPDATE): 비밀번호를 사용할 수 없는 값으로, `must_change_password` 해제·만료 시각 제거, `auth_version` +1, 세션 종료, 관리 이력 `password_revoke`.
  상태(이용 중지)는 그대로. 재개해도 로그인할 수 없고(일반 401 — 폐기 여부를 알리지 않음), 로그인하려면 재발급한다. 이용 중(임시 포함)·임시 상태가 아닌
  중지·승인 대기·반려·최고 관리자는 409이고 아무것도 바꾸지 않는다(거절 이력만).
- 동시 요청: 폐기↔재개·폐기↔재발급·같은 버전 폐기 2건은 하나만 반영된다 — 상태 변경(승인·반려·중지·재개)도 요청이 읽은 인증 버전을 조건으로 한다.
  시험: `backend/tests/test_password_revoke.py`(실제 요청 경로·barrier, SQLite·PostgreSQL 모두).
- downgrade 보호는 그대로다 — 폐기된 계정은 임시 상태가 아니므로 세지 않는다(플래그만 지우는 경로는 여전히 없다). 폐기는 이용 중지된 계정에만 하므로
  downgrade를 위해 이용 중 계정을 중지시키는 일은 하지 않는다(12-4 처리 순서).

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

### 4차 — 백업 내부 DB 검증 (2026-10-14, 소스 `2d28225`)

명령 대역 시험 `scripts/test_deploy.sh`: 기존 10개 + 새 13개 = **23개 모두 통과**. 모든 사례에서 운영 볼륨은 읽기 전용으로만 마운트되고 백업 DB 검사 컨테이너에는 운영 볼륨이 마운트되지 않음을 함께 확인 (일부러 어긴 스크립트 사본 2개로 이 확인이 실제로 실패를 잡는 것도 확인).

| 시험 (명령 대역) | 결과 |
| --- | --- |
| 정상 백업 / 핵심 테이블 일부가 없는 이전 리비전 | 검증 통과 → 전환, 요약 `verified yes` / `[없음]`으로 기록하고 통과 |
| 반영 전 리비전이 인자와 다름 | 정지 전에 중단 (정지·백업·전환 없음) |
| 운영 볼륨 조회 실패 · 결과 누락 | 백업·전환 없음 · 리비전 확인 후 재개 |
| 백업 DB 손상(원본 정상) · 리비전 다름 · 행 수 다름 · 테이블 누락 · 추출 실패 · SQLite 조회 실패 · 결과 누락 · sha256 불일치 | 전환(`up`) 호출 없음 · 리비전 확인 후 재개 · 요약 `verified no` |

실제 Docker 격리 시험 (프로젝트 `moajwo-deploytest`, 127.0.0.1:8084, 새로 만든 합성 자료 볼륨 — 사용자 4·관리 이력 8·세션 2·매핑 확정 6·원본 파일 5개, 반영 전 이미지 = 운영과 같은 이미지 `30e3602b78c4`·`072c9d015e10`(별칭 태그), 롤백 태그는 `moajwo-deploytest-*`로만 생성, 배포 스크립트는 격리 프로젝트·볼륨이 아니면 실행을 거부하는 실행기를 거쳐서만 호출): **65개 확인 모두 통과**. 비교 로그를 한 번에 쓰도록 바꾸기 전(`6d8de1d`)과 후(`2d28225`) 두 번 실행해 둘 다 통과했고, 정상 전환의 중단은 33초 → 26초, 5단계는 15초 → 8초로 줄었습니다. 백업 DB 고장은 시험 전용 이미지가 압축 직전의 **복사본**에만 주입하고(원본 볼륨은 읽기 전용), 시나리오마다 전후 상태를 비교했습니다.

| 시나리오 | 중단 사유 / 결과 | 실패 전후 상태 |
| --- | --- | --- |
| 반영 전 리비전 불일치 (인자 `0016_account_status_roles`) | 2단계에서 중단 | 정지·백업 없음, 컨테이너 시작 시각·리비전 0017·모든 테이블·파일·세션 그대로 |
| 백업 DB 손상, 원본 정상 (압축 직전 복사본에 고장 주입) | `백업 DB 무결성 검사 실패` | 6단계 없음, 리비전 0017 확인 후 **같은 컨테이너** 재개(반영 전 이미지), 원본 DB 무결성 ok, 모든 테이블 행 수·내용 해시·파일·세션 그대로, 요약 `verified no` |
| 백업 DB 행 수 다름 (복사본에서 매핑 확정 1행 삭제) | `[불일치] mapping_decisions (핵심)` | 위와 같음 |
| 백업 DB를 열 수 없음 (복사본 머리 덮어씀) | `백업에서 DB를 꺼내거나 여는 데 실패` | 위와 같음 |
| 정상 | 모든 테이블 `[일치]` → 전환 완료 | 0017 → 0018, 새 이미지, 기존 테이블 행 수·내용 해시·파일·세션 그대로, `mapping_analyses` 0행, 요약 `verified yes` |

### 5차 — 8장 반영·롤백 (포트 80 → 8080) (2026-10-23, 격리 러너 ubuntu-24.04, run 39938263086)

같은 Compose 프로젝트 `moajwo`·서비스 이름·볼륨 `moajwo_moajwo-data`로: 이전 = main `adebb6c` → 후보 `9c3842b` 반영(`scripts/deploy.sh` 그대로) → 8장 롤백 명령 그대로(이전 이미지 + `rollback-<시각>.compose.yml`) → 다시 후보 반영 → 트리가 다른 승인 SHA. 합성 계정·CSV·묶기 결과 1건. 러너에 맞춘 것은 두 가지뿐입니다: `pwd -W`(Git Bash) 대신 `pwd`, 백업 컨테이너(uid 1000)가 러너의 `backups/`에 쓸 수 있게 폴더 권한 (Docker Desktop의 바인드 마운트는 필요 없음).

| 단계 | 결과 |
| --- | --- |
| 이전 (main) | 웹·`/api/health`(프록시)·backend health 200, 합성 계정 로그인·결과 내려받기, frontend uid 0 · `80/tcp → 127.0.0.1:8080` |
| 후보 반영 | 종료 0, **중단 9초**, `포트 연결: frontend 127.0.0.1:8080:80/tcp → 127.0.0.1:8080:8080/tcp`, frontend uid 101 · `8080/tcp → 127.0.0.1:8080` |
| 롤백 | `config`: backend 대상 8000·공개 8000 하나, frontend 대상 **80**·공개 8080 하나 → `up` 0, 이전 이미지 ID 그대로, uid 0 · `80/tcp → 127.0.0.1:8080` |
| 다시 후보 | 종료 0, **중단 10초**, 포트 연결 80 → 8080 |
| 모든 단계 공통 | 같은 계정 로그인 200·역할 그대로, 실행 기록 1건, **반영 전에 만든 결과를 다시 내려받아 해시 같음**, 원본 미리보기 200, 행 수(users 1·source_files 1·runs 1·mapping_decisions 3)·리비전 `0019_analysis_runs` 같음, 원본·결과 파일 해시 목록 같음, 볼륨은 `moajwo_moajwo-data` 하나 (새 빈 볼륨 없음) |
| 승인 SHA의 트리가 다름 (main SHA를 승인으로) | 1단계에서 종료 1 '배포 대상의 내용이 승인한 커밋과 다름', 컨테이너 그대로 |

- 1회차(run 39937781741)는 백업 컨테이너가 러너의 `backups/`에 쓰지 못해 5b단계에서 멈췄고, 스크립트가 리비전 그대로를 확인한 뒤 기존 컨테이너를 재개했습니다 (설계대로). 폴더 권한을 맞춘 2회차가 위 결과입니다.
- 증거: 저장소 밖 `backups/review-20261023b/deploy-rollback/` (합성 자료 압축 백업은 올리지 않음).

### 6차 — 통합 후보 반영·롤백 (0019 → 0020, downgrade 포함) (2026-10-25, 격리 러너 ubuntu-24.04, run 40170744105)

같은 Compose 프로젝트(`moajwo`)·서비스·볼륨: main `adebb6c` 기동·합성 자료(최초 소유자·CSV·매핑 확정·묶기 결과) → `bash scripts/deploy.sh 969d08e 0019_analysis_runs` → 8장 롤백(서버 정지 → 끝난 담당의 '실행 중' 행 하나(합성) → 정리 전 downgrade → `recover` → `status` → 새 이미지로 downgrade → 이전 이미지 + 이전 포트) → 다시 반영 → 트리가 다른 승인 SHA. 스크립트 `diagnostics/ci/deploy_rollback_check.sh`.

| 단계 | 결과 |
| --- | --- |
| 반영 | 종료 0, `0020_run_worker_lease (head)`, 중단 8초, frontend 포트 `127.0.0.1:8080:80` → `127.0.0.1:8080:8080` |
| 반영 뒤 | 같은 계정 로그인·역할, 실행 기록 1건, 반영 전에 만든 결과를 다시 내려받아 해시 같음, 원본 미리보기 200, 행 수(users 1·source_files 1·runs 1·mapping_decisions 3)·원본·결과 파일 해시 목록 같음, 볼륨은 `moajwo_moajwo-data` 하나 |
| 정리 전 downgrade | 거절 (종료 1) "이 판에서 시작한 실행 중인 결과 실행이 1건 …" — 리비전 그대로 |
| `recover` (one-off 컨테이너) | 종료 0 — 실패 1건(끝난 담당, 저장 전), `blocking` 0 · `status`: 실행 중 0, 결과 확인 필요 0 |
| downgrade (새 이미지, one-off) | 종료 0, `Running downgrade 0020_run_worker_lease -> 0019_analysis_runs` |
| 롤백 뒤 (이전 이미지 + 이전 포트 80) | 웹·프록시 health 200, 같은 계정 로그인, 결과 다시 내려받기 해시 같음, 실행 기록 2건(정리된 실패 1건 포함), `0019_analysis_runs`, frontend 대상 포트 80 하나 |
| 다시 반영 | 종료 0, 중단 9초, `0020_run_worker_lease` |
| 트리가 다른 승인 SHA | 1단계에서 멈춤 ("내용이 승인한 커밋(adebb6c…)과 다름"), 컨테이너 그대로 |

- 리눅스 러너 맞춤 두 가지(`pwd -W` → `pwd`, 백업 폴더 권한)는 5차와 같습니다.
- **Windows Docker Desktop (이 PC, Docker 27.5.1 · Compose 2.32.4-desktop.1, 2026-10-25) — 명령 수준만:** 일회용 볼륨·일회용 폴더로, 운영 컨테이너·볼륨은 쓰지 않았습니다 (`backups/review-20261025/rc/docker-desktop-check/`). `ports: !override` 덧붙임 파일은 `docker compose config`에서 frontend를 대상 80 하나로 바꾸고(덧붙임 없이 쓰면 8080에 대상 8080·80 두 개), `pwd -W` 경로 바인드 마운트에 uid 1000 컨테이너가 백업 단계와 같은 `tar`·`gzip -t`·`sha256sum`으로 쓰고(호스트에서 다시 계산한 해시 같음) 읽기 전용으로 꺼내 `integrity_check` ok. 앞의 세 차례는 확인 스크립트 오류로 실패했고(합성 자료 준비 두 번 — 빈 볼륨을 이미지에 없는 경로에 붙여 root 소유, Windows 경로 변환 한 번) 그 기록도 남겼습니다. `scripts/deploy.sh` 전체를 이 PC에서 돌리지는 않았습니다 (운영과 같은 엔진·포트를 써야 함).

### 7차 — 4장 B·R·A 단계 상자 그대로 (2026-10-26, 격리 러너 ubuntu-24.04)

`diagnostics/ci/rollback_doc_rehearsal.sh`가 후보 트리의 이 문서에서 단계 상자(`<!-- rollback-step:N -->` 0~7·R·A)를 꺼내 실행합니다. 명령을 옮겨 적거나 고치지 않고, 단계마다 `[N 완료]`가 찍혀야 다음 단계로 갑니다.
- 같은 Compose 프로젝트(`moajwo`)·서비스·볼륨을 씁니다.
- 합성 자료(최초 소유자·CSV·매핑 확정·묶기 결과)를 씁니다. 비밀번호는 실행 때 만들고 출력하지 않습니다.
- 러너 맞춤은 5·6차와 같은 두 가지(deploy.sh의 `pwd -W`, 백업 폴더 권한)이고, 단계 상자는 맞춤 없이 돕니다.

순서:
1. main `adebb6c` 기동 → `scripts/deploy.sh <후보> 0019_analysis_runs`
2. 실패 경로 6가지
3. 정상 0~7단계(결과 확인 필요 행·이전 판 실행 중 행 포함) → 되돌린 상태에서 0·1단계를 다시 붙여 넣기
4. 다시 반영 → 이 정리 전 문서(`4d245b8`) 9장 명령의 수정 전 모습 → 6·7단계로 바로잡기
5. 앞 반영이 있는 상태에서 전환에 실패한 반영 되돌리기와 다시 붙여 넣기 — 새 backend가 마이그레이션 뒤 끝나게 하는 덧붙임 Compose는 deploy.sh에만 줌
6. 전환 도중에 끊긴 반영 되돌리기 — 위와 같은 실패 반영의 로그에서 전환 실패 줄부터 끝을 지워 만듦
7. 새 반영 직후 R(롤백 태그를 지운 뒤)·A

| 확인 | 결과 (`ce75199`, run 40265065753 — 판정 40건 모두 yes, 상자 내용 해시 `4c2e357162d5588d`) |
| --- | --- |
| 이전 포트 파일 없음 | 1단계 정지 전에 멈춤 — 서비스·리비전·이미지 그대로 |
| 데이터 볼륨을 쓰는 다른 컨테이너 | 1단계 정지 뒤 확인에서 멈춤 — 백업·정리 없음 |
| 백업 폴더에 쓸 수 없음 | 2단계에서 `[중단] 백업 만들기·압축 검사 실패` — 정리·downgrade 없음 |
| 다른 디스크의 새 신호 실행 | 3단계 `recover` 종료 2에서 멈춤 — downgrade·이미지 전환 없음 |
| 정리할 행이 있는데 DB에 쓸 수 없음 | 3단계 종료 1 — 같음 |
| 4단계 뒤 담당 있는 실행 중 행이 생김 | 5단계 downgrade 거절 '이 판에서 시작한 실행 중인 결과 실행이 1건' — 이전 이미지로 바꾸지 않음 |
| 정상 0~7단계 | `0019_analysis_runs`, latest = 롤백 태그 이미지, frontend `80/tcp`, 같은 계정 로그인 200, 반영 전 결과 다시 내려받기 해시 같음. 4단계 분류 `running(담당 있음) [] · running(이전 판) [n] · check [m]`이고, downgrade 뒤에도 그 두 행은 `check`·`running` 그대로 |
| 되돌린 뒤 0·1단계를 다시 붙여 넣음 | 1단계가 '이미 되돌렸거나 이 반영이 이미지를 바꾸지 않음'으로 정지 전에 멈춤 — 서비스 그대로 |
| 이 정리 전 9장 명령 (덧붙임 없는 up) | 종료 0인데 웹 000·로그인 0 (이전 frontend가 80에서 듣는데 새 설정은 8080 대상) → 4장 B 6·7단계로 웹 200·로그인 200·결과 같음 |
| 전환에 실패한 반영 (앞 반영 로그가 더 있음) | 0단계가 그 반영의 '[중단] … 4장 (…)' 줄을 읽고(앞 반영의 배포 단위를 고르지 않음) 지금 DB 리비전 0020에서 0~7단계 → 0019·그 반영의 롤백 태그 이미지·80/tcp, 로그인·결과 같음. 되돌린 뒤 다시 붙여 넣은 1단계는 정지 전에 거절 |
| 전환 도중에 끊긴 반영 | 0단계가 '[주의] 반영 로그가 전환 도중에 끝남'과 deploy.sh 2단계 줄의 시각·반영 전 리비전으로 0~7단계 → 0019·롤백 태그 이미지, 로그인·결과 같음 |
| R | 롤백 태그를 지우면 1단계가 '롤백 태그 … 이미지 없음'으로 정지 전에 멈춤 → `REBUILD_FROM` 없이 R은 거절 → `REBUILD_FROM=adebb6c`로 R: 다시 만든 이미지 ID가 이전 포트 파일에 기록한 반영 전 이미지와 같음, 작업 트리 남지 않음 → 다시 R은 '이미 있음'으로 거절 |
| A | 서비스가 떠 있으면 거절(볼륨 그대로) → B 0·1단계(R이 만든 태그로 확인) → 기록한 sha256과 다른 백업 거절·앞 롤백 때의 2단계 백업은 '이 반영의 백업이 아님'으로 거절(볼륨 그대로) → 반영 직전 백업 복원(`_pre_rollback_<시각>/`에 지금 자료 보존 — 반영 뒤 생긴 실행 기록 8행은 보존 폴더에, 복원한 DB는 6행), `[A 완료]`가 다음 단계로 'B 6·7단계'를 알림 → 같은 A 다시 돌리기는 거절 → 6·7단계 뒤 로그인·결과 같음 |

- 같은 상자 내용 해시의 뒤 후보는 이 결과를 그대로 씁니다 — 최종 후보에서 다시 돌린 기록은 보고서(`diagnostics/REPORT-20261026.md`) 13장.
- 앞 차례:
  - 임시 후보 `a1c4810`(run 40245599465): 실패 경로·정상·바로잡기는 기대대로였고, 판정 'no' 5건은 리허설의 기대 문자열 결함입니다.
  - `a4da1f1`(run 40246803091): 모두 yes.
  - `69ec619`(run 40251110098): 새 시나리오는 모두 yes였고, A 거절 2건 'no'는 리허설이 0단계의 안내 줄을 A의 문구로 잘못 집은 결함입니다.
  - `ad0b3f8`(run 40252469189): 판정 19건 모두 yes. 이 정리 전 상자 기준이며, 그 뒤 재검토가 이미 되돌린 상태 거절·끊긴 로그·R·A 확인을 더했습니다.
- 7차까지는 백업 DB 불일치(VERIFY_PY) 경로가 리허설에 없고(명령 수준의 임시 SQLite로만 확인), 전환 실패를 `restart: "no"`로 만들어 운영 설정(`unless-stopped`)의 재시작 반복 상태는 시험하지 않았습니다. 이 둘은 8차에서 더했습니다.

### 8차 — 경계 2가지 (2026-10-26, 같은 러너 · 단계 상자는 7차와 같음)

`rollback_doc_rehearsal.sh` 9장이 7차 순서 뒤에 덧붙습니다. 상자 명령은 그대로이고, 상황만 만듭니다.

| 확인 | 결과 (사전 점검 `5971e71` = `2b1a995` + 이 리허설 커밋, run 40274942019 — 판정 52건 모두 yes) |
| --- | --- |
| 운영 설정(`restart: unless-stopped`) 그대로 새 backend가 마이그레이션 뒤 끝나 재시작을 반복하는 전환 실패 | deploy.sh의 `up -d --wait`는 스스로 끝났고(종료 1, 900초 제한에 걸리지 않음) '[중단] 전환 실패 … 4장 (…)' 안내를 남겼습니다. 그 상태는 backend 재시작 반복·리비전 `0020`입니다 |
| 그 상태에서 데이터 볼륨을 쓰는 다른 재시작 컨테이너가 있음 | 1단계가 `stop` 뒤 확인에서 '데이터 볼륨을 쓰는 컨테이너가 남음: rb-loop'으로 멈춤 — 백업·정리 없음, 리비전 `0020` 그대로 |
| 그 컨테이너를 지운 뒤 1단계 | backend가 'exited'(재시작 반복이 아님)이고 볼륨을 쓰는 컨테이너 0개 → `[1 완료]` → 2~7단계: `0019_analysis_runs`, latest = 롤백 태그 이미지, `80/tcp`, 로그인 200, 반영 전 결과 다시 내려받기 해시 같음 |
| 백업 DB ≠ 볼륨 DB (1단계로 멈춘 상태에서 Docker 밖 호스트 프로세스가 볼륨 DB에 표를 만들고 0.05초마다 한 행씩 쓰는 동안 2단계) | '[중단] 백업 검증 실패 — 정리·downgrade를 하지 않음 (… 는 쓰지 않음)'으로 멈춤. `.sha256` 표시가 생기지 않았고(새 백업 파일 1개는 쓰지 않은 채 남음), 리비전 `0020`·recover 기록 없음. 지금 판 재개 뒤 로그인 200·결과 같음 |

- 호스트 프로세스의 쓰기는 1단계가 보지 못하는 경우(Docker 밖)를 흉내 낸 것입니다. 운영에서는 그런 프로세스를 먼저 찾아 멈추고 1단계부터 다시 합니다.
- 최종 후보에서 다시 돌린 기록은 보고서(`diagnostics/REPORT-20261026.md`) 7장·13장.
- 증거: 저장소 밖 `backups/review-20261026/ci-rollback-doc-p1`·`-p2`·`-p3`·`-cand-ad0b3f8`·`-cand-8ac8ae0`·`-cand-ce75199`·`-pre-5971e71`. 합성 자료 압축 백업은 올리지 않았습니다.

## 알려진 제한

- 의미가 불명확한 월별 실적 열이 시점 값 항목(차량·인구 등)으로 매핑되고 합계가 가장 늦은 시점 값과 같으면 누적 예외로 오판될 수 있습니다.
- 주민등록번호가 탐지된 파일의 과거 기록에서는 같은 형태(13자리, 생년월일·성별 자리 유효)의 정상 숫자도 마스킹될 수 있습니다.
- 배포 명령과 스크립트는 Git Bash에서 검증했습니다 (PowerShell 미검증).
- 운영 반영 이력: 0015는 2026-10-12, 0016·0017은 2026-10-14에 반영했습니다 (2026-10-12에 이 문서에 적었던 '0016 운영 미반영'은 그 시점 기록). **현재 운영 리비전은 이 문서가 아니라** `docker compose exec -T backend alembic current`로 확인합니다 — 2026-10-14 조회 기준 `0017_auth_version (head)`, 0018은 반영 전입니다.
