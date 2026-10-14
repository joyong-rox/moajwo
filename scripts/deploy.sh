#!/usr/bin/env bash
# 운영 반영 (Git Bash). 사용법: scripts/deploy.sh <승인한 PR HEAD 커밋 SHA> <반영 전 리비전>
#   예: scripts/deploy.sh 8be8436… 0017_auth_version   (반영 전 리비전이 다르면 정지하지 않고 멈춘다)
#
# 실패하면 그 자리에서 멈춘다. set -e에 맡기지 않고, 판단에 쓰는 모든 조회는 **종료 코드와 필수 결과**를 함께 검사한다.
# (조회가 실패한 것과, 조회는 성공했는데 결과가 비어 있는 것을 구분한다.)
#   1) 승인 변경과 배포 대상 일치 (병합 방식에 따라 main SHA는 달라질 수 있어 트리(파일 내용)를 비교)
#   2) 미커밋 변경 없음 (backend/frontend의 추적하지 않는 파일 포함 — 빌드에 들어가므로)
#   3) 이미지 빌드 성공 (실패하면 서비스는 그대로)
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
#   6) 백업 성공을 확인한 뒤에만 새 이미지로 전환하고, 전환 후 리비전·이미지 조회가 모두 성공해야 '전환 완료'로 보고한다.
#
# 격리 환경 검증용 환경변수 (운영에서는 쓰지 않음):
#   DEPLOY_COMPOSE_ARGS  docker compose 인자 (예: "-f rehearsal/docker-compose.yml")
#   DEPLOY_VOLUME        데이터 볼륨 이름 (기본 moajwo_moajwo-data)
#   DEPLOY_BACKUP_DIR    백업 폴더 (기본 backups)
#   DEPLOY_BACKUP_IMAGE  백업 one-off에 쓸 이미지 (기본: 반영 전 backend 이미지)
#   DEPLOY_TAG_REPO      롤백 태그 이름 앞부분 (기본 moajwo → moajwo-backend:rollback-<시각>). 격리 시험이 운영 이름으로 태그를 만들지 않게
set -uo pipefail
export MSYS_NO_PATHCONV=1

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
[ "$PREV" = "$EXPECT_PREV" ] || fail "반영 전 리비전이 $PREV 입니다 (예상 $EXPECT_PREV). 원인을 확인하기 전까지 정지·전환하지 않습니다"
TAG="rollback-$TS"
TAG_REPO="${DEPLOY_TAG_REPO:-moajwo}"
docker tag "$OLD_B" "$TAG_REPO-backend:$TAG" 2>>"$LOG" && docker tag "$OLD_F" "$TAG_REPO-frontend:$TAG" 2>>"$LOG" || fail "롤백 태그를 만들 수 없음"
log "반영 전 리비전 $PREV (예상과 같음) · backend ${OLD_B:7:12} · frontend ${OLD_F:7:12} · 롤백 태그 $TAG_REPO-backend·frontend:$TAG"

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
