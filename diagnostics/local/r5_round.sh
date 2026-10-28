#!/usr/bin/env bash
# 5장 두 차례 기능 검증 — 한 차례 실행기. 진단 스크립트를 메모리 보호(guarded_run) 아래 차례로 돌리고 종료 코드·시간·운영 health를 남긴다.
# 사용법: r5_round.sh <sqlite|pg> <1|2> <출력 폴더> <데이터 폴더>
#   1차: 새 계정 A(diag-user1)의 최초 사용 — 계정만 만든 뒤 A가 화면에서 먼저 올리고, 그 뒤 합성 자료·흐름
#   2차: 재시작(SQLite) 또는 빈 데이터 폴더로 재배포(PostgreSQL) 뒤 같은 A의 재로그인·반복 사용, B 소유권
set -u
MODE="$1"; ROUND="$2"; OUT="$3"; DATA="$4"
SCR="${R5_WORK:?작업 폴더(R5_WORK) — wtrc·wt36 작업 트리, build-rc2, pwvenv·dbvenv, pgsecret, tools(axe.min.js) 위치}"
DIAG="$SCR/wtrc/diagnostics/scripts"            # 최종 후보의 진단 스크립트
R5="$SCR/wt36/diagnostics/scripts/round5_check.py"
PW="$SCR/pwvenv/Scripts/python.exe"
W() { cygpath -w "$1"; }
mkdir -p "$OUT" "$SCR/ev/r5-$MODE/accounts"
export DIAG_EVIDENCE="$(W "$SCR/ev/r5-$MODE/accounts")" DIAG_SCRIPTS="$(W "$DIAG")" PYTHONIOENCODING=utf-8
export ISO_DATA="$(W "$DATA")" R5_AXE="$(W "$SCR/tools/axe.min.js")" R5_ZOOMEXT="$(W "$SCR/wtrc/diagnostics/tools/zoomext")"
if [ "$MODE" = pg ]; then
  export ISO_PG_URL="$("$SCR/dbvenv/Scripts/python.exe" "$SCR/pgurl.py" moajwo_app r5pg "sslmode=require")"
  unset ISO_DB
else
  export ISO_DB="$(W "$DATA/moajwo.db")"
  unset ISO_PG_URL
fi
SUM="$OUT/summary.tsv"
[ -f "$SUM" ] || printf "단계\t종료코드\t시간\t운영health\t시각\n" > "$SUM"
T="r5${MODE:0:1}$ROUND"   # 새 계정·라벨 접두 (예: r5s1, r5p2)
run() {  # run <이름> <스크립트> [인자...]
  local name="$1"; shift
  local start=$(date +%s)
  "$PW" -X utf8 "$SCR/tools/guarded_run.py" --start-mb 700 --floor-mb 350 --wait-s 900 -- \
    "$(W "$PW")" -X utf8 "$(W "$SCR/tools/local_diag.py")" script "$@" > "$OUT/$name.log" 2>&1
  local code=$?
  local prod; prod=$(curl -s -o /dev/null -m 5 -w '%{http_code} %{time_total}s' http://127.0.0.1:8080/api/health)
  printf "%s\t%s\t%ss\t%s\t%s\n" "$name" "$code" "$(( $(date +%s) - start ))" "$prod" "$(date +%H:%M:%S)" | tee -a "$SUM"
}
local_cmd() {  # local_cmd <이름> <local_diag 명령...>
  local name="$1"; shift
  local start=$(date +%s)
  "$PW" -X utf8 "$SCR/tools/guarded_run.py" --start-mb 700 --floor-mb 350 --wait-s 900 -- \
    "$(W "$PW")" -X utf8 "$(W "$SCR/tools/local_diag.py")" "$@" > "$OUT/$name.log" 2>&1
  printf "%s\t%s\t%ss\t%s\t%s\n" "$name" "$?" "$(( $(date +%s) - start ))" "-" "$(date +%H:%M:%S)" | tee -a "$SUM"
}
DEMO="$(W "$SCR/demo-in")"
GB="97$([ "$MODE" = pg ] && echo $((ROUND + 2)) || echo "$ROUND")"   # 용어사전 코드 앞자리 (971·972·973·974)
if [ "$ROUND" = 1 ]; then
  local_cmd seed-accounts seed
  run r5_U "$(W "$R5")" "$(W "$OUT/r5")" 1 U
  run seed "$(W "$DIAG/seed.py")"
  run flow_check "$(W "$DIAG/flow_check.py")" "$(W "$OUT/flow")"
  run r5_CDMB "$(W "$R5")" "$(W "$OUT/r5")" 1 C,D,M,B
  run review_status "$(W "$DIAG/review_status_check.py")" "$(W "$OUT/review_status")" "$T" "$T-review" "$DEMO"
  run upload_busy "$(W "$DIAG/upload_busy_check.py")" "$(W "$OUT/upload_busy")" "$T" "$(W "$SCR/tools/axe.min.js")"
  run summary_source "$(W "$DIAG/summary_source_check.py")" "$(W "$OUT/summary_source")" "$T" "$T-sum" "$DEMO"
  run mail_formula "$(W "$DIAG/mail_formula_check.py")" "$(W "$OUT/mail_formula")" "$T" "$T-mail" "$DEMO"
  run sum_partial "$(W "$DIAG/sum_partial_check.py")" "$(W "$OUT/sum_partial")" "$T" "$T-partial"
  run precision "$(W "$DIAG/precision_check.py")" "$(W "$OUT/precision")" "$T" "$T-precision"
  run glossary_basis "$(W "$DIAG/glossary_basis_check.py")" "$(W "$OUT/glossary_basis")" "$T" "$T-glossary" "$GB"
  local_cmd f1 f1 "$(W "$OUT/f1")" "$T"
  run authz "$(W "$DIAG/authz_matrix.py")" "$(W "$OUT/authz")" "$T"
  run automation "$(W "$SCR/tools/a02_automation.py")" "$(W "$OUT/a02.json")"
  run demo_expected "$(W "$SCR/tools/s9_demo_expected.py")" "$(W "$OUT/demo-expected.json")" "$DEMO"
  if [ "$MODE" = sqlite ]; then  # 화면 배치는 DB 종류와 무관 — SQLite 차례에서만
    run session "$(W "$SCR/tools/session_probe.py")" "$(W "$OUT/session.json")" "$ISO_DB"
    run r5_V "$(W "$R5")" "$(W "$OUT/r5")" 1 V
    run responsive "$(W "$SCR/tools/u05_responsive.py")" "$(W "$OUT/responsive")" "$(W "$SCR/tools/axe.min.js")"
    run a11y_keyboard_zoom "$(W "$DIAG/a11y_manual.py")" "$(W "$OUT/a11y_manual")" keyboard,zoom
  fi
else
  run reentry "$(W "$SCR/tools/s9_reentry.py")" "$(W "$OUT/reentry")"
  run flow_check_reuse "$(W "$DIAG/flow_check.py")" "$(W "$OUT/flow")"
  run r5_all "$(W "$R5")" "$(W "$OUT/r5")" 2 "U,C,D,M,B$([ "$MODE" = sqlite ] && echo ,V)"
  run review_status "$(W "$DIAG/review_status_check.py")" "$(W "$OUT/review_status")" "$T" "$T-review" "$DEMO"
  run summary_source "$(W "$DIAG/summary_source_check.py")" "$(W "$OUT/summary_source")" "$T" "$T-sum" "$DEMO"
  run mail_formula "$(W "$DIAG/mail_formula_check.py")" "$(W "$OUT/mail_formula")" "$T" "$T-mail" "$DEMO"
  run sum_partial "$(W "$DIAG/sum_partial_check.py")" "$(W "$OUT/sum_partial")" "$T" "$T-partial"
  run glossary_basis "$(W "$DIAG/glossary_basis_check.py")" "$(W "$OUT/glossary_basis")" "$T" "$T-glossary" "$GB"
  local_cmd f1 f1 "$(W "$OUT/f1")" "$T"
  run authz "$(W "$DIAG/authz_matrix.py")" "$(W "$OUT/authz")" "$T"
  run automation "$(W "$SCR/tools/a02_automation.py")" "$(W "$OUT/a02.json")"
  run demo_expected "$(W "$SCR/tools/s9_demo_expected.py")" "$(W "$OUT/demo-expected.json")" "$DEMO"
fi
echo "done" >> "$SUM"
