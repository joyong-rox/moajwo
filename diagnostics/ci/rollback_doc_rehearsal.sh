#!/usr/bin/env bash
# docs/DEPLOY.md 4장 B·A 롤백 리허설 (격리 러너 전용 — 운영 PC·운영 Docker 엔진에서 돌리지 않는다).
# 문서의 단계 상자(<!-- rollback-step:N --> 다음 bash 상자)를 후보 트리의 문서에서 그대로 꺼내 실행한다 — 명령을 옮겨
# 적거나 고치지 않는다. 운영자처럼 단계마다 '[N 완료]'가 찍힌 뒤에만 다음 단계로 간다.
#   이전(main) 기동·합성 자료 → 후보 반영(scripts/deploy.sh 그대로) → 실패 경로 6가지(그 뒤 파괴적 단계가 실행되지 않는지:
#   리비전·latest 이미지·서비스·백업·정리 기록) → 정상 롤백(0~7단계 — 결과 확인 필요 행·이전 판 실행 중 행을 일괄로 바꾸지
#   않는지) → 이미 되돌린 상태의 0·1단계 거절 → 다시 후보 반영 → 이전 9장 명령(덧붙임 없이 up)의 수정 전 모습 → 4장 B
#   6·7단계로 바로잡기 → 전환에서 멈춘 반영(앞의 성공 반영보다 나중 — 0단계가 그 로그만 읽는지) 되돌리기와 다시 붙여 넣기
#   거절 → 전환 도중에 끊긴 반영(로그 끝을 잘라 만듦) 되돌리기 → 새 반영 직후 R(롤백 태그를 지운 뒤 이전 커밋으로 다시
#   만들기)·A(서비스가 떠 있으면 거절·검증 기록이 맞지 않는 백업·다른 반영의 백업 거절·복원·다시 돌리기 거절, 반영 뒤 생긴
#   행은 보존 폴더로) 뒤 6·7단계 → 경계 2가지(운영 설정의 재시작 반복 중 전환 실패와 볼륨을 쓰는 컨테이너·백업 DB와 볼륨
#   DB의 불일치). 판정(yes/no)이 하나라도 no(또는 fail)이면 종료 1.
# 러너 맞춤 두 가지(5·6차와 같음): deploy.sh의 'pwd -W'(Git Bash)를 리눅스 경로로, 백업 컨테이너(uid 1000)가 쓸 수 있게
# backups/ 권한. 'pwd -W' 맞춤은 deploy.sh에만 쓰고 문서 단계 상자는 맞춤 없이('pwd -W 2>/dev/null || pwd') 돌린다.
# 합성 자료만 쓰고, 비밀번호는 실행 때 만들고 출력하지 않는다.
# 사용법: bash rollback_doc_rehearsal.sh <출력 폴더> <이전 main SHA> <후보 SHA>   (저장소 루트에서)
set -uo pipefail
OUT=$(mkdir -p "$1" && cd "$1" && pwd); OLD=$2; NEW=$3
HERE=$(cd "$(dirname "$0")" && pwd)
RES="$OUT/results.tsv"; : > "$RES"
NEW_HEAD=$(python3 - backend/alembic/versions <<'PY'
import re, sys
from pathlib import Path
revs, downs = set(), set()
for f in Path(sys.argv[1]).glob('*.py'):
    s = f.read_text(encoding='utf-8')
    m = re.search(r'^revision\s*(?::\s*str\s*)?=\s*[\'"]([^\'"]+)', s, re.M)
    d = re.search(r'^down_revision\s*(?::[^=]*)?=\s*[\'"]([^\'"]+)', s, re.M)
    if m:
        revs.add(m.group(1))
    if d:
        downs.add(d.group(1))
heads = sorted(revs - downs)
assert len(heads) == 1, heads
print(heads[0])
PY
) || { echo "[중단] 후보 트리의 alembic head를 정하지 못함"; exit 2; }
echo "후보 DB head: $NEW_HEAD"
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
rc=$?; check old.up "exit=$rc · docker compose up -d --build --wait ($(git rev-parse --short HEAD))" "exit=0"
res seed "$(python3 "$HERE/deploy_probe.py" seed http://127.0.0.1:8080 "$OUT/seed.json" 2>&1 | tail -1)" "최초 소유자·합성 CSV·매핑 확정·묶기 결과"
snapshot old
OLD_B=$(img moajwo-backend:latest); OLD_F=$(img moajwo-frontend:latest)
mkdir -p backups && chmod 777 backups
git checkout -q --detach "$NEW"
bash scripts/deploy.sh "$NEW" 0019_analysis_runs > "$OUT/deploy-1.out" 2>&1
rc=$?; check deploy1.exit "exit=$rc · scripts/deploy.sh $NEW 0019_analysis_runs" "exit=0"
snapshot new
res steps "$(extract_steps)" "docs/DEPLOY.md 4장 B·A 단계 상자 수 · 이름 · 내용 해시 (후보 트리 그대로)"
TS=$(ts_of_unit); RBF="backups/rollback-$TS.compose.yml"
NEWSTATE_B=$(img moajwo-backend:latest); NEWSTATE_F=$(img moajwo-frontend:latest)
res unit "$(unit)" "반영 로그의 배포 단위 (0단계가 읽는 줄)"
clean_records() { rm -f "backups/rollback-$TS.recover.json" "backups/rollback-$TS.status.json" "backups/rollback-$TS.status-before.json"; }
NEW_STATE="rev=$NEW_HEAD backend=$NEWSTATE_B frontend=$NEWSTATE_F"

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
# 이미 되돌린 상태에서 0·1단계를 다시 붙여 넣으면: 정지하지 않고 거절 (latest가 backend·frontend 모두 롤백 태그 이미지)
r=$(run_steps rollback_again 1 1)
expect rollback_again.refused "1:1" "$r" "$(state)" "rev=0019_analysis_runs" "running=[backend frontend]"
check rollback_again.message "$(grep -h '^\[중단\]' "$OUT/rollback_again-step1.log" | head -1)" "이미 되돌렸거나"

# ---------------------------------------------------------------- 5. 다시 후보 → 6. 이전 9장 명령(덧붙임 없이 up)의 수정 전 모습
bash scripts/deploy.sh "$NEW" 0019_analysis_runs > "$OUT/deploy-2.out" 2>&1
rc=$?; check deploy2.exit "exit=$rc · scripts/deploy.sh $NEW 0019_analysis_runs (롤백 뒤 다시)" "exit=0"
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
  "되돌릴 배포 단위: 시각 $TS3 " "전환에서 멈춘 반영 — 지금 DB 리비전 $NEW_HEAD"
expect f1.steps "done" "$r" "$(state "$TS3")" "rev=0019_analysis_runs" "backend=$(img "moajwo-backend:rollback-$TS3")" \
  "frontend=$(img "moajwo-frontend:rollback-$TS3")" "running=[backend frontend]" "recover_json=yes"
snapshot f1
check f1.serves "$API $FRONT" '"login": 200' '"result_same": true' '"80/tcp"'
r=$(run_steps f1_again 1 1)   # 되돌린 뒤 같은 로그로 다시 붙여 넣기 — 전환 실패 로그에는 '전환 완료' 이미지가 없어도 거절해야 함
expect f1_again.refused "1:1" "$r" "$(state "$TS3")" "rev=0019_analysis_runs" "running=[backend frontend]"
check f1_again.message "$(grep -h '^\[중단\]' "$OUT/f1_again-step1.log" | head -1)" "이미 되돌렸거나"

# ---------------------------------------------------------------- 7b. 전환 도중에 끊긴 반영 (창 닫힘·재부팅) — 로그 끝을 잘라 만든다
DEPLOY_COMPOSE_ARGS="-f docker-compose.yml -f $OUT/fail-switch.yml" bash scripts/deploy.sh "$NEW" 0019_analysis_runs > "$OUT/deploy-3b.out" 2>&1
D3B=$?
LOG3B=$(newest_log); cp "$LOG3B" "$OUT/deploy-3b-full.log"
sed -i '/\[중단\]/,$d' "$LOG3B"   # 전환 실패 줄부터 끝을 지움 — '== 6.' 뒤 compose 출력까지만 남은, 도중에 끊긴 로그
TS3B=$(sed -n 's|.*롤백 태그 .*:rollback-\([0-9-]*\)$|\1|p' "$LOG3B" | tail -1)
check deploy3b.cut "exit=$D3B · hint=$(grep -c '\[중단\]' "$LOG3B") · six=$(grep -c '== 6\. ' "$LOG3B") · ts=$TS3B" "exit=1" "hint=0" "six=1"
r=$(run_steps k1 1 7)
check k1.unit_chosen "$(grep -h -E '^(\[주의\] 반영 로그가 전환|되돌릴 배포 단위|전환에서 멈춘 반영)' "$OUT/k1-step1.log" | paste -sd' ')" \
  "[주의] 반영 로그가 전환" "되돌릴 배포 단위: 시각 $TS3B " "지금 DB 리비전 $NEW_HEAD"
expect k1.steps "done" "$r" "$(state "$TS3B")" "rev=0019_analysis_runs" "backend=$(img "moajwo-backend:rollback-$TS3B")" \
  "frontend=$(img "moajwo-frontend:rollback-$TS3B")" "running=[backend frontend]" "recover_json=yes"
snapshot k1
check k1.serves "$API $FRONT" '"login": 200' '"result_same": true' '"80/tcp"'

# ---------------------------------------------------------------- 8. 새 반영 직후 R·A — 롤백 태그가 없을 때 다시 만들기 → A(서비스가 떠
#   있으면 거절·검증 기록이 맞지 않는 백업·다른 반영의 백업 거절·복원·다시 돌리기 거절) → 6·7단계. 반영 뒤 생긴 행은 보존 폴더로
bash scripts/deploy.sh "$NEW" 0019_analysis_runs > "$OUT/deploy-4.out" 2>&1
rc=$?; check deploy4.exit "exit=$rc · scripts/deploy.sh $NEW 0019_analysis_runs (R·A 앞 새 반영)" "exit=0"
TS4=$(ts_of_unit); TAG4="rollback-$TS4"
res a.after_deploy_rows "$(in_app "$CHECK_AND_LEGACY_RUNS")" "반영 뒤 생긴 행 (A 뒤에는 보존 폴더에만 있어야 함)"
runs_in() {  # runs_in <볼륨 안 DB 경로> — 실행 기록 행 수 (읽기 전용)
  docker run --rm --entrypoint python -v moajwo_moajwo-data:/data:ro moajwo-backend:latest -c \
    "import sqlite3, sys; print(sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True).execute('select count(*) from runs').fetchone()[0])" "$1" 2>&1 | tail -1
}
RUNS_AFTER_DEPLOY=$(runs_in /data/moajwo.db)
# R: 롤백 태그를 지우면 1단계가 정지 전에 멈추고, R이 이전 커밋으로 다시 만든다 (REBUILD_FROM 없음·이미 있음은 거절)
docker image rm "moajwo-backend:$TAG4" "moajwo-frontend:$TAG4" > "$OUT/r-untag.log" 2>&1
r=$(run_steps r_missing 1 1)
expect r.tag_missing "1:1" "$r" "$(state "$TS4")" "rev=$NEW_HEAD" "running=[backend frontend]"
check r.tag_missing_message "$(grep -h '^\[중단\]' "$OUT/r_missing-step1.log" | head -1)" "롤백 태그 $TAG4 이미지 없음"
r=$(run_box r_nosrc R)
check r.needs_source "$r · $(grep -h '^\[중단\]' "$OUT/r_nosrc-stepR.log" | head -1)" "R:1" "REBUILD_FROM"
export REBUILD_FROM="$OLD"; r=$(run_box r R); unset REBUILD_FROM
check r.rebuilt "$r · $(grep -h '^\[R 완료\]' "$OUT/r-stepR.log") · tags=$(img "moajwo-backend:$TAG4")+$(img "moajwo-frontend:$TAG4") · worktrees=$(git worktree list | wc -l)" \
  "done" "[R 완료] 이전 커밋 ${OLD:0:7}" "worktrees=1"
export REBUILD_FROM="$OLD"; r=$(run_box r_again R); unset REBUILD_FROM
check r.rerun_refused "$r · $(grep -h '^\[중단\]' "$OUT/r_again-stepR.log" | head -1)" "R:1" "이미 있음"
# A: 서비스가 떠 있으면 아무것도 옮기지 않고 거절
ls_before=$(volume_ls); db_before=$(volume_db_sha)
r=$(run_box a_running A)
check a.running_refused "$r · $(grep -h '^\[중단\]' "$OUT/a_running-stepA.log" | head -1) ·$( [ "$(volume_ls)" = "$ls_before" ] && [ "$(volume_db_sha)" = "$db_before" ] && echo volume_unchanged || echo volume_changed)" \
  "A:1" "실행 중인 서비스" "volume_unchanged"
r=$(run_steps a 1 1)   # B 0·1단계 (정지 — R이 다시 만든 롤백 태그로 확인)
check a.stop "$r" "done"
BAD="moajwo-data-before-rollback-$(date +%Y%m%d-%H%M%S).tgz"   # 이 반영 뒤의 시각 — 이름 규칙은 통과하고 sha256이 다름
cp "backups/moajwo-data-$TS4.tgz" "backups/$BAD"
sha256sum "backups/$BAD" | sed "s|  .*|  backups/$BAD|" > "backups/$BAD.sha256"
printf 'x' >> "backups/$BAD"   # 기록한 sha256과 달라짐
ls_before=$(volume_ls); db_before=$(volume_db_sha)
export RESTORE_BACKUP="$BAD"; r=$(run_box a_bad A); unset RESTORE_BACKUP
check a.bad_backup_refused "$r · $(grep -h '^\[중단\]' "$OUT/a_bad-stepA.log" | head -1) ·$( [ "$(volume_ls)" = "$ls_before" ] && [ "$(volume_db_sha)" = "$db_before" ] && echo volume_unchanged || echo volume_changed)" \
  "A:1" "sha256이 기록과 다름" "volume_unchanged"
OLDER=$(ls backups/moajwo-data-before-rollback-*.tgz.sha256 | sed 's|.*/||; s|\.sha256$||' | sort | head -1)   # 앞 롤백의 2단계 백업
export RESTORE_BACKUP="$OLDER"; r=$(run_box a_other A); unset RESTORE_BACKUP
check a.other_deploy_backup_refused "$r · $OLDER · $(grep -h '^\[중단\]' "$OUT/a_other-stepA.log" | head -1) ·$( [ "$(volume_ls)" = "$ls_before" ] && [ "$(volume_db_sha)" = "$db_before" ] && echo volume_unchanged || echo volume_changed)" \
  "A:1" "이 반영($TS4)의 백업이 아님" "volume_unchanged"
r=$(run_box a_ok A)
check a.restored "$r · $(grep -h '^\[A 완료\]' "$OUT/a_ok-stepA.log") · $(volume_ls)" "done" "다음: B 6·7단계" "_pre_rollback_$TS4" "moajwo.db"
RESTORED=$(runs_in /data/moajwo.db); KEPT=$(runs_in "/data/_pre_rollback_$TS4/moajwo.db")
check a.rows_moved "restored=$RESTORED · kept=$KEPT · after_deploy=$RUNS_AFTER_DEPLOY · $([ "$RESTORED" -lt "$KEPT" ] 2>/dev/null && echo fewer_after_restore || echo not_fewer)" \
  "kept=$RUNS_AFTER_DEPLOY" "fewer_after_restore"
ls_after=$(volume_ls); db_after=$(volume_db_sha)
r=$(run_box a_again A)
check a.rerun_refused "$r · $(grep -h '^\[중단\]' "$OUT/a_again-stepA.log" | head -1) ·$( [ "$(volume_ls)" = "$ls_after" ] && [ "$(volume_db_sha)" = "$db_after" ] && echo volume_unchanged || echo volume_changed)" \
  "A:1" "보존 폴더가 이미 있음" "volume_unchanged"
r=$(run_steps a 6 7)
snapshot after_a
check a.serves "$r $API $FRONT" "done" '"login": 200' '"result_same": true' '"80/tcp"'

# ---------------------------------------------------------------- 9. 경계 2가지 (2026-10-26 지시 6장) — 그 뒤 단계(백업·정리·downgrade·이전
#   이미지 기동)가 실행되지 않는지. 9a: 운영 설정(restart: unless-stopped) 그대로 새 backend가 마이그레이션 뒤 끝나 재시작을
#   반복하는 동안의 전환 실패 → 정지 확인이 정확한지, 볼륨을 쓰는 컨테이너가 남으면 멈추는지. 9b: 백업 DB와 볼륨 DB의 불일치
#   (Docker 밖 호스트 프로세스가 2단계 동안 볼륨 DB에 계속 씀) → 검증에서 멈추고 .sha256 표시가 생기지 않는지
cat > "$OUT/fail-switch-restart.yml" <<'YML'
services:
  backend:
    command: ["sh", "-c", "alembic upgrade head && exit 3"]
YML
DEPLOY_COMPOSE_ARGS="-f docker-compose.yml -f $OUT/fail-switch-restart.yml" timeout -k 10 900 bash scripts/deploy.sh "$NEW" 0019_analysis_runs > "$OUT/deploy-5.out" 2>&1
D5=$?
[ "$D5" -ne 124 ] || pkill -f "compose up -d --wait" || true   # up --wait가 끝나지 않아 끊었으면 남은 compose도 끝낸다 (컨테이너는 그대로)
LOG5=$(newest_log)
TS5=$(sed -n 's|.*롤백 태그 .*:rollback-\([0-9-]*\)$|\1|p' "$LOG5" | tail -1)
how5=$( [ "$D5" -eq 124 ] && echo "up --wait가 900초 안에 끝나지 않아 끊음 (끊긴 로그)" || echo "스스로 끝남" )
res deploy5.exit "exit=$D5 · $how5 · hint=$(grep -c '\[중단\].*4장 (백업 ' "$LOG5") · ts5=$TS5" "재시작 반복(unless-stopped) 중 전환 실패"
BSTATE=$(docker inspect moajwo-backend-1 --format '{{.State.Status}} restarts={{.RestartCount}} policy={{.HostConfig.RestartPolicy.Name}}' 2>&1)
check restart.loop_before "$BSTATE · $(state "$TS5")" "policy=unless-stopped" "rev=$NEW_HEAD"
# 9a-1. 볼륨을 쓰는 다른 컨테이너가 재시작을 반복하는 동안 — 1단계가 정지 뒤 확인에서 멈추고 백업·정리를 하지 않아야 한다
rm -f "backups/rollback-$TS5.recover.json" "backups/rollback-$TS5.status.json" "backups/rollback-$TS5.status-before.json"
before=$(ls backups/moajwo-data-before-rollback-*.tgz 2>/dev/null | wc -l)
docker run -d --name rb-loop --restart unless-stopped --entrypoint sh -v moajwo_moajwo-data:/data moajwo-backend:latest -c 'sleep 5; exit 1' > /dev/null
r=$(run_steps rs 1 7); docker rm -f rb-loop > /dev/null
expect restart.writer_left "1:1" "$r" "$(state "$TS5")" "rev=$NEW_HEAD" "running=[]" "before_rollback_backups=$before" "recover_json=no"
check restart.writer_message "$(grep -h '^\[중단\]' "$OUT/rs-step1.log" | head -1)" "데이터 볼륨을 쓰는 컨테이너가 남음" "rb-loop"
# 9a-2. 되돌리기 — 1단계 뒤 backend 컨테이너는 '멈춤'(재시작 반복이 아님)이고 볼륨을 쓰는 컨테이너가 없어야 한다
r=$(run_steps rs2 1 1)
ST1="$(docker inspect moajwo-backend-1 --format '{{.State.Status}} restarts={{.RestartCount}}' 2>&1) · writers=[$(docker ps --filter volume=moajwo_moajwo-data --format '{{.Names}}' | paste -sd' ')]"
check restart.stop_accurate "$r · $ST1" "done" "exited" "writers=[]"
[ "$r" = done ] && r=$(run_steps rs2 2 7)
expect restart.rollback "done" "$r" "$(state "$TS5")" "rev=0019_analysis_runs" "backend=$(img "moajwo-backend:rollback-$TS5")" \
  "frontend=$(img "moajwo-frontend:rollback-$TS5")" "running=[backend frontend]" "recover_json=yes"
snapshot rs2
check restart.serves "$API $FRONT" '"login": 200' '"result_same": true' '"80/tcp"'

# 9b. 2단계(백업·검증)의 두 정지 조건을 각각 제어해 고정한다 — 둘 다 정리(recover)·downgrade 전에 멈추고 .sha256이 없어야 한다.
#   쓰기 시점을 sleep 간격의 운에 맡기지 않는다: 호스트 감시기(/proc)가 상자의 'tar czf' 프로세스를 보고 그 생존 구간 안(가) 또는
#   끝난 직후(나)에만 쓰고, 그 순서를 기록해 판정한다(순서가 어긋나면 시나리오 제어 실패로 no).
cat > "$OUT/vm-watch.py" <<'PY'
import os, sqlite3, sys, time
mp, mode = sys.argv[1], sys.argv[2]
def alive(p):
    try:
        with open(f"/proc/{p}/stat") as f:
            return f.read().split(") ", 1)[1][0] != "Z"
    except OSError:
        return False
def tar_czf():
    found = []
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/comm") as f:
                if f.read().strip() != "tar":
                    continue
            with open(f"/proc/{p}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if b"czf" in args and any(a.startswith(b"/backup/moajwo-data-before-rollback-") for a in args):
            found.append(int(p))
    return found
def probe_row():
    con = sqlite3.connect(os.path.join(mp, "moajwo.db"), isolation_level=None)
    con.execute("create table if not exists rehearsal_probe (n integer)")
    con.execute("insert into rehearsal_probe (n) values (1)")
    con.close()
deadline = time.monotonic() + 600
pids = []
while not pids and time.monotonic() < deadline:
    pids = tar_czf()
    time.sleep(0.002)
if not pids:
    print("tar_seen=no")
    sys.exit(0)
if mode == "during":
    time.sleep(0.3)  # tar가 큰 채움 파일을 압축하는 중 — 아래에서 쓰기 앞뒤로 tar가 살아 있는지 확인한다
    before = all(alive(p) for p in pids)
    with open(os.path.join(mp, "rehearsal-probe-during-tar"), "w") as f:
        f.write("x")  # 볼륨 맨 위 폴더가 바뀜
    probe_row()  # DB 내용도 바뀜
    after = all(alive(p) for p in pids)
    print(f"tar_seen=yes tar_alive_before={before} tar_alive_after={after}")
else:
    def verify_pids():  # 상자의 검증 컨테이너 프로세스: python -c '<VERIFY_PY — tarfile로 백업을 여는 코드>'
        found = []
        for p in os.listdir("/proc"):
            if not p.isdigit():
                continue
            try:
                with open(f"/proc/{p}/cmdline", "rb") as f:
                    args = f.read().split(b"\0")
            except OSError:
                continue
            if len(args) > 2 and args[0].endswith(b"python") and args[1] == b"-c" and b"tarfile" in args[2]:
                found.append(p)
        return found
    def uptime():
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    tick = os.sysconf("SC_CLK_TCK")
    while any(alive(p) for p in pids):
        time.sleep(0.001)
    before = bool(verify_pids())
    probe_row()
    written = uptime()
    seen = []
    deadline = time.monotonic() + 180
    while not seen and time.monotonic() < deadline:
        seen = verify_pids()
        time.sleep(0.002)
    started = None
    for p in seen:
        try:
            with open(f"/proc/{p}/stat") as f:
                started = int(f.read().split(") ", 1)[1].split()[19]) / tick
        except OSError:
            pass
    order = "yes" if (not before and started is not None and started > written) else "no"
    print(f"tar_seen=yes verify_before_write={before} written_uptime={written:.2f} verify_started_uptime={started} 쓰기가 먼저={order}")
PY

vm_case() {  # vm_case <이름> <during|after> <deploy 번호|-> — (새 반영 →) 1단계 정지 → 감시기 → 2단계 → 판정
  # '-'이면 새로 반영하지 않고 앞 경우가 '지금 판 재개'로 돌려놓은 같은 배포 단위를 1단계부터 다시 쓴다
  # (deploy.sh는 반영 전 리비전이 0019일 때만 반영하므로, 재개 뒤 0020 상태에서 다시 반영하지 않는다)
  local name=$1 mode=$2 dn=$3 ts r mp before sha_before wpid ctl
  if [ "$dn" != - ]; then
    bash scripts/deploy.sh "$NEW" 0019_analysis_runs > "$OUT/deploy-$dn.out" 2>&1
    r=$?; check "$name.deploy" "exit=$r · scripts/deploy.sh $NEW 0019_analysis_runs (9b $name 앞 새 반영)" "exit=0"
  fi
  ts=$(ts_of_unit)
  mp=$(docker volume inspect moajwo_moajwo-data --format '{{.Mountpoint}}')
  r=$(run_steps "$name" 1 1)
  check "$name.stop" "$r" "done"
  if [ "$r" != done ]; then  # 정지하지 못했으면 2단계를 돌리지 않는다 (떠 있는 서비스로 백업 단계를 돌리면 판정이 흐려짐)
    res "$name.skipped" "fail" "1단계가 끝나지 않아 2단계·감시기를 돌리지 않음"
    res "$name.resume" "$(resume && echo ok || echo fail)" "지금 판 재개"
    return
  fi
  [ "$mode" = during ] && sudo dd if=/dev/urandom of="$mp/rehearsal-filler.bin" bs=1M count=200 status=none   # tar가 몇 초 걸리게
  before=$(ls backups/moajwo-data-before-rollback-*.tgz 2>/dev/null | wc -l); sha_before=$(ls backups/*.sha256 2>/dev/null | wc -l)
  sudo python3 "$OUT/vm-watch.py" "$mp" "$mode" > "$OUT/$name-watch.log" 2>&1 &
  wpid=$!
  r=$(run_box "$name" 2)
  wait "$wpid" 2>/dev/null || true
  ctl=$(tail -1 "$OUT/$name-watch.log")
  expect "$name.stops" "2:1" "$r" "$(state "$ts")" "rev=$NEW_HEAD" "running=[]" "recover_json=no"
  if [ "$mode" = during ]; then
    check "$name.control" "$ctl · tar 경고 $(grep -c 'file changed as we read it' "$OUT/$name-step2.log")줄" \
      "tar_seen=yes" "tar_alive_before=True" "tar_alive_after=True"
    check "$name.message" "$(grep -h '^\[중단\]' "$OUT/$name-step2.log" | head -1)" "백업 만들기·압축 검사 실패" "정리·downgrade를 하지 않음"
  else
    check "$name.control" "$ctl" "tar_seen=yes" "verify_before_write=False" "쓰기가 먼저=yes"
    check "$name.message" "$(grep -h '^\[중단\]' "$OUT/$name-step2.log" | head -1)" "백업 검증 실패" "정리·downgrade를 하지 않음"
  fi
  check "$name.no_sha256" "new_backups=$(( $(ls backups/moajwo-data-before-rollback-*.tgz 2>/dev/null | wc -l) - before )) · new_sha256=$(( $(ls backups/*.sha256 2>/dev/null | wc -l) - sha_before ))" "new_backups=1" "new_sha256=0"
  # 리허설이 만든 것만 지운다: 채움 파일·표시 파일·탐침 표, 그리고 쓰지 않는 백업(합성 자료, 검증 표시 없음)
  sudo rm -f "$mp/rehearsal-filler.bin" "$mp/rehearsal-probe-during-tar"
  sudo python3 -c "import sqlite3, sys; c = sqlite3.connect(sys.argv[1]); c.execute('drop table if exists rehearsal_probe'); c.commit()" "$mp/moajwo.db"
  [ "$mode" = during ] && rm -f "$(ls -t backups/moajwo-data-before-rollback-*.tgz | head -1)"
  res "$name.resume" "$(resume && echo ok || echo fail)" "지금 판 재개 (리허설 파일·표는 지움)"
  snapshot "$name"
  check "$name.serves" "$API" '"login": 200' '"result_same": true'
}
# 9b-1. 압축 중 원본이 바뀜 → tar 'file changed as we read it' → '[중단] 백업 만들기·압축 검사 실패' (검증 컨테이너는 뜨지 않음)
vm_case vm_tar during 6
# 9b-2. 압축이 끝난 뒤·검증 전에 볼륨 DB가 바뀜 → '[중단] 백업 검증 실패' (백업은 만들어졌지만 검증 표시 .sha256 없음)
vm_case vm_verify after -

cp -r backups "$OUT/backups-meta" 2>/dev/null; rm -f "$OUT"/backups-meta/*.tgz   # 압축 백업(합성 자료)은 올리지 않음
docker compose logs --no-color > "$OUT/compose.log" 2>&1
column -t -s $'\t' "$RES" > "$OUT/results.txt"
bad=$(awk -F'\t' '$2 == "no" || $2 == "fail"' "$RES")
if [ -n "$bad" ]; then
  echo "== 판정 no·fail:"; printf '%s\n' "$bad"
  exit 1
fi
echo "== 판정 모두 yes"
