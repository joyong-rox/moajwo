#!/usr/bin/env bash
# 관찰 기간 점검 (읽기 전용) — docs/RECOVERY_RUNBOOK.md 8절. 운영을 바꾸지 않는다: docker inspect·logs, 데이터 볼륨을 읽기 전용으로 붙인 one-off(python),
# curl GET과 공개 가입·최초 설정의 종료 경로(본문을 읽지 않는 410)만 쓴다. 사용법 (Git Bash, 저장소 루트에서):
#   bash scripts/observe.sh            → backups/followup-20261030/observation/observe-<시각>.txt (+ 같은 이름 .db.json)
# 출력에는 건수·상태 코드·시각·이미지 ID만 남는다 — 아이디·표시 이름·비밀번호·쿠키·업무 자료 내용은 읽지도 쓰지도 않는다.
# 판정 항목(소유자 결정 2026-10-31, 7차): 1 소유자 로그인 · 2 업무 흐름 1회(근거만) · 3 로그 오류 0 · 4 가입 410 유지 · 5 로그인 실패 급증 없음 · 6 DB 무결성 ·
#   7 판정 시점 이용 중 임시 상태 계정 0 + 관찰 중 24시간 넘게 남은 적 없음 · 8 복구 사건 0. 관찰 종료 = 2026-11-07 16:58 KST 경과 + 업무 흐름 1회 이상(둘 다).
set -uo pipefail
export MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 1
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
VOLUME=moajwo_moajwo-data
WEB=http://127.0.0.1:8080
API=http://127.0.0.1:8000
PASS=0; WARN=0; FAILN=0
say() { printf '%s\n' "$*" | tee -a "$OUT"; }
verdict() {  # verdict <항목> <통과|주의|실패|근거> <내용>
  case "$2" in 통과) PASS=$((PASS + 1)) ;; 주의) WARN=$((WARN + 1)) ;; 실패) FAILN=$((FAILN + 1)) ;; esac
  say "$1 | $2 | $3"
}
: > "$OUT"
say "# 관찰 점검 $(date '+%Y-%m-%d %H:%M:%S %z') · 저장소 $(git rev-parse --short HEAD 2>/dev/null || echo ?) · 관찰 시작 $START_UTC (2026-10-31 16:58:32 KST) · 종료 가능 시각 $END_KST KST"
NOW_S=$(date +%s); END_S=$(date -d "$END_KST" +%s 2>/dev/null || echo 0); START_S=$(date -d "$START_UTC" +%s 2>/dev/null || echo 0)
say "경과: $(( (NOW_S - START_S) / 86400 ))일 $(( ((NOW_S - START_S) % 86400) / 3600 ))시간 · 종료 가능 시각 경과: $([ "$END_S" -gt 0 ] && [ "$NOW_S" -ge "$END_S" ] && echo 예 || echo 아니오)"

# ---------------------------------------------------------------- 운영 상태 (배포 단위와 대조)
UNIT=$(grep -h '전환 완료:' "$DEPLOY_LOG" 2>/dev/null | tail -1)
DEP_B=$(printf '%s\n' "$UNIT" | sed -n 's/.* backend \([0-9a-f]\{12\}\) .*/\1/p'); DEP_F=$(printf '%s\n' "$UNIT" | sed -n 's/.* frontend \([0-9a-f]\{12\}\) .*/\1/p')
DEP_REV=$(printf '%s\n' "$UNIT" | sed -n 's/.*리비전 \([0-9a-z_]*\) (head).*/\1/p')
B_CID=$(docker compose ps -q backend 2>/dev/null); F_CID=$(docker compose ps -q frontend 2>/dev/null)
inspect() { docker inspect "$1" --format "$2" 2>/dev/null; }
RUN_B=$(inspect "$B_CID" '{{.Image}}'); RUN_F=$(inspect "$F_CID" '{{.Image}}')
say "운영: backend ${RUN_B:7:12} $(inspect "$B_CID" '{{.State.Status}}/{{.State.Health.Status}} 재시작 {{.RestartCount}} 시작 {{.State.StartedAt}}') · frontend ${RUN_F:7:12} $(inspect "$F_CID" '{{.State.Status}} 재시작 {{.RestartCount}} 시작 {{.State.StartedAt}}')"
say "배포 단위($DEPLOY_LOG): backend $DEP_B · frontend $DEP_F · 리비전 $DEP_REV"

# ---------------------------------------------------------------- DB (읽기 전용 one-off) — 건수·판정 재료만
docker run --rm --entrypoint python -e START_UTC="$START_UTC" -e BASE_ROWS="$(grep '^rows ' "$BASELINE" 2>/dev/null | tr '\n' ';')" \
  -v "$VOLUME:/data:ro" "$RUN_B" -c '
import json, os, sqlite3
from datetime import datetime, timedelta, timezone
c = sqlite3.connect("file:/data/moajwo.db?mode=ro", uri=True)
UTC = timezone.utc
start = datetime.fromisoformat(os.environ["START_UTC"].replace("Z", "+00:00"))
now = datetime.now(UTC)
def ts(v):
    if v is None:
        return None
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)   # 시각대 없는 값은 UTC로 본다
def since(table, col, where="1=1"):
    # 시각대 없는 값이 KST일 가능성에 대비해 두 기준(UTC·KST 해석) 가운데 큰 건수 — 활동 유무를 놓치지 않게
    a = c.execute(f"select count(*) from {table} where {where} and {col} >= ?", (start.strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0]
    b = c.execute(f"select count(*) from {table} where {where} and {col} >= ?", ((start + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0]
    return max(a, b)
out = {"revision": c.execute("select version_num from alembic_version").fetchone()[0], "integrity": c.execute("pragma integrity_check").fetchone()[0]}
names = [r[0] for r in c.execute("select name from sqlite_master where type = ? and name not like ? order by name", ("table", "sqlite_%"))]
rows = {n: c.execute("select count(*) from [" + n + "]").fetchone()[0] for n in names}
base = dict((p.split(" ")[1], int(p.split(" ")[2])) for p in os.environ.get("BASE_ROWS", "").split(";") if p.strip())
out["rows"] = rows
out["rows_changed_vs_baseline"] = {n: [base.get(n), rows[n]] for n in rows if base and base.get(n) != rows[n]}
# 1 소유자 로그인 근거 · 2 업무 흐름 근거
owner_login = c.execute("select last_login_at from users where role = ? order by last_login_at desc limit 1", ("owner",)).fetchone()
out["owner_login_since_start"] = bool(owner_login and owner_login[0] and ts(owner_login[0]) >= start - timedelta(hours=9))
out["sessions_since_start"] = since("auth_sessions", "created_at")
out["sessions_active_now"] = c.execute("select count(*) from auth_sessions where expires_at > ?", (now.isoformat(),)).fetchone()[0]
out["flow_since_start"] = {"uploads": since("source_files", "uploaded_at"), "mapping_decisions": since("mapping_decisions", "decided_at"),
                           "review_sessions": since("review_sessions", "created_at"), "runs_created": since("runs", "created_at"),
                           "runs_done": since("runs", "created_at", "status = \"done\""), "analysis_runs": since("analysis_runs", "created_at")}
# 5 로그인 실패 재료
out["locked_accounts_now"] = c.execute("select count(*) from users where locked_until is not null and locked_until > ?", (now.isoformat(),)).fetchone()[0]
out["failed_logins_counter_sum"] = c.execute("select coalesce(sum(failed_logins), 0) from users").fetchone()[0]
# 7 임시 상태 계정 — 지금 상태와 관찰 중 이력(발급·재발급 → 본인 변경·폐기까지 24시간 넘었는지)
out["temp_now"] = {"active": c.execute("select count(*) from users where must_change_password and status = ?", ("active",)).fetchone()[0],
                   "other": c.execute("select count(*) from users where must_change_password and status != ?", ("active",)).fetchone()[0]}
ev = c.execute("select target_id, action, created_at from account_events where action in (?, ?, ?, ?) and result = ? order by id",
               ("provision", "password_reset", "first_password_change", "password_revoke", "ok")).fetchall()
issues = []
for tid, action, created in ev:
    t = ts(created)
    if action in ("provision", "password_reset") and t >= start:
        close = next((ts(c2) for t2, a2, c2 in ev if t2 == tid and a2 in ("first_password_change", "password_revoke") and ts(c2) > t), None)
        hours = ((close or now) - t).total_seconds() / 3600
        issues.append({"kind": action, "closed": close is not None, "open_hours": round(hours, 1), "over_24h": hours > 24})
out["issues_since_start"] = {"count": len(issues), "over_24h": sum(1 for i in issues if i["over_24h"]), "still_open": sum(1 for i in issues if not i["closed"]), "items": issues}
out["events_since_start"] = dict(c.execute("select action, count(*) from account_events where created_at >= ? group by action", (start.strftime("%Y-%m-%d %H:%M:%S"),)).fetchall())
print(json.dumps(out, ensure_ascii=False))' > "$DBJ" 2>"$OUT.dberr"
DBRC=$?
if [ "$DBRC" -ne 0 ] || [ ! -s "$DBJ" ]; then say "[주의] DB 읽기 실패 (종료 $DBRC) — $OUT.dberr 확인"; else rm -f "$OUT.dberr"; fi
j() { docker run --rm -i --entrypoint python "$RUN_B" -c "import json, sys; d = json.load(sys.stdin); print(eval(sys.argv[1], {'d': d}))" "$1" < "$DBJ" 2>/dev/null; }

# ---------------------------------------------------------------- 1·2 근거
verdict "1 소유자 로그인" 근거 "반영 뒤 소유자 로그인 기록 $([ "$(j "d['owner_login_since_start']")" = True ] && echo 있음 || echo 없음) · 반영 뒤 만든 세션 $(j "d['sessions_since_start']")개 · 지금 유효 세션 $(j "d['sessions_active_now']")개 — 화면 확인은 7차 W1 기록(backups/followup-20261030/round7/)"
verdict "2 업무 흐름 1회" 근거 "반영 뒤 업로드 $(j "d['flow_since_start']['uploads']") · 매핑 확정 $(j "d['flow_since_start']['mapping_decisions']") · 검토 $(j "d['flow_since_start']['review_sessions']") · 통합 실행 $(j "d['flow_since_start']['runs_created']")(완료 $(j "d['flow_since_start']['runs_done']")) · 분석 $(j "d['flow_since_start']['analysis_runs']") → 업무 흐름 완료 기록 $(j "d['flow_since_start']['runs_done']")건"

# ---------------------------------------------------------------- 3 로그 오류
BLOG=$(docker logs --since "$START_UTC" "$B_CID" 2>&1); FLOG=$(docker logs --since "$START_UTC" "$F_CID" 2>&1)
TB=$(printf '%s\n' "$BLOG" | grep -c "Traceback"); ER=$(printf '%s\n' "$BLOG" | grep -c " ERROR"); FX=$(printf '%s\n' "$BLOG$FLOG" | grep -cE '" 5[0-9][0-9] '); NE=$(printf '%s\n' "$FLOG" | grep -cE '\[(error|emerg|crit|alert)\]')
if [ "$((TB + ER + FX + NE))" -eq 0 ]; then verdict "3 로그 오류 0" 통과 "backend Traceback 0 · ERROR 0 · 5xx 0 · nginx error 0 (backend $(printf '%s\n' "$BLOG" | grep -c .)줄 · frontend $(printf '%s\n' "$FLOG" | grep -c .)줄, 관찰 시작 이후)"
else verdict "3 로그 오류 0" 실패 "Traceback $TB · ERROR $ER · 5xx $FX · nginx error $NE — docker logs --since $START_UTC 확인"; fi

# ---------------------------------------------------------------- 4 가입 410 유지 (본문을 읽지 않는 종료 경로 — 운영 자료 변화 없음)
S1=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' "$WEB/api/auth/signup"); S2=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' "$WEB/api/auth/setup")
S3=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' "$API/auth/signup"); ST=$(curl -s "$WEB/api/auth/status" | tr -d ' ')
if [ "$S1" = 410 ] && [ "$S2" = 410 ] && [ "$S3" = 410 ] && printf '%s' "$ST" | grep -q '"setup_required":false'; then verdict "4 가입 410 유지" 통과 "signup $S1 · setup $S2 · backend 직접 signup $S3 · status setup_required false · health $(curl -s "$WEB/api/health" | tr -d ' ')"
else verdict "4 가입 410 유지" 실패 "signup $S1 · setup $S2 · 직접 $S3 · status $ST"; fi

# ---------------------------------------------------------------- 5 로그인 실패 급증 없음 (급증 = 최근 24시간 로그인 401 20건 이상 또는 잠긴 계정 1개 이상 → 주의, 사람 판단)
L401=$(printf '%s\n' "$BLOG" | grep -c 'POST /auth/login HTTP/1.1" 401'); L24=$(docker logs --since 24h "$B_CID" 2>&1 | grep -c 'POST /auth/login HTTP/1.1" 401'); L200=$(printf '%s\n' "$BLOG" | grep -c 'POST /auth/login HTTP/1.1" 200')
LOCKED=$(j "d['locked_accounts_now']"); FSUM=$(j "d['failed_logins_counter_sum']")
if [ "${L24:-0}" -lt 20 ] && [ "${LOCKED:-1}" = 0 ]; then verdict "5 로그인 실패 급증 없음" 통과 "관찰 시작 이후 로그인 401 $L401건(200 $L200건) · 최근 24시간 401 $L24건 · 지금 잠긴 계정 $LOCKED · 실패 횟수 누적 $FSUM"
else verdict "5 로그인 실패 급증 없음" 주의 "최근 24시간 로그인 401 $L24건 · 잠긴 계정 $LOCKED · 관찰 이후 401 $L401건 — 사람이 판단"; fi

# ---------------------------------------------------------------- 6 DB 무결성 (+ 표별 행 수 추이 — 정보)
INT=$(j "d['integrity']"); REV=$(j "d['revision']")
if [ "$INT" = ok ] && [ "$REV" = "$DEP_REV" ]; then verdict "6 DB 무결성" 통과 "integrity_check ok · 리비전 $REV · 반영 직전 대비 행 수가 바뀐 표: $(j "d['rows_changed_vs_baseline']")"
else verdict "6 DB 무결성" 실패 "integrity $INT · 리비전 $REV (배포 단위 $DEP_REV)"; fi

# ---------------------------------------------------------------- 7 임시 상태 계정
TA=$(j "d['temp_now']['active']"); TO=$(j "d['temp_now']['other']"); IC=$(j "d['issues_since_start']['count']"); IO=$(j "d['issues_since_start']['over_24h']"); IS=$(j "d['issues_since_start']['still_open']")
if [ "$TA" = 0 ] && [ "$TO" = 0 ] && [ "$IO" = 0 ]; then verdict "7 임시 상태 계정" 통과 "지금 이용 중 임시 $TA · 그 밖(중지 등) 임시 $TO · 관찰 중 발급·재발급 $IC건(미변경 $IS, 24시간 초과 $IO) · 관찰 중 계정 이력 $(j "d['events_since_start']")"
else verdict "7 임시 상태 계정" $([ "$IO" = 0 ] && [ "$TA" = 0 ] && echo 주의 || echo 실패) "지금 이용 중 임시 $TA · 그 밖 임시 $TO · 발급·재발급 $IC건(미변경 $IS, 24시간 초과 $IO) — 런북 8절(원칙 금지·당일 변경 확인)"; fi

# ---------------------------------------------------------------- 8 복구 사건 0
NEWLOGS=$(ls "$BK_DIR"/deploy-*.log 2>/dev/null | sort | awk -v d="$DEPLOY_LOG" '$0 > d' | wc -l)
RB=$(find "$BK_DIR" -maxdepth 1 \( -name 'moajwo-data-before-rollback-*.tgz' -o -name 'bprime-*.conf' -o -name 'rollback-*.status*.json' \) -newer "$DEPLOY_LOG" 2>/dev/null | wc -l)
RS_B=$(inspect "$B_CID" '{{.RestartCount}}'); RS_F=$(inspect "$F_CID" '{{.RestartCount}}')
if [ "${RUN_B:7:12}" = "$DEP_B" ] && [ "${RUN_F:7:12}" = "$DEP_F" ] && [ "$NEWLOGS" = 0 ] && [ "$RB" = 0 ] && [ "${RS_B:-1}" = 0 ] && [ "${RS_F:-1}" = 0 ]; then
  verdict "8 복구 사건 0" 통과 "실행 이미지 = 배포 단위 · 그 뒤 반영 로그 0 · 롤백 백업·B′ 설정 파일 0 · 재시작 0"
else verdict "8 복구 사건 0" 실패 "실행 backend ${RUN_B:7:12}(단위 $DEP_B) · frontend ${RUN_F:7:12}(단위 $DEP_F) · 새 반영 로그 $NEWLOGS · 롤백 흔적 $RB · 재시작 $RS_B/$RS_F — 사건이 있었으면 런북 1절 기록 확인"; fi

say "== 자동 판정: 통과 $PASS · 주의 $WARN · 실패 $FAILN (항목 3~8) · 1·2는 근거만 — 관찰 종료 판정은 소유자(두 조건: 종료 가능 시각 경과 + 업무 흐름 1회 이상)"
say "기록: $OUT · DB 재료 $DBJ"
[ "$FAILN" -eq 0 ]
