#!/usr/bin/env bash
# 반영 전 이미지 검증 (docs/DEPLOY.md 11장). 사용법: bash scripts/image_gate.sh <backend 이미지 ID> <frontend 이미지 ID> <기록 폴더>
# deploy.sh 3단계가 후보 이미지(ID)로 부른다. 혼자 돌려도 운영 서비스·데이터 볼륨·latest 태그를 바꾸지 않는다.
#   1) 완성 이미지 목록: 후보 이미지 안의 실행 환경을 새로 읽어(scripts/image_inventory.py — 네트워크·볼륨 없는 일회용 컨테이너) 이
#      저장소의 검증 목록 deploy/expected-images/<서비스>.json과 대조한다. 종료 0과 '실행 환경 항목 같음' 요약 줄이 모두 있어야 같다고 본다.
#      검증 목록의 출처는 이미지 ID가 아니라 (가) 이 고정 경로의 내용이 HEAD에 커밋된 것과 같음(아래에서 확인 — deploy.sh는 1단계에서
#      HEAD가 승인한 트리임을 확인), (나) CI 출처 기록 verified(image_inventory.py)로 본다. 후보에서 모은 목록은 기대값에 쓰지 않는다.
#   2) 자원 확인(scripts/memory_guard.py): 격리 점검을 시작해도 되는 여유인지 — 미달·측정 실패면 멈춘다(점검을 건너뛰지 않는다).
#   3) 격리 기동 점검(scripts/image_smoke.py) — 1이 같고 2가 통과일 때만. 종료 0이고 결과 JSON이 이 두 이미지 ID·필수 점검 전부
#      통과를 말할 때만 통과로 본다. 점검을 감싼 자원 감시(memory_guard watch)의 감시 컨테이너 정리도 그 기록(smoke.watch.json)으로
#      따로 확인한다 — 남았거나 확인하지 못하면 점검 결과와 상관없이 통과가 아니고, 둘 다 실패면 두 사유를 함께 남긴다.
# 통과하면 마지막에 <기록 폴더>/gate.result에 'status=pass'와 두 이미지 ID를 쓴다 — deploy.sh는 종료 코드와 이 파일을 함께 확인한다.
# 검증 목록·검사 도구는 이 저장소(승인한 트리)의 파일만 쓴다: 다른 목록·다른 인터프리터를 고르는 환경 변수는 두지 않는다.
set -uo pipefail
export NoDefaultCurrentDirectoryInExePath=1   # Windows: docker 등을 현재 폴더(저장소)에서 먼저 찾지 않게
# 셸 함수·시작 파일이 검증 명령을 바꾸지 못하게 — 내보낸 함수·BASH_ENV·ENV가 있거나 이미 정의된 함수가 있으면 검증하지 않는다
if env | grep -qE '^(BASH_ENV|ENV)=|^BASH_FUNC_' || [ -n "$(declare -F)" ]; then
  echo "[검증 실패] 내보낸 셸 함수·BASH_ENV·ENV가 있음 — 새 Git Bash 창에서 다시 실행"
  exit 1
fi
B="${1:-}" F="${2:-}" OUT="${3:-}"
[ -n "$B" ] && [ -n "$F" ] && [ -n "$OUT" ] || { echo "사용법: bash scripts/image_gate.sh <backend 이미지> <frontend 이미지> <기록 폴더>"; exit 2; }
ROOT=$(cd "$(dirname "$0")/.." && pwd) || { echo "[검증 실패] 저장소 위치를 알 수 없음"; exit 1; }
# 기록 폴더는 절대 경로로 — Git Bash에서는 Windows 꼴(C:/…)이어야 MSYS_NO_PATHCONV=1(deploy.sh)에서도 Windows Python이 연다
mkdir -p "$OUT" && OUT=$(cd "$OUT" && { pwd -W 2>/dev/null || pwd; }) || { echo "[검증 실패] 기록 폴더를 만들 수 없음: $OUT"; exit 1; }
cd "$ROOT" || exit 1
unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES   # 아래 git 확인이 이 저장소를 읽게
EXP=deploy/expected-images
RESULT="$OUT/gate.result"
WATCH_JSON="$OUT/smoke.watch.json"
rm -f "$RESULT" "$OUT"/backend.inventory.json "$OUT"/frontend.inventory.json "$OUT"/*.compare.txt "$OUT/smoke.json" "$WATCH_JSON"   # 앞 실행의 결과를 이번 것으로 읽지 않게
[ ! -e "$RESULT" ] || { echo "[검증 실패] 앞 실행의 검증 기록을 지우지 못함: $RESULT"; exit 1; }
verdict() {  # 실패 기록을 남기고 끝낸다 — 통과(status=pass)는 맨 끝에서만 쓴다
  printf 'status=fail\nreason=%s\nbackend=%s\nfrontend=%s\n' "$1" "$B" "$F" > "$RESULT"
  echo "[검증 실패] $1"
  exit 1
}
find_python() {  # Python 3.10 이상 — 무작위 응답을 확인해 실행 파일인 척하는 가짜(예: true)를 거른다
  local c n="pyok$RANDOM$RANDOM"
  for c in python python3; do
    [ "$("$c" -c "import sys; print('$n' if sys.version_info >= (3, 10) else 'old')" 2>/dev/null | tr -d '\r')" = "$n" ] && { printf '%s\n' "$c"; return 0; }
  done
  return 1
}
PY=$(find_python) || verdict "Python 3.10 이상을 찾을 수 없음"
echo "[검증] 후보 backend $B · frontend $F · 검증 목록 $ROOT/$EXP · 기록 $OUT"
for k in backend frontend; do
  img=$B
  [ "$k" = frontend ] && img=$F
  [ -s "$EXP/$k.json" ] || verdict "검증한 목록이 없음: $EXP/$k.json"
  # 파일 내용 그대로(변환·필터 없이)의 해시가 HEAD에 커밋된 blob과 같아야 한다 — 커밋하지 않은 변경(예: 후보에서 모은 목록을 덮어씀)을 막음
  want=$(git rev-parse -q --verify "HEAD:$EXP/$k.json" 2>/dev/null) && got=$(git hash-object --no-filters -- "$EXP/$k.json" 2>/dev/null) \
    && [ -n "$want" ] && [ "$want" = "$got" ] \
    || verdict "검증 목록이 HEAD에 커밋된 내용과 다르거나 커밋되지 않음: $EXP/$k.json — 승인한 트리에 커밋된 목록만 쓴다"
  "$PY" -I -X utf8 scripts/image_inventory.py collect "$k" "$img" "$OUT/$k.inventory.json"
  r=$?
  [ "$r" -eq 0 ] && [ -s "$OUT/$k.inventory.json" ] || verdict "$k 완성 이미지 목록을 읽을 수 없음 (종료 $r)"
  "$PY" -I -X utf8 scripts/image_inventory.py compare "$EXP/$k.json" "$OUT/$k.inventory.json" > "$OUT/$k.compare.txt" 2>&1
  r=$?
  cat "$OUT/$k.compare.txt"
  case $r in
    0) grep -q "^요약 | $k 실행 환경 항목 같음 |" "$OUT/$k.compare.txt" || verdict "$k 대조 결과를 확인할 수 없음 (종료 0인데 '같음' 요약 없음)" ;;
    4) verdict "$k: 완성 이미지가 검증한 이미지와 실행 환경이 다름 — CI 검증을 다시 하고 검증 목록을 갱신해야 함" ;;
    3) verdict "$k: 목록 대조를 할 수 없음 (입력 오류 — 필수 항목·형식·출처 기록, 또는 같은 이미지 ID인데 실행 환경이 다름)" ;;
    *) verdict "$k: 목록 대조 도구가 예상치 못하게 끝남 (종료 $r)" ;;
  esac
done
"$PY" -I -X utf8 scripts/memory_guard.py check smoke "$B" "$OUT/memory.tsv"
r=$?
[ "$r" -eq 0 ] || verdict "격리 점검 전 자원 $( [ "$r" -eq 4 ] && echo '기준 미달' || echo "확인 실패 (종료 $r)" ) — 운영은 그대로, 여유가 생긴 뒤 다시"
"$PY" -I -X utf8 scripts/memory_guard.py watch smoke "$B" "$OUT/memory.tsv" --result "$WATCH_JSON" -- "$PY" -I -X utf8 scripts/image_smoke.py "$B" "$F" "$OUT/smoke.json"
r=$?
# watch의 종료 코드는 점검 실패와 감시 자원 정리 실패를 함께 나타낸다 — 이번 watch의 기록에서 점검(감싼 명령)의 종료 코드와 정리
# 결과를 따로 읽어 판정하고, 둘 다 실패면 두 사유를 모두 남긴다. 기록이 없거나 읽을 수 없으면 정리를 확인하지 못한 것이다
WATCH=$("$PY" -I -X utf8 - "$WATCH_JSON" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as f:
        d = json.load(f)
    rc, c = d["command_exit"], d["cleanup"]
    ok = c["state"] == "removed"
    if type(rc) is not int or not isinstance(c.get("name"), str):
        raise TypeError("command_exit·cleanup 형식")
except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
    print("?")
    print(f"감시 자원 정리 확인 실패 — 감시 기록을 읽을 수 없음 ({type(e).__name__})")
    sys.exit(0)
print(rc)
if ok:
    print("removed")
elif c["state"] == "left":
    print(f"감시 자원 정리 확인 실패 — 감시 컨테이너 {c['name']}가 남음 (삭제 종료 {c.get('remove_exit')})")
else:
    print(f"감시 자원 정리 확인 실패 — 감시 컨테이너 {c['name']}가 남았는지 확인 불가 (목록 조회 종료 {c.get('list_exit')})")
PY
)
SMOKE_RC=$(printf '%s\n' "$WATCH" | sed -n 1p | tr -d '\r')
CLEAN=$(printf '%s\n' "$WATCH" | sed -n 2p | tr -d '\r')
if [ "$r" -ne 0 ] || [ "$CLEAN" != removed ] || [ "$SMOKE_RC" != 0 ]; then
  sr=$SMOKE_RC
  [ "$sr" = "?" ] && sr=$r
  case $sr in
    0) what="" ;;
    4) what="격리 기동 점검 실패 (측정한 점검이 실패)" ;;
    3) what="격리 기동 점검 도구 오류 (빠짐·중복·형식·명령 실패·점검 자원 정리 확인 불가)" ;;
    *) what="격리 기동 점검 예상치 못한 중단 (종료 $sr)" ;;
  esac
  [ "$CLEAN" = removed ] || what="${what:+$what · }${CLEAN:-감시 자원 정리 확인 실패 — 판정을 읽지 못함}"
  verdict "${what:-자원 감시(watch)가 예상치 못하게 끝남} (watch 종료 $r) — $OUT/smoke.json · $WATCH_JSON"
fi
CONFIRM=$("$PY" -I -X utf8 - "$OUT/smoke.json" "$B" "$F" <<'PY'
import json
import runpy
import sys

m = runpy.run_path("scripts/image_smoke.py", run_name="image_smoke_confirm")  # 원본을 직접 읽음 (__pycache__를 읽거나 쓰지 않음)
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        d = json.load(f)
except (OSError, ValueError) as e:
    print(f"결과 JSON을 읽을 수 없음: {e}")
    sys.exit(0)
want = [cid for cid, _ in m["REQUIRED"]]
rows = d.get("checks") if isinstance(d.get("checks"), list) else []
problems = []
if d.get("tool") != m["TOOL"]:
    problems.append(f"도구 판 {d.get('tool')!r}")
if d.get("status") != "pass" or d.get("ok") is not True:
    problems.append(f"상태 {d.get('status')!r}")
if d.get("images") != {"backend": sys.argv[2], "frontend": sys.argv[3]}:
    problems.append("이미지 ID가 이번 후보와 다름")
if [r.get("id") for r in rows if isinstance(r, dict)] != want:
    problems.append("필수 점검 목록이 다름")
if not all(isinstance(r, dict) and r.get("ok") is True and r.get("measured") is True for r in rows):
    problems.append("통과·측정되지 않은 점검이 있음")
if d.get("errors"):
    problems.append(f"도구 오류 {len(d['errors'])}건")
print("smoke-confirmed" if not problems else "; ".join(problems))
PY
)
[ "$CONFIRM" = "smoke-confirmed" ] || verdict "격리 점검 결과를 확인할 수 없음: $CONFIRM"
printf 'status=pass\nbackend=%s\nfrontend=%s\ninventory=same\nsmoke=pass\n' "$B" "$F" > "$RESULT" \
  || { echo "[검증 실패] 통과 기록을 쓰지 못함: $RESULT"; exit 1; }
echo "[검증 통과] 완성 이미지 목록 = 검증한 목록 (backend·frontend) · 자원 확인 통과 · 격리 기동 점검 통과 — 기록 $OUT"
