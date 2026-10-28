#!/usr/bin/env bash
# 반영 전 이미지 검증(docs/DEPLOY.md 11장) 리허설 — 격리 러너 전용 (운영 PC에서 돌리지 않는다).
# 이전 판(main — 지금 운영과 같은 DB 0021)을 띄우고 합성 자료를 만든 뒤, 후보 트리의 scripts/deploy.sh <후보> 0021_run_request_key로
#   1) 검증 실패: DEPLOY_EXPECTED_DIR에 OS 패키지 판 하나만 바꾼 목록 → 3단계에서 멈춤 — 서비스·컨테이너·latest 그대로, 정지·백업 없음
#   2) 정상 반영: 후보 태그 빌드 → 검증 통과 → 백업 → latest를 검증한 ID로 → up --no-build → 실행 중 이미지 = 검증한 ID,
#      리비전 0021 그대로, 같은 계정 로그인·이전 결과 다시 내려받기 해시 같음
#   3) 4장 B 롤백 (리비전이 같은 반영 — 1·2·6·7단계. 단계 상자를 후보 트리의 문서에서 그대로 꺼내 실행) → 이전 이미지·0021·자료 그대로
# 사용법: bash image_gate_rehearsal.sh <결과 폴더> <이전 SHA> <후보 SHA>   (deploy_probe.py가 이 스크립트와 같은 폴더에 있어야 함)
# 러너 맞춤: deploy.sh의 'pwd -W'(Git Bash)를 리눅스 경로로, 백업 컨테이너(uid 1000)가 쓸 수 있게 backups/ 권한.
set -uo pipefail
OUT=$(realpath -m "$1")
OLD=$2
NEW=$3
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT/steps"
RES="$OUT/results.tsv"
: > "$RES"
PASS=0
FAIL=0
check() {  # check <이름> <값> <값에 있어야 하는 조각...>
  local name=$1 val=$2 ok=통과 w
  shift 2
  for w in "$@"; do case "$val" in *"$w"*) ;; *) ok=실패 ;; esac; done
  if [ "$ok" = 통과 ]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi
  printf '%s\t%s\t%s\n' "$ok" "$name" "$val" | tee -a "$RES"
}
export PROBE_PW="Rh-$(openssl rand -hex 12)"
pwd() { if [ "${1:-}" = "-W" ]; then builtin pwd; else builtin pwd "$@"; fi; }
export -f pwd   # deploy.sh용 — 4장 단계 상자는 이 함수를 빼고 돌린다
running() { docker compose ps --status running --services 2>/dev/null | sort | paste -sd' '; }
cid() { docker compose ps -q "$1" 2>/dev/null; }
img_of() { docker inspect "$(cid "$1")" --format '{{.Image}}' 2>/dev/null; }
latest() { docker image inspect "moajwo-$1:latest" --format '{{.Id}}' 2>/dev/null; }
rev() { docker compose exec -T backend alembic current 2>/dev/null | tail -1; }
probe() { python3 "$HERE/deploy_probe.py" check http://127.0.0.1:8080 "$OUT/seed.json" "$1" 2>&1 | tail -1; }
same() { if [ -n "$1" ] && [ "$1" = "$2" ]; then echo 같음; else echo 다름; fi; }

# ---------------------------------------------------------------- 0) 이전 판 기동 · 합성 자료
git checkout -q "$OLD" || exit 2
mkdir -p backups && chmod 777 backups
docker compose up -d --build --wait > "$OUT/old-up.log" 2>&1
check old.up "exit=$? · 실행 중 $(running)" "exit=0" "backend frontend"
check old.rev "$(rev)" "0021_run_request_key (head)"
check old.seed "$(python3 "$HERE/deploy_probe.py" seed http://127.0.0.1:8080 "$OUT/seed.json" 2>&1 | tail -1)" '"bundle": 201' '"download": 200'
OLD_B=$(img_of backend)
OLD_F=$(img_of frontend)
echo "이전 이미지 backend $OLD_B · frontend $OLD_F" | tee -a "$OUT/notes.txt"
git checkout -q "$NEW" || exit 2

# ---------------------------------------------------------------- 1) 검증 실패 — 정지·백업·latest 이동 없음
BAD="$OUT/expected-bad"
mkdir -p "$BAD" && cp deploy/expected-images/*.json "$BAD/"
python3 - "$BAD/backend.json" <<'PY'
import json
import sys

p = sys.argv[1]
d = json.load(open(p, encoding="utf-8"))
d["os_packages"]["tzdata"] = d["os_packages"]["tzdata"] + "~rehearsal"  # 검증한 목록과 판 하나만 다르게
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PY
B0=$(cid backend)
F0=$(cid frontend)
LB0=$(latest backend)
LF0=$(latest frontend)
DEPLOY_EXPECTED_DIR="$BAD" bash scripts/deploy.sh "$NEW" 0021_run_request_key > "$OUT/deploy-gatefail.out" 2>&1
check gatefail.exit "exit=$?" "exit=1"
check gatefail.stop "$(grep -h '\[중단\]' "$OUT/deploy-gatefail.out" | tail -1)" "이미지 검증 실패" "latest 태그는 그대로"
check gatefail.diff "$(grep -h '^다름 |' "$OUT/deploy-gatefail.out" | head -1)" "os_packages.tzdata" "~rehearsal"
check gatefail.untouched "실행 중 $(running) · 같은 컨테이너 $(same "$B0 $F0" "$(cid backend) $(cid frontend)") · latest $(same "$LB0 $LF0" "$(latest backend) $(latest frontend)") · 백업 $(find backups -name 'moajwo-data-*.tgz' | wc -l)개 · 4단계 $(grep -c '== 4\.' "$OUT/deploy-gatefail.out")번" \
  "실행 중 backend frontend" "같은 컨테이너 같음" "latest 같음" "백업 0개" "4단계 0번"
check gatefail.api "$(probe after-gatefail)" '"login": 200' '"result_same": true'

# ---------------------------------------------------------------- 2) 정상 반영 — 검증한 ID로만 전환
bash scripts/deploy.sh "$NEW" 0021_run_request_key > "$OUT/deploy-ok.out" 2>&1
check deploy.exit "exit=$?" "exit=0"
LOG=$(find backups -name 'deploy-*.log' | sort | tail -1)
CB=$(sed -n 's/.*후보 이미지: backend \(sha256:[0-9a-f]*\) · frontend .*/\1/p' "$LOG" | tail -1)
CF=$(sed -n 's/.*후보 이미지: backend sha256:[0-9a-f]* · frontend \(sha256:[0-9a-f]*\) .*/\1/p' "$LOG" | tail -1)
echo "후보 이미지 backend $CB · frontend $CF" | tee -a "$OUT/notes.txt"
check deploy.ids "후보=실행 backend $(same "$CB" "$(img_of backend)") frontend $(same "$CF" "$(img_of frontend)") · 후보=latest backend $(same "$CB" "$(latest backend)") frontend $(same "$CF" "$(latest frontend)") · 이전 backend와 $(same "$CB" "$OLD_B")" \
  "후보=실행 backend 같음 frontend 같음" "후보=latest backend 같음 frontend 같음" "이전 backend와 다름"
check deploy.gate "$(grep -h '\[검증 통과\]' "$LOG" | tail -1)" "완성 이미지 목록 = 검증한 목록" "격리 기동 점검 통과"
check deploy.lines "$(grep -c -e '이미지 검증 통과:' -e '실행 중 이미지 = 3단계에서 검증한 이미지' -e '이미지 연결:' -e '배포 단위:' "$LOG")줄" "4줄"
check deploy.build "$(grep -h 'image: moajwo-backend:cand-' "$(find backups -name 'candidate-*.compose.yml' | sort | tail -1)")" "image: moajwo-backend:cand-"
check deploy.rev "$(rev)" "0021_run_request_key (head)"
check deploy.unit "$(grep -h '배포 단위:' "$LOG" | tail -1)" "리비전 0021_run_request_key → 0021_run_request_key (head)"
check deploy.base "$(docker image inspect "$CB" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^PYTHON_VERSION=')" "PYTHON_VERSION=3.11.18"
check deploy.api "$(probe after-deploy)" '"login": 200' '"result_same": true' '"original_preview": 200'

# ---------------------------------------------------------------- 3) 4장 B 롤백 — 리비전이 같아 1·2·6·7단계
python3 - docs/DEPLOY.md "$OUT/steps" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
found = re.findall(r"<!-- rollback-step:(\w+) -->\n```bash\n(.*?)\n```", text, re.S)
for n, body in found:
    Path(sys.argv[2], f"step{n}.sh").write_text(body + "\n", encoding="utf-8")
print("단계 상자", len(found), "".join(n for n, _ in found))
PY
run_box() {  # 0단계와 N단계를 한 셸에서 (운영자가 같은 창에서 0단계를 한 것과 같게) → done 또는 'N:종료 코드'
  local n=$1 rc
  env -u 'BASH_FUNC_pwd%%' bash -c "$(cat "$OUT/steps/step0.sh")
$(cat "$OUT/steps/step$n.sh")" > "$OUT/rollback-step$n.log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ] || ! grep -q "^\[$n 완료\]" "$OUT/rollback-step$n.log"; then echo "$n:$rc"; else echo done; fi
}
check rollback.unit "$(env -u 'BASH_FUNC_pwd%%' bash "$OUT/steps/step0.sh" 2>&1 | grep -h '되돌릴 배포 단위:' | tail -1)" "리비전 0021_run_request_key → 0021_run_request_key"
R=done
for n in 1 2 6 7; do
  r=$(run_box "$n")
  [ "$r" = done ] || { R=$r; break; }
done
check rollback.steps "$R (1·2·6·7단계 — 리비전이 같아 3~5단계 없음)" "done"
check rollback.ids "실행=이전 backend $(same "$OLD_B" "$(img_of backend)") frontend $(same "$OLD_F" "$(img_of frontend)") · latest=이전 backend $(same "$OLD_B" "$(latest backend)") frontend $(same "$OLD_F" "$(latest frontend)")" \
  "실행=이전 backend 같음 frontend 같음" "latest=이전 backend 같음 frontend 같음"
check rollback.rev "$(rev)" "0021_run_request_key (head)"
check rollback.api "$(probe after-rollback)" '"login": 200' '"result_same": true'
docker compose logs --no-color > "$OUT/compose.log" 2>&1
echo "== 결과: 통과 $PASS · 실패 $FAIL" | tee -a "$RES"
[ "$FAIL" -eq 0 ]
