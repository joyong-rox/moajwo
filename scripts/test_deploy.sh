#!/usr/bin/env bash
# scripts/deploy.sh 실패 처리 시험 (Git Bash). 실제 Docker를 쓰지 않는다:
# 임시 git 저장소 + 명령 대역(가짜 docker)을 PATH 앞에 두고, 모든 docker 호출을 기록해 후속 단계(백업·전환·재시작)가
# 실행됐는지 확인한다. 사용법: bash scripts/test_deploy.sh
#   다른 판 시험: DEPLOY_SH=<그 판의 deploy.sh> TEST_SCRIPTS=<그 판의 scripts 폴더 — 끝의 진짜 검증 단계 사례가 쓰는 image_gate.sh·memory_guard.py>
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
STUB="$WORK/bin"
mkdir -p "$STUB" "$WORK/repo/scripts" "$WORK/repo/backend" "$WORK/repo/frontend"

# ---------------------------------------------------------------- 명령 대역 (가짜 docker)
cat > "$STUB/docker" <<'EOF'
#!/usr/bin/env bash
{ printf 'docker'; printf ' %s' "$@"; } | tr '\n' ' ' >> "$STUB_LOG"; echo >> "$STUB_LOG"   # 호출 하나 = 한 줄
S="${STUB_SCENARIO:-ok}"
switched() { [ -f "$STUB_STATE/switched" ]; }
if [ "$1" = compose ]; then
  shift
  while [ "${1:-}" = "-f" ] || [ "${1:-}" = "-p" ]; do shift 2; done
  cmd="$1"; shift
  case "$cmd" in
    ps)
      if [ "${1:-}" = "-q" ]; then
        if switched && [ "$S" = post_cid_fail ]; then exit 1; fi
        echo "cid-$2"; exit 0
      fi
      case "$S" in                       # ps --status running --services
        ps_fail|ps_fail_rev_fail) exit 1 ;;   # 비정상 종료 + 빈 표준출력
        ps_running) echo backend; exit 0 ;;
      esac
      exit 0 ;;                          # 정상 종료 + 빈 결과
    exec)                                # exec -T backend alembic current
      if switched; then
        [ "$S" = post_rev_fail ] && exit 1
        echo "0015_new (head)"
      else
        echo "0014_old (head)"
      fi
      exit 0 ;;
    up) touch "$STUB_STATE/switched"; exit 0 ;;
    config)                              # config --format json — 서비스별 image(없으면 <name>-<서비스>) · config --images — 전체 목록
      [ "$S" = name_fail ] && exit 1
      if [ "${1:-}" = "--format" ]; then
        if [ "$S" = image_tagged ]; then echo '{"name":"moajwo","services":{"backend":{"image":"moajwo-backend:v1"},"frontend":{}}}'   # image: <이름>:<태그>
        else echo '{"name":"moajwo","services":{"backend":{},"frontend":{}}}'; fi
        exit 0
      fi
      B=moajwo-backend; [ "$S" = image_tagged ] && B=moajwo-backend:v1
      [ "$S" = images_mismatch ] && B=other-backend    # Compose의 기본 이름이 <name>-<서비스> 규칙과 다른 경우
      case "${2:-}" in                   # --images <서비스>: 실제 Compose처럼 frontend는 depends_on의 backend까지 (순서 무작위 → 대역은 backend 먼저)
        backend) echo "$B" ;;
        *) printf '%s\nmoajwo-frontend\n' "$B" ;;
      esac
      exit 0 ;;
    build|stop|start) exit 0 ;;
  esac
  exit 0
fi
case "$1" in
  image)                                 # image inspect <참조> --format {{.Id}} — 후보 참조는 후보 ID, <이름>:latest는 지금 태그 상태
    [ "$S" = cand_id_fail ] && exit 1
    repo=${3%:*}
    case "$3" in
      *:latest) if [ -f "$STUB_STATE/latest-$repo" ]; then cat "$STUB_STATE/latest-$repo"; else echo "sha256:old000000000000cid-${repo#moajwo-}"; fi ;;
      *backend*) echo "sha256:cand00000000000cid-backend" ;;
      *) echo "sha256:cand00000000000cid-frontend" ;;
    esac
    exit 0 ;;
  inspect)
    case "$*" in *Health*) echo healthy; exit 0 ;; esac
    case "$*" in *PortBindings*)          # 포트 연결: 반영 전 frontend는 80, 전환 뒤는 8080
      [ "$S" = ports_fail ] && exit 1
      case "$2" in
        *backend*) if [ "$S" = ports_none ] && ! switched; then echo ""; else echo "127.0.0.1:8000:8000/tcp "; fi ;;   # ports_none: B′처럼 backend 호스트 포트 없음(조회는 성공)
        *) if switched; then echo "127.0.0.1:8080:8080/tcp "; else echo "127.0.0.1:8080:80/tcp "; fi ;;
      esac
      exit 0 ;;
    esac
    if switched; then                    # 전환 뒤 컨테이너 이미지 = 검증한 후보 ID (post_image_mismatch만 다른 ID)
      [ "$S" = post_image_fail ] && exit 1
      if [ "$S" = post_image_mismatch ]; then echo "sha256:other0000000000$2"; else echo "sha256:cand00000000000$2"; fi
    else
      echo "sha256:old000000000000$2"
    fi
    exit 0 ;;
  tag)                                   # latest 태그 상태를 기억한다. tag_fail: 후보→latest가 모두 실패.
    #                                      tag_partial: backend 후보 이동 성공 → frontend 후보 실패 → backend 되돌림 실패 (frontend 되돌림 성공)
    case "$S:$2:$3" in
      tag_fail:sha256:cand*) exit 1 ;;
      tag_partial:sha256:cand*frontend:*) exit 1 ;;
      tag_partial:sha256:old*backend:*latest) exit 1 ;;
      tag_all_fail:*:latest) exit 1 ;;     # latest를 옮기는 명령이 모두 실패 (되돌림 명령 포함) — 태그는 반영 전 그대로
    esac
    case "$3" in *:latest) printf '%s\n' "$2" > "$STUB_STATE/latest-${3%:*}" ;; esac
    exit 0 ;;
  ps)                                    # ps --filter volume=… --format {{.Names}}
    [ "$S" = volps_fail ] && exit 1      # 비정상 종료 + 빈 표준출력
    exit 0 ;;                            # 정상 종료 + 빈 결과
  run)
    ROWS='rows account_events 4\nrows alembic_version 1\nrows mapping_decisions 7\nrows users 3\n'
    case "$*" in
      *"moajwo.step=source-check"*)      # 5a. 정지 상태 운영 볼륨의 리비전·행 수
        case "$S" in
          src_fail) echo "sqlite3.OperationalError: unable to open database file" >&2; exit 1 ;;
          src_incomplete) printf 'revision 0014_old\n'; exit 0 ;;   # 정상 종료지만 행 수·끝 표시 누락
        esac
        printf "revision 0014_old\n${ROWS}end ok\n"; exit 0 ;;
      *"moajwo.step=backup-check"*)      # 5c. 백업에서 꺼낸 DB 검사
        case "$S" in
          extract_fail) echo "tar: ./moajwo.db*: Not found in archive" >&2; exit 2 ;;
          sqlite_fail) echo "sqlite3.DatabaseError: file is not a database" >&2; exit 1 ;;
          bk_corrupt) printf "revision 0014_old\nintegrity *** in database main ***\njournal delete\n${ROWS}end ok\n"; exit 0 ;;
          bk_rev) printf "revision 0013_older\nintegrity ok\njournal delete\n${ROWS}end ok\n"; exit 0 ;;
          bk_rows) printf 'revision 0014_old\nintegrity ok\njournal delete\nrows account_events 4\nrows alembic_version 1\nrows mapping_decisions 6\nrows users 3\nend ok\n'; exit 0 ;;
          bk_missing_table) printf 'revision 0014_old\nintegrity ok\njournal delete\nrows alembic_version 1\nrows mapping_decisions 7\nrows users 3\nend ok\n'; exit 0 ;;
          bk_incomplete) printf 'revision 0014_old\nintegrity ok\n'; exit 0 ;;
        esac
        printf "revision 0014_old\nintegrity ok\njournal delete\n${ROWS}end ok\n"; exit 0 ;;
      *"moajwo.step=backup"*)            # 5b. 백업 생성·압축 검사·sha256
        [ "$S" = backup_fail ] && exit 125
        dir=$(printf '%s\n' "$*" | sed -n 's/.*-v \([^ ]*\):\/backup.*/\1/p')
        arc=$(printf '%s\n' "$*" | grep -o 'moajwo-data-[0-9-]*\.tgz' | head -1)
        f="$(cygpath -u "$dir")/$arc"
        printf 'archive' > "$f"
        if [ "$S" = sha_mismatch ]; then printf '%064d\n' 0; else sha256sum "$f" | cut -d' ' -f1; fi
        exit 0 ;;
      *"--entrypoint python"*)           # 재개 전 볼륨의 DB 리비전 조회
        [ "$S" = ps_fail_rev_fail ] && exit 1
        echo "0014_old"; exit 0 ;;
    esac
    exit 0 ;;
esac
exit 0
EOF
chmod +x "$STUB/docker"

# ---------------------------------------------------------------- 임시 저장소 (승인 커밋 = HEAD, 다른 커밋 하나)
# deploy.sh는 3단계 검증(scripts/image_gate.sh)·자원 확인(scripts/memory_guard.py)을 이 저장소(승인한 트리)의 파일로만 부른다 —
# 그래서 대역도 환경 변수가 아니라 임시 저장소에 커밋한 파일로 둔다 (운영과 같은 경로).
cp "${DEPLOY_SH:-$ROOT/scripts/deploy.sh}" "$WORK/repo/scripts/deploy.sh"   # DEPLOY_SH: 다른 버전 시험용
SCRIPTS_UT="${TEST_SCRIPTS:-$ROOT/scripts}"   # TEST_SCRIPTS: 끝의 '진짜 검증 단계' 사례에 쓸 image_gate.sh·memory_guard.py의 판 (다른 버전 시험용)
unset DEPLOY_SH TEST_SCRIPTS   # deploy.sh는 DEPLOY_* 변수가 있으면 멈추므로 시험 대상에게 넘기지 않는다
cat > "$WORK/repo/scripts/image_gate.sh" <<'EOF'
#!/usr/bin/env bash
# 검증 대역 — 호출을 기록하고 시나리오대로 통과 기록(gate.result)을 쓰거나 쓰지 않는다
{ printf 'gate'; printf ' %s' "$@"; echo; } >> "$STUB_LOG"
mkdir -p "$3"
case "${STUB_SCENARIO:-ok}" in
  gate_fail) printf 'status=fail\nreason=대역\n' > "$3/gate.result"; echo "[검증 실패] (대역) 실행 환경이 다름"; exit 1 ;;
  gate_lie) echo "[검증 통과] (대역 — 종료 0이지만 통과 기록 없음)"; exit 0 ;;
  gate_wrongid) printf 'status=pass\nbackend=sha256:other\nfrontend=%s\n' "$2" > "$3/gate.result"; echo "[검증 통과] (대역 — 다른 ID)"; exit 0 ;;
esac
printf 'status=pass\nbackend=%s\nfrontend=%s\ninventory=same\nsmoke=pass\n' "$1" "$2" > "$3/gate.result"
echo "[검증 통과] (대역)"
EOF
cat > "$WORK/repo/scripts/memory_guard.py" <<'EOF'
"""자원 확인 대역 — 호출을 기록한다. check: mem_low면 4(기준 미달), mem_fail이면 3(측정 실패). watch: 감싼 명령은 실행하지 않고
가짜 docker와 같은 꼴로 호출만 기록한다(Windows Python이 bash -c 인자의 따옴표를 망가뜨리므로 — 시험이 보는 것은 deploy.sh가 넘긴
인자다). build_fail이면 빌드 실패. Windows Python은 Git Bash 꼴 경로(/tmp/…)를 못 열어 STUB_LOG_W(Windows 꼴)를 먼저 쓴다."""
import os
import sys

s = os.environ.get("STUB_SCENARIO", "ok")
log = os.environ.get("STUB_LOG_W") or os.environ["STUB_LOG"]
with open(log, "a", encoding="utf-8") as f:
    f.write("memory_guard " + " ".join(sys.argv[1:]) + "\n")
if sys.argv[1] == "check":
    rc = {"mem_low": 4, "mem_fail": 3}.get(s, 0)
    print(f"자원 | {sys.argv[2]} 시작 전: host 900 MiB · vm 5000 MiB (대역) → {({0: '통과', 4: '기준 미달', 3: '확인 실패'})[rc]}")
    sys.exit(rc)
cmd = sys.argv[sys.argv.index("--") + 1 :]
with open(log, "a", encoding="utf-8") as f:
    f.write(" ".join(cmd) + "\n")
rc = 1 if s == "build_fail" and "build" in cmd else 0
print(f"자원 | {sys.argv[2]} 동안: host 시작 900 MiB · 최저 800 MiB (표본 3) · vm 시작 5000 MiB · 최저 4900 MiB (표본 2) (대역)")
sys.exit(rc)
EOF
cd "$WORK/repo" || exit 1
git init -q && git config user.email t@example.invalid && git config user.name t && git config core.autocrlf false
echo old > backend/app.txt && echo f > frontend/app.txt && printf 'backups/\n.env\n' > .gitignore   # 운영 저장소처럼 .env는 무시
git add -A && git commit -qm old && OLD_COMMIT=$(git rev-parse HEAD)
echo new > backend/app.txt && git add -A && git commit -qm new && APPROVED=$(git rev-parse HEAD)

PASS=0; FAILN=0
run_case() {  # 이름, 시나리오, 승인 SHA, 기대 종료 코드, 있어야 할 로그(정규식), 없어야 할 호출들…
  local name="$1" scenario="$2" sha="$3" want_rc="$4" want_log="$5"; shift 5
  if [ -n "${ONLY:-}" ] && ! [[ "$name" =~ $ONLY ]]; then return 0; fi   # ONLY=<정규식>: 고른 사례만 (다른 판 비교용)
  local state="$WORK/state-$name"; rm -rf "$state" backups; mkdir -p "$state"
  export STUB_LOG="$state/calls" STUB_STATE="$state" STUB_SCENARIO="$scenario"
  STUB_LOG_W=$(cygpath -w "$STUB_LOG" 2>/dev/null || printf '%s' "$STUB_LOG")
  export STUB_LOG_W
  if [ -n "${CASE_GATE:-}" ]; then export DEPLOY_IMAGE_GATE="$CASE_GATE"; else unset DEPLOY_IMAGE_GATE; fi   # 우회 시험: 운영 판은 무시해야 함
  if [ -n "${CASE_COMPOSE_FILE:-}" ]; then export COMPOSE_FILE="$CASE_COMPOSE_FILE"; else unset COMPOSE_FILE; fi   # 기본 파일 찾기를 바꾸는 변수
  if [ -n "${CASE_ENV_FILES:-}" ]; then export COMPOSE_ENV_FILES="$CASE_ENV_FILES"; else unset COMPOSE_ENV_FILES; fi
  if [ -n "${CASE_COMPOSE_ARGS:-}" ]; then export DEPLOY_COMPOSE_ARGS="$CASE_COMPOSE_ARGS"; else unset DEPLOY_COMPOSE_ARGS; fi
  if [ -n "${CASE_REHEARSAL:-}" ]; then export DEPLOY_REHEARSAL=1 DEPLOY_TAG_REPO=rehearsal; else unset DEPLOY_REHEARSAL DEPLOY_TAG_REPO; fi
  if [ -n "${CASE_BASH_ENV:-}" ]; then printf 'true\n' > "$state/benv.sh"; export BASH_ENV="$state/benv.sh"; else unset BASH_ENV; fi
  : > "$STUB_LOG"
  PATH="$STUB:$PATH" bash scripts/deploy.sh "$sha" "${CASE_PREV:-0014_old}" > "$state/out" 2>&1
  local rc=$? ok=1 why=""
  unset BASH_ENV
  [ "$rc" -eq "$want_rc" ] || { ok=0; why="종료 코드 $rc (기대 $want_rc)"; }
  grep -Eq "$want_log" "$state/out" || { ok=0; why="$why 로그 없음: $want_log"; }
  local forbidden
  for forbidden in "$@"; do
    case "$forbidden" in
      NO_BACKUP) grep -qE "moajwo.step=backup( |$)" "$STUB_LOG" && { ok=0; why="$why 백업이 실행됨"; } ;;
      BK_CHECKED) grep -q "moajwo.step=backup-check" "$STUB_LOG" || { ok=0; why="$why 백업 DB 검사가 실행되지 않음"; } ;;
      VERIFIED) grep -qx "verified yes" backups/moajwo-data-*[0-9].txt 2>/dev/null || { ok=0; why="$why 요약에 'verified yes' 없음"; } ;;
      UNVERIFIED) grep -qx "verified no" backups/moajwo-data-*[0-9].txt 2>/dev/null || { ok=0; why="$why 요약에 'verified no' 없음"; } ;;
      NO_SWITCH) grep -qE "compose( -[fp] [^ ]+)* up( |$)" "$STUB_LOG" && { ok=0; why="$why 전환(up)이 실행됨"; } ;;
      NO_START) grep -qE "compose( -[fp] [^ ]+)* start( |$)" "$STUB_LOG" && { ok=0; why="$why 재시작이 실행됨"; } ;;
      NO_STOP) grep -qE "compose( -[fp] [^ ]+)* stop( |$)" "$STUB_LOG" && { ok=0; why="$why 정지가 실행됨"; } ;;
      NO_DONE) grep -q "전환 완료" "$state/out" && { ok=0; why="$why '전환 완료'가 출력됨"; } ;;
      START) grep -qE "compose( -[fp] [^ ]+)* start( |$)" "$STUB_LOG" || { ok=0; why="$why 재시작이 실행되지 않음"; } ;;
      ALL_COMPOSE_F)  # 모든 Compose 호출이 docker-compose.yml을 직접 지정 (기본 파일 찾기·COMPOSE_FILE이 끼어들지 못하게)
        grep "^docker compose" "$STUB_LOG" | grep -qv "^docker compose -f docker-compose.yml " && { ok=0; why="$why -f docker-compose.yml 없는 Compose 호출이 있음"; } ;;
      DONE) grep -q "전환 완료" "$state/out" || { ok=0; why="$why '전환 완료'가 없음"; } ;;
      NO_RETAG) grep -q "^docker tag sha256:cand" "$STUB_LOG" && { ok=0; why="$why 후보 이미지에 latest 태그가 붙음"; } ;;
      CAND_BUILD)  # 빌드는 후보 태그 덧붙임 파일로만, backend·frontend 모두 (한 번에든 하나씩이든) — latest를 옮기는 그냥 build 없음
        for svc in backend frontend; do
          grep -qE "compose .*-f backups/candidate-[0-9-]+\.compose\.yml build( [a-z]+)* $svc( |$)" "$STUB_LOG" \
            || { ok=0; why="$why 후보 태그 $svc 빌드가 없음"; }
        done
        grep "compose" "$STUB_LOG" | grep " build" | grep -qv "candidate-" && { ok=0; why="$why 후보 파일 없는 build가 실행됨"; }
        f=$(ls backups/candidate-*.compose.yml 2>/dev/null | head -1)
        { [ -n "$f" ] && grep -qE '^    image: moajwo-backend:cand-[0-9-]+$' "$f" && grep -qE '^    image: moajwo-frontend:cand-[0-9-]+$' "$f"; } \
          || { ok=0; why="$why 후보 빌드 덧붙임 파일이 없거나 다름"; } ;;
      GATE_CALLED) grep -qE "^gate sha256:cand00000000000cid-backend sha256:cand00000000000cid-frontend backups/image-gate-[0-9-]+$" "$STUB_LOG" \
          || { ok=0; why="$why 검증이 후보 이미지 ID로 불리지 않음"; } ;;
      RETAG_THEN_UP)  # latest를 검증한 ID로 옮긴 뒤 다시 빌드하지 않는 up
        awk '/^docker tag sha256:cand00000000000cid-backend moajwo-backend:latest/ { t = NR }
             /^docker tag sha256:cand00000000000cid-frontend moajwo-frontend:latest/ { u = NR }
             /compose .*up / { if (!w) w = NR; if ($0 ~ /--no-build/) nb = 1 }
             /moajwo.step=backup-check/ { b = NR }
             END { exit !(t && u && w && b && b < t && t < w && u < w && nb) }' "$STUB_LOG" \
          || { ok=0; why="$why 백업 검증 뒤 latest→검증 ID 태그 후 up --no-build 순서가 아님"; } ;;
      RETAG_RESTORED) grep -q "^docker tag sha256:old000000000000cid-backend moajwo-backend:latest" "$STUB_LOG" \
          && grep -q "^docker tag sha256:old000000000000cid-frontend moajwo-frontend:latest" "$STUB_LOG" \
          || { ok=0; why="$why latest가 반영 전 이미지로 되돌아가지 않음"; } ;;
      RETAG_TRUTH)  # 끝난 뒤 실제 latest 상태(대역이 기억한 값)와 출력이 맞는지: 모두 되돌아갔으면 '되돌림'을 말하고,
        #            일부만 되돌아갔으면 '되돌림 확인 안 됨'과 남은 태그를 말해야 하며 되돌렸다고 말하면 안 된다
        lb=$(cat "$state/latest-moajwo-backend" 2>/dev/null || echo sha256:old000000000000cid-backend)
        lf=$(cat "$state/latest-moajwo-frontend" 2>/dev/null || echo sha256:old000000000000cid-frontend)
        if [ "$lb" = sha256:old000000000000cid-backend ] && [ "$lf" = sha256:old000000000000cid-frontend ]; then
          { grep -Eq "반영 전 이미지로 되돌림 확인|읽은 latest는 반영 전 이미지와 같음" "$state/out" && ! grep -q "되돌림 확인 안 됨" "$state/out"; } \
            || { ok=0; why="$why latest가 반영 전 그대로인데 출력이 그렇게 말하지 않음"; }
        else
          grep -q "되돌림 확인 안 됨" "$state/out" && grep -q "backend latest ${lb:7:12}" "$state/out" \
            || { ok=0; why="$why latest가 일부만 되돌아갔는데(backend ${lb:7:12}) 출력이 그렇게 말하지 않음"; }
          grep -Eq "latest는 반영 전 이미지로 되돌림|반영 전 이미지로 되돌림 확인" "$state/out" && { ok=0; why="$why 되돌리지 못했는데 되돌렸다고 말함"; }
        fi ;;
      NO_RESTORE_CLAIM)  # 되돌림 명령이 실패했으면 '되돌림 확인'이라고 말하지 않음
        grep -q "반영 전 이미지로 되돌림 확인" "$state/out" && { ok=0; why="$why 되돌림 명령이 실패했는데 '되돌림 확인'이라고 말함"; } ;;
      RESUME_AND_TAG)  # 컨테이너 재개와 latest 태그 상태를 따로 적음
        grep -q "결과: 기존 컨테이너 재개 확인 · latest 태그:" "$state/out" || { ok=0; why="$why 재개·태그 상태를 따로 적지 않음"; } ;;
      NO_BUILD) grep "compose" "$STUB_LOG" | grep -q " build" && { ok=0; why="$why 빌드가 실행됨"; } ;;
      NO_GATE) grep -q "^gate " "$STUB_LOG" && { ok=0; why="$why 검증이 실행됨"; } ;;
      NO_GATE_RESULT) ls backups/image-gate-*/gate.result >/dev/null 2>&1 && { ok=0; why="$why 검증 단계가 실행됨 (gate.result 있음)"; } ;;
      GATE_PASS_RECORD) f="$(ls -d backups/image-gate-*/ 2>/dev/null | tail -1)gate.result"
        grep -qx 'status=pass' "$f" 2>/dev/null || { ok=0; why="$why gate.result에 통과 기록이 없음"; } ;;
      GATE_FAIL_CLEANUP)  # 진짜 검증 단계의 기록: 실패 + 감시 자원 정리 사유, 통과 기록 없음
        f="$(ls -d backups/image-gate-*/ 2>/dev/null | tail -1)gate.result"
        { [ -f "$f" ] && grep -qx 'status=fail' "$f" && ! grep -qx 'status=pass' "$f" && grep -q '감시 자원 정리 확인 실패' "$f"; } \
          || { ok=0; why="$why gate.result에 감시 정리 실패 기록이 없거나 통과 기록이 남음"; } ;;
      NO_STEP6_HEADER) grep -q "== 6\. 새 이미지로 전환" "$state/out" && { ok=0; why="$why 6단계 머리줄이 출력됨 (4장 B 0단계가 전환 도중으로 읽음)"; } ;;
      MEM_FIRST)  # 빌드 전 자원 확인(운영 backend 이미지로)이 어떤 빌드보다 먼저
        awk '/^memory_guard check build sha256:old000000000000cid-backend / { if (!m) m = NR } / build / && /compose/ { if (!b) b = NR }
             END { exit !(m && b && m < b) }' "$STUB_LOG" || { ok=0; why="$why 빌드 전 자원 확인이 먼저 오지 않음"; } ;;
      SEQ_BUILD)  # 하나씩 빌드하고 각 빌드를 자원 기록(watch)으로 감쌈
        grep -q "^memory_guard watch build-backend " "$STUB_LOG" && grep -q "^memory_guard watch build-frontend " "$STUB_LOG" \
          && ! grep -qE "build backend frontend" "$STUB_LOG" || { ok=0; why="$why 하나씩 빌드·자원 기록이 아님"; } ;;
      ROLLBACK_PORTS)  # 반영 전 포트 연결을 그대로 되돌리는 덧붙임 파일 (덧붙이지 않고 바꾸는 !override)
        f=$(ls backups/rollback-*.compose.yml 2>/dev/null | head -1)
        { [ -n "$f" ] && grep -q '^    ports: !override$' "$f" && grep -qx '      - "127.0.0.1:8080:80/tcp"' "$f" \
          && grep -qx '      - "127.0.0.1:8000:8000/tcp"' "$f"; } || { ok=0; why="$why 롤백용 포트 파일이 없거나 다름"; }
        grep -q "포트 연결: frontend 127.0.0.1:8080:80/tcp → 127.0.0.1:8080:8080/tcp" "$state/out" \
          || { ok=0; why="$why 전후 포트 연결 기록 없음"; } ;;
      ROLLBACK_PORTS_NONE)  # B′(backend 호스트 포트 없음)에서 반영: backend는 빈 목록(!override []), frontend는 그대로 — 조회 실패와 구분
        f=$(ls backups/rollback-*.compose.yml 2>/dev/null | head -1)
        { [ -n "$f" ] && grep -qx '    ports: !override \[\]' "$f" && grep -qx '      - "127.0.0.1:8080:80/tcp"' "$f" \
          && ! grep -q '8000' "$f"; } || { ok=0; why="$why backend 빈 포트 목록 기록이 없거나 다름"; } ;;
    esac
  done
  # 모든 사례 공통: 운영 볼륨은 읽기 전용으로만 마운트, 백업 DB 검사 컨테이너에는 운영 볼륨을 마운트하지 않음
  grep -o -- "-v moajwo_moajwo-data:[^ ]*" "$STUB_LOG" | grep -qv ":/data:ro$" && { ok=0; why="$why 운영 볼륨을 쓰기 가능으로 마운트"; }
  grep "moajwo.step=backup-check" "$STUB_LOG" | grep -q "moajwo_moajwo-data" && { ok=0; why="$why 백업 검사에 운영 볼륨 마운트"; }
  local calls
  calls=$(awk '/moajwo.step=/ { match($0, /moajwo.step=[a-z-]+/); print substr($0, RSTART + 12, RLENGTH - 12); next }
               /--entrypoint python/ { print "revision-check"; next }
               /^docker compose/ && match($0, / (stop|start|up)( |$)/) { print substr($0, RSTART + 1, (substr($0, RSTART + RLENGTH - 1, 1) == " ") ? RLENGTH - 2 : RLENGTH - 1) }' "$STUB_LOG" | tr '\n' ' ')
  if [ "$ok" -eq 1 ]; then PASS=$((PASS + 1)); echo "통과 | $name | 종료 $rc | 호출: ${calls:-없음}"
  else FAILN=$((FAILN + 1)); echo "실패 | $name | $why | 호출: ${calls:-없음}"; sed 's/^/      /' "$state/out" | tail -8; fi
}

echo "== deploy.sh 실패 처리 시험 (명령 대역)"
run_case "승인SHA불일치"          ok              "$OLD_COMMIT" 1 "승인한 커밋.*과 다름"                NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "Compose조회실패"        ps_fail         "$APPROVED"   1 "실행 중 서비스를 확인할 수 없음"      NO_BACKUP NO_SWITCH NO_DONE START
run_case "Compose조회실패+리비전확인실패" ps_fail_rev_fail "$APPROVED" 1 "수동 확인 필요"               NO_BACKUP NO_SWITCH NO_DONE NO_START
run_case "볼륨조회실패"           volps_fail      "$APPROVED"   1 "볼륨을 쓰는 컨테이너를 확인할 수 없음" NO_BACKUP NO_SWITCH NO_DONE START
run_case "정지후실행중서비스남음" ps_running      "$APPROVED"   1 "정지 후에도 실행 중"                 NO_BACKUP NO_SWITCH NO_DONE START
run_case "두조회정상+빈결과"      ok              "$APPROVED"   0 "정지 확인 \\(두 조회 모두 성공"      DONE NO_START
run_case "롤백단위에포트연결기록"  ok              "$APPROVED"   0 "이전 포트 backups/rollback-"          DONE ROLLBACK_PORTS
run_case "포트조회실패"           ports_fail      "$APPROVED"   1 "frontend 컨테이너의 포트 연결을 확인할 수 없음|backend 컨테이너의 포트 연결을 확인할 수 없음" NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "B′상태_backend포트없음_진행" ports_none    "$APPROVED"   0 "반영 전 포트 연결: backend \(없음 — 호스트 포트를 닫은 덧붙임 상태" DONE ROLLBACK_PORTS_NONE NO_START
run_case "백업실패"               backup_fail     "$APPROVED"   1 "백업 생성·압축 검사 실패"            UNVERIFIED NO_SWITCH NO_DONE START
run_case "전환후이미지조회실패"   post_image_fail "$APPROVED"   1 "전환 후 backend 이미지를 확인할 수 없음" NO_DONE
run_case "전환후리비전조회실패"   post_rev_fail   "$APPROVED"   1 "전환 후 리비전을 확인할 수 없음"       NO_DONE
run_case "전환후컨테이너조회실패" post_cid_fail   "$APPROVED"   1 "전환 후 backend 컨테이너를 확인할 수 없음" NO_DONE
echo "== 백업 내부 DB 검증 (전환 차단)"
run_case "정상백업_검증후전환"     ok               "$APPROVED" 0 "비교 결과: 리비전 같음 \(0014_old\) · 테이블 4개 행 수 모두 같음" BK_CHECKED VERIFIED DONE NO_START
run_case "핵심테이블일부없는리비전" ok              "$APPROVED" 0 "\[없음\] auth_sessions \(핵심\): 이 리비전의 DB에 없는 테이블" BK_CHECKED VERIFIED DONE NO_START
CASE_PREV=0017_auth_version \
run_case "반영전리비전이예상과다름" ok               "$APPROVED" 1 "반영 전 리비전이 0014_old 입니다 \(예상 0017_auth_version\)" NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "운영볼륨조회실패"       src_fail         "$APPROVED" 1 "운영 볼륨 DB 조회 실패"             NO_BACKUP NO_SWITCH NO_DONE START
run_case "운영볼륨결과누락"       src_incomplete   "$APPROVED" 1 "운영 볼륨 DB 조회 결과가 불완전함"   NO_BACKUP NO_SWITCH NO_DONE START
run_case "백업DB손상(원본정상)"   bk_corrupt       "$APPROVED" 1 "백업 DB 무결성 검사 실패"           UNVERIFIED NO_SWITCH NO_DONE START
run_case "백업DB리비전다름"       bk_rev           "$APPROVED" 1 "리비전 불일치: 운영 볼륨 0014_old · 백업 0013_older" UNVERIFIED NO_SWITCH NO_DONE START
run_case "백업DB행수다름"         bk_rows          "$APPROVED" 1 "\[불일치\] mapping_decisions \(핵심\): 운영 볼륨 7 · 백업 6" UNVERIFIED NO_SWITCH NO_DONE START
run_case "백업DB테이블누락"       bk_missing_table "$APPROVED" 1 "\[불일치\] account_events \(핵심\): 운영 볼륨 4 · 백업 없음" UNVERIFIED NO_SWITCH NO_DONE START
run_case "백업DB추출실패"         extract_fail     "$APPROVED" 1 "백업에서 DB를 꺼내거나 여는 데 실패" UNVERIFIED NO_SWITCH NO_DONE START
run_case "백업DB조회실패"         sqlite_fail      "$APPROVED" 1 "백업에서 DB를 꺼내거나 여는 데 실패" UNVERIFIED NO_SWITCH NO_DONE START
run_case "백업DB결과누락"         bk_incomplete    "$APPROVED" 1 "백업 DB 검사 결과가 불완전함"       UNVERIFIED NO_SWITCH NO_DONE START
run_case "백업해시불일치"         sha_mismatch     "$APPROVED" 1 "백업 sha256 불일치"                 BK_CHECKED UNVERIFIED NO_SWITCH NO_DONE START
echo "== 후보 이미지 빌드 · 반영 전 검증 · 검증한 ID로만 전환 (docs/DEPLOY.md 11장)"
run_case "검증한ID로만전환"         ok                  "$APPROVED" 0 "실행 중 이미지 = 3단계에서 검증한 이미지" CAND_BUILD GATE_CALLED RETAG_THEN_UP VERIFIED DONE NO_START ALL_COMPOSE_F
run_case "이미지검증실패_정지없음"  gate_fail           "$APPROVED" 1 "이미지 검증 실패 \(종료 코드 1\) — 기존 서비스·latest 태그는 그대로" CAND_BUILD GATE_CALLED NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "후보ID조회실패"           cand_id_fail        "$APPROVED" 1 "후보 backend 이미지 ID를 읽을 수 없음"  NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "이미지이름조회실패"       name_fail           "$APPROVED" 1 "backend의 Compose 이미지 이름을 읽을 수 없음" NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "latest태그실패_되돌리고재개" tag_fail          "$APPROVED" 1 "검증한 이미지에 latest 태그를 붙" VERIFIED RETAG_RESTORED RETAG_TRUTH NO_STEP6_HEADER NO_SWITCH NO_DONE START
run_case "전환후이미지가검증과다름" post_image_mismatch "$APPROVED" 1 "전환 후 backend 이미지\(other0000000\)가 검증한 이미지\(cand00000000\)와 다름" NO_DONE
echo "== 2026-10-28 PR #59 보완: 검증 기록 확인 · 환경 변수로 검증 바꾸기 없음 · 자원 확인 · latest 복구를 사실대로"
run_case "검증종료0_통과기록없음"     gate_lie     "$APPROVED" 1 "이번 후보의 통과를 확인할 수 없음" GATE_CALLED NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "검증기록이_다른ID"         gate_wrongid "$APPROVED" 1 "이번 후보의 통과를 확인할 수 없음" GATE_CALLED NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
CASE_COMPOSE_ARGS="-f elsewhere/compose.yml" \
run_case "DEPLOY_COMPOSE_ARGS_멈춤" ok "$APPROVED" 1 "DEPLOY_\* 변수가 있음 \(DEPLOY_COMPOSE_ARGS\)" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
CASE_GATE=true \
run_case "DEPLOY_IMAGE_GATE=true_멈춤" gate_fail   "$APPROVED" 1 "DEPLOY_\* 변수가 있음 \(DEPLOY_IMAGE_GATE\)" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "자원기준미달_빌드안함"       mem_low      "$APPROVED" 1 "빌드 전 자원 기준 미달 — 후보를 만들지 않음" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "자원확인실패_빌드안함"       mem_fail     "$APPROVED" 1 "빌드 전 자원 확인 실패 \(종료 3\)" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "하나씩빌드_자원기록"         ok           "$APPROVED" 0 "자원 \| build-frontend 동안"       MEM_FIRST SEQ_BUILD CAND_BUILD GATE_CALLED DONE
run_case "latest일부복구실패_사실대로" tag_partial  "$APPROVED" 1 "되돌림 확인 안 됨"                VERIFIED RETAG_TRUTH RESUME_AND_TAG NO_STEP6_HEADER NO_SWITCH NO_DONE START
# 1단계가 막아야 하는 것: 변경을 숨기는 git 표시, 검증 입력(scripts)의 추적하지 않는 파일, Compose가 저절로 읽는 덧붙임 파일.
# 준비·정리는 사례 밖에서 한다 (ONLY로 거른 경우에도 정리는 실행됨)
git update-index --skip-worktree backend/app.txt && echo tampered > backend/app.txt
run_case "숨김표시skip-worktree_멈춤" ok "$APPROVED" 1 "변경을 숨기는 git 표시" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
git update-index --no-skip-worktree backend/app.txt && git checkout -q -- backend/app.txt
git update-index --assume-unchanged backend/app.txt && echo tampered > backend/app.txt
run_case "숨김표시assume-unchanged_멈춤" ok "$APPROVED" 1 "변경을 숨기는 git 표시" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
git update-index --no-assume-unchanged backend/app.txt && git checkout -q -- backend/app.txt
echo 'raise SystemExit(0)' > scripts/json.py
run_case "검증입력에추적안하는파일_멈춤" ok "$APPROVED" 1 "빌드·검증 입력.*추적하지 않는 파일이 있음: \?\? scripts/json.py" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
rm -f scripts/json.py
printf 'services:\n  backend:\n    image: other\n' > docker-compose.override.yml
run_case "Compose덧붙임파일_멈춤" ok "$APPROVED" 1 "추적하지 않는 파일이 있음: \?\? docker-compose.override.yml" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
rm -f docker-compose.override.yml
run_case "이미지이름에태그_멈춤" image_tagged "$APPROVED" 1 "backend의 Compose 이미지 참조에 태그·digest가 지정됨 \(moajwo-backend:v1\)" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
# 후보 빌드(-f docker-compose.yml)와 전환(기본 파일 찾기)이 다른 설정을 읽게 하는 것 — 다른 이름의 Compose 파일, COMPOSE_FILE(셸·.env)
echo 'services: {}' > docker-compose.yaml
run_case "다른이름Compose파일_멈춤" ok "$APPROVED" 1 "추적하지 않는 파일이 있음: \?\? docker-compose.yaml" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
rm -f docker-compose.yaml
# COMPOSE_ENV_FILES가 가리키는 env 파일의 COMPOSE_FILE — 멈추지는 않지만, 모든 Compose 호출이 -f를 직접 주므로 끼어들지 못한다
printf 'COMPOSE_FILE=docker-compose.yml:local.yml\n' > "$WORK/extra.env"
CASE_ENV_FILES="$WORK/extra.env" \
run_case "COMPOSE_ENV_FILES있음_같은설정" ok "$APPROVED" 0 "전환 완료" ALL_COMPOSE_F CAND_BUILD DONE
CASE_COMPOSE_FILE=docker-compose.yml:local.yml \
run_case "COMPOSE_FILE변수_멈춤" ok "$APPROVED" 1 "COMPOSE_FILE 환경 변수가 있음 \(docker-compose.yml:local.yml\)" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
echo 'COMPOSE_FILE=docker-compose.yml:local.yml' > .env
run_case "env파일COMPOSE_FILE_멈춤" ok "$APPROVED" 1 "\.env에 COMPOSE_FILE이 있음" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
printf '\xef\xbb\xbfCOMPOSE_FILE: docker-compose.yml:local.yml\n' > .env   # BOM + '키: 값' 꼴도 Compose는 읽는다
run_case "env파일COMPOSE_FILE_BOM꼴_멈춤" ok "$APPROVED" 1 "\.env에 COMPOSE_FILE이 있음" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
rm -f .env
# 저장소 루트의 추적하지 않는 파일 — Windows는 docker를 PATH보다 현재 폴더에서 먼저 찾아, 검증 도구(Python)만 그것을 실행할 수 있다
: > docker.exe
run_case "루트에추적안하는실행파일_멈춤" ok "$APPROVED" 1 "추적하지 않는 파일이 있음: \?\? docker.exe" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
rm -f docker.exe
# filter 속성(clean 필터) — 작업 파일이 달라도 git status·트리 비교가 같다고 볼 수 있음
printf 'backend/app.txt filter=hide\n' > .git/info/attributes && git config filter.hide.clean cat
run_case "filter속성_멈춤" ok "$APPROVED" 1 "filter 속성이 붙은 추적 파일이 있음.*backend/app.txt \(filter=hide\)" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
rm -f .git/info/attributes && git config --unset filter.hide.clean
# 2026-10-28 수정본 검토(배포) — 이름 규칙·숨긴 덧붙임 파일·stat 캐시·셸 시작 파일·리허설 표지·되돌림 명령 실패
run_case "이미지이름규칙다름_멈춤" images_mismatch "$APPROVED" 1 "서비스별 Compose 이미지 이름을 정하지 못함" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
echo docker-compose.override.yml >> .git/info/exclude && printf 'services: {}\n' > docker-compose.override.yml   # git status에는 안 보임
run_case "제외목록에숨긴덧붙임파일_멈춤" ok "$APPROVED" 1 "Compose가 저절로 읽는 다른 파일이 있음: docker-compose.override.yml" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
rm -f docker-compose.override.yml && sed -i '/^docker-compose.override.yml$/d' .git/info/exclude
cp -p backend/app.txt "$WORK/app.txt.orig" && printf 'neW\n' > backend/app.txt && touch -r "$WORK/app.txt.orig" backend/app.txt   # 같은 크기·같은 수정 시각
run_case "같은크기수정시각되돌림_멈춤" ok "$APPROVED" 1 "작업 파일 내용이 승인한 트리와 다름.*backend/app.txt" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
cp -p "$WORK/app.txt.orig" backend/app.txt
CASE_BASH_ENV=1 \
run_case "BASH_ENV있음_멈춤" ok "$APPROVED" 1 "내보낸 셸 함수·BASH_ENV·ENV가 있음" NO_BUILD NO_GATE NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
CASE_REHEARSAL=1 \
run_case "격리리허설표지_진행" ok "$APPROVED" 0 "DEPLOY_REHEARSAL=1 — 격리 리허설, 운영 반영 아님" CAND_BUILD DONE
run_case "latest되돌림명령실패_사실대로" tag_all_fail "$APPROVED" 1 "되돌림 명령 실패 \(종료 1/1\)" VERIFIED RETAG_TRUTH NO_RESTORE_CLAIM RESUME_AND_TAG NO_STEP6_HEADER NO_SWITCH NO_DONE START

# ---------------------------------------------------------------- 2026-10-29 보완: 진짜 검증 단계·자원 감시까지 잇기
# 위 사례의 검증·자원 도구는 대역이다. 아래는 시험할 판의 image_gate.sh를 그대로, memory_guard.py 자리에는 진짜 파일을 불러 docker 호출만
# 가짜로 답하는 감싸개를 커밋한 승인 커밋으로, 감시 컨테이너 정리 실패가 gate.result와 deploy.sh 3단계 뒤(정지·백업·latest·전환)까지
# 어떻게 이어지는지 본다. 목록 수집·격리 점검은 정상 응답 대역이고, 실제 docker에는 닿지 않는다(후보 빌드도 실행하지 않고 기록만).
echo "== 2026-10-29 보완: 진짜 검증 단계(image_gate.sh)·자원 감시(memory_guard.py watch) — 감시 컨테이너 정리 확인"
MG_REAL=$(cygpath -w "$SCRIPTS_UT/memory_guard.py" 2>/dev/null || printf '%s' "$SCRIPTS_UT/memory_guard.py")
export MG_REAL
git checkout -q -b realgate "$APPROVED" || exit 1
cp "$SCRIPTS_UT/image_gate.sh" scripts/image_gate.sh
cat > scripts/memory_guard.py <<'EOF'
"""자원 확인 감싸개 — 시험할 판의 진짜 memory_guard.py(MG_REAL)를 그대로 돌리고, 그 안의 docker 호출과 호스트 측정만 가짜로 답한다
(실제 docker에 닿지 않음). watch가 감싼 명령 가운데 docker compose(후보 빌드)는 실행하지 않고 가짜 docker와 같은 꼴로 기록만 하고,
그 밖의 명령(대역 격리 점검)은 실행한다. STUB_SCENARIO realgate_smoke_left·realgate_build_left: 그 단계(smoke·build-backend)의
감시 컨테이너 삭제가 실패해 목록에 남음."""
import importlib.util
import os
import subprocess
import sys

s = os.environ.get("STUB_SCENARIO", "ok")
log = os.environ.get("STUB_LOG_W") or os.environ["STUB_LOG"]
with open(log, "a", encoding="utf-8") as f:
    f.write("memory_guard " + " ".join(sys.argv[1:]) + "\n")
spec = importlib.util.spec_from_file_location("memory_guard_under_test", os.environ["MG_REAL"])
mg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mg)
real_run = subprocess.run
stage = sys.argv[2] if len(sys.argv) > 2 else ""
fail_rm = (s, stage) in (("realgate_smoke_left", "smoke"), ("realgate_build_left", "build-backend"))
made, gone = [], []


def cp(args, rc, out="", err=""):
    return subprocess.CompletedProcess(args, rc, out.encode("utf-8"), err.encode("utf-8"))


def fake(args, *a, **kw):
    if not args or args[0] != "docker":
        return real_run(args, *a, **kw)
    d = list(args[1:])
    if d[:1] == ["compose"]:
        with open(log, "a", encoding="utf-8") as f:
            f.write(" ".join(args) + "\n")
        return cp(args, 0)
    if d[:2] == ["run", "-d"]:
        made.append(d[d.index("--name") + 1])
        return cp(args, 0, "cid\n")
    if d[:1] == ["run"]:
        return cp(args, 0, "MemTotal: 8000000 kB\nMemAvailable: 5120000 kB\n")
    if d[:1] == ["logs"]:
        return cp(args, 0, "1700000000 5120000\n")
    if d[:2] == ["rm", "-f"]:
        if fail_rm:
            return cp(args, 1, err="Error response from daemon: (대역) 삭제 실패")
        gone.append(d[2])
        return cp(args, 0, d[2] + "\n")
    if d[:2] == ["ps", "-a"]:
        return cp(args, 0, "".join(n + "\n" for n in made if n not in gone))
    return cp(args, 1, err="모르는 docker 명령 (대역)")


subprocess.run = fake
mg.host_available = lambda: (900, "대역 호스트 측정")
if hasattr(mg, "RECHECK"):
    mg.RECHECK = ()
sys.exit(mg.main())
EOF
cat > scripts/image_inventory.py <<'EOF'
"""목록 도구 대역 — collect는 종류만 적은 목록을 쓰고, compare는 '같음'을 말한다 (docker 없음)."""
import json
import sys

if sys.argv[1] == "collect":
    with open(sys.argv[4], "w", encoding="utf-8") as f:
        json.dump({"kind": sys.argv[2]}, f)
    sys.exit(0)
with open(sys.argv[3], encoding="utf-8") as f:
    kind = json.load(f)["kind"]
print(f"요약 | {kind} 실행 환경 항목 같음 | 기대 대역 | 실제 대역")
EOF
cat > scripts/image_smoke.py <<'EOF'
"""격리 점검 대역 — 필수 점검 하나를 통과로 쓴다 (docker 없음). image_gate.sh가 읽는 TOOL·REQUIRED를 둔다."""
TOOL = "image_smoke 대역"
REQUIRED = [("stub.ok", "대역 점검")]
if __name__ == "__main__":
    import json
    import sys

    b, f, out = sys.argv[1:4]
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"tool": TOOL, "status": "pass", "ok": True, "images": {"backend": b, "frontend": f}, "errors": [],
                   "checks": [{"id": "stub.ok", "ok": True, "measured": True}]}, fh)
EOF
mkdir -p deploy/expected-images && for k in backend frontend; do printf '{"kind": "%s"}\n' "$k" > "deploy/expected-images/$k.json"; done
git add -A && git commit -qm "real gate + memory_guard wrapper" && REALGATE=$(git rev-parse HEAD)
run_case "진짜검증_감시정리확인_전환"      realgate_ok         "$REALGATE" 0 "실행 중 이미지 = 3단계에서 검증한 이미지" GATE_PASS_RECORD CAND_BUILD DONE NO_START
run_case "진짜검증_점검감시남음_정지없음"  realgate_smoke_left "$REALGATE" 1 "이미지 검증 실패 \(종료 코드 1\) — 기존 서비스·latest 태그는 그대로" GATE_FAIL_CLEANUP NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "진짜검증_빌드감시남음_검증없음"  realgate_build_left "$REALGATE" 1 "후보 backend 빌드 단계 실패 \(종료 3 · 자원 \| build-backend 판정: 감싼 명령 종료 0 · 감시 자원 정리 확인 실패" NO_GATE_RESULT NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
printf '{"kind": "backend", "edited": true}\n' > deploy/expected-images/backend.json   # 검증 목록을 커밋하지 않고 바꿈
run_case "검증목록무단변경_멈춤" realgate_ok "$REALGATE" 1 "추적 중인 파일에 미커밋 변경이 있음" NO_BUILD NO_GATE_RESULT NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
git checkout -q -- deploy/expected-images/backend.json
echo "== 결과: 통과 $PASS · 실패 $FAILN"
[ "$FAILN" -eq 0 ]
