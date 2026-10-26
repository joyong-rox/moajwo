#!/usr/bin/env bash
# docs/DEPLOY.md 4장 B·A 롤백 리허설 (격리 러너 전용 — 운영 PC·운영 Docker 엔진에서 돌리지 않는다).
# 문서의 단계 상자(<!-- rollback-step:N --> 다음 bash 상자)를 후보 트리의 문서에서 그대로 꺼내 실행한다 — 명령을 옮겨
# 적거나 고치지 않는다. 운영자처럼 단계마다 '[N 완료]'가 찍힌 뒤에만 다음 단계로 간다.
#   이전(main) 기동·합성 자료 → 후보 반영(scripts/deploy.sh 그대로) → 실패 경로 6가지(그 뒤 파괴적 단계가 실행되지 않는지:
#   리비전·latest 이미지·서비스·백업·정리 기록) → 정상 롤백(0~7단계 — 결과 확인 필요 행·이전 판 실행 중 행을 일괄로 바꾸지
#   않는지) → 다시 후보 반영 → 이전 9장 명령(덧붙임 없이 up)의 수정 전 모습 → 4장 B 6·7단계로 바로잡기 → 전환에서 멈춘
#   반영(앞의 성공 반영보다 나중 — 0단계가 그 로그만 읽는지) 되돌리기 → A(검증 기록이 맞지 않는 백업 거절·복원·다시 돌리기
#   거절) 뒤 6·7단계. 판정(yes/no)이 하나라도 no이면 종료 1.
# 러너 맞춤 두 가지(5·6차와 같음): deploy.sh의 'pwd -W'(Git Bash)를 리눅스 경로로, 백업 컨테이너(uid 1000)가 쓸 수 있게
# backups/ 권한. 'pwd -W' 맞춤은 deploy.sh에만 쓰고 문서 단계 상자는 맞춤 없이('pwd -W 2>/dev/null || pwd') 돌린다.
# 합성 자료만 쓰고, 비밀번호는 실행 때 만들고 출력하지 않는다.
# 사용법: bash rollback_doc_rehearsal.sh <출력 폴더> <이전 main SHA> <후보 SHA>   (저장소 루트에서)
set -uo pipefail
OUT=$(mkdir -p "$1" && cd "$1" && pwd); OLD=$2; NEW=$3
HERE=$(cd "$(dirname "$0")" && pwd)
RES="$OUT/results.tsv"; : > "$RES"
res() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" | tee -a "$RES"; }
export PROBE_PW="Rh-$(openssl rand -hex 12)"
pwd() { if [ "${1:-}" = "-W" ]; then builtin pwd; else builtin pwd "$@"; fi; }
export -f pwd   # deploy.sh용 — 단계 상자는 run_box가 이 함수를 빼고 돌린다
STEPS="$OUT/steps"
OLD_DOC=4d245b8  # 이 문서 정리 전 통합 후보 — 수정 전 9장 롤백 명령의 출처

extract_steps() {  # 후보 트리의 docs/DEPLOY.md에서 단계 상자(0~7, A)를 그대로 꺼낸다 (내용 해시도 남김)
  mkdir -p "$STEPS"
  python3 - docs/DEPLOY.md "$STEPS" <<'PY'
import hashlib, re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
found = re.findall(r"<!-- rollback-step:(\w+) -->\n```bash\n(.*?)\n```", text, re.S)
for n, body in found:
    Path(sys.argv[2], f"step{n}.sh").write_text(body + "\n", encoding="utf-8")
print(len(found), "".join(n for n, _ in found), hashlib.sha256("".join(b for _, b in found).encode()).hexdigest()[:16])
PY
}

run_box() {  # run_box <이름> <상자> → done 또는 '<상자>:<종료 코드>' (0단계를 먼저 — 운영자가 같은 창에서 0단계를 한 것과 같게)
  local label=$1 n=$2 rc
  env -u 'BASH_FUNC_pwd%%' bash -c "$(cat "$STEPS/step0.sh")
$(cat "$STEPS/step$n.sh")" > "$OUT/$label-step$n.log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ] || ! grep -q "^\[$n 완료\]" "$OUT/$label-step$n.log"; then echo "$n:$rc"; else echo done; fi
}
run_steps() {  # run_steps <이름> <첫 단계> <끝 단계> → done 또는 '<멈춘 단계>:<종료 코드>'
  local label=$1 n r
  for n in $(seq "$2" "$3"); do
    r=$(run_box "$label" "$n")
    [ "$r" = done ] || { echo "$r"; return; }
  done
  echo done
}

newest_log() { ls backups/deploy-*.log 2>/dev/null | sort | tail -1; }
unit() { grep -h "배포 단위:" backups/deploy-*.log | tail -1; }
ts_of_unit() { unit | sed -n 's|.*backups/rollback-\([0-9-]*\)\.compose\.yml.*|\1|p'; }
dbrev() {
  docker run --rm --entrypoint python -v moajwo_moajwo-data:/data:ro moajwo-backend:latest -c \
    'import sqlite3; print(sqlite3.connect("file:/data/moajwo.db?mode=ro", uri=True).execute("select version_num from alembic_version").fetchone()[0])' 2>&1 | tail -1
}
img() { docker image inspect "$1" --format '{{.Id}}' 2>/dev/null | cut -c8-19; }
state() {  # state [배포 시각] — DB 리비전 · latest 이미지 · 실행 중 서비스 · 롤백 전 백업 수 · 정리 기록
  local ts=${1:-$(ts_of_unit)}
  printf 'rev=%s backend=%s frontend=%s running=[%s] before_rollback_backups=%s recover_json=%s' "$(dbrev)" \
    "$(img moajwo-backend:latest)" "$(img moajwo-frontend:latest)" \
    "$(docker compose ps --status running --services 2>/dev/null | sort | paste -sd' ')" \
    "$(ls backups/moajwo-data-before-rollback-*.tgz 2>/dev/null | wc -l)" \
    "$([ -f "backups/rollback-$ts.recover.json" ] && echo yes || echo no)"
}
resume() {  # 문서 '멈췄을 때' 표의 지금 판 재개 → backend healthy까지
  docker compose start backend frontend >> "$OUT/resume.log" 2>&1
  for _ in $(seq 1 90); do
    [ "$(docker inspect moajwo-backend-1 --format '{{.State.Health.Status}}' 2>/dev/null)" = healthy ] && return 0
    sleep 1
  done
  return 1
}
in_app() {  # in_app <파이썬> — 새 이미지의 one-off로 볼륨 DB에 합성 행을 넣거나 고친다 (서버를 띄우지 않음)
  docker compose run --rm --no-deps -T backend python -c "$1" 2>&1 | tail -1
}
volume_ls() {  # 볼륨 맨 위 목록 (읽기 전용)
  docker run --rm --entrypoint sh -v moajwo_moajwo-data:/data:ro moajwo-backend:latest -c 'ls -a /data | sort | paste -sd" "'
}
volume_db_sha() {
  docker run --rm --entrypoint sh -v moajwo_moajwo-data:/data:ro moajwo-backend:latest -c 'sha256sum /data/moajwo.db | cut -c1-16'
}
statuses() {  # statuses <id>... — 실행 기록 상태 (읽기 전용)
  docker run --rm --entrypoint python -v moajwo_moajwo-data:/data:ro moajwo-backend:latest -c \
    "import sqlite3, sys; c = sqlite3.connect('file:/data/moajwo.db?mode=ro', uri=True); print(' '.join(f'{i}={c.execute(\"select status from runs where id = ?\", (int(i),)).fetchone()[0]}' for i in sys.argv[1:]))" "$@" 2>&1 | tail -1
}
OTHER_DISK_RUN='import json
from datetime import UTC, datetime
from app.db import SessionLocal
from app.models import IntegrationRun
with SessionLocal() as db:
    run = IntegrationRun(status="running", trigger="manual", file_ids="[]", summary=json.dumps({"mode": "sheets"}),
                         worker="0123456789ab-otherdisk", heartbeat_at=datetime.now(UTC))
    db.add(run)
    db.commit()
    print(f"run={run.id}")'
DEAD_WORKER_RUN='import json, os
from app import runlease
from app.db import SessionLocal
from app.models import IntegrationRun
proc = "dead" + os.urandom(4).hex()
runlease.WORKERS_DIR.mkdir(parents=True, exist_ok=True)
(runlease.WORKERS_DIR / f"{proc}.lock").write_bytes(b"")
disk = runlease.identity()["disk"]
with SessionLocal() as db:
    run = IntegrationRun(status="running", trigger="manual", file_ids="[]", summary=json.dumps({"mode": "sheets"}),
                         worker=f"{disk}-{proc}")
    db.add(run)
    db.commit()
    print(f"run={run.id}")'
CHECK_AND_LEGACY_RUNS='import json
from app.db import SessionLocal
from app.models import IntegrationRun
with SessionLocal() as db:
    rows = [IntegrationRun(status="check", trigger="manual", file_ids="[]", summary=json.dumps({"mode": "sheets"}),
                           worker="0123456789ab-otherdisk", error="리허설 — 결과 확인 필요"),
            IntegrationRun(status="running", trigger="manual", file_ids="[]", summary=json.dumps({"mode": "sheets"}))]
    db.add_all(rows)
    db.commit()
    print("runs=" + " ".join(str(r.id) for r in rows))'
settle() {  # settle <행 번호> — 리허설이 넣은 행을 실패로 정리 (운영자가 판단해 정리했다고 보고)
  in_app "from sqlalchemy import update
from app.db import SessionLocal
from app.models import IntegrationRun
with SessionLocal() as db:
    db.execute(update(IntegrationRun).where(IntegrationRun.id == $1).values(status=\"failed\", error=\"리허설 정리\"))
    db.commit()
print(\"settled $1\")"
}
snapshot() {  # snapshot <단계> — 웹·프록시 health·로그인·이전 결과 다시 내려받기·행 수·볼륨·포트·이미지
  local stage=$1
  res "$stage.web" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/)" "GET / (frontend)"
  res "$stage.proxy_health" "$(curl -s http://127.0.0.1:8080/api/health)" "GET /api/health (frontend → backend)"
  API=$(python3 "$HERE/deploy_probe.py" check http://127.0.0.1:8080 "$OUT/seed.json" "$stage" 2>&1 | tail -1)
  res "$stage.api" "$API" "같은 계정 로그인·실행 기록·이전 결과 다시 내려받기"
  res "$stage.db" "$(docker run --rm --entrypoint python -v moajwo_moajwo-data:/data:ro moajwo-backend:latest -c "import sqlite3; c = sqlite3.connect('file:/data/moajwo.db?mode=ro', uri=True); print({t: c.execute('select count(*) from ' + t).fetchone()[0] for t in ('users', 'source_files', 'runs', 'mapping_decisions')}, c.execute('select version_num from alembic_version').fetchone()[0])" 2>&1 | tail -1)" "행 수·리비전 (읽기 전용)"
  res "$stage.volumes" "$(docker volume ls --format '{{.Name}}' | grep -i moajwo | tr '\n' ' ')" "moajwo 볼륨 (새 빈 볼륨 없음)"
  FRONT=$(docker inspect moajwo-frontend-1 --format '{{.Image}} {{json .HostConfig.PortBindings}}' 2>&1 | cut -c8-19,72-)
  res "$stage.frontend" "$FRONT" "frontend 이미지·포트 연결"
  res "$stage.backend_image" "$(docker inspect moajwo-backend-1 --format '{{.Image}}' 2>&1 | cut -c8-19)" "backend 이미지"
}
expect() {  # expect <이름> <기대 결과> <실제 결과> <뒤 상태> <뒤 상태에 있어야 하는 조각들...>
  local name=$1 want=$2 got=$3 after=$4 ok=yes piece
  shift 4
  case "$got" in $want) ;; *) ok=no ;; esac   # 기대는 패턴 (예: 2:* — 종료 코드는 그 명령의 것)
  for piece in "$@"; do case "$after" in *"$piece"*) ;; *) ok=no ;; esac; done
  res "$name" "$ok" "기대 $want · 실제 $got · 뒤 상태 $after"
}
check() {  # check <이름> <값> <있어야 하는 조각들...> — 모두 있으면 yes
  local name=$1 got=$2 ok=yes piece
  shift 2
  for piece in "$@"; do case "$got" in *"$piece"*) ;; *) ok=no ;; esac; done
  res "$name" "$ok" "$got"
}

# ---------------------------------------------------------------- 1. 이전 (main) → 2. 후보 반영
git checkout -q --detach "$OLD"
docker compose up -d --build --wait > "$OUT/old-up.log" 2>&1
res old.up $? "docker compose up -d --build --wait ($(git rev-parse --short HEAD))"
res seed "$(python3 "$HERE/deploy_probe.py" seed http://127.0.0.1:8080 "$OUT/seed.json" 2>&1 | tail -1)" "최초 소유자·합성 CSV·매핑 확정·묶기 결과"
snapshot old
OLD_B=$(img moajwo-backend:latest); OLD_F=$(img moajwo-frontend:latest)
mkdir -p backups && chmod 777 backups
git checkout -q --detach "$NEW"
bash scripts/deploy.sh "$NEW" 0019_analysis_runs > "$OUT/deploy-1.out" 2>&1
res deploy1.exit $? "scripts/deploy.sh $NEW 0019_analysis_runs"
snapshot new
res steps "$(extract_steps)" "docs/DEPLOY.md 4장 B·A 단계 상자 수 · 이름 · 내용 해시 (후보 트리 그대로)"
TS=$(ts_of_unit); RBF="backups/rollback-$TS.compose.yml"
NEWSTATE_B=$(img moajwo-backend:latest); NEWSTATE_F=$(img moajwo-frontend:latest)
res unit "$(unit)" "반영 로그의 배포 단위 (0단계가 읽는 줄)"
clean_records() { rm -f "backups/rollback-$TS.recover.json" "backups/rollback-$TS.status.json" "backups/rollback-$TS.status-before.json"; }
NEW_STATE="rev=0020_run_worker_lease backend=$NEWSTATE_B frontend=$NEWSTATE_F"

# ---------------------------------------------------------------- 3. 실패 경로 — 그 뒤 단계가 실행되지 않는지
# S1 이전 포트 파일 없음 → 1단계 정지 전 확인에서 멈춤 (서비스 그대로)
clean_records; mv "$RBF" "$RBF.hidden"
r=$(run_steps s1 1 7); mv "$RBF.hidden" "$RBF"
expect s1.port_file_missing "1:1" "$r" "$(state)" "$NEW_STATE" "running=[backend frontend]" "recover_json=no"
# S2 데이터 볼륨을 쓰는 다른 컨테이너 → 1단계 정지 뒤 확인에서 멈춤 (백업·정리 없음)
clean_records; before=$(ls backups/moajwo-data-before-rollback-*.tgz 2>/dev/null | wc -l)
docker run -d --name rb-extra --entrypoint sleep -v moajwo_moajwo-data:/data moajwo-backend:latest 600 > /dev/null
r=$(run_steps s2 1 7); docker rm -f rb-extra > /dev/null
expect s2.volume_in_use "1:1" "$r" "$(state)" "$NEW_STATE" "running=[]" "before_rollback_backups=$before" "recover_json=no"
res s2.resume "$(resume && echo ok || echo fail)" "docker compose start backend frontend (지금 판 재개)"
# S3 백업 폴더에 쓸 수 없음 → 2단계에서 멈춤 (정리·downgrade 없음)
clean_records; chmod 555 backups
r=$(run_steps s3 1 7); chmod 777 backups
expect s3.backup_fails "2:*" "$r" "$(state)" "$NEW_STATE" "running=[]" "recover_json=no"
res s3.resume "$(resume && echo ok || echo fail)" "지금 판 재개"
# S4 다른 디스크의 실행(생존 신호 새로움) → 3단계 recover 종료 2에서 멈춤
clean_records; s4=$(in_app "$OTHER_DISK_RUN" | sed -n 's/^run=//p')
r=$(run_steps s4 1 7)
expect s4.recover_exit_2 "3:2" "$r" "$(state)" "$NEW_STATE" "running=[]" "recover_json=yes"
res s4.settle "$(settle "$s4")" "리허설 행 정리 (run $s4)"
res s4.resume "$(resume && echo ok || echo fail)" "지금 판 재개"
# S5 정리할 행이 있는데 DB에 쓸 수 없음 → 3단계 recover 종료 1에서 멈춤
clean_records; s5=$(in_app "$DEAD_WORKER_RUN" | sed -n 's/^run=//p')
docker run --rm -u 0 --entrypoint chmod -v moajwo_moajwo-data:/data moajwo-backend:latest 444 /data/moajwo.db
r=$(run_steps s5 1 7)
docker run --rm -u 0 --entrypoint chmod -v moajwo_moajwo-data:/data moajwo-backend:latest 644 /data/moajwo.db
expect s5.recover_exit_1 "3:1" "$r" "$(state)" "$NEW_STATE" "running=[]" "recover_json=yes"
res s5.resume "$(resume && echo ok || echo fail)" "지금 판 재개 (끝난 담당의 행 run $s5는 남김 — 다음 recover가 근거대로 정리)"
# S6 4단계 뒤 담당이 있는 실행 중 행이 생김 → 5단계 downgrade 거절에서 멈춤 (이전 이미지로 바꾸지 않음)
clean_records; r=$(run_steps s6 1 4)
s6=$(in_app "$OTHER_DISK_RUN" | sed -n 's/^run=//p')
[ "$r" = done ] && r=$(run_steps s6 5 7)
expect s6.downgrade_refused "5:1" "$r" "$(state)" "$NEW_STATE" "running=[]"
res s6.downgrade_message "$(grep -o '이 판에서 시작한 실행 중인 결과 실행이 [0-9]*건' "$OUT/s6-step5.log" | head -1)" "downgrade 거절 문구"
res s6.settle "$(settle "$s6")" "리허설 행 정리 (run $s6)"
res s6.resume "$(resume && echo ok || echo fail)" "지금 판 재개"

# ---------------------------------------------------------------- 4. 정상 롤백 (0~7단계 그대로)
# 결과 확인 필요 행 하나와 이전 판(담당 없음) 실행 중 행 하나를 둔다 — 4단계가 따로 보이고, downgrade까지 바꾸지 않아야 함
read -r CHK LEG <<< "$(in_app "$CHECK_AND_LEGACY_RUNS" | sed -n 's/^runs=//p')"
clean_records; r=$(run_steps rollback 1 5)
check rollback.step4_split "$(grep -h '^running(담당 있음)' "$OUT/rollback-step4.log" 2>&1)" "running(담당 있음) []" "running(이전 판) [$LEG]" "check [$CHK]"
check rollback.rows_kept "$(statuses "$CHK" "$LEG")" "$CHK=check" "$LEG=running"
[ "$r" = done ] && r=$(run_steps rollback 6 7)
TAG="rollback-$TS"
expect rollback.steps "done" "$r" "$(state)" "rev=0019_analysis_runs" "backend=$(img "moajwo-backend:$TAG")" \
  "frontend=$(img "moajwo-frontend:$TAG")" "running=[backend frontend]" "recover_json=yes"
res rollback.recover "$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print({k: d.get(k) for k in ("failed","done","check","waiting","blocking","legacy","complete")})' "backups/rollback-$TS.recover.json" 2>&1 | tail -1)" "3단계 recover 결과"
grep -h "^\[[0-9] 완료\]" "$OUT"/rollback-step*.log | sed 's/^/  /' >> "$RES"
snapshot rollback
check rollback.serves "$API $FRONT" '"login": 200' '"result_same": true' '"80/tcp"' "$OLD_F"

# ---------------------------------------------------------------- 5. 다시 후보 → 6. 이전 9장 명령(덧붙임 없이 up)의 수정 전 모습
bash scripts/deploy.sh "$NEW" 0019_analysis_runs > "$OUT/deploy-2.out" 2>&1
res deploy2.exit $? "scripts/deploy.sh $NEW 0019_analysis_runs (롤백 뒤 다시)"
snapshot new_again
TS2=$(ts_of_unit)
git show "$OLD_DOC:docs/DEPLOY.md" > "$OUT/DEPLOY-old.md"
python3 - "$OUT/DEPLOY-old.md" "$OUT/old-ch9-rollback.sh" <<'PY'
import re, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
part = text[text.index("### 롤백 B — 데이터를 지키며 0019로 되돌리기"):]
block = re.search(r"```bash\n(.*?)\n```", part, re.S).group(1)
Path(sys.argv[2]).write_text(block + "\n", encoding="utf-8")
PY
bash -c "TAG=rollback-$TS2
$(cat "$OUT/old-ch9-rollback.sh")" > "$OUT/old-ch9.log" 2>&1
res old_ch9.exit $? "이전 문서(4d245b8) 9장 롤백 B 명령 그대로 (마지막 up에 이전 포트 덧붙임 없음)"
snapshot old_ch9
res old_ch9.config "$(docker inspect moajwo-frontend-1 --format '{{json .HostConfig.PortBindings}}' 2>&1)" "이전 frontend 이미지(80에서 들음)에 새 설정 8080 대상 — 수정 전 결함"
# 바로잡기: 4장 B 6·7단계 (리비전은 이미 0019 — 6단계 앞 확인을 통과해야 함)
r=$(run_steps fix 6 7)
res fix.steps "$r" "4장 B 6·7단계로 이전 포트 적용"
snapshot fixed
check fix.serves "$r $API $FRONT" "done" '"login": 200' '"result_same": true' '"80/tcp"'

# ---------------------------------------------------------------- 7. 전환에서 멈춘 반영 되돌리기 (앞에 성공한 반영 로그가 있어도)
# 새 backend가 마이그레이션까지 한 뒤 끝나게(덧붙임 Compose — deploy.sh에만) → deploy.sh 6단계 '전환 실패'. 그 로그에는 배포
# 단위 줄이 없고 '[중단] … 4장 (백업 …, 롤백 태그 …)' 줄만 있다. 0단계가 그 로그만 읽어야 한다 (앞 반영 TS2를 고르면 안 됨)
cat > "$OUT/fail-switch.yml" <<'YML'
services:
  backend:
    command: ["sh", "-c", "alembic upgrade head && exit 3"]
    restart: "no"
YML
DEPLOY_COMPOSE_ARGS="-f docker-compose.yml -f $OUT/fail-switch.yml" bash scripts/deploy.sh "$NEW" 0019_analysis_runs > "$OUT/deploy-3.out" 2>&1
D3=$?
LOG3=$(newest_log)
HINT3=$(grep -h "\[중단\].*docs/DEPLOY.md 4장 (백업 " "$LOG3" | tail -1)
TS3=$(printf '%s\n' "$HINT3" | sed -n 's|.*롤백 태그 rollback-\([0-9-]*\),.*|\1|p')
check deploy3.switch_failed "exit=$D3 · units_in_log=$(grep -c '배포 단위:' "$LOG3") · ts3=$TS3 · ts2=$TS2 · $HINT3" "exit=1" "units_in_log=0" "전환 실패"
res deploy3.state "$(state "$TS3")" "전환 실패 뒤 (마이그레이션은 됨 — 0020)"
r=$(run_steps f1 1 7)
check f1.unit_chosen "$(grep -h -E '^(되돌릴 배포 단위|전환에서 멈춘 반영)' "$OUT/f1-step1.log" | paste -sd' ')" \
  "되돌릴 배포 단위: 시각 $TS3 " "전환에서 멈춘 반영 — 지금 DB 리비전 0020_run_worker_lease"
expect f1.steps "done" "$r" "$(state "$TS3")" "rev=0019_analysis_runs" "backend=$(img "moajwo-backend:rollback-$TS3")" \
  "frontend=$(img "moajwo-frontend:rollback-$TS3")" "running=[backend frontend]" "recover_json=yes"
snapshot f1
check f1.serves "$API $FRONT" '"login": 200' '"result_same": true' '"80/tcp"'

# ---------------------------------------------------------------- 8. A — 검증 기록이 맞지 않는 백업 거절 → 복원 → 다시 돌리기 거절
r=$(run_steps a 1 1)   # B 0·1단계 (정지)
res a.stop "$r" "A 앞의 B 0·1단계"
cp "backups/moajwo-data-$TS3.tgz" backups/moajwo-data-before-rollback-bad.tgz
sha256sum backups/moajwo-data-before-rollback-bad.tgz | sed 's|  .*|  backups/moajwo-data-before-rollback-bad.tgz|' > backups/moajwo-data-before-rollback-bad.tgz.sha256
printf 'x' >> backups/moajwo-data-before-rollback-bad.tgz   # 기록한 sha256과 달라짐
ls_before=$(volume_ls); db_before=$(volume_db_sha)
export BK=moajwo-data-before-rollback-bad.tgz; r=$(run_box a_bad A); unset BK
check a.bad_backup_refused "$r · $(grep -h '\[중단\]' "$OUT/a_bad-stepA.log" | head -1) · $( [ "$(volume_ls)" = "$ls_before" ] && [ "$(volume_db_sha)" = "$db_before" ] && echo volume_unchanged || echo volume_changed)" \
  "A:1" "sha256이 기록과 다름" "volume_unchanged"
r=$(run_box a_ok A)
check a.restored "$r · $(volume_ls)" "done" "_pre_rollback_$TS3" "moajwo.db"
ls_after=$(volume_ls); db_after=$(volume_db_sha)
r=$(run_box a_again A)
check a.rerun_refused "$r · $(grep -h '\[중단\]' "$OUT/a_again-stepA.log" | head -1) · $( [ "$(volume_ls)" = "$ls_after" ] && [ "$(volume_db_sha)" = "$db_after" ] && echo volume_unchanged || echo volume_changed)" \
  "A:1" "보존 폴더가 이미 있음" "volume_unchanged"
r=$(run_steps a 6 7)
snapshot after_a
check a.serves "$r $API $FRONT" "done" '"login": 200' '"result_same": true' '"80/tcp"'

cp -r backups "$OUT/backups-meta" 2>/dev/null; rm -f "$OUT"/backups-meta/*.tgz   # 압축 백업(합성 자료)은 올리지 않음
docker compose logs --no-color > "$OUT/compose.log" 2>&1
column -t -s $'\t' "$RES" > "$OUT/results.txt"
bad=$(awk -F'\t' '$2 == "no" || $2 == "fail"' "$RES")
if [ -n "$bad" ]; then
  echo "== 판정 no·fail:"; printf '%s\n' "$bad"
  exit 1
fi
echo "== 판정 모두 yes"
