#!/usr/bin/env bash
# 로컬 운영 정기 백업 — 서비스를 멈추지 않는 읽기 전용 백업 (docs/DEPLOY.md 14장 · docs/RECOVERY_RUNBOOK.md 6절).
#
# 절차는 deploy.sh 5단계 백업 검증(a~e)과 같고, 2026-10-31 6차 운영 백업(서비스 정지 없음)과 같은 방식이다:
#   0) 사용 중인지 본다 — 실행 중인 결과(runs.status = running), 최근 N분 안에 바뀐 볼륨 파일·폴더, 최근 N분의 쓰기 요청
#      (backend 접근 기록의 POST·PUT·PATCH·DELETE 건수). 하나라도 있으면 아무것도 만들지 않고 종료 3(미룸).
#      디스크 여유가 기준(최소값과 볼륨 크기 3배 중 큰 쪽) 미만이면 종료 4(건너뜀).
#   a) 운영 볼륨 DB(읽기 전용)의 리비전·표별 행 수
#   b) 볼륨 전체를 tgz로(읽기 전용 마운트) → 압축 검사 → moajwo.db 포함 → sha256(컨테이너)
#   c) 백업에서 꺼낸 DB를 운영 볼륨을 마운트하지 않은 컨테이너에서 읽기 전용으로: 무결성·리비전·저널·표별 행 수 · 압축 안 파일 수·바이트
#   d) a와 c 비교 + 백업을 만든 뒤 볼륨을 다시 본다(바뀐 파일 0 · 실행 중 0 · 그동안 쓰기 요청 0 · 파일 수·바이트 = 압축 안).
#      쓰기와 겹쳤으면 이번 실행이 만든 백업 파일만 지우고(기록은 남김) 종료 3 — 나중에 다시 만든다.
#   e) 호스트에서 sha256 재계산 → <백업>.sha256 · summary.txt(verified yes)
#   그다음 보존 후보 목록(지우지 않음): 로컬 정기 백업(검증됨) 최근 --keep-weekly개 + 반영 직전 백업(deploy.sh가 만든
#   backups/moajwo-data-<시각>.tgz) 최근 --keep-predeploy개를 뺀 나머지를 retention-candidates.txt에 적는다.
# 운영 볼륨은 읽기 전용으로만 마운트하고 운영 컨테이너를 멈추거나 다시 만들지 않는다. 기록에는 리비전·표 이름·행 수·크기·sha256·건수만
# 남긴다(아이디·요청 경로·업무 내용 없음). 백업 파일 자체에는 운영 자료(개인정보 포함)가 들어 있으므로 Git 밖에 둔다.
#
# 결과: <out-root>/<시각>/moajwo-local-<시각>.tgz · .sha256 · summary.txt · source.txt · backup-check.txt · after.txt · backup.log
#       시도마다 <out-root>/logs/attempts.tsv 에 한 줄(시각·구분·결과·요약).
# 종료: 0 완료 · 3 미룸(사용 중 — 나중에 다시) · 4 건너뜀(디스크 기준 미달) · 1 실패(도구·Docker·검증 오류)
# 사용: bash scripts/backup_local.sh [--out-root <폴더>] [--label weekly|manual] [--quiet-minutes 10] [--min-free-mib 5120]
#        [--keep-weekly 8] [--keep-predeploy 3] [--volume moajwo_moajwo-data] [--container moajwo-backend-1] [--predeploy-dir <폴더>]
set -uo pipefail
export MSYS_NO_PATHCONV=1
export PATH="/usr/bin:/bin:$PATH"   # 작업 스케줄러·PowerShell에서 불러도 find·sort 가 Windows 것이 되지 않게
exec 2>&1
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT_ROOT="$ROOT/backups/local-weekly"
PREDEPLOY_DIR="$ROOT/backups"
LABEL=manual
QUIET=10
MIN_FREE=5120
KEEP_WEEKLY=8
KEEP_PRE=3
VOLUME=moajwo_moajwo-data
CONTAINER=moajwo-backend-1
while [ $# -gt 0 ]; do
  case "$1" in
    --out-root) OUT_ROOT=${2:-}; shift 2 ;;
    --label) LABEL=${2:-}; shift 2 ;;
    --quiet-minutes) QUIET=${2:-}; shift 2 ;;
    --min-free-mib) MIN_FREE=${2:-}; shift 2 ;;
    --keep-weekly) KEEP_WEEKLY=${2:-}; shift 2 ;;
    --keep-predeploy) KEEP_PRE=${2:-}; shift 2 ;;
    --volume) VOLUME=${2:-}; shift 2 ;;
    --container) CONTAINER=${2:-}; shift 2 ;;
    --predeploy-dir) PREDEPLOY_DIR=${2:-}; shift 2 ;;
    *) echo "[중단] 모르는 인자: $1"; exit 1 ;;
  esac
done
for v in "$QUIET" "$MIN_FREE" "$KEEP_WEEKLY" "$KEEP_PRE"; do
  case "$v" in '' | *[!0-9]*) echo "[중단] 숫자가 아닌 인자: '$v'"; exit 1 ;; esac
done
[ "$QUIET" -ge 1 ] || { echo "[중단] --quiet-minutes 는 1 이상"; exit 1; }
case "$LABEL" in weekly | manual) ;; *) echo "[중단] --label 은 weekly 또는 manual"; exit 1 ;; esac
mkdir -p "$OUT_ROOT/logs" || { echo "[중단] 결과 폴더를 만들 수 없음: $OUT_ROOT"; exit 1; }
OUT_ROOT=$(cd "$OUT_ROOT" && pwd)

hostpath() { (cd "$1" && { pwd -W 2>/dev/null || pwd; }); }   # Docker 바인드 마운트용 (Git Bash는 Windows 경로)
say() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
attempt() { printf '%s\t%s\t%s\t%s\n' "$(date '+%F %T')" "$LABEL" "$1" "$2" >> "$OUT_ROOT/logs/attempts.tsv"; }
val() { sed -n "s/^$1 //p" "$2" | head -1; }

# 볼륨 상태(읽기 전용): 최근 QUIET분 안에 바뀐 파일·폴더 수 · 실행 중 결과 수 · 파일 수 · 바이트
STATE_PY='import os, sqlite3, sys, time
window = float(sys.argv[1]) * 60
now = time.time()
recent = files = size = 0
for d, dirs, names in os.walk("/data"):
    if now - os.lstat(d).st_mtime < window:
        recent += 1
    for n in names:
        st = os.lstat(os.path.join(d, n))
        files += 1
        size += st.st_size
        if now - st.st_mtime < window:
            recent += 1
if not os.path.isfile("/data/moajwo.db"):
    running = "missing"
else:
    try:
        c = sqlite3.connect("file:/data/moajwo.db?mode=ro", uri=True, timeout=5)
        running = c.execute("select count(*) from runs where status = ?", ("running",)).fetchone()[0]
    except Exception as e:  # 잠금 등 — 사용 중으로 보고 미룬다
        running = "error:" + type(e).__name__
print("recent", recent)
print("running", running)
print("files", files)
print("bytes", size)
print("end ok")'
# deploy.sh 5단계·6차 운영 백업과 같은 DB 검사: 리비전 · (full) 무결성·저널 · 모든 표의 행 수
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
ARCHIVE_PY='import sys, tarfile
with tarfile.open(sys.argv[1]) as t:
    m = [x for x in t.getmembers() if x.isfile()]
print("archive_files", len(m))
print("archive_bytes", sum(x.size for x in m))'

state() {  # state <기록 파일> — 운영 볼륨(읽기 전용)의 지금 상태
  docker run --rm --network none --label moajwo.step=local-backup-state --entrypoint python -v "$VOLUME:/data:ro" "$IMG" \
    -c "$STATE_PY" "$QUIET" > "$1" 2>&1 && grep -qx 'end ok' "$1"
}
writes_since() {  # writes_since <docker logs --since 값> — backend 접근 기록의 쓰기 요청 건수(내용은 보지 않음)
  local logs
  logs=$(docker logs --since "$1" "$CONTAINER" 2>&1) || return 1
  printf '%s\n' "$logs" | grep -cE '"(POST|PUT|PATCH|DELETE) ' || true
}

# 0) 준비 · 사용 중인지 · 디스크
docker info --format '{{.ServerVersion}}' > /dev/null 2>&1 || { say "[실패] Docker 엔진 응답 없음"; attempt 1 "Docker 엔진 응답 없음"; exit 1; }
IMG=$(docker inspect "$CONTAINER" --format '{{.Image}}' 2>/dev/null) || { say "[실패] backend 컨테이너($CONTAINER) 조회 실패"; attempt 1 "backend 컨테이너 조회 실패"; exit 1; }
docker volume inspect "$VOLUME" > /dev/null 2>&1 || { say "[실패] 볼륨($VOLUME) 없음"; attempt 1 "볼륨 없음"; exit 1; }
PRE=$(mktemp)
trap 'rm -f "$PRE"' EXIT
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ); T0S=$(date +%s)
state "$PRE" || { say "[실패] 볼륨 상태 조회 실패: $(tr '\n' ' ' < "$PRE" | cut -c1-200)"; attempt 1 "볼륨 상태 조회 실패"; exit 1; }
RECENT=$(val recent "$PRE"); RUNNING=$(val running "$PRE"); VFILES=$(val files "$PRE"); VBYTES=$(val bytes "$PRE")
[ "$RUNNING" = missing ] && { say "[실패] 볼륨($VOLUME)에 moajwo.db 가 없음 — 운영 볼륨이 맞는지 확인"; attempt 1 "볼륨에 DB 없음"; exit 1; }
WRITES=$(writes_since "${QUIET}m") || { say "[실패] backend 접근 기록 조회 실패"; attempt 1 "접근 기록 조회 실패"; exit 1; }
say "볼륨 상태(읽기 전용): 최근 ${QUIET}분 안에 바뀐 파일·폴더 $RECENT · 실행 중 결과 $RUNNING · 최근 ${QUIET}분 쓰기 요청 $WRITES · 파일 $VFILES개 · $VBYTES bytes"
BUSY=""
[ "$RUNNING" = 0 ] || BUSY="$BUSY 실행 중 결과 $RUNNING;"
[ "$RECENT" = 0 ] || BUSY="$BUSY 최근 ${QUIET}분 안에 바뀐 파일·폴더 $RECENT;"
[ "$WRITES" = 0 ] || BUSY="$BUSY 최근 ${QUIET}분 쓰기 요청 $WRITES;"
if [ -n "$BUSY" ]; then
  say "[미룸] 사용 중 —$BUSY 백업을 만들지 않음"
  attempt 3 "미룸:$BUSY"
  exit 3
fi
FREE=$(df -Pm "$OUT_ROOT" | awk 'NR == 2 { print $4 }')
NEED=$((VBYTES * 3 / 1048576))
[ "$NEED" -ge "$MIN_FREE" ] || NEED=$MIN_FREE
case "$FREE" in '' | *[!0-9]*) say "[실패] 디스크 여유를 재지 못함"; attempt 1 "디스크 여유 측정 실패"; exit 1 ;; esac
if [ "$FREE" -lt "$NEED" ]; then
  say "[건너뜀] 디스크 여유 $FREE MiB < 기준 $NEED MiB (최소 $MIN_FREE · 볼륨 크기의 3배)"
  attempt 4 "디스크 여유 $FREE MiB < 기준 $NEED MiB"
  exit 4
fi

TS=$(date +%Y%m%d-%H%M%S)
OUT="$OUT_ROOT/$TS"
mkdir -p "$OUT" || { say "[실패] 폴더를 만들 수 없음: $OUT"; attempt 1 "폴더 생성 실패"; exit 1; }
A="moajwo-local-$TS.tgz"
WINOUT=$(hostpath "$OUT")
LOG="$OUT/backup.log"
log() { say "$*" | tee -a "$LOG"; }
fail() { log "[실패] $1"; attempt 1 "$TS $1"; [ -f "$OUT/$A" ] && rm -f "$OUT/$A"; echo "$1" > "$OUT/INCOMPLETE"; exit 1; }
abandon() { log "[미룸] $1 — 이번 실행이 만든 백업 파일은 지우고 기록만 남김"; attempt 3 "$TS $1"; rm -f "$OUT/$A"; echo "$1" > "$OUT/INCOMPLETE"; exit 3; }
cp "$PRE" "$OUT/before.txt"
log "로컬 운영 백업($LABEL, 서비스 정지 없음 · 볼륨 읽기 전용) 시작 · backend 이미지 ${IMG:7:12} · 볼륨 $VOLUME"
log "0. 사용 중 아님: 최근 ${QUIET}분 바뀐 파일·폴더 0 · 실행 중 결과 0 · 쓰기 요청 0 · 디스크 여유 $FREE MiB ≥ $NEED MiB"

log "a. 운영 볼륨 DB(읽기 전용)의 리비전·표별 행 수"
docker run --rm --network none --label moajwo.step=local-backup-source --entrypoint python -v "$VOLUME:/data:ro" "$IMG" \
  -c "$CHECK_PY" /data/moajwo.db rows > "$OUT/source.txt" 2>&1
RC=$?
{ [ "$RC" -eq 0 ] && grep -qx 'end ok' "$OUT/source.txt"; } || fail "a. 운영 볼륨 DB 조회 실패 (종료 $RC)"
SRC_REV=$(val revision "$OUT/source.txt")
log "[운영 볼륨] 리비전 $SRC_REV · 표 $(grep -c '^rows ' "$OUT/source.txt")개"

log "b. 백업 생성(tgz) · 압축 검사 · moajwo.db 포함 · sha256 (컨테이너)"
docker run --rm --network none --label moajwo.step=local-backup --entrypoint sh -v "$VOLUME:/data:ro" -v "$WINOUT:/backup" "$IMG" -c \
  'tar czf "/backup/$0" -C /data .; rc=$?; echo "tar_rc $rc"; [ "$rc" -eq 0 ] || exit 0
   gzip -t "/backup/$0" && echo "gzip ok"
   tar tzf "/backup/$0" | grep -qx "./moajwo.db" && echo "has_db yes"
   sha256sum "/backup/$0" | cut -d" " -f1 | sed "s/^/sha256 /"' "$A" > "$OUT/archive.txt" 2>&1
RC=$?
TAR_RC=$(val tar_rc "$OUT/archive.txt")
[ "$TAR_RC" = 1 ] && abandon "b. 압축 중 볼륨 파일이 바뀜(tar 종료 1)"
{ [ "$RC" -eq 0 ] && [ "$TAR_RC" = 0 ] && grep -qx 'gzip ok' "$OUT/archive.txt" && grep -qx 'has_db yes' "$OUT/archive.txt"; } \
  || fail "b. 백업 생성·압축 검사 실패 (종료 $RC · tar ${TAR_RC:-없음})"
SHA_C=$(val sha256 "$OUT/archive.txt")
[ "${#SHA_C}" -eq 64 ] || fail "b. 컨테이너 sha256 을 얻지 못함"
SIZE=$(stat -c %s "$OUT/$A")
log "[백업 파일] $A · $SIZE bytes · sha256 $SHA_C"

log "c. 백업에서 DB를 꺼내 별도 임시 위치에서 읽기 전용 검사 (운영 볼륨 마운트 없음) · 압축 안 파일 수·바이트"
docker run --rm --network none --label moajwo.step=local-backup-check -e CHECK_PY="$CHECK_PY" -e ARCHIVE_PY="$ARCHIVE_PY" -e ARCHIVE="$A" \
  --entrypoint sh -v "$WINOUT:/backup:ro" "$IMG" -c \
  'set -e; d=$(mktemp -d); tar xzf "/backup/$ARCHIVE" -C "$d" --wildcards "./moajwo.db*"; test -s "$d/moajwo.db"
   for f in "$d"/moajwo.db*; do echo "extracted $(basename "$f")"; done
   python -c "$CHECK_PY" "$d/moajwo.db" full
   python -c "$ARCHIVE_PY" "/backup/$ARCHIVE"
   sha256sum "$d/moajwo.db" | cut -d" " -f1 | sed "s/^/db_sha256 /"' > "$OUT/backup-check.txt" 2>&1
RC=$?
grep -q '^extracted moajwo\.db-journal$' "$OUT/backup-check.txt" && abandon "c. 백업 안에 쓰는 중이던 저널(moajwo.db-journal)이 있음"
{ [ "$RC" -eq 0 ] && grep -qx 'end ok' "$OUT/backup-check.txt"; } || fail "c. 백업 DB 꺼내기·검사 실패 (종료 $RC)"
BK_INT=$(val integrity "$OUT/backup-check.txt"); BK_REV=$(val revision "$OUT/backup-check.txt"); JOURNAL=$(val journal "$OUT/backup-check.txt")
AFILES=$(val archive_files "$OUT/backup-check.txt"); ABYTES=$(val archive_bytes "$OUT/backup-check.txt"); DBSHA=$(val db_sha256 "$OUT/backup-check.txt")
log "[백업 DB] 무결성 $BK_INT · 리비전 $BK_REV · 저널 $JOURNAL · 꺼낸 파일: $(sed -n 's/^extracted //p' "$OUT/backup-check.txt" | tr '\n' ' ')· 압축 안 파일 $AFILES개 · $ABYTES bytes"
[ "$BK_INT" = ok ] || abandon "c. 백업 DB 무결성 실패($BK_INT)"

log "d. 운영 볼륨 값과 백업 DB 값 비교 (모든 표) · 백업 뒤 볼륨 다시 보기"
CMP=$(awk '
  FILENAME == ARGV[1] { if ($1 == "rows") { src[$2] = $3; order[++n] = $2 }; next }
  $1 == "rows" { bk[$2] = $3 }
  END { bad = 0
    for (i = 1; i <= n; i++) { t = order[i]
      if ((t in bk) && bk[t] == src[t]) printf "  [일치] %s: 운영 볼륨 %s · 백업 %s\n", t, src[t], bk[t]
      else { bad = 1; printf "  [불일치] %s: 운영 볼륨 %s · 백업 %s\n", t, src[t], ((t in bk) ? bk[t] : "없음") } }
    for (t in bk) if (!(t in src)) { bad = 1; printf "  [불일치] 백업에만 있는 표: %s\n", t }
    print (bad ? "result mismatch" : "result match") }' "$OUT/source.txt" "$OUT/backup-check.txt")
printf '%s\n' "$CMP" | grep -v '^result ' >> "$LOG"
[ "$BK_REV" = "$SRC_REV" ] && [ "$(printf '%s\n' "$CMP" | tail -1)" = "result match" ] || abandon "d. 리비전·행 수 불일치 — 쓰기와 겹쳤을 수 있음"
state "$OUT/after.txt" || fail "d. 백업 뒤 볼륨 상태 조회 실패"
WRITES2=$(writes_since "$T0") || fail "d. 백업 동안의 접근 기록 조회 실패"
ELAPSED=$(($(date +%s) - T0S))
A_RECENT=$(val recent "$OUT/after.txt"); A_RUNNING=$(val running "$OUT/after.txt"); A_FILES=$(val files "$OUT/after.txt"); A_BYTES=$(val bytes "$OUT/after.txt")
log "[백업 뒤 볼륨] 최근 ${QUIET}분 바뀐 파일·폴더 $A_RECENT · 실행 중 결과 $A_RUNNING · 시작 뒤 쓰기 요청 $WRITES2 · 파일 $A_FILES개 · $A_BYTES bytes · 걸린 시간 ${ELAPSED}초"
[ "$ELAPSED" -lt $((QUIET * 60)) ] || abandon "d. 백업에 ${ELAPSED}초가 걸려 확인 창(${QUIET}분)을 넘음 — 한 시점인지 보장할 수 없음"
{ [ "$A_RECENT" = 0 ] && [ "$A_RUNNING" = 0 ] && [ "$WRITES2" = 0 ]; } || abandon "d. 백업 동안 볼륨이 바뀌었거나 쓰기 요청이 있었음"
{ [ "$A_FILES" = "$AFILES" ] && [ "$A_BYTES" = "$ABYTES" ] && [ "$A_FILES" = "$VFILES" ] && [ "$A_BYTES" = "$VBYTES" ]; } \
  || abandon "d. 파일 수·바이트가 볼륨(전 $VFILES·$VBYTES / 뒤 $A_FILES·$A_BYTES)과 압축($AFILES·$ABYTES) 사이에 다름"

log "e. 호스트에서 sha256 재계산"
SHA_H=$(sha256sum "$OUT/$A" | cut -d' ' -f1)
[ "$SHA_H" = "$SHA_C" ] || fail "e. sha256 불일치 (컨테이너 $SHA_C · 호스트 $SHA_H)"
printf '%s *%s\n' "$SHA_H" "$A" > "$OUT/$A.sha256"
{
  echo "archive $A"
  echo "verified yes"
  echo "label $LABEL"
  echo "sha256 $SHA_H"
  echo "size_bytes $SIZE"
  echo "db_sha256_inside $DBSHA"
  echo "revision $BK_REV (운영 볼륨 = 백업 DB)"
  echo "integrity $BK_INT"
  echo "journal $JOURNAL"
  echo "tables $(grep -c '^rows ' "$OUT/source.txt") (행 수 모두 같음 — source.txt · backup-check.txt)"
  echo "volume_files $AFILES"
  echo "volume_bytes $ABYTES"
  echo "made_at $(date '+%F %T %z')"
  echo "elapsed_seconds $ELAPSED"
  echo "service_stopped no"
  echo "backend_image $IMG"
  echo "tool scripts/backup_local.sh"
} > "$OUT/summary.txt"
log "백업 확인: $A · $SIZE bytes · sha256 $SHA_H · DB sha256 $DBSHA · 리비전 $BK_REV · 표 $(grep -c '^rows ' "$OUT/source.txt")개 행 수 같음"

# 보존 후보 목록 — 지우지 않는다
RET="$OUT_ROOT/retention-candidates.txt"
{
  echo "# 보존 후보 목록 $(date '+%F %T') — 지우지 않음. 정리는 소유자 승인 뒤 (docs/DEPLOY.md 14장)"
  echo "# 규칙: 로컬 정기 백업(검증됨) 최근 ${KEEP_WEEKLY}개 + 반영 직전 백업(deploy.sh) 최근 ${KEEP_PRE}개 보존"
  n=0
  for d in $(find "$OUT_ROOT" -mindepth 1 -maxdepth 1 -type d -name '[0-9]*-[0-9]*' -printf '%f\n' | sort -r); do
    if [ -f "$OUT_ROOT/$d/summary.txt" ] && grep -qx 'verified yes' "$OUT_ROOT/$d/summary.txt"; then
      n=$((n + 1))
      if [ "$n" -le "$KEEP_WEEKLY" ]; then echo "보존 정기 $d"; else echo "정리후보 정기 $d $(du -sb "$OUT_ROOT/$d" | cut -f1) bytes"; fi
    else
      echo "미완료 $d (검증 기록 없음 — 이유는 INCOMPLETE·backup.log, 백업 파일 없음)"
    fi
  done
  n=0
  if [ -d "$PREDEPLOY_DIR" ]; then
    for p in $(find "$PREDEPLOY_DIR" -mindepth 1 -maxdepth 1 -type f -name 'moajwo-data-*.tgz' -printf '%f\n' | grep -E '^moajwo-data-[0-9]{8}-[0-9]{6}\.tgz$' | sort -r); do
      n=$((n + 1))
      if [ "$n" -le "$KEEP_PRE" ]; then echo "보존 반영직전 $p"; else echo "정리후보 반영직전 $p $(stat -c %s "$PREDEPLOY_DIR/$p") bytes"; fi
    done
  fi
} > "$RET"
cp "$RET" "$OUT/retention-candidates.txt"
log "보존 후보 목록: $(grep -c '^정리후보 ' "$RET")건 (지우지 않음) — $RET"
attempt 0 "$TS 완료 $SIZE bytes sha256 $SHA_H"
echo "result_dir $(hostpath "$OUT")"
exit 0
