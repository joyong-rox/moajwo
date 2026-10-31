#!/usr/bin/env bash
# 관찰 기간 점검 (읽기 전용) — docs/RECOVERY_RUNBOOK.md 8절. 운영을 바꾸지 않는다: docker inspect·logs, 데이터 볼륨을 읽기 전용으로 붙인 one-off(python —
# scripts/observe_db.py), curl GET과 공개 가입·최초 설정의 종료 경로(본문을 읽지 않는 410)만 쓴다. 사용법 (Git Bash, 저장소 루트에서):
#   bash scripts/observe.sh            → backups/followup-20261030/observation/observe-<시각>.txt (+ 같은 이름 .db.json)
# 출력에는 건수·상태 코드·시각·이미지 ID만 남는다 — 아이디·표시 이름·비밀번호·쿠키·업무 자료 내용은 읽지도 쓰지도 않는다.
# 판정 항목(소유자 결정 2026-10-31, 7차): 1 소유자 로그인 · 2 업무 흐름 1회(근거만) · 3 로그 오류 0 · 4 가입 410 유지 · 5 로그인 실패 급증 없음 · 6 DB 무결성 ·
#   7 판정 시점 이용 중 임시 상태 계정 0 + 관찰 중 24시간 넘게 남은 적 없음 · 8 복구 사건 0. 관찰 종료 = 2026-11-07 16:58 KST 경과 + 업무 흐름 1회 이상(둘 다).
# 시각 기준(7-2차): DB의 시각 열은 시각대 없는 UTC 문자열(표별 확인 — observe_db.py 머리글), docker logs -t의 시각은 UTC(Z). 비교는 모두 같은 형식의 문자열 한 기준
#   (관찰 시작 ≤ 시각 ≤ 지금, 최근 24시간 = 지금 - 86400초)으로 한다 — "KST일지도 모른다"는 완화는 쓰지 않는다.
# 시험 모드(scripts/test_observe.sh — 운영·Docker·네트워크 없음): OBS_LIVE=0 OBS_NOW_UTC=<고정 시각 Z> OBS_DB_PATH=<합성 DB> OBS_BLOG_FILE/OBS_FLOG_FILE=<docker logs -t 형식의
#   합성 로그> DEPLOY_LOG/BASELINE=<합성 반영 기록·기준선> OBS_DIR=<임시 폴더> OBS_IMAGE_APP_DIR=<합성 이미지 앱 폴더(표식 일치 확인)>. 운영 조회가 필요한 4·8번은 '건너뜀'으로 적는다.
# 표식 일치(7-4차): 실행 중 backend 이미지의 app/account_markers.py(없으면 app/adminctl.py)에 저장소 표식(backend/app/account_markers.py)이 있는지 one-off 컨테이너로 읽어 확인하고,
#   다르면 7번을 '주의'로 낸다(이미지의 사유 문자열이 다르면 생성 비밀번호 이력을 발급으로 세지 못한다).
set -uo pipefail
export MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 1
PYROOT=$ROOT; command -v cygpath >/dev/null 2>&1 && PYROOT=$(cygpath -m "$ROOT")   # 호스트 python에 넘길 경로 (Git Bash: MSYS 경로 변환을 꺼 두므로 C:/… 형태로)
LIVE=${OBS_LIVE:-1}
OBS_DIR=${OBS_DIR:-$ROOT/backups/followup-20261030/observation}
mkdir -p "$OBS_DIR" || { echo "[중단] 기록 폴더를 만들 수 없음: $OBS_DIR"; exit 1; }
TS=$(date +%Y%m%d-%H%M%S)
OUT="$OBS_DIR/observe-$TS.txt"
DBJ="$OBS_DIR/observe-$TS.db.json"
DEPLOY_LOG=${DEPLOY_LOG:-$ROOT/backups/deploy-20261031-165653.log}      # 관찰 대상 반영의 기록 (배포 단위·전환 완료 줄) — 운영 저장소의 backups/
BASELINE=${BASELINE:-$ROOT/backups/moajwo-data-20261031-165653.source.txt}   # 반영 직전(정지 상태) 표별 행 수 — 추이 비교용
BK_DIR=$(dirname "$DEPLOY_LOG")
[ -f "$DEPLOY_LOG" ] || { echo "[중단] 반영 기록이 없음: $DEPLOY_LOG — 운영 저장소 루트에서 실행하거나 DEPLOY_LOG=<경로>를 지정"; exit 1; }
START_UTC=${OBS_START_UTC:-2026-10-31T07:58:32Z}                  # 관찰 시작 = 전환 완료 2026-10-31 16:58:32 KST
END_KST=${OBS_END_KST:-"2026-11-07 16:58"}
NOW_UTC=${OBS_NOW_UTC:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}             # 고정 시각(시험)이 없으면 지금 — 아래 모든 '지금'은 이 값 하나
VOLUME=moajwo_moajwo-data
WEB=http://127.0.0.1:8080
API=http://127.0.0.1:8000
PASS=0; WARN=0; FAILN=0; SKIP=0
say() { printf '%s\n' "$*" | tee -a "$OUT"; }
verdict() {  # verdict <항목> <통과|주의|실패|건너뜀|근거> <내용>
  case "$2" in 통과) PASS=$((PASS + 1)) ;; 주의) WARN=$((WARN + 1)) ;; 실패) FAILN=$((FAILN + 1)) ;; 건너뜀) SKIP=$((SKIP + 1)) ;; esac
  say "$1 | $2 | $3"
}
NOW_S=$(date -u -d "$NOW_UTC" +%s 2>/dev/null) || { echo "[중단] 시각을 읽을 수 없음: $NOW_UTC"; exit 1; }
START_S=$(date -u -d "$START_UTC" +%s 2>/dev/null || echo 0)
END_S=$(date -u -d "$END_KST +0900" +%s 2>/dev/null || echo 0)   # 종료 가능 시각은 KST로 적혀 있다 — 실행 PC의 시간대와 무관하게 +0900으로 읽는다
FIXED=""; [ -z "${OBS_NOW_UTC:-}" ] || FIXED=" (고정 시각 $NOW_UTC)"
: > "$OUT"
say "# 관찰 점검 $(date -d "@$NOW_S" '+%Y-%m-%d %H:%M:%S %z')$FIXED · 저장소 $(git rev-parse --short HEAD 2>/dev/null || echo ?) · 관찰 시작 $START_UTC (2026-10-31 16:58:32 KST) · 종료 가능 시각 $END_KST KST"
say "경과: $(( (NOW_S - START_S) / 86400 ))일 $(( ((NOW_S - START_S) % 86400) / 3600 ))시간 · 종료 가능 시각 경과: $([ "$END_S" -gt 0 ] && [ "$NOW_S" -ge "$END_S" ] && echo 예 || echo 아니오)"

# ---------------------------------------------------------------- 운영 상태 (배포 단위와 대조)
UNIT=$(grep -h '전환 완료:' "$DEPLOY_LOG" 2>/dev/null | tail -1)
DEP_B=$(printf '%s\n' "$UNIT" | sed -n 's/.* backend \([0-9a-f]\{12\}\) .*/\1/p'); DEP_F=$(printf '%s\n' "$UNIT" | sed -n 's/.* frontend \([0-9a-f]\{12\}\) .*/\1/p')
DEP_REV=$(printf '%s\n' "$UNIT" | sed -n 's/.*리비전 \([0-9a-z_]*\) (head).*/\1/p')
inspect() { docker inspect "$1" --format "$2" 2>/dev/null; }
B_CID=""; F_CID=""; RUN_B=""; RUN_F=""
if [ "$LIVE" = 1 ]; then
  B_CID=$(docker compose ps -q backend 2>/dev/null); F_CID=$(docker compose ps -q frontend 2>/dev/null)
  RUN_B=$(inspect "$B_CID" '{{.Image}}'); RUN_F=$(inspect "$F_CID" '{{.Image}}')
  say "운영: backend ${RUN_B:7:12} $(inspect "$B_CID" '{{.State.Status}}/{{.State.Health.Status}} 재시작 {{.RestartCount}} 시작 {{.State.StartedAt}}') · frontend ${RUN_F:7:12} $(inspect "$F_CID" '{{.State.Status}} 재시작 {{.RestartCount}} 시작 {{.State.StartedAt}}')"
else
  say "운영: 조회하지 않음 (시험 모드 OBS_LIVE=0 — 합성 DB·합성 로그)"
fi
say "배포 단위($DEPLOY_LOG): backend $DEP_B · frontend $DEP_F · 리비전 $DEP_REV"

# ---------------------------------------------------------------- DB (읽기 전용) — 건수·판정 재료만 (scripts/observe_db.py)
BASE_ROWS=$(grep '^rows ' "$BASELINE" 2>/dev/null | tr '\n' ';')
PY=${OBS_PY:-}
if [ -n "${OBS_DB_PATH:-}" ]; then
  if [ -z "$PY" ]; then for cnd in python3 python; do "$cnd" -c 'import sqlite3' >/dev/null 2>&1 && { PY=$cnd; break; }; done; fi
  [ -n "$PY" ] || { say "[중단] 호스트 python을 찾지 못함 (OBS_PY=<python>으로 지정)"; exit 1; }
  DB_PATH="$OBS_DB_PATH" START_UTC="$START_UTC" NOW_UTC="$NOW_UTC" BASE_ROWS="$BASE_ROWS" OBS_MARKERS_PATH="$PYROOT/backend/app/account_markers.py" \
    "$PY" -I -X utf8 "$PYROOT/scripts/observe_db.py" > "$DBJ" 2>"$OUT.dberr"
  DBRC=$?
  j() { "$PY" -I -X utf8 -c "import json, sys; d = json.load(open(sys.argv[2], encoding='utf-8')); print(eval(sys.argv[1], {'d': d}))" "$1" "$DBJ" 2>/dev/null; }
else
  # 이력 표식 모듈은 저장소 파일을 읽기 전용으로 붙여 넘긴다 — 실행 중인 이미지가 그 파일을 모르는 판이어도 같은 정의를 쓴다
  docker run --rm -i --entrypoint python -e START_UTC="$START_UTC" -e NOW_UTC="${OBS_NOW_UTC:-}" -e BASE_ROWS="$BASE_ROWS" -e OBS_MARKERS_PATH=/markers/account_markers.py \
    -v "$VOLUME:/data:ro" -v "$PYROOT/backend/app/account_markers.py:/markers/account_markers.py:ro" "$RUN_B" - < "$ROOT/scripts/observe_db.py" > "$DBJ" 2>"$OUT.dberr"
  DBRC=$?
  j() { docker run --rm -i --entrypoint python "$RUN_B" -c "import json, sys; d = json.load(sys.stdin); print(eval(sys.argv[1], {'d': d}))" "$1" < "$DBJ" 2>/dev/null; }
fi
if [ "$DBRC" -ne 0 ] || [ ! -s "$DBJ" ]; then say "[주의] DB 읽기 실패 (종료 $DBRC) — $OUT.dberr 확인"; else rm -f "$OUT.dberr"; fi

# ---------------------------------------------------------------- 1·2 근거
verdict "1 소유자 로그인" 근거 "반영 뒤 소유자 로그인 기록 $([ "$(j "d['owner_login_since_start']")" = True ] && echo 있음 || echo 없음) · 반영 뒤 만든 세션 $(j "d['sessions_since_start']")개 · 지금 유효 세션 $(j "d['sessions_active_now']")개 — 화면 확인은 7차 W1 기록(backups/followup-20261030/round7/)"
verdict "2 업무 흐름 1회" 근거 "반영 뒤 업로드 $(j "d['flow_since_start']['uploads']") · 매핑 확정 $(j "d['flow_since_start']['mapping_decisions']") · 검토 $(j "d['flow_since_start']['review_sessions']") · 통합 실행 $(j "d['flow_since_start']['runs_created']")(완료 $(j "d['flow_since_start']['runs_done']")) · 분석 $(j "d['flow_since_start']['analysis_runs']") → 업무 흐름 완료 기록 $(j "d['flow_since_start']['runs_done']")건"

# ---------------------------------------------------------------- 로그 (docker logs -t: 줄마다 UTC 시각 Z) — 관찰 시작 ≤ 시각 ≤ 지금 창과 최근 24시간 창을 같은 형식의 문자열로 자른다
if [ -n "${OBS_BLOG_FILE:-}" ]; then BLOG_RAW=$(cat "$OBS_BLOG_FILE" 2>/dev/null); FLOG_RAW=$(cat "${OBS_FLOG_FILE:-/dev/null}" 2>/dev/null)
else BLOG_RAW=$(docker logs -t --since "$START_UTC" "$B_CID" 2>&1); FLOG_RAW=$(docker logs -t --since "$START_UTC" "$F_CID" 2>&1); fi
T0=$(date -u -d "@$START_S" +%Y-%m-%dT%H:%M:%S.000000000Z); T1=$(date -u -d "@$NOW_S" +%Y-%m-%dT%H:%M:%S.999999999Z); T24=$(date -u -d "@$((NOW_S - 86400))" +%Y-%m-%dT%H:%M:%S.000000000Z)
[[ "$T24" < "$T0" ]] && T24=$T0   # 최근 24시간 창도 관찰 시작 전으로는 거슬러 가지 않는다(반영 전 줄 제외)
win() { awk -v a="$1" -v b="$2" '$1 >= a && $1 <= b'; }
BLOG=$(printf '%s\n' "$BLOG_RAW" | win "$T0" "$T1"); FLOG=$(printf '%s\n' "$FLOG_RAW" | win "$T0" "$T1"); BLOG24=$(printf '%s\n' "$BLOG_RAW" | win "$T24" "$T1")

# ---------------------------------------------------------------- 3 로그 오류 — uvicorn "ERROR:" 머리(시각 뒤라 줄 중간) · 파이썬 Traceback · 앱 실패 기록(failures.log_failure: "… run N failed: …" — 앱 로거는 머리말 없이 메시지만 남긴다) · 5xx · nginx error
TB=$(printf '%s\n' "$BLOG" | grep -c "Traceback"); ER=$(printf '%s\n' "$BLOG" | grep -cE '(^|[[:space:]])(ERROR|CRITICAL)([:[:space:]]|$)'); AF=$(printf '%s\n' "$BLOG" | grep -cE ' failed: ')
FX=$(printf '%s\n%s\n' "$BLOG" "$FLOG" | grep -cE '" 5[0-9][0-9] '); NE=$(printf '%s\n' "$FLOG" | grep -cE '\[(error|emerg|crit|alert)\]')
if [ "$((TB + ER + AF + FX + NE))" -eq 0 ]; then verdict "3 로그 오류 0" 통과 "backend Traceback 0 · ERROR 0 · 앱 실패 기록 0 · 5xx 0 · nginx error 0 (backend $(printf '%s\n' "$BLOG" | grep -c .)줄 · frontend $(printf '%s\n' "$FLOG" | grep -c .)줄, 관찰 시작 이후 지금까지)"
else verdict "3 로그 오류 0" 실패 "Traceback $TB · ERROR $ER · 앱 실패 기록 $AF · 5xx $FX · nginx error $NE — docker logs -t --since $START_UTC 확인"; fi

# ---------------------------------------------------------------- 4 가입 410 유지 (본문을 읽지 않는 종료 경로 — 운영 자료 변화 없음)
if [ "$LIVE" = 1 ]; then
  S1=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' "$WEB/api/auth/signup"); S2=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' "$WEB/api/auth/setup")
  S3=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' "$API/auth/signup"); ST=$(curl -s "$WEB/api/auth/status" | tr -d ' ')
  if [ "$S1" = 410 ] && [ "$S2" = 410 ] && [ "$S3" = 410 ] && printf '%s' "$ST" | grep -q '"setup_required":false'; then verdict "4 가입 410 유지" 통과 "signup $S1 · setup $S2 · backend 직접 signup $S3 · status setup_required false · health $(curl -s "$WEB/api/health" | tr -d ' ')"
  else verdict "4 가입 410 유지" 실패 "signup $S1 · setup $S2 · 직접 $S3 · status $ST"; fi
else verdict "4 가입 410 유지" 건너뜀 "시험 모드 — 운영에 요청하지 않음"; fi

# ---------------------------------------------------------------- 5 로그인 실패 급증 없음 (급증 = 최근 24시간 로그인 401 20건 이상 또는 잠긴 계정 1개 이상 → 주의, 사람 판단)
L401=$(printf '%s\n' "$BLOG" | grep -c 'POST /auth/login HTTP/1.1" 401'); L24=$(printf '%s\n' "$BLOG24" | grep -c 'POST /auth/login HTTP/1.1" 401'); L200=$(printf '%s\n' "$BLOG" | grep -c 'POST /auth/login HTTP/1.1" 200')
LOCKED=$(j "d['locked_accounts_now']"); FSUM=$(j "d['failed_logins_counter_sum']")
if [ "${L24:-0}" -lt 20 ] && [ "${LOCKED:-1}" = 0 ]; then verdict "5 로그인 실패 급증 없음" 통과 "관찰 시작 이후 로그인 401 $L401건(200 $L200건) · 최근 24시간 401 $L24건 · 지금 잠긴 계정 $LOCKED · 실패 횟수 누적 $FSUM"
else verdict "5 로그인 실패 급증 없음" 주의 "최근 24시간 로그인 401 $L24건 · 잠긴 계정 $LOCKED · 관찰 이후 401 $L401건(200 $L200건) — 사람이 판단"; fi

# ---------------------------------------------------------------- 6 DB 무결성 (+ 표별 행 수 추이 — 정보)
INT=$(j "d['integrity']"); REV=$(j "d['revision']")
if [ "$INT" = ok ] && [ "$REV" = "$DEP_REV" ]; then verdict "6 DB 무결성" 통과 "integrity_check ok · 리비전 $REV · 반영 직전 대비 행 수가 바뀐 표: $(j "d['rows_changed_vs_baseline']")"
else verdict "6 DB 무결성" 실패 "integrity $INT · 리비전 $REV (배포 단위 $DEP_REV)"; fi

# ---------------------------------------------------------------- 표식 일치(7-4차) — 실행 중 backend 이미지의 app/account_markers.py(또는 app/adminctl.py)에 저장소 표식이 있는지 (읽기 전용)
if [ "$LIVE" = 1 ]; then
  MARK=$(docker run --rm -i --entrypoint python -e IMAGE_APP_DIR=/app/app -e OBS_MARKERS_PATH=/markers/account_markers.py \
    -v "$PYROOT/backend/app/account_markers.py:/markers/account_markers.py:ro" "$RUN_B" - < "$ROOT/scripts/observe_marker.py" 2>/dev/null)
else
  MARK=$(IMAGE_APP_DIR="${OBS_IMAGE_APP_DIR:-}" OBS_MARKERS_PATH="$PYROOT/backend/app/account_markers.py" "$PY" -I -X utf8 "$PYROOT/scripts/observe_marker.py" 2>/dev/null)
fi
MARK=$(printf '%s' "${MARK:-error 결과 없음}" | head -1 | tr -d '\r')
case "$MARK" in match*) MARK_OK=1; MARK_TXT="표식 일치(이미지 ${MARK#match })" ;; *) MARK_OK=0; MARK_TXT="운영 이미지와 저장소 표식 불일치 — 발급 이력을 놓칠 수 있음 ($MARK)" ;; esac

# ---------------------------------------------------------------- 7 임시 상태 계정 — 발급(provision)·재발급(password_reset)·설치 담당자 명령의 생성 비밀번호(admin_recover·setup) 모두
TA=$(j "d['temp_now']['active']"); TO=$(j "d['temp_now']['other']"); IC=$(j "d['issues_since_start']['count']"); IO=$(j "d['issues_since_start']['over_24h']"); IS=$(j "d['issues_since_start']['still_open']")
KINDS="발급 $(j "d['issues_since_start']['kinds']['provision']") · 재발급 $(j "d['issues_since_start']['kinds']['password_reset']") · 복구 명령 임시 $(j "d['issues_since_start']['kinds']['admin_recover']")"
if [ "$TA" = 0 ] && [ "$TO" = 0 ] && [ "$IO" = 0 ]; then
  if [ "$MARK_OK" = 1 ]; then verdict "7 임시 상태 계정" 통과 "지금 이용 중 임시 $TA · 그 밖(중지 등) 임시 $TO · 관찰 중 발급·재발급 $IC건($KINDS; 미변경 $IS, 24시간 초과 $IO) · 관찰 중 계정 이력 $(j "d['events_since_start']") · $MARK_TXT"
  else verdict "7 임시 상태 계정" 주의 "$MARK_TXT · 지금 이용 중 임시 $TA · 그 밖 임시 $TO · 발급·재발급 $IC건($KINDS; 미변경 $IS, 24시간 초과 $IO) — 이미지의 표식이 다르면 생성 비밀번호 이력(admin_recover·setup)을 발급으로 세지 못한다"; fi
else verdict "7 임시 상태 계정" $([ "$IO" = 0 ] && [ "$TA" = 0 ] && echo 주의 || echo 실패) "지금 이용 중 임시 $TA · 그 밖 임시 $TO · 발급·재발급 $IC건($KINDS; 미변경 $IS, 24시간 초과 $IO) — 런북 8절(원칙 금지·당일 변경 확인) · $MARK_TXT"; fi

# ---------------------------------------------------------------- 8 복구 사건 0
if [ "$LIVE" = 1 ]; then
  NEWLOGS=$(ls "$BK_DIR"/deploy-*.log 2>/dev/null | sort | awk -v d="$DEPLOY_LOG" '$0 > d' | wc -l)
  RB=$(find "$BK_DIR" -maxdepth 1 \( -name 'moajwo-data-before-rollback-*.tgz' -o -name 'bprime-*.conf' -o -name 'rollback-*.status*.json' \) -newer "$DEPLOY_LOG" 2>/dev/null | wc -l)
  RS_B=$(inspect "$B_CID" '{{.RestartCount}}'); RS_F=$(inspect "$F_CID" '{{.RestartCount}}')
  if [ "${RUN_B:7:12}" = "$DEP_B" ] && [ "${RUN_F:7:12}" = "$DEP_F" ] && [ "$NEWLOGS" = 0 ] && [ "$RB" = 0 ] && [ "${RS_B:-1}" = 0 ] && [ "${RS_F:-1}" = 0 ]; then
    verdict "8 복구 사건 0" 통과 "실행 이미지 = 배포 단위 · 그 뒤 반영 로그 0 · 롤백 백업·B′ 설정 파일 0 · 재시작 0"
  else verdict "8 복구 사건 0" 실패 "실행 backend ${RUN_B:7:12}(단위 $DEP_B) · frontend ${RUN_F:7:12}(단위 $DEP_F) · 새 반영 로그 $NEWLOGS · 롤백 흔적 $RB · 재시작 $RS_B/$RS_F — 사건이 있었으면 런북 1절 기록 확인"; fi
else verdict "8 복구 사건 0" 건너뜀 "시험 모드 — 운영 컨테이너·반영 기록 폴더를 조회하지 않음"; fi

say "== 자동 판정: 통과 $PASS · 주의 $WARN · 실패 $FAILN · 건너뜀 $SKIP (항목 3~8) · 1·2는 근거만 — 관찰 종료 판정은 소유자(두 조건: 종료 가능 시각 경과 + 업무 흐름 1회 이상)"
say "기록: $OUT · DB 재료 $DBJ"
[ "$FAILN" -eq 0 ]
