#!/usr/bin/env bash
# 반영 전 이미지 검증(docs/DEPLOY.md 11장) 리허설 — 격리 러너 전용 (운영 PC에서 돌리지 않는다).
# 이전 판(main — 지금 운영과 같은 DB 0021)을 띄우고 합성 자료를 만든 뒤, 후보 트리의 scripts/deploy.sh <후보> 0021_run_request_key로
#   1) 검증 실패: OS 패키지 판 하나만 바꾼 검증 목록을 담은 시험용 커밋을 '승인'으로 반영 → 3단계에서 멈춤 — 서비스·컨테이너·
#      latest 그대로, 정지·백업 없음 (검증 목록을 바꾸는 환경 변수는 없다 — 목록은 승인한 트리의 파일)
#   1b) 격리 기동 점검 실패: frontend 설정에서 CSP 헤더만 뺀 시험용 커밋 → 목록은 같고 점검이 실패 → 3단계에서 멈춤, 점검 자원 정리 · 감시 기록은 점검 종료 4와 감시 컨테이너 정리 확인을 따로 남김
#   2) 정상 반영: 후보 태그 빌드 → 검증 통과 → 백업 → latest를 검증한 ID로 → up --no-build → 실행 중 이미지 = 검증한 ID,
#      리비전 0021 그대로, 같은 계정 로그인·이전 결과 다시 내려받기 해시 같음, 격리 점검을 감싼 감시 컨테이너를 지우고 목록으로 확인
#   3) 4장 B 롤백 (리비전이 같은 반영 — 1·2·6·7단계. 단계 상자를 후보 트리의 문서에서 그대로 꺼내 실행) → 이전 이미지·0021·자료 그대로
# 사용법: bash image_gate_rehearsal.sh <결과 폴더> <이전 SHA> <후보 SHA>   (deploy_probe.py가 이 스크립트와 같은 폴더에 있어야 함)
#   결과 폴더는 저장소 밖에 둔다 — deploy.sh 1단계가 저장소의 추적하지 않는 파일을 막는다
# 러너 맞춤: 백업 컨테이너(uid 1000)가 쓸 수 있게 backups/ 권한. (deploy.sh는 'pwd -W'가 없으면 pwd를 쓰고, 내보낸 셸 함수가 있으면 멈춘다)
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
running() { docker compose ps --status running --services 2>/dev/null | sort | paste -sd' '; }
cid() { docker compose ps -q "$1" 2>/dev/null; }
img_of() { docker inspect "$(cid "$1")" --format '{{.Image}}' 2>/dev/null; }
latest() { docker image inspect "moajwo-$1:latest" --format '{{.Id}}' 2>/dev/null; }
rev() { docker compose exec -T backend alembic current 2>/dev/null | tail -1; }
probe() { python3 "$HERE/deploy_probe.py" check http://127.0.0.1:8080 "$OUT/seed.json" "$1" 2>&1 | tail -1; }
leftover() {  # 남은 점검·감시 자원 수 — 목록 조회가 실패하면 그 사실을 값에 넣는다 (실패를 '0개'로 읽지 않음)
  local c v n
  c=$(docker ps -a --format '{{.Names}}' 2>&1) || { echo "컨테이너 목록 조회 실패: $c"; return; }
  v=$(docker volume ls --format '{{.Name}}' 2>&1) || { echo "볼륨 목록 조회 실패: $v"; return; }
  n=$(docker network ls --format '{{.Name}}' 2>&1) || { echo "네트워크 목록 조회 실패: $n"; return; }
  echo "컨테이너 $(printf '%s\n' "$c" | grep -cE '^moajwo-(smoke|memwatch)-') · 볼륨 $(printf '%s\n' "$v" | grep -c '^moajwo-smoke-') · 네트워크 $(printf '%s\n' "$n" | grep -c '^moajwo-smoke-') ·"
}
watchrec() {  # 격리 점검을 감싼 자원 감시의 기록(smoke.watch.json) — 점검의 종료 코드와 감시 컨테이너 정리 결과
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); c=d["cleanup"]; print(d["tool"], "command_exit", d["command_exit"], "exit", d["exit"], "cleanup", c["state"], "remove_exit", c["remove_exit"], "list_exit", c["list_exit"])' "$1" 2>&1
}
same() {  # 두 값이 같고 둘 다 비어 있지 않을 때만 '같음' (조회 실패로 둘 다 빈 값이면 '다름')
  if [ -n "${1// /}" ] && [ -n "${2// /}" ] && [ "$1" = "$2" ]; then echo 같음; else echo 다름; fi
}

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
git checkout -q -b rehearsal-bad-list "$NEW" || exit 2
python3 - deploy/expected-images/backend.json <<'PY'
import json
import sys

p = sys.argv[1]
d = json.load(open(p, encoding="utf-8"))
d["os_packages"]["tzdata"] = d["os_packages"]["tzdata"] + "~rehearsal"  # 검증한 목록과 판 하나만 다르게
json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PY
git -c user.email=rehearsal@example.invalid -c user.name=rehearsal commit -qam "rehearsal: one package version changed in the expected list (the gate must stop)" || exit 2
BAD_SHA=$(git rev-parse HEAD)
B0=$(cid backend)
F0=$(cid frontend)
LB0=$(latest backend)
LF0=$(latest frontend)
bash scripts/deploy.sh "$BAD_SHA" 0021_run_request_key > "$OUT/deploy-gatefail.out" 2>&1
check gatefail.exit "exit=$? ·" "exit=1 ·"   # 정확히 1 (126·127 등이 부분 문자열로 통과하지 않게)
check gatefail.stop "$(grep -h '\[중단\]' "$OUT/deploy-gatefail.out" | tail -1)" "이미지 검증 실패" "latest 태그는 그대로"
check gatefail.diff "$(grep -h '^다름 |' "$OUT/deploy-gatefail.out" | head -1)" "os_packages.tzdata" "~rehearsal"
check gatefail.result "$(cat "$(find backups -maxdepth 1 -name 'image-gate-*' -type d | sort | tail -1)/gate.result" 2>&1 | head -2 | paste -sd' ')" "status=fail"
check gatefail.untouched "실행 중 $(running) · 같은 컨테이너 backend $(same "$B0" "$(cid backend)") frontend $(same "$F0" "$(cid frontend)") · latest backend $(same "$LB0" "$(latest backend)") frontend $(same "$LF0" "$(latest frontend)") · 백업 $(find backups -name 'moajwo-data-*.tgz' | wc -l)개 · 4단계 $(grep -c '== 4\.' "$OUT/deploy-gatefail.out")번" \
  "실행 중 backend frontend" "같은 컨테이너 backend 같음 frontend 같음" "latest backend 같음 frontend 같음" "백업 0개" "4단계 0번"
check gatefail.api "$(probe after-gatefail)" '"login": 200' '"result_same": true'
git checkout -q "$NEW" || exit 2

# ---------------------------------------------------------------- 1b) 격리 기동 점검 실패 — 실제 Docker에서 점검이 실패해도 3단계에서 멈추고 정리
# frontend 설정에서 CSP 헤더만 뺀 시험용 커밋: 실행 환경 목록은 같고(설정 파일은 비교 항목이 아님) 격리 점검의 front.index만 실패해야 한다
git checkout -q -b rehearsal-bad-smoke "$NEW" || exit 2
sed -i '/add_header Content-Security-Policy/d' frontend/nginx.conf
git -c user.email=rehearsal@example.invalid -c user.name=rehearsal commit -qam "rehearsal: CSP header removed (the isolated smoke must fail)" || exit 2
SMOKE_SHA=$(git rev-parse HEAD)
B0=$(cid backend)
F0=$(cid frontend)
LB0=$(latest backend)
LF0=$(latest frontend)
bash scripts/deploy.sh "$SMOKE_SHA" 0021_run_request_key > "$OUT/deploy-smokefail.out" 2>&1
check smokefail.exit "exit=$? ·" "exit=1 ·"
check smokefail.gate "$(grep -h '^\[검증' "$OUT/deploy-smokefail.out" | tail -1) · $(grep -h '^\[[0-9:]*\] \[중단\]' "$OUT/deploy-smokefail.out" | tail -1)" \
  "[검증 실패] 격리 기동 점검 실패 (측정한 점검이 실패)" "이미지 검증 실패" "latest 태그는 그대로"
SD=$(find backups -maxdepth 1 -name 'image-gate-*' -type d | sort | tail -1)
check smokefail.json "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["status"], "failed", d["failed"], "unmeasured", len(d["unmeasured"]), "errors", len(d["errors"]))' "$SD/smoke.json" 2>&1) · $(paste -sd' ' "$SD/gate.result" 2>&1)" \
  "fail failed ['front.index']" "unmeasured 0" "errors 0" "status=fail"
check smokefail.watch "$(watchrec "$SD/smoke.watch.json")" "command_exit 4" "exit 4" "cleanup removed" "list_exit 0"   # 점검 실패는 그대로, 감시 정리는 확인
check smokefail.leftover "$(leftover)" "컨테이너 0 ·" "볼륨 0 ·" "네트워크 0 ·"
check smokefail.untouched "실행 중 $(running) · 같은 컨테이너 backend $(same "$B0" "$(cid backend)") frontend $(same "$F0" "$(cid frontend)") · latest backend $(same "$LB0" "$(latest backend)") frontend $(same "$LF0" "$(latest frontend)") · 백업 $(find backups -name 'moajwo-data-*.tgz' | wc -l)개 · 4단계 $(grep -c '== 4\.' "$OUT/deploy-smokefail.out")번" \
  "실행 중 backend frontend" "같은 컨테이너 backend 같음 frontend 같음" "latest backend 같음 frontend 같음" "백업 0개" "4단계 0번"
check smokefail.api "$(probe after-smokefail)" '"login": 200' '"result_same": true'
git checkout -q "$NEW" || exit 2

# ---------------------------------------------------------------- 2) 정상 반영 — 검증한 ID로만 전환
bash scripts/deploy.sh "$NEW" 0021_run_request_key > "$OUT/deploy-ok.out" 2>&1
check deploy.exit "exit=$? ·" "exit=0 ·"
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
GD=$(sed -n 's/.*이미지 검증 통과: .*(기록 \(backups\/image-gate-[0-9-]*\)).*/\1/p' "$LOG" | tail -1)
check deploy.gateresult "$(paste -sd' ' "$GD/gate.result" 2>&1)" "status=pass" "backend=$CB" "frontend=$CF"
check deploy.smokejson "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["status"], d["passed"], "/", d["required"], "errors", len(d["errors"]))' "$GD/smoke.json" 2>&1)" "pass 22 / 22 errors 0"
check deploy.watch "$(watchrec "$GD/smoke.watch.json")" "command_exit 0" "exit 0" "cleanup removed" "list_exit 0"   # 실제 Docker에서 감시 컨테이너를 지우고 목록으로 확인
MEMV=""   # 자원 줄은 단계마다 있어야 한다 (watch 줄은 로그에 원문과 시각 붙은 줄 두 번 남으므로 개수가 아니라 있는지로 본다)
for w in 'build 시작 전' 'build-backend 동안' 'build-frontend 동안' 'smoke 시작 전' 'smoke 동안'; do
  MEMV+="$w $(grep -qF "자원 | $w" "$LOG" && echo 있음 || echo 없음) · "
done
check deploy.memory "$MEMV$(grep -h '자원 | smoke 동안' "$LOG" | tail -1)" "build 시작 전 있음" "build-backend 동안 있음" "build-frontend 동안 있음" \
  "smoke 시작 전 있음" "smoke 동안 있음" "host 시작" "vm 시작"
check deploy.leftover "$(leftover)" "컨테이너 0 ·" "볼륨 0 ·" "네트워크 0 ·"

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
