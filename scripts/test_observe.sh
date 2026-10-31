#!/usr/bin/env bash
# scripts/observe.sh 고정 시각 시험 (7-2차) — 합성 SQLite·합성 로그(docker logs -t 형식)·합성 반영 기록으로 판정 논리만 확인한다.
# 운영·Docker·네트워크를 쓰지 않는다(OBS_LIVE=0). 사례는 scripts/test_observe_cases.py: 같은 날 과거·미래, 자정 경계, 24시간 경계(23:59·24:00·24:01),
# 만료 세션·잠금 해제 직전/직후, admin_recover 발급(생성/고른 비밀번호/닫힘), 반영 전 로그인 제외, 시작·지금 경계, ERROR:·Traceback·앱 실패 기록 줄, 최근 24시간 401 19/20건, 업무 흐름·행 수 추이.
# 사용: bash scripts/test_observe.sh  (Git Bash 또는 리눅스; python3 또는 python 필요) — 사례별 기대 문구가 모두 기록에 있으면 통과, 하나라도 없으면 종료 1.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY=${OBS_PY:-}
if [ -z "$PY" ]; then for cnd in python3 python; do "$cnd" -c 'import sqlite3' >/dev/null 2>&1 && { PY=$cnd; break; }; done; fi
[ -n "$PY" ] || { echo "[중단] python을 찾지 못함 (OBS_PY=<python>으로 지정)"; exit 1; }
bash -n "$ROOT/scripts/observe.sh" || { echo "[중단] observe.sh 문법 오류"; exit 1; }
T=$(mktemp -d)
if command -v cygpath >/dev/null 2>&1; then TW=$(cygpath -m "$T"); else TW=$T; fi   # Git Bash: python과 bash가 같이 읽을 수 있는 경로(C:/…) — observe.sh는 MSYS 경로 변환을 끈다
CR=$(printf '\r')   # Windows python의 표준 출력·파일은 CRLF일 수 있다 — 비교 전에 뗀다
CASES=$("$PY" -I -X utf8 "$ROOT/scripts/test_observe_cases.py" list | tr -d '\r') || { echo "[중단] 사례 목록 실패"; exit 1; }
PASSN=0; FAILN=0; CASEN=0
for name in $CASES; do
  CASEN=$((CASEN + 1))
  D="$TW/$name"
  "$PY" -I -X utf8 "$ROOT/scripts/test_observe_cases.py" "$name" "$D" > /dev/null || { echo "[실패] $name: 사례 생성 실패"; FAILN=$((FAILN + 1)); continue; }
  NOW=$(head -1 "$D/now.txt" | tr -d '\r')
  mkdir -p "$D/out"
  OBS_LIVE=0 OBS_NOW_UTC="$NOW" OBS_DB_PATH="$D/synthetic.db" OBS_BLOG_FILE="$D/backend.log" OBS_FLOG_FILE="$D/frontend.log" \
    DEPLOY_LOG="$D/deploy.log" BASELINE="$D/baseline.txt" OBS_DIR="$D/out" OBS_PY="$PY" bash "$ROOT/scripts/observe.sh" > "$D/console.txt" 2>&1
  RC=$?
  OUTF=$(ls "$D/out"/observe-*.txt 2>/dev/null | head -1); DBJ=$(ls "$D/out"/observe-*.db.json 2>/dev/null | head -1)
  ok=1
  if [ -z "$OUTF" ]; then echo "[실패] $name: 기록 파일 없음 (종료 $RC)"; ok=0
  else
    while IFS= read -r exp; do exp=${exp%"$CR"}; [ -n "$exp" ] || continue; grep -qF -- "$exp" "$OUTF" || { echo "[실패] $name: 기대 문구 없음 → $exp"; ok=0; }; done < "$D/expect.txt"
    while IFS= read -r exp; do exp=${exp%"$CR"}; [ -n "$exp" ] || continue; grep -qF -- "$exp" "$DBJ" || { echo "[실패] $name: DB 재료 기대값 없음 → $exp"; ok=0; }; done < "$D/expect_json.txt"
    want=$(head -1 "$D/exit.txt" | tr -d '\r')
    if [ "$want" = nonzero ] && [ "$RC" -eq 0 ]; then echo "[실패] $name: 종료 코드 0 (실패 사례인데)"; ok=0; fi
    if [ "$want" = zero ] && [ "$RC" -ne 0 ]; then echo "[실패] $name: 종료 코드 $RC (통과 사례인데)"; ok=0; fi
    grep -q "DB 읽기 실패" "$OUTF" && { echo "[실패] $name: DB 읽기 실패 — $(tail -3 "$OUTF.dberr" 2>/dev/null)"; ok=0; }
  fi
  if [ "$ok" = 1 ]; then PASSN=$((PASSN + 1)); echo "[통과] $name (지금 $NOW, 종료 $RC)"; else FAILN=$((FAILN + 1)); [ -n "$OUTF" ] && sed 's/^/    /' "$OUTF"; fi
done
echo "== test_observe: 사례 $CASEN · 통과 $PASSN · 실패 $FAILN · 기록 $TW"
[ "$FAILN" -eq 0 ]
