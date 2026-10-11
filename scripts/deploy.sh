#!/usr/bin/env bash
# 운영 반영 (Git Bash). 사용법: scripts/deploy.sh <승인한 PR HEAD 커밋 SHA>
#
# 실패하면 그 자리에서 멈춘다. set -e에 맡기지 않고, 판단에 쓰는 모든 조회는 **종료 코드와 필수 결과**를 함께 검사한다.
# (조회가 실패한 것과, 조회는 성공했는데 결과가 비어 있는 것을 구분한다.)
#   1) 승인 변경과 배포 대상 일치 (병합 방식에 따라 main SHA는 달라질 수 있어 트리(파일 내용)를 비교)
#   2) 미커밋 변경 없음 (backend/frontend의 추적하지 않는 파일 포함 — 빌드에 들어가므로)
#   3) 이미지 빌드 성공 (실패하면 서비스는 그대로)
#   4) 서비스 정지 성공 · 실행 중 서비스 0개 · 데이터 볼륨을 쓰는 컨테이너 0개 (각 조회가 성공한 경우에만 '없음'으로 판단)
#   5) 백업 성공 · 백업 파일 존재·압축 무결성 · DB 무결성 ok · 리비전이 반영 전 리비전과 같음
#   → 4·5가 실패하면, 마이그레이션이 시작되지 않았음(리비전 그대로)을 **확인할 수 있을 때만** 기존 컨테이너를 다시 시작한다.
#     확인할 수 없으면 재시작하지 않고 현재 상태와 수동 복구 절차를 보고한다.
#   6) 백업 성공을 확인한 뒤에만 새 이미지로 전환하고, 전환 후 리비전·이미지 조회가 모두 성공해야 '전환 완료'로 보고한다.
#
# 격리 환경 검증용 환경변수 (운영에서는 쓰지 않음):
#   DEPLOY_COMPOSE_ARGS  docker compose 인자 (예: "-f rehearsal/docker-compose.yml")
#   DEPLOY_VOLUME        데이터 볼륨 이름 (기본 moajwo_moajwo-data)
#   DEPLOY_BACKUP_DIR    백업 폴더 (기본 backups)
#   DEPLOY_BACKUP_IMAGE  백업 one-off에 쓸 이미지 (기본: 반영 전 backend 이미지)
set -uo pipefail
export MSYS_NO_PATHCONV=1

APPROVED="${1:-}"
[ -n "$APPROVED" ] || { echo "사용법: scripts/deploy.sh <승인한 PR HEAD 커밋 SHA>"; exit 2; }
read -r -a COMPOSE_ARGS <<< "${DEPLOY_COMPOSE_ARGS:-}"
VOLUME="${DEPLOY_VOLUME:-moajwo_moajwo-data}"
BACKUP_DIR="${DEPLOY_BACKUP_DIR:-backups}"
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR" || { echo "[중단] 백업 폴더를 만들 수 없음: $BACKUP_DIR"; exit 1; }
LOG="$BACKUP_DIR/deploy-$TS.log"
if ! WIN_BACKUP_DIR=$(cd "$BACKUP_DIR" && pwd -W) || [ -z "$WIN_BACKUP_DIR" ]; then
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
  log "[중단] $*"
  restart_old
  exit 1
}

log "== 1. 승인 변경과 배포 대상 확인"
git rev-parse --verify --quiet "$APPROVED^{commit}" >/dev/null || fail "승인한 커밋을 찾을 수 없음: $APPROVED (git fetch 필요)"
HEAD_SHA=$(git rev-parse HEAD 2>>"$LOG") && [ -n "$HEAD_SHA" ] || fail "배포 대상 커밋(HEAD)을 읽을 수 없음"
APPROVED_TREE=$(git rev-parse "$APPROVED^{tree}" 2>>"$LOG") && [ -n "$APPROVED_TREE" ] || fail "승인한 커밋의 트리를 읽을 수 없음"
HEAD_TREE=$(git rev-parse "HEAD^{tree}" 2>>"$LOG") && [ -n "$HEAD_TREE" ] || fail "배포 대상의 트리를 읽을 수 없음"
if [ "$APPROVED_TREE" != "$HEAD_TREE" ]; then
  git diff --stat "$APPROVED" HEAD 2>>"$LOG" | tail -5 | tee -a "$LOG"
  fail "배포 대상($HEAD_SHA)의 내용이 승인한 커밋($APPROVED)과 다름"
fi
TRACKED=$(git status --porcelain --untracked-files=no 2>>"$LOG") || fail "git 상태를 읽을 수 없음"
[ -z "$TRACKED" ] || fail "추적 중인 파일에 미커밋 변경이 있음"
BUILD_SRC=$(git status --porcelain -- backend frontend 2>>"$LOG") || fail "backend/frontend의 git 상태를 읽을 수 없음"
[ -z "$BUILD_SRC" ] || fail "backend/frontend에 미커밋·추적하지 않는 파일이 있음 (빌드에 들어감)"
log "승인 PR HEAD $APPROVED · 배포 대상 $HEAD_SHA · 내용(트리 $HEAD_TREE) 일치"

log "== 2. 반영 전 상태 기록"
B_CID=$(container_id backend) || fail "실행 중인 backend 컨테이너를 확인할 수 없음"
F_CID=$(container_id frontend) || fail "실행 중인 frontend 컨테이너를 확인할 수 없음"
OLD_B=$(image_of "$B_CID") || fail "backend 컨테이너의 이미지 ID를 확인할 수 없음"
OLD_F=$(image_of "$F_CID") || fail "frontend 컨테이너의 이미지 ID를 확인할 수 없음"
PREV_LINE=$(current_revision) || fail "반영 전 리비전을 읽을 수 없음"
PREV=$(printf '%s\n' "$PREV_LINE" | grep -o '^[0-9a-z_]*')
[ -n "$PREV" ] || fail "반영 전 리비전 형식을 알 수 없음: $PREV_LINE"
TAG="rollback-$TS"
docker tag "$OLD_B" "moajwo-backend:$TAG" 2>>"$LOG" && docker tag "$OLD_F" "moajwo-frontend:$TAG" 2>>"$LOG" || fail "롤백 태그를 만들 수 없음"
log "반영 전 리비전 $PREV · backend ${OLD_B:7:12} · frontend ${OLD_F:7:12} · 롤백 태그 $TAG"

log "== 3. 새 이미지 빌드 (서비스 중단 없음)"
dc build >>"$LOG" 2>&1 || fail "이미지 빌드 실패 — 기존 서비스는 그대로 동작 중"
log "빌드 완료"

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
docker run --rm --entrypoint sh -v "$VOLUME:/data:ro" -v "$WIN_BACKUP_DIR:/backup" "$BACKUP_IMAGE" -c \
  "tar czf /backup/$ARCHIVE -C /data . && gzip -t /backup/$ARCHIVE && tar tzf /backup/$ARCHIVE | grep -qx './moajwo.db' && python -c \"import sqlite3,hashlib; c=sqlite3.connect('file:/data/moajwo.db?mode=ro', uri=True); print('revision', c.execute('select version_num from alembic_version').fetchone()[0]); print('integrity', c.execute('pragma integrity_check').fetchone()[0]); print('journal', c.execute('pragma journal_mode').fetchone()[0]); print('sha256', hashlib.sha256(open('/backup/$ARCHIVE','rb').read()).hexdigest())\"" \
  >"$BACKUP_DIR/moajwo-data-$TS.txt" 2>>"$LOG"
RC=$?
cat "$BACKUP_DIR/moajwo-data-$TS.txt" >>"$LOG" 2>/dev/null
[ "$RC" -eq 0 ] || abort_after_stop "백업 명령 실패 (종료 코드 $RC)"
[ -s "$BACKUP_DIR/$ARCHIVE" ] || abort_after_stop "백업 파일이 없거나 비어 있음"
grep -qx "integrity ok" "$BACKUP_DIR/moajwo-data-$TS.txt" || abort_after_stop "DB 무결성 검사 실패"
grep -qx "revision $PREV" "$BACKUP_DIR/moajwo-data-$TS.txt" || abort_after_stop "백업의 리비전이 반영 전 리비전($PREV)과 다름"
SHA_LINE=$(grep '^sha256 ' "$BACKUP_DIR/moajwo-data-$TS.txt") || abort_after_stop "백업 sha256 기록이 없음"
log "백업 확인: $ARCHIVE ($SHA_LINE)"

log "== 6. 새 이미지로 전환 (백업 성공 확인 후)"
ROLLBACK_HINT="docs/DEPLOY.md 4장 (백업 $ARCHIVE, 롤백 태그 $TAG, 반영 전 리비전 $PREV)"
dc up -d --wait >>"$LOG" 2>&1 || fail "전환 실패 — 마이그레이션이 일부 진행됐을 수 있음. 자동 롤백하지 않습니다: $ROLLBACK_HINT"
NEW_REV=$(current_revision) || fail "전환 후 리비전을 확인할 수 없음 — 성공으로 보고하지 않습니다. 상태를 확인하세요: $ROLLBACK_HINT"
case "$NEW_REV" in *"(head)"*) ;; *) fail "전환 후 리비전이 head가 아님: $NEW_REV — $ROLLBACK_HINT";; esac
NB_CID=$(container_id backend) || fail "전환 후 backend 컨테이너를 확인할 수 없음 — 성공으로 보고하지 않습니다: $ROLLBACK_HINT"
NF_CID=$(container_id frontend) || fail "전환 후 frontend 컨테이너를 확인할 수 없음 — 성공으로 보고하지 않습니다: $ROLLBACK_HINT"
NEW_B=$(image_of "$NB_CID") || fail "전환 후 backend 이미지를 확인할 수 없음 — 성공으로 보고하지 않습니다: $ROLLBACK_HINT"
NEW_F=$(image_of "$NF_CID") || fail "전환 후 frontend 이미지를 확인할 수 없음 — 성공으로 보고하지 않습니다: $ROLLBACK_HINT"
log "전환 완료: 리비전 $NEW_REV · backend ${NEW_B:7:12} · frontend ${NEW_F:7:12} · 중단 $(( $(date +%s) - T0 ))초"
log "배포 단위: 승인 $APPROVED / 대상 $HEAD_SHA / 이전 ${OLD_B:7:12}+${OLD_F:7:12}($TAG) / 백업 $ARCHIVE / 리비전 $PREV → $NEW_REV"
log "반영 후 점검을 진행하세요 (docs/DEPLOY.md 3장). 기록: $LOG"
