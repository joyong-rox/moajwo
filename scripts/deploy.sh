#!/usr/bin/env bash
# 운영 반영 (Git Bash). 사용법: scripts/deploy.sh <승인한 PR HEAD 커밋 SHA>
#
# 실패하면 그 자리에서 멈춘다. 중요한 조건은 set -e에 맡기지 않고 하나씩 명시적으로 검사한다.
#   1) 승인 변경과 배포 대상 일치 (병합 방식에 따라 main SHA는 달라질 수 있어 트리(파일 내용)를 비교)
#   2) 미커밋 변경 없음 (backend/frontend의 추적하지 않는 파일 포함 — 빌드에 들어가므로)
#   3) 이미지 빌드 성공 (실패하면 서비스는 그대로)
#   4) 서비스 정지 성공 · 데이터 볼륨을 쓰는 컨테이너 없음
#   5) 백업 성공 · 백업 파일 존재·압축 무결성 · DB 무결성 ok · 리비전이 반영 전 리비전과 같음
#   → 4·5가 실패하면 마이그레이션이 시작되지 않았음(리비전 그대로)을 확인하고 기존 컨테이너를 다시 시작한 뒤 실패를 보고한다
#   6) 백업 성공을 확인한 뒤에만 새 이미지로 전환
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
WIN_BACKUP_DIR=$(cd "$BACKUP_DIR" && pwd -W)

dc() { docker compose "${COMPOSE_ARGS[@]}" "$@"; }
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
fail() { log "[중단] $*"; exit 1; }
revision_in_volume() {  # 볼륨의 DB 리비전을 서버 없이 읽는다
  docker run --rm --entrypoint python -v "$VOLUME:/data:ro" "$1" -c \
    "import sqlite3; print(sqlite3.connect('file:/data/moajwo.db?mode=ro', uri=True).execute('select version_num from alembic_version').fetchone()[0])"
}
wait_healthy() {  # backend healthy 대기 (최대 90초)
  local cid; cid=$(dc ps -q backend)
  for _ in $(seq 1 90); do
    [ "$(docker inspect "$cid" --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] && return 0
    sleep 1
  done
  return 1
}
restart_old() {  # 백업·정지 단계 실패: 마이그레이션 미시작 확인 → 기존 컨테이너 재시작 (up이 아니라 start: 새 이미지로 바꾸지 않음)
  local now_rev
  now_rev=$(revision_in_volume "$OLD_B") || now_rev="(읽기 실패)"
  if [ "$now_rev" != "$PREV" ]; then
    log "[주의] 리비전이 반영 전과 다릅니다 ($PREV → $now_rev). 자동으로 재시작하지 않습니다 — docs/DEPLOY.md 롤백 절차로 확인하세요."
    return 1
  fi
  log "마이그레이션이 시작되지 않았음 확인 (리비전 $now_rev) → 기존 컨테이너 재시작"
  dc start backend frontend >>"$LOG" 2>&1 || { log "[주의] 기존 컨테이너 재시작 실패"; return 1; }
  wait_healthy || { log "[주의] 기존 backend가 healthy가 되지 않음"; return 1; }
  log "기존 서비스 재개 (backend $(docker inspect "$(dc ps -q backend)" --format '{{.Image}}' | cut -c8-19))"
}

log "== 1. 승인 변경과 배포 대상 확인"
git rev-parse --verify --quiet "$APPROVED^{commit}" >/dev/null || fail "승인한 커밋을 찾을 수 없음: $APPROVED (git fetch 필요)"
HEAD_SHA=$(git rev-parse HEAD)
if [ "$(git rev-parse "$APPROVED^{tree}")" != "$(git rev-parse HEAD^{tree})" ]; then
  git diff --stat "$APPROVED" HEAD | tail -5 | tee -a "$LOG"
  fail "배포 대상($HEAD_SHA)의 내용이 승인한 커밋($APPROVED)과 다름"
fi
[ -z "$(git status --porcelain --untracked-files=no)" ] || fail "추적 중인 파일에 미커밋 변경이 있음"
[ -z "$(git status --porcelain -- backend frontend)" ] || fail "backend/frontend에 미커밋·추적하지 않는 파일이 있음 (빌드에 들어감)"
log "승인 PR HEAD $APPROVED · 배포 대상 $HEAD_SHA · 내용(트리) 일치"

log "== 2. 반영 전 상태 기록"
B_CID=$(dc ps -q backend); F_CID=$(dc ps -q frontend)
[ -n "$B_CID" ] && [ -n "$F_CID" ] || fail "실행 중인 backend/frontend 컨테이너를 찾을 수 없음"
OLD_B=$(docker inspect "$B_CID" --format '{{.Image}}'); OLD_F=$(docker inspect "$F_CID" --format '{{.Image}}')
PREV=$(dc exec -T backend alembic current 2>/dev/null | grep -o '^[0-9a-z_]*' | head -1)
[ -n "$PREV" ] || fail "반영 전 리비전을 읽을 수 없음"
TAG="rollback-$TS"
docker tag "$OLD_B" "moajwo-backend:$TAG" && docker tag "$OLD_F" "moajwo-frontend:$TAG" || fail "롤백 태그를 만들 수 없음"
log "반영 전 리비전 $PREV · backend ${OLD_B:7:12} · frontend ${OLD_F:7:12} · 롤백 태그 $TAG"

log "== 3. 새 이미지 빌드 (서비스 중단 없음)"
dc build >>"$LOG" 2>&1 || fail "이미지 빌드 실패 — 기존 서비스는 그대로 동작 중"
log "빌드 완료"

log "== 4. 쓰기 중단 (backend·frontend 정지)"
T0=$(date +%s)
dc stop -t 30 frontend backend >>"$LOG" 2>&1 || { log "[중단] 서비스 정지 명령 실패"; restart_old; exit 1; }
RUNNING=$(dc ps --status running --services)
[ -z "$RUNNING" ] || { log "[중단] 정지 후에도 실행 중: $RUNNING"; restart_old; exit 1; }
WRITERS=$(docker ps --filter "volume=$VOLUME" --format '{{.Names}}')
[ -z "$WRITERS" ] || { log "[중단] 데이터 볼륨을 쓰는 컨테이너가 남아 있음: $WRITERS"; restart_old; exit 1; }
log "정지 확인 (실행 중 서비스·볼륨 사용 컨테이너 없음)"

log "== 5. 백업 (볼륨 전체 한 시점) 과 검증"
ARCHIVE="moajwo-data-$TS.tgz"
BACKUP_IMAGE="${DEPLOY_BACKUP_IMAGE:-$OLD_B}"
docker run --rm --entrypoint sh -v "$VOLUME:/data:ro" -v "$WIN_BACKUP_DIR:/backup" "$BACKUP_IMAGE" -c \
  "tar czf /backup/$ARCHIVE -C /data . && gzip -t /backup/$ARCHIVE && tar tzf /backup/$ARCHIVE | grep -qx './moajwo.db' && python -c \"import sqlite3,hashlib; c=sqlite3.connect('file:/data/moajwo.db?mode=ro', uri=True); print('revision', c.execute('select version_num from alembic_version').fetchone()[0]); print('integrity', c.execute('pragma integrity_check').fetchone()[0]); print('journal', c.execute('pragma journal_mode').fetchone()[0]); print('sha256', hashlib.sha256(open('/backup/$ARCHIVE','rb').read()).hexdigest())\"" \
  >"$BACKUP_DIR/moajwo-data-$TS.txt" 2>>"$LOG"
RC=$?
cat "$BACKUP_DIR/moajwo-data-$TS.txt" >>"$LOG" 2>/dev/null
[ "$RC" -eq 0 ] || { log "[중단] 백업 명령 실패 (종료 코드 $RC)"; restart_old; exit 1; }
[ -s "$BACKUP_DIR/$ARCHIVE" ] || { log "[중단] 백업 파일이 없거나 비어 있음"; restart_old; exit 1; }
grep -qx "integrity ok" "$BACKUP_DIR/moajwo-data-$TS.txt" || { log "[중단] DB 무결성 검사 실패"; restart_old; exit 1; }
grep -qx "revision $PREV" "$BACKUP_DIR/moajwo-data-$TS.txt" || { log "[중단] 백업의 리비전이 반영 전 리비전($PREV)과 다름"; restart_old; exit 1; }
log "백업 확인: $ARCHIVE ($(grep '^sha256' "$BACKUP_DIR/moajwo-data-$TS.txt"))"

log "== 6. 새 이미지로 전환 (백업 성공 확인 후)"
dc up -d --wait >>"$LOG" 2>&1 || fail "전환 실패 — 마이그레이션이 일부 진행됐을 수 있음. 자동 롤백하지 않습니다: docs/DEPLOY.md 4장 (백업 $ARCHIVE, 롤백 태그 $TAG)"
NEW_REV=$(dc exec -T backend alembic current 2>/dev/null | head -1)
case "$NEW_REV" in *"(head)"*) ;; *) fail "전환 후 리비전이 head가 아님: $NEW_REV";; esac
NEW_B=$(docker inspect "$(dc ps -q backend)" --format '{{.Image}}'); NEW_F=$(docker inspect "$(dc ps -q frontend)" --format '{{.Image}}')
log "전환 완료: 리비전 $NEW_REV · backend ${NEW_B:7:12} · frontend ${NEW_F:7:12} · 중단 $(( $(date +%s) - T0 ))초"
log "배포 단위: 승인 $APPROVED / 대상 $HEAD_SHA / 이전 ${OLD_B:7:12}+${OLD_F:7:12}($TAG) / 백업 $ARCHIVE / 리비전 $PREV → $NEW_REV"
log "반영 후 점검을 진행하세요 (docs/DEPLOY.md 3장). 기록: $LOG"
