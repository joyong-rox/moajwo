#!/usr/bin/env bash
# 운영 반영 (Git Bash). 사용법: scripts/deploy.sh <승인한 PR HEAD 커밋 SHA> <반영 전 리비전>
#   예: scripts/deploy.sh <SHA> 0021_run_request_key   (2026-10-28 이후 운영 리비전. 반영 전 리비전이 다르면 정지하지 않고 멈춘다)
#
# 실패하면 그 자리에서 멈춘다. set -e에 맡기지 않고, 판단에 쓰는 모든 조회는 **종료 코드와 필수 결과**를 함께 검사한다.
# (조회가 실패한 것과, 조회는 성공했는데 결과가 비어 있는 것을 구분한다.)
#   1) 승인 변경과 배포 대상 일치 (병합 방식에 따라 main SHA는 달라질 수 있어 트리(파일 내용)를 비교)
#   2) 미커밋 변경 없음 — 저장소 어디에도 추적하지 않는 파일이 없어야 함(.gitignore 대상 제외 — 빌드·검증 입력, 그리고 Windows가 현재
#      폴더에서 먼저 찾는 docker.exe 같은 실행 파일), 변경을 숨기는 git 표시(assume-unchanged·skip-worktree)·filter 속성 없음,
#      Compose 기본 파일 찾기를 바꾸는 것(다른 이름·덧붙임 Compose 파일, 셸·.env의 COMPOSE_FILE)도 없어야 함
#   3) 자원 확인(scripts/memory_guard.py — 기준 미달·측정 실패면 멈춤) → 후보 이미지를 하나씩 빌드(latest가 아닌 cand-<시각> 태그,
#      빌드 동안 여유 메모리 기록) → 반영 전 이미지 검증(scripts/image_gate.sh — 완성 이미지 목록이 검증한 목록 deploy/expected-images/와
#      같음 · 자원 확인 · 격리 기동 점검)과 그 통과 기록(gate.result의 두 이미지 ID) 확인. 실패하면 서비스·latest 태그 그대로 (11장)
#   4) 서비스 정지 성공 · 실행 중 서비스 0개 · 데이터 볼륨을 쓰는 컨테이너 0개 (각 조회가 성공한 경우에만 '없음'으로 판단)
#   5) 백업과 검증 — 운영 볼륨 값과 백업 안의 값을 구분해 기록하고 비교한다
#      a. 정지 상태의 운영 DB(읽기 전용): 리비전 · 모든 테이블의 행 수
#      b. 볼륨 백업 생성 · gzip 압축 검사 · 목록에 moajwo.db 있음 · sha256 기록
#      c. 백업에서 DB를 꺼내 별도 임시 위치(운영 볼륨을 마운트하지 않은 one-off 컨테이너의 /tmp)에 두고 읽기 전용으로
#         무결성(integrity_check) · 리비전 · 모든 테이블의 행 수 확인
#      d. a와 c 비교: 리비전 = 반영 전 리비전, 테이블 목록·행 수 모두 같음 (비교 대상은 고정하지 않고 원본 DB의 테이블 전체,
#         문서의 핵심 테이블은 따로 표시)
#      e. 백업 파일 sha256을 호스트에서 다시 계산해 b의 기록과 같음
#      하나라도 실패하거나 필요한 결과가 빠지면 전환하지 않는다. 백업 검증 중 운영 DB는 읽기만 한다.
#   → 4·5가 실패하면, 마이그레이션이 시작되지 않았음(리비전 그대로)을 **확인할 수 있을 때만** 기존 컨테이너를 다시 시작한다.
#     확인할 수 없으면 재시작하지 않고 현재 상태와 수동 복구 절차를 보고한다.
#   6) 백업 성공을 확인한 뒤에만 3단계에서 검증한 이미지 ID에 latest 태그를 옮기고, 다시 빌드하지 않고(up --no-build) 전환한다.
#      전환 후 실행 중 이미지가 검증한 ID와 같고 리비전·이미지 조회가 모두 성공해야 '전환 완료'로 보고한다.
#   롤백 단위 = 이전 이미지(롤백 태그) + 이전 컨테이너의 포트 연결(backups/rollback-<시각>.compose.yml) + 백업.
#   이미지만 되돌리면 안 되는 반영(예: frontend 컨테이너 포트 80 → 8080)이 있어, 2단계에서 실행 중 컨테이너의 실제 포트
#   연결을 Compose 덧붙임 파일(ports: !override)로 남긴다 — docs/DEPLOY.md 4장·8장.
#
# 격리 환경 검증용 환경변수 — 운영 반영에서는 하나라도 있으면 1단계에서 멈춘다. 격리 리허설은 DEPLOY_REHEARSAL=1을 함께 지정:
#   DEPLOY_COMPOSE_ARGS  docker compose 인자 (예: "-f rehearsal/docker-compose.yml")
#   DEPLOY_VOLUME        데이터 볼륨 이름 (기본 moajwo_moajwo-data)
#   DEPLOY_BACKUP_DIR    백업 폴더 (기본 backups)
#   DEPLOY_BACKUP_IMAGE  백업 one-off에 쓸 이미지 (기본: 반영 전 backend 이미지)
#   DEPLOY_TAG_REPO      롤백 태그 이름 앞부분 (기본 moajwo → moajwo-backend:rollback-<시각>). 격리 시험이 운영 이름으로 태그를 만들지 않게
# 3단계 검증(scripts/image_gate.sh)·검증 목록(deploy/expected-images/)·자원 기준(scripts/memory_guard.py)을 바꾸거나 건너뛰는 환경
# 변수는 없다 — 모두 1단계에서 승인한 트리와 같다고 확인한 이 저장소의 파일이다 (명령 대역 시험은 임시 저장소에 대역 파일을 커밋해 쓴다).
set -uo pipefail
export MSYS_NO_PATHCONV=1
export NoDefaultCurrentDirectoryInExePath=1   # Windows: 실행 파일을 현재 폴더(저장소)에서 먼저 찾지 않게 — 검증 도구(Python)의 docker 포함
# 셸 함수·시작 파일이 이 스크립트와 검증 스크립트의 명령(docker·git 등)을 바꾸지 못하게: 내보낸 함수·BASH_ENV·ENV가 있거나
# 시작할 때 이미 정의된 함수가 있으면 멈춘다 (새 Git Bash 창에서 다시 실행)
if env | grep -qE '^(BASH_ENV|ENV)=|^BASH_FUNC_' || [ -n "$(declare -F)" ]; then
  echo "[중단] 내보낸 셸 함수·BASH_ENV·ENV가 있음 — 새 Git Bash 창에서 다시 실행: $(env | grep -oE '^(BASH_ENV|ENV)=|^BASH_FUNC_[^=]*' | tr '\n' ' ')$(declare -F | tr '\n' ' ')"
  exit 1
fi

APPROVED="${1:-}"
EXPECT_PREV="${2:-}"
[ -n "$APPROVED" ] && [ -n "$EXPECT_PREV" ] || { echo "사용법: scripts/deploy.sh <승인한 PR HEAD 커밋 SHA> <반영 전 리비전>"; exit 2; }
# 문서에서 비교 결과를 따로 보여 줄 핵심 테이블 (이 리비전에 없으면 '없음'으로 기록 — 비교 자체는 원본 DB의 모든 테이블)
KEY_TABLES="users account_events auth_sessions mapping_decisions source_files"
# 리비전·행 수(전체 테이블)·(full이면) 무결성·저널 모드를 읽기 전용으로 읽는다. 마지막 줄 'end ok'가 없으면 결과 누락으로 본다.
CHECK_PY='import sqlite3, sys
c = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
print("revision", c.execute("select version_num from alembic_version").fetchone()[0])
if sys.argv[2] == "full":
    print("integrity", c.execute("pragma integrity_check").fetchone()[0])
    print("journal", c.execute("pragma journal_mode").fetchone()[0])
names = [r[0] for r in c.execute("select name from sqlite_master where type = ? and name not like ? order by name", ("table", "sqlite_%"))]
for n in names:
    print("rows", n, c.execute("select count(*) from [" + n + "]").fetchone()[0])
print("end ok")'
read -r -a COMPOSE_ARGS <<< "${DEPLOY_COMPOSE_ARGS:-}"
VOLUME="${DEPLOY_VOLUME:-moajwo_moajwo-data}"
BACKUP_DIR="${DEPLOY_BACKUP_DIR:-backups}"
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR" || { echo "[중단] 백업 폴더를 만들 수 없음: $BACKUP_DIR"; exit 1; }
LOG="$BACKUP_DIR/deploy-$TS.log"
if ! WIN_BACKUP_DIR=$(cd "$BACKUP_DIR" && { pwd -W 2>/dev/null || pwd; }) || [ -z "$WIN_BACKUP_DIR" ]; then   # Git Bash는 Windows 경로
  echo "[중단] 백업 폴더의 Windows 경로를 알 수 없음: $BACKUP_DIR" | tee -a "$LOG"
  exit 1
fi

dc() { docker compose "${COMPOSE_ARGS[@]}" "$@"; }
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
fail() { log "[중단] $*"; exit 1; }
manual_recovery() {  # 상태를 확인할 수 없어 자동으로 재개하지 않을 때
  log "[수동 확인 필요] 자동으로 서비스를 재개하지 않았습니다. 현재 상태를 확인한 뒤 판단하세요:"
  log "  docker compose ps -a                       # 컨테이너 상태"
  log "  docker ps --filter volume=$VOLUME          # 데이터 볼륨을 쓰는 컨테이너"
  log "  (리비전) docker run --rm --entrypoint python -v $VOLUME:/data:ro ${OLD_B:-<이전 backend 이미지>} -c \"import sqlite3; print(sqlite3.connect('file:/data/moajwo.db?mode=ro', uri=True).execute('select version_num from alembic_version').fetchone()[0])\""
  log "  리비전이 ${PREV:-<반영 전 리비전>}이면: docker compose start backend frontend   (새 이미지로 바꾸는 up은 쓰지 않음)"
  log "  다르면 docs/DEPLOY.md 4장 롤백 절차"
}
container_id() {  # 서비스 컨테이너 ID (조회 실패·결과 없음이면 실패)
  local id
  id=$(dc ps -q "$1" 2>>"$LOG") || return 1
  [ -n "$id" ] || return 1
  printf '%s\n' "$id"
}
image_of() {  # 컨테이너가 쓰는 이미지 ID (sha256:… 형식이어야 함)
  local img
  img=$(docker inspect "$1" --format '{{.Image}}' 2>>"$LOG") || return 1
  case "$img" in sha256:*) printf '%s\n' "$img" ;; *) return 1 ;; esac
}
current_revision() {  # 실행 중 backend의 alembic current 첫 줄 (조회 실패·빈 결과면 실패)
  local out
  out=$(dc exec -T backend alembic current 2>>"$LOG") || return 1
  out=$(printf '%s\n' "$out" | head -1)
  [ -n "$out" ] || return 1
  printf '%s\n' "$out"
}
revision_in_volume() {  # 볼륨의 DB 리비전을 서버 없이 읽는다 (실패·빈 결과면 실패)
  local out
  out=$(docker run --rm --entrypoint python -v "$VOLUME:/data:ro" "$1" -c \
    "import sqlite3; print(sqlite3.connect('file:/data/moajwo.db?mode=ro', uri=True).execute('select version_num from alembic_version').fetchone()[0])" \
    2>>"$LOG") || return 1
  [ -n "$out" ] || return 1
  printf '%s\n' "$out"
}
ports_of() {  # 컨테이너의 호스트 포트 연결 ('127.0.0.1:8080:80/tcp' 꼴, 공백 구분). 조회 실패·결과 없음이면 실패
  local out
  out=$(docker inspect "$1" --format \
    '{{range $p, $b := .HostConfig.PortBindings}}{{range $b}}{{.HostIp}}:{{.HostPort}}:{{$p}} {{end}}{{end}}' 2>>"$LOG") || return 1
  out=$(printf '%s' "$out" | tr -s ' ' | sed 's/^ //; s/ $//')
  case "$out" in *:*:*) printf '%s\n' "$out" ;; *) return 1 ;; esac
}
wait_healthy() {  # backend healthy 대기 (최대 90초). 컨테이너 조회가 실패하면 실패
  local cid status
  cid=$(container_id backend) || return 1
  for _ in $(seq 1 90); do
    status=$(docker inspect "$cid" --format '{{.State.Health.Status}}' 2>>"$LOG") || return 1
    [ "$status" = "healthy" ] && return 0
    sleep 1
  done
  return 1
}
restart_old() {  # 정지·백업 단계 실패: 마이그레이션 미시작을 확인할 수 있을 때만 기존 컨테이너 재시작 (up이 아니라 start)
  local now_rev cid img
  if ! now_rev=$(revision_in_volume "$OLD_B"); then
    log "[주의] 볼륨의 DB 리비전을 확인할 수 없음"
    manual_recovery
    return 1
  fi
  if [ "$now_rev" != "$PREV" ]; then
    log "[주의] 리비전이 반영 전과 다릅니다 ($PREV → $now_rev). 자동으로 재시작하지 않습니다."
    manual_recovery
    return 1
  fi
  log "마이그레이션이 시작되지 않았음 확인 (리비전 $now_rev) → 기존 컨테이너 재시작"
  if ! dc start backend frontend >>"$LOG" 2>&1; then
    log "[주의] 기존 컨테이너 재시작 명령 실패"
    manual_recovery
    return 1
  fi
  if ! wait_healthy; then
    log "[주의] 기존 backend가 healthy인지 확인할 수 없음"
    manual_recovery
    return 1
  fi
  if ! cid=$(container_id backend) || ! img=$(image_of "$cid"); then
    log "[주의] 재시작한 backend의 이미지를 확인할 수 없음"
    manual_recovery
    return 1
  fi
  log "기존 서비스 재개 (backend ${img:7:12})"
}
abort_after_stop() {  # 정지 이후 실패: 사유 기록 → (확인되면) 기존 서비스 재개 → 실패로 끝냄
  local resumed
  log "[중단] $*"
  if restart_old; then resumed="기존 컨테이너 재개 확인"; else resumed="기존 컨테이너 재개 안 됨 또는 확인 불가 (위 [주의]·수동 확인)"; fi
  # 컨테이너 재개와 latest 태그 상태는 따로 판단해 따로 적는다 (6단계 태그 단계에서 멈췄을 때만 태그 상태가 있음)
  [ -z "${RETAG_NOTE:-}" ] || log "결과: $resumed · latest 태그: $RETAG_NOTE"
  exit 1
}

log "== 1. 승인 변경과 배포 대상 확인"
DEPLOY_VARS=$(env | grep -oE '^DEPLOY_[A-Z_]+' | grep -vx DEPLOY_REHEARSAL | sort | paste -sd' ')
log "격리 시험용 DEPLOY_* 변수: ${DEPLOY_VARS:-없음}$([ "${DEPLOY_REHEARSAL:-}" = 1 ] && echo ' (DEPLOY_REHEARSAL=1 — 격리 리허설, 운영 반영 아님)') · COMPOSE_* 변수: $(env | grep -oE '^COMPOSE_[A-Z_]+' | sort | paste -sd' ' | grep . || echo 없음)"
# Compose 파일·볼륨·백업 위치를 바꾸는 변수는 승인하지 않은 설정으로 빌드·검증·전환하게 할 수 있다 — 운영 반영에서는 쓰지 않는다
[ -z "$DEPLOY_VARS" ] || [ "${DEPLOY_REHEARSAL:-}" = 1 ] \
  || fail "DEPLOY_* 변수가 있음 ($DEPLOY_VARS) — 운영 반영에서는 쓰지 않음. 격리 리허설이면 DEPLOY_REHEARSAL=1을 함께 지정"
git rev-parse --verify --quiet "$APPROVED^{commit}" >/dev/null || fail "승인한 커밋을 찾을 수 없음: $APPROVED (git fetch 필요)"
HEAD_SHA=$(git rev-parse HEAD 2>>"$LOG") && [ -n "$HEAD_SHA" ] || fail "배포 대상 커밋(HEAD)을 읽을 수 없음"
APPROVED_TREE=$(git rev-parse "$APPROVED^{tree}" 2>>"$LOG") && [ -n "$APPROVED_TREE" ] || fail "승인한 커밋의 트리를 읽을 수 없음"
HEAD_TREE=$(git rev-parse "HEAD^{tree}" 2>>"$LOG") && [ -n "$HEAD_TREE" ] || fail "배포 대상의 트리를 읽을 수 없음"
if [ "$APPROVED_TREE" != "$HEAD_TREE" ]; then
  git diff --stat "$APPROVED" HEAD 2>>"$LOG" | tail -5 | tee -a "$LOG"
  fail "배포 대상($HEAD_SHA)의 내용이 승인한 커밋($APPROVED)과 다름"
fi
# git 조회는 fsmonitor 훅·untracked 캐시 없이 직접 훑게 한다 (훅·캐시가 바뀐 파일을 숨기지 못하게)
GITQ=(git -c core.fsmonitor=false -c core.untrackedCache=false)
TRACKED=$("${GITQ[@]}" status --porcelain --untracked-files=no 2>>"$LOG") || fail "git 상태를 읽을 수 없음"
[ -z "$TRACKED" ] || fail "추적 중인 파일에 미커밋 변경이 있음"
# 추적하지 않는 파일은 저장소 어디에 있어도 멈춘다 (.gitignore 대상인 backups/ 등은 제외): 빌드 입력(backend·frontend), 검증 입력
# (scripts·deploy — json.py 같은 파일이 표준 모듈을 가릴 수 있음), Compose가 저절로 읽는 덧붙임 파일, 그리고 Windows가 PATH보다 먼저
# 현재 폴더(저장소)에서 찾는 docker.exe — 검증 도구(Python)만 그것을 실행하게 될 수 있다
COMPOSE_OTHER="docker-compose.override.yml docker-compose.override.yaml compose.override.yml compose.override.yaml compose.yml compose.yaml docker-compose.yaml"
BUILD_SRC=$("${GITQ[@]}" status --porcelain --untracked-files=all 2>>"$LOG") || fail "저장소의 git 상태를 읽을 수 없음"
[ -z "$BUILD_SRC" ] || fail "저장소(빌드·검증 입력 포함)에 미커밋·추적하지 않는 파일이 있음: $(printf '%s' "$BUILD_SRC" | head -3 | tr '\n' ' ')"
# filter 속성(.gitattributes·.git/info/attributes)이 붙은 추적 파일은 작업 파일이 달라도 git이 같다고 볼 수 있다 (clean 필터) — 멈춘다.
# 시스템 설정의 filter.lfs 같은 정의만 있고 파일에 붙지 않았으면 괜찮다
FILTERED=$(git ls-files -z 2>>"$LOG" | git check-attr --stdin -z filter 2>>"$LOG" | tr '\0' '\n' | paste - - - \
  | awk -F'\t' '$3 != "unspecified" && $3 != "unset" && n < 3 { print $1 " (filter=" $3 ")"; n++ }') || fail "git 속성(filter)을 읽을 수 없음"
[ -z "$FILTERED" ] || fail "filter 속성이 붙은 추적 파일이 있음 — 작업 파일과 승인한 트리가 달라도 git이 같다고 볼 수 있음: $(printf '%s' "$FILTERED" | tr '\n' ' ')"
# 후보 빌드는 docker-compose.yml을 직접 지정하고(-f), 정지·전환(up)은 Compose의 기본 파일 찾기를 따른다 — 둘이 같은 설정을 읽도록
# 기본 파일 찾기를 바꾸는 것(다른 이름의 Compose 파일, 덧붙임 파일, 셸이나 .env의 COMPOSE_FILE)이 하나라도 있으면 멈춘다
for f in $COMPOSE_OTHER; do [ ! -e "$f" ] || fail "Compose가 저절로 읽는 다른 파일이 있음: $f — 빌드와 전환이 다른 설정을 쓸 수 있음"; done
[ -z "${COMPOSE_FILE:-}" ] || fail "COMPOSE_FILE 환경 변수가 있음 ($COMPOSE_FILE) — 빌드와 전환이 다른 설정을 쓸 수 있음"
! grep -q 'COMPOSE_FILE' .env 2>/dev/null || fail ".env에 COMPOSE_FILE이 있음 — 빌드와 전환이 다른 설정을 쓸 수 있음"   # 꼴(BOM·'키: 값')과 상관없이
# assume-unchanged·skip-worktree 표시가 붙은 파일은 git status·diff가 로컬 수정을 보여 주지 않는다 — 검증 스크립트·검증 목록·
# 빌드 입력이 승인한 트리와 달라도 위 확인을 지나가므로, 그런 표시가 하나라도 있으면 멈춘다
INDEX_FLAGS=$("${GITQ[@]}" ls-files -v 2>>"$LOG") || fail "git 색인을 읽을 수 없음"
HIDDEN=$(printf '%s\n' "$INDEX_FLAGS" | grep -E '^[a-zS] ' | head -5)
[ -z "$HIDDEN" ] || fail "변경을 숨기는 git 표시(assume-unchanged·skip-worktree)가 붙은 파일이 있음 — 표시를 풀고 다시: $(printf '%s' "$HIDDEN" | tr '\n' ' ')"
# git status는 크기·수정 시각(stat)이 색인과 같으면 내용을 읽지 않는다 — 같은 크기로 고치고 수정 시각을 되돌린 파일은 위 확인을
# 지나가므로, 빌드 입력(backend·frontend·docker-compose.yml)과 검증 입력(scripts·deploy)의 내용을 승인한 트리의 blob과 직접 대조한다
VLS=$(git -c core.quotePath=false ls-files -s -- backend frontend scripts deploy docker-compose.yml 2>>"$LOG") && [ -n "$VLS" ] \
  || fail "빌드·검증 입력의 색인을 읽을 수 없음"
VWANT=$(printf '%s\n' "$VLS" | awk -F'\t' '{ split($1, a, " "); if (a[1] != "160000" && a[1] != "120000") print a[2] "\t" $2 }')
VGOT=$(printf '%s\n' "$VWANT" | cut -f2- | git hash-object --stdin-paths 2>>"$LOG") || fail "빌드·검증 입력의 내용 해시를 계산할 수 없음"
[ "$(printf '%s\n' "$VWANT" | wc -l)" -eq "$(printf '%s\n' "$VGOT" | wc -l)" ] || fail "빌드·검증 입력의 내용 해시 개수가 색인과 다름"
VBAD=$(paste <(printf '%s\n' "$VWANT") <(printf '%s\n' "$VGOT") | awk -F'\t' '$1 != $3 && n < 3 { print $2; n++ }')
[ -z "$VBAD" ] || fail "작업 파일 내용이 승인한 트리와 다름 (git 상태로는 보이지 않음 — 같은 크기·되돌린 수정 시각 등): $(printf '%s' "$VBAD" | tr '\n' ' ')"
log "승인 PR HEAD $APPROVED · 배포 대상 $HEAD_SHA · 내용(트리 $HEAD_TREE) 일치"

log "== 2. 반영 전 상태 기록"
B_CID=$(container_id backend) || fail "실행 중인 backend 컨테이너를 확인할 수 없음"
F_CID=$(container_id frontend) || fail "실행 중인 frontend 컨테이너를 확인할 수 없음"
OLD_B=$(image_of "$B_CID") || fail "backend 컨테이너의 이미지 ID를 확인할 수 없음"
OLD_F=$(image_of "$F_CID") || fail "frontend 컨테이너의 이미지 ID를 확인할 수 없음"
PREV_LINE=$(current_revision) || fail "반영 전 리비전을 읽을 수 없음"
PREV=$(printf '%s\n' "$PREV_LINE" | grep -o '^[0-9a-z_]*')
[ -n "$PREV" ] || fail "반영 전 리비전 형식을 알 수 없음: $PREV_LINE"
[ "$PREV" = "$EXPECT_PREV" ] || fail "반영 전 리비전이 $PREV 입니다 (예상 $EXPECT_PREV). 원인을 확인하기 전까지 정지·전환하지 않습니다"
TAG="rollback-$TS"
TAG_REPO="${DEPLOY_TAG_REPO:-moajwo}"
docker tag "$OLD_B" "$TAG_REPO-backend:$TAG" 2>>"$LOG" && docker tag "$OLD_F" "$TAG_REPO-frontend:$TAG" 2>>"$LOG" || fail "롤백 태그를 만들 수 없음"
log "반영 전 리비전 $PREV (예상과 같음) · backend ${OLD_B:7:12} · frontend ${OLD_F:7:12} · 롤백 태그 $TAG_REPO-backend·frontend:$TAG"
OLD_B_PORTS=$(ports_of "$B_CID") || fail "backend 컨테이너의 포트 연결을 확인할 수 없음"
OLD_F_PORTS=$(ports_of "$F_CID") || fail "frontend 컨테이너의 포트 연결을 확인할 수 없음"
ROLLBACK_COMPOSE="$BACKUP_DIR/rollback-$TS.compose.yml"
{
  echo "# 반영 전($TS) 실행 중이던 컨테이너의 포트 연결 — 롤백 때 docker-compose.yml 뒤에 -f로 덧붙인다 (docs/DEPLOY.md 4장)"
  echo "# 롤백 태그: $TAG_REPO-backend:$TAG ${OLD_B:7:12} · $TAG_REPO-frontend:$TAG ${OLD_F:7:12}"
  echo "services:"
  echo "  backend:"
  echo "    ports: !override"
  for p in $OLD_B_PORTS; do echo "      - \"$p\""; done
  echo "  frontend:"
  echo "    ports: !override"
  for p in $OLD_F_PORTS; do echo "      - \"$p\""; done
} >"$ROLLBACK_COMPOSE" || fail "롤백용 포트 설정 파일을 쓸 수 없음: $ROLLBACK_COMPOSE"
log "반영 전 포트 연결: backend $OLD_B_PORTS · frontend $OLD_F_PORTS → $ROLLBACK_COMPOSE"

log "== 3. 후보 이미지 빌드와 반영 전 검증 (서비스 중단 없음 · latest 태그는 그대로)"
image_name() {  # 서비스의 Compose 이미지 이름 — 설정(JSON)의 image, 없으면 Compose 기본 이름 <프로젝트>-<서비스>.
  # 'config --images <서비스>'는 depends_on 서비스 이름까지 순서 없이 찍어 쓰지 않는다 (frontend가 backend 이름을 받을 수 있음).
  # 조회 실패·결과 없음이면 1, 태그·digest가 지정돼 있으면 그 참조를 찍고 2
  local out
  out=$(dc config --format json 2>>"$LOG" | "$PY" -I -X utf8 -c 'import json, sys
d = json.load(sys.stdin)
print(d["services"][sys.argv[1]].get("image") or d["name"] + "-" + sys.argv[1])' "$1" 2>>"$LOG") || return 1
  [ -n "$out" ] || return 1
  printf '%s\n' "$out"
  case "${out##*/}" in *:*|*@*) return 2 ;; esac   # 이 스크립트가 옮기는 것은 '<이름>:latest'뿐 — 다른 태그를 조용히 떼지 않는다
}
image_id() {  # 이미지 참조의 ID (sha256:… 형식이어야 함)
  local id
  id=$(docker image inspect "$1" --format '{{.Id}}' 2>>"$LOG") || return 1
  case "$id" in sha256:*) printf '%s\n' "$id" ;; *) return 1 ;; esac
}
find_python() {  # Python 3.10 이상 — 환경 변수로 고르지 않는다. 무작위 응답을 확인해 실행 파일인 척하는 가짜(예: true)를 거른다
  local c n="pyok$RANDOM$RANDOM"
  for c in python python3; do
    [ "$("$c" -c "import sys; print('$n' if sys.version_info >= (3, 10) else 'old')" 2>/dev/null | tr -d '\r')" = "$n" ] && { printf '%s\n' "$c"; return 0; }
  done
  return 1
}
PY=$(find_python) || fail "Python 3.10 이상을 찾을 수 없음 (자원 확인·이미지 검증에 필요) — 기존 서비스·latest 태그는 그대로"
B_REPO=$(image_name backend); RC=$?
[ "$RC" -eq 0 ] || fail "backend의 Compose 이미지 $([ "$RC" -eq 2 ] && echo "참조에 태그·digest가 지정됨 ($B_REPO) — 이 스크립트는 <이름>:latest만 옮김" || echo '이름을 읽을 수 없음')"
F_REPO=$(image_name frontend); RC=$?
[ "$RC" -eq 0 ] || fail "frontend의 Compose 이미지 $([ "$RC" -eq 2 ] && echo "참조에 태그·digest가 지정됨 ($F_REPO) — 이 스크립트는 <이름>:latest만 옮김" || echo '이름을 읽을 수 없음')"
# 기본 이름 규칙을 Compose 자신의 이미지 목록과 맞춰 본다 — 둘이 다르고 둘 다 목록에 있어야 한다
ALL_IMAGES=$(dc config --images 2>>"$LOG") || fail "Compose 이미지 이름 목록을 읽을 수 없음"
[ "$B_REPO" != "$F_REPO" ] && printf '%s\n' "$ALL_IMAGES" | grep -qxF "$B_REPO" && printf '%s\n' "$ALL_IMAGES" | grep -qxF "$F_REPO" \
  || fail "서비스별 Compose 이미지 이름을 정하지 못함 (backend $B_REPO · frontend $F_REPO · Compose 목록: $(printf '%s' "$ALL_IMAGES" | tr '\n' ' '))"
GATE_DIR="$BACKUP_DIR/image-gate-$TS"
mkdir -p "$GATE_DIR" || fail "검증 기록 폴더를 만들 수 없음: $GATE_DIR"
MEM_TSV="$GATE_DIR/memory.tsv"
"$PY" -I -X utf8 scripts/memory_guard.py check build "$OLD_B" "$MEM_TSV" 2>&1 | tee -a "$LOG"   # -I: 환경 변수·스크립트 폴더의 모듈을 쓰지 않음
RC=${PIPESTATUS[0]}
[ "$RC" -eq 0 ] || fail "빌드 전 자원 $([ "$RC" -eq 4 ] && echo '기준 미달' || echo "확인 실패 (종료 $RC)") — 후보를 만들지 않음, 기존 서비스·latest 태그는 그대로 (docs/DEPLOY.md 11장 자원 기준)"
CAND_TAG="cand-$TS"
CAND_COMPOSE="$BACKUP_DIR/candidate-$TS.compose.yml"
{
  echo "# 후보 이미지 빌드용 덧붙임 ($TS) — 3단계에서만 쓴다. latest가 아닌 후보 태그로 만들어 검증 전에는 운영 태그를 옮기지 않는다"
  echo "services:"
  echo "  backend:"
  echo "    image: $B_REPO:$CAND_TAG"
  echo "  frontend:"
  echo "    image: $F_REPO:$CAND_TAG"
} >"$CAND_COMPOSE" || fail "후보 빌드용 덧붙임 파일을 쓸 수 없음: $CAND_COMPOSE"
case " ${COMPOSE_ARGS[*]} " in
  *" -f "*) BUILD_ARGS=("${COMPOSE_ARGS[@]}") ;;
  *) BUILD_ARGS=(-f docker-compose.yml "${COMPOSE_ARGS[@]}") ;;   # -f를 하나라도 주면 기본 파일을 읽지 않으므로 함께 준다
esac
for svc in backend frontend; do   # 하나씩 빌드한다 (동시 빌드보다 메모리를 덜 씀) — 빌드 동안의 시작·최저 여유를 기록
  "$PY" -I -X utf8 scripts/memory_guard.py watch "build-$svc" "$OLD_B" "$MEM_TSV" -- \
    docker compose "${BUILD_ARGS[@]}" -f "$CAND_COMPOSE" build "$svc" >>"$LOG" 2>&1 \
    || fail "후보 $svc 빌드 실패 — 기존 서비스·latest 태그는 그대로"
  log "$(grep "^자원 | build-$svc 동안" "$LOG" | tail -1)"
done
CAND_B=$(image_id "$B_REPO:$CAND_TAG") || fail "후보 backend 이미지 ID를 읽을 수 없음 — 기존 서비스·latest 태그는 그대로"
CAND_F=$(image_id "$F_REPO:$CAND_TAG") || fail "후보 frontend 이미지 ID를 읽을 수 없음 — 기존 서비스·latest 태그는 그대로"
[ "$CAND_B" != "$CAND_F" ] || fail "후보 backend·frontend 이미지 ID가 같음 ($CAND_B) — 이름 지정이 잘못됨, 기존 서비스·latest 태그는 그대로"
log "후보 이미지: backend $CAND_B · frontend $CAND_F (태그 $CAND_TAG — latest는 그대로)"
# 검증 스크립트와 검증 목록은 1단계에서 승인한 트리와 같다고 확인한 이 저장소의 것만 쓴다 (바꾸는 환경 변수 없음)
bash scripts/image_gate.sh "$CAND_B" "$CAND_F" "$GATE_DIR" 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
[ "$RC" -eq 0 ] || fail "이미지 검증 실패 (종료 코드 $RC) — 기존 서비스·latest 태그는 그대로. 기록: $GATE_DIR"
GATE_RESULT="$GATE_DIR/gate.result"   # 종료 코드만 믿지 않는다 — 이번 후보 두 ID의 통과 기록이 있어야 한다
{ grep -qx 'status=pass' "$GATE_RESULT" && grep -qx "backend=$CAND_B" "$GATE_RESULT" && grep -qx "frontend=$CAND_F" "$GATE_RESULT"; } 2>>"$LOG" \
  || fail "이미지 검증 기록($GATE_RESULT)에서 이번 후보의 통과를 확인할 수 없음 — 기존 서비스·latest 태그는 그대로"
log "이미지 검증 통과: backend ${CAND_B:7:12} · frontend ${CAND_F:7:12} — 6단계는 이 ID로만 전환 (기록 $GATE_DIR)"

log "== 4. 쓰기 중단 (backend·frontend 정지)"
T0=$(date +%s)
dc stop -t 30 frontend backend >>"$LOG" 2>&1 || abort_after_stop "서비스 정지 명령 실패"
RUNNING=$(dc ps --status running --services 2>>"$LOG") || abort_after_stop "정지 후 실행 중 서비스를 확인할 수 없음 (조회 실패) — 백업·전환하지 않음"
[ -z "$RUNNING" ] || abort_after_stop "정지 후에도 실행 중: $RUNNING"
WRITERS=$(docker ps --filter "volume=$VOLUME" --format '{{.Names}}' 2>>"$LOG") || abort_after_stop "데이터 볼륨을 쓰는 컨테이너를 확인할 수 없음 (조회 실패) — 백업·전환하지 않음"
[ -z "$WRITERS" ] || abort_after_stop "데이터 볼륨을 쓰는 컨테이너가 남아 있음: $WRITERS"
log "정지 확인 (두 조회 모두 성공 · 실행 중 서비스·볼륨 사용 컨테이너 없음)"

log "== 5. 백업 (볼륨 전체 한 시점) 과 검증"
ARCHIVE="moajwo-data-$TS.tgz"
BACKUP_IMAGE="${DEPLOY_BACKUP_IMAGE:-$OLD_B}"
SRC_TXT="$BACKUP_DIR/moajwo-data-$TS.source.txt"   # a. 운영 볼륨(정지 상태)에서 읽은 값
BK_TXT="$BACKUP_DIR/moajwo-data-$TS.backup.txt"    # c. 백업에서 꺼낸 DB에서 읽은 값
SUM_TXT="$BACKUP_DIR/moajwo-data-$TS.txt"          # 요약 (검증 결과·sha256)
complete() { grep -qx "end ok" "$1" && grep -q '^revision ' "$1" && grep -q '^rows ' "$1"; }
bk_fail() {  # 백업을 만든 뒤의 검증 실패: 이 백업을 '검증 안 됨'으로 남기고(복원에 쓰지 않게) 중단 처리
  printf 'archive %s\nverified no\nreason %s\n' "$ARCHIVE" "$*" >"$SUM_TXT"
  abort_after_stop "$* — 백업 $ARCHIVE 는 검증되지 않았으므로 복원에 쓰지 않습니다"
}

log "5a. 운영 볼륨(정지 상태, 읽기 전용)의 리비전·행 수"
docker run --rm --label moajwo.step=source-check --entrypoint python \
  -v "$VOLUME:/data:ro" "$BACKUP_IMAGE" -c "$CHECK_PY" /data/moajwo.db rows >"$SRC_TXT" 2>>"$LOG"
RC=$?
[ "$RC" -eq 0 ] || abort_after_stop "운영 볼륨 DB 조회 실패 (종료 코드 $RC) — 백업·전환하지 않음"
complete "$SRC_TXT" || abort_after_stop "운영 볼륨 DB 조회 결과가 불완전함 (리비전·행 수·끝 표시 누락)"
SRC_REV=$(sed -n 's/^revision //p' "$SRC_TXT")
[ "$SRC_REV" = "$PREV" ] || abort_after_stop "정지 상태 운영 DB의 리비전($SRC_REV)이 반영 전 리비전($PREV)과 다름"
log "[운영 볼륨] 리비전 $SRC_REV · 테이블 $(grep -c '^rows ' "$SRC_TXT")개"

log "5b. 백업 생성 · 압축 검사 · sha256 기록"
SHA_REC=$(docker run --rm --label moajwo.step=backup --entrypoint sh -v "$VOLUME:/data:ro" -v "$WIN_BACKUP_DIR:/backup" "$BACKUP_IMAGE" -c \
  "tar czf /backup/$ARCHIVE -C /data . && gzip -t /backup/$ARCHIVE && tar tzf /backup/$ARCHIVE | grep -qx './moajwo.db' && sha256sum /backup/$ARCHIVE | cut -d' ' -f1" \
  2>>"$LOG")
RC=$?
[ "$RC" -eq 0 ] || bk_fail "백업 생성·압축 검사 실패 (종료 코드 $RC)"
[ -s "$BACKUP_DIR/$ARCHIVE" ] || bk_fail "백업 파일이 없거나 비어 있음"
SHA_REC=$(printf '%s\n' "$SHA_REC" | head -1)
case "$SHA_REC" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) [ "${#SHA_REC}" -eq 64 ] || bk_fail "백업 sha256 기록 형식이 잘못됨" ;;
  *) bk_fail "백업 sha256 기록이 없음" ;; esac
log "[백업 파일] $ARCHIVE · 압축 검사 ok · moajwo.db 포함 · sha256 $SHA_REC"

log "5c. 백업에서 DB를 꺼내 별도 임시 위치에서 읽기 전용 검사 (운영 볼륨은 마운트하지 않음)"
docker run --rm --label moajwo.step=backup-check -e CHECK_PY="$CHECK_PY" -e ARCHIVE="$ARCHIVE" --entrypoint sh \
  -v "$WIN_BACKUP_DIR:/backup:ro" "$BACKUP_IMAGE" -c \
  'set -e; d=$(mktemp -d); tar xzf "/backup/$ARCHIVE" -C "$d" --wildcards "./moajwo.db*"; test -s "$d/moajwo.db"; python -c "$CHECK_PY" "$d/moajwo.db" full' \
  >"$BK_TXT" 2>>"$LOG"
RC=$?
[ "$RC" -eq 0 ] || bk_fail "백업에서 DB를 꺼내거나 여는 데 실패 (종료 코드 $RC) — 전환하지 않음"
complete "$BK_TXT" || bk_fail "백업 DB 검사 결과가 불완전함 (리비전·행 수·끝 표시 누락)"
BK_INTEGRITY=$(sed -n 's/^integrity //p' "$BK_TXT")
[ "$BK_INTEGRITY" = "ok" ] || bk_fail "백업 DB 무결성 검사 실패: ${BK_INTEGRITY:-결과 없음}"
BK_REV=$(sed -n 's/^revision //p' "$BK_TXT")
log "[백업 DB] 무결성 $BK_INTEGRITY · 리비전 $BK_REV · 저널 $(sed -n 's/^journal //p' "$BK_TXT") · 테이블 $(grep -c '^rows ' "$BK_TXT")개"

log "5d. 운영 볼륨(정지 상태)과 백업 DB 비교 — 원본 DB의 모든 테이블 (핵심: $KEY_TABLES)"
[ "$BK_REV" = "$SRC_REV" ] || bk_fail "리비전 불일치: 운영 볼륨 $SRC_REV · 백업 $BK_REV"
# 테이블별 비교는 awk 한 번으로 만들어 로그에 한 번에 남긴다 (테이블마다 프로세스를 띄우면 정지 시간이 늘어남)
CMP=$(awk -v keys=" $KEY_TABLES " '
  FILENAME == ARGV[1] { if ($1 == "rows") { src[$2] = $3; order[++n] = $2 }; next }
  $1 == "rows" { bk[$2] = $3 }
  END {
    bad = 0
    for (i = 1; i <= n; i++) {
      t = order[i]; name = index(keys, " " t " ") ? t " (핵심)" : t
      if ((t in bk) && bk[t] == src[t]) printf "  [일치] %s: 운영 볼륨 %s · 백업 %s\n", name, src[t], bk[t]
      else { bad = 1; printf "  [불일치] %s: 운영 볼륨 %s · 백업 %s\n", name, src[t], ((t in bk) ? bk[t] : "없음") }
    }
    nk = split(keys, ks, " ")
    for (i = 1; i <= nk; i++) if (!(ks[i] in src)) printf "  [없음] %s (핵심): 이 리비전의 DB에 없는 테이블 — 비교 대상 아님\n", ks[i]
    for (t in bk) if (!(t in src)) { bad = 1; printf "  [불일치] 백업에만 있는 테이블: %s\n", t }
    print (bad ? "result mismatch" : "result match")
  }' "$SRC_TXT" "$BK_TXT")
RC=$?
[ "$RC" -eq 0 ] || bk_fail "운영 볼륨과 백업 DB를 비교할 수 없음 (종료 코드 $RC)"
printf '%s\n' "$CMP" | grep -v '^result ' | sed "s/^/[$(date +%H:%M:%S)] /" | tee -a "$LOG"
case "$(printf '%s\n' "$CMP" | tail -1)" in
  "result match") ;;
  "result mismatch") bk_fail "운영 볼륨과 백업 DB의 테이블·행 수가 다름 — 전환하지 않음" ;;
  *) bk_fail "운영 볼륨과 백업 DB의 비교 결과가 없음" ;;
esac
log "비교 결과: 리비전 같음 ($SRC_REV) · 테이블 $(grep -c '^rows ' "$SRC_TXT")개 행 수 모두 같음"

log "5e. 백업 파일 sha256 재확인 (호스트에서 다시 계산)"
SHA_NOW=$(sha256sum "$BACKUP_DIR/$ARCHIVE" 2>>"$LOG" | cut -d' ' -f1) && [ -n "$SHA_NOW" ] || bk_fail "백업 sha256을 다시 계산할 수 없음"
[ "$SHA_NOW" = "$SHA_REC" ] || bk_fail "백업 sha256 불일치 (기록 $SHA_REC · 재계산 $SHA_NOW)"
{
  echo "archive $ARCHIVE"
  echo "verified yes"
  echo "sha256 $SHA_REC"
  echo "source_revision $SRC_REV (운영 볼륨, 정지 상태)"
  echo "backup_revision $BK_REV (백업에서 꺼낸 DB)"
  echo "backup_integrity $BK_INTEGRITY (백업에서 꺼낸 DB)"
  echo "compared_tables $(grep -c '^rows ' "$SRC_TXT") (행 수 모두 같음)"
} >"$SUM_TXT"
SHA_LINE="sha256 $SHA_REC"
log "백업 확인: $ARCHIVE ($SHA_LINE) — 운영 볼륨 값 $SRC_TXT · 백업 DB 값 $BK_TXT"

# 태그 이동은 6단계 머리줄 앞에서 한다 — 여기서 실패하면 기존 컨테이너를 재개하고 끝나는데(전환 없음), 머리줄 뒤였다면
# 4장 B 0단계가 그 로그를 '전환 도중 끊긴 반영'으로 분류한다
log "== 5f. 전환 준비: latest 태그를 3단계에서 검증한 이미지로 (컨테이너는 멈춘 그대로)"
latest_id() {  # <이미지 이름>:latest가 지금 가리키는 ID (조회 실패면 빈 값)
  docker image inspect "$1:latest" --format '{{.Id}}' 2>>"$LOG"
}
short() { if [ -n "$1" ]; then printf '%s' "${1:7:12}"; else printf '조회 실패'; fi; }
RETAG_NOTE=""
retag_latest() {  # latest를 3단계에서 검증한 ID로 옮기고 실제 ID로 확인한다. 실패하면 둘 다 반영 전 이미지로 되돌리고,
  #               되돌린 결과도 실제 ID로 확인해 RETAG_NOTE에 그대로 적는다 (명령 결과만으로 '되돌림'이라 하지 않음)
  local mb=1 mf=1 rb rf nb nf
  docker tag "$CAND_B" "$B_REPO:latest" 2>>"$LOG" && mb=0
  if [ "$mb" -eq 0 ]; then docker tag "$CAND_F" "$F_REPO:latest" 2>>"$LOG" && mf=0; fi
  nb=$(latest_id "$B_REPO")
  nf=$(latest_id "$F_REPO")
  if [ "$mb" -eq 0 ] && [ "$mf" -eq 0 ] && [ "$nb" = "$CAND_B" ] && [ "$nf" = "$CAND_F" ]; then
    return 0
  fi
  RETAG_NOTE="이동 backend $([ "$mb" -eq 0 ] && echo 성공 || echo 실패) · frontend $([ "$mb" -ne 0 ] && echo '시도 안 함' || { [ "$mf" -eq 0 ] && echo 성공 || echo 실패; }) · 이동 뒤 latest backend $(short "$nb") · frontend $(short "$nf") → "
  docker tag "$OLD_B" "$B_REPO:latest" 2>>"$LOG"
  rb=$?
  docker tag "$OLD_F" "$F_REPO:latest" 2>>"$LOG"
  rf=$?
  nb=$(latest_id "$B_REPO")
  nf=$(latest_id "$F_REPO")
  if [ "$nb" = "$OLD_B" ] && [ "$nf" = "$OLD_F" ] && [ "$rb" -eq 0 ] && [ "$rf" -eq 0 ]; then
    RETAG_NOTE+="반영 전 이미지로 되돌림 확인 (backend $(short "$OLD_B") · frontend $(short "$OLD_F"), 되돌림 명령 종료 0/0)"
  elif [ "$nb" = "$OLD_B" ] && [ "$nf" = "$OLD_F" ]; then   # 태그는 반영 전 그대로지만 되돌림 명령이 실패 — 따로 적는다
    RETAG_NOTE+="읽은 latest는 반영 전 이미지와 같음 (backend $(short "$nb") · frontend $(short "$nf")) — 되돌림 명령 실패 (종료 $rb/$rf)"
  else
    RETAG_NOTE+="[주의] 되돌림 확인 안 됨 — 지금 backend latest $(short "$nb") (반영 전 $(short "$OLD_B"), 되돌림 명령 종료 $rb) · frontend latest $(short "$nf") (반영 전 $(short "$OLD_F"), 되돌림 명령 종료 $rf). 직접 확인: docker image inspect $B_REPO:latest $F_REPO:latest"
  fi
  return 1
}
retag_latest || abort_after_stop "검증한 이미지에 latest 태그를 붙이지 못함 — latest 태그: $RETAG_NOTE"
log "latest 태그 → 검증한 이미지 (실제 ID 확인): $B_REPO ${CAND_B:7:12} · $F_REPO ${CAND_F:7:12}"

log "== 6. 새 이미지로 전환 (백업 성공 확인 후 · 3단계에서 검증한 이미지 ID · 다시 빌드하지 않음)"   # 제목 앞부분은 4장 B 0단계가 찾는 문구
ROLLBACK_HINT="docs/DEPLOY.md 4장 (백업 $ARCHIVE, 롤백 태그 $TAG, 포트 $ROLLBACK_COMPOSE, 반영 전 리비전 $PREV)"
dc up -d --no-build --wait >>"$LOG" 2>&1 || fail "전환 실패 — 마이그레이션이 일부 진행됐을 수 있음. 자동 롤백하지 않습니다: $ROLLBACK_HINT"
NEW_REV=$(current_revision) || fail "전환 후 리비전을 확인할 수 없음 — 성공으로 보고하지 않습니다. 상태를 확인하세요: $ROLLBACK_HINT"
case "$NEW_REV" in *"(head)"*) ;; *) fail "전환 후 리비전이 head가 아님: $NEW_REV — $ROLLBACK_HINT";; esac
NB_CID=$(container_id backend) || fail "전환 후 backend 컨테이너를 확인할 수 없음 — 성공으로 보고하지 않습니다: $ROLLBACK_HINT"
NF_CID=$(container_id frontend) || fail "전환 후 frontend 컨테이너를 확인할 수 없음 — 성공으로 보고하지 않습니다: $ROLLBACK_HINT"
NEW_B=$(image_of "$NB_CID") || fail "전환 후 backend 이미지를 확인할 수 없음 — 성공으로 보고하지 않습니다: $ROLLBACK_HINT"
NEW_F=$(image_of "$NF_CID") || fail "전환 후 frontend 이미지를 확인할 수 없음 — 성공으로 보고하지 않습니다: $ROLLBACK_HINT"
[ "$NEW_B" = "$CAND_B" ] || fail "전환 후 backend 이미지(${NEW_B:7:12})가 검증한 이미지(${CAND_B:7:12})와 다름 — 성공으로 보고하지 않습니다: $ROLLBACK_HINT"
[ "$NEW_F" = "$CAND_F" ] || fail "전환 후 frontend 이미지(${NEW_F:7:12})가 검증한 이미지(${CAND_F:7:12})와 다름 — 성공으로 보고하지 않습니다: $ROLLBACK_HINT"
log "실행 중 이미지 = 3단계에서 검증한 이미지 (backend $NEW_B · frontend $NEW_F · 검증 기록 $GATE_DIR)"
log "전환 완료: 리비전 $NEW_REV · backend ${NEW_B:7:12} · frontend ${NEW_F:7:12} · 중단 $(( $(date +%s) - T0 ))초"
NEW_F_PORTS=$(ports_of "$NF_CID") || NEW_F_PORTS="(조회 실패)"
log "포트 연결: frontend $OLD_F_PORTS → $NEW_F_PORTS"
log "배포 단위: 승인 $APPROVED / 대상 $HEAD_SHA / 이전 ${OLD_B:7:12}+${OLD_F:7:12}($TAG) / 이전 포트 $ROLLBACK_COMPOSE / 백업 $ARCHIVE / 리비전 $PREV → $NEW_REV"
log "이미지 연결: 빌드(후보 태그 $CAND_TAG) → 검증($GATE_DIR) → 실행 = 같은 ID (backend $CAND_B · frontend $CAND_F)"
log "반영 후 점검을 진행하세요 (docs/DEPLOY.md 3장). 기록: $LOG"
