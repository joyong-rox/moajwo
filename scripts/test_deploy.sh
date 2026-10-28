#!/usr/bin/env bash
# scripts/deploy.sh 실패 처리 시험 (Git Bash). 실제 Docker를 쓰지 않는다:
# 임시 git 저장소 + 명령 대역(가짜 docker)을 PATH 앞에 두고, 모든 docker 호출을 기록해 후속 단계(백업·전환·재시작)가
# 실행됐는지 확인한다. 사용법: bash scripts/test_deploy.sh
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
    config)                              # config --images <서비스> — 서비스의 Compose 이미지 이름
      [ "$S" = name_fail ] && exit 1
      echo "moajwo-${2:-x}"; exit 0 ;;
    build|stop|start) exit 0 ;;
  esac
  exit 0
fi
case "$1" in
  image)                                 # image inspect <후보 참조> --format {{.Id}} — 후보 이미지 ID
    [ "$S" = cand_id_fail ] && exit 1
    case "$3" in *backend*) echo "sha256:cand00000000000cid-backend" ;; *) echo "sha256:cand00000000000cid-frontend" ;; esac
    exit 0 ;;
  inspect)
    case "$*" in *Health*) echo healthy; exit 0 ;; esac
    case "$*" in *PortBindings*)          # 포트 연결: 반영 전 frontend는 80, 전환 뒤는 8080
      [ "$S" = ports_fail ] && exit 1
      case "$2" in
        *backend*) echo "127.0.0.1:8000:8000/tcp " ;;
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
  tag)                                   # tag_fail: 검증한 후보에 latest를 붙이는 호출만 실패
    case "$S:$2" in tag_fail:sha256:cand*) exit 1 ;; esac
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
# 3단계 검증 명령 대역 (scripts/image_gate.sh 대신 — DEPLOY_IMAGE_GATE). 호출을 기록하고 gate_fail이면 실패
cat > "$STUB/gate" <<'EOF'
#!/usr/bin/env bash
{ printf 'gate'; printf ' %s' "$@"; echo; } >> "$STUB_LOG"
if [ "${STUB_SCENARIO:-ok}" = gate_fail ]; then echo "[검증 실패] (대역) 완성 이미지가 검증한 이미지와 실행 환경이 다름"; exit 1; fi
echo "[검증 통과] (대역)"
EOF
chmod +x "$STUB/gate"
export DEPLOY_IMAGE_GATE="$STUB/gate"

# ---------------------------------------------------------------- 임시 저장소 (승인 커밋 = HEAD, 다른 커밋 하나)
cp "${DEPLOY_SH:-$ROOT/scripts/deploy.sh}" "$WORK/repo/scripts/deploy.sh"   # DEPLOY_SH: 다른 버전 시험용
cd "$WORK/repo" || exit 1
git init -q && git config user.email t@example.invalid && git config user.name t && git config core.autocrlf false
echo old > backend/app.txt && echo f > frontend/app.txt && printf 'backups/\n' > .gitignore
git add -A && git commit -qm old && OLD_COMMIT=$(git rev-parse HEAD)
echo new > backend/app.txt && git add -A && git commit -qm new && APPROVED=$(git rev-parse HEAD)

PASS=0; FAILN=0
run_case() {  # 이름, 시나리오, 승인 SHA, 기대 종료 코드, 있어야 할 로그(정규식), 없어야 할 호출들…
  local name="$1" scenario="$2" sha="$3" want_rc="$4" want_log="$5"; shift 5
  local state="$WORK/state-$name"; rm -rf "$state" backups; mkdir -p "$state"
  export STUB_LOG="$state/calls" STUB_STATE="$state" STUB_SCENARIO="$scenario"
  : > "$STUB_LOG"
  PATH="$STUB:$PATH" bash scripts/deploy.sh "$sha" "${CASE_PREV:-0014_old}" > "$state/out" 2>&1
  local rc=$? ok=1 why=""
  [ "$rc" -eq "$want_rc" ] || { ok=0; why="종료 코드 $rc (기대 $want_rc)"; }
  grep -Eq "$want_log" "$state/out" || { ok=0; why="$why 로그 없음: $want_log"; }
  local forbidden
  for forbidden in "$@"; do
    case "$forbidden" in
      NO_BACKUP) grep -qE "moajwo.step=backup( |$)" "$STUB_LOG" && { ok=0; why="$why 백업이 실행됨"; } ;;
      BK_CHECKED) grep -q "moajwo.step=backup-check" "$STUB_LOG" || { ok=0; why="$why 백업 DB 검사가 실행되지 않음"; } ;;
      VERIFIED) grep -qx "verified yes" backups/moajwo-data-*[0-9].txt 2>/dev/null || { ok=0; why="$why 요약에 'verified yes' 없음"; } ;;
      UNVERIFIED) grep -qx "verified no" backups/moajwo-data-*[0-9].txt 2>/dev/null || { ok=0; why="$why 요약에 'verified no' 없음"; } ;;
      NO_SWITCH) grep -q "compose up" "$STUB_LOG" && { ok=0; why="$why 전환(up)이 실행됨"; } ;;
      NO_START) grep -q "compose start" "$STUB_LOG" && { ok=0; why="$why 재시작이 실행됨"; } ;;
      NO_STOP) grep -q "compose stop" "$STUB_LOG" && { ok=0; why="$why 정지가 실행됨"; } ;;
      NO_DONE) grep -q "전환 완료" "$state/out" && { ok=0; why="$why '전환 완료'가 출력됨"; } ;;
      START) grep -q "compose start" "$STUB_LOG" || { ok=0; why="$why 재시작이 실행되지 않음"; } ;;
      DONE) grep -q "전환 완료" "$state/out" || { ok=0; why="$why '전환 완료'가 없음"; } ;;
      NO_RETAG) grep -q "^docker tag sha256:cand" "$STUB_LOG" && { ok=0; why="$why 후보 이미지에 latest 태그가 붙음"; } ;;
      CAND_BUILD)  # 빌드는 후보 태그 덧붙임 파일로만 (latest를 옮기는 그냥 build 없음)
        grep -qE "compose .*-f backups/candidate-[0-9-]+\.compose\.yml build backend frontend" "$STUB_LOG" \
          || { ok=0; why="$why 후보 태그 빌드가 없음"; }
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
      ROLLBACK_PORTS)  # 반영 전 포트 연결을 그대로 되돌리는 덧붙임 파일 (덧붙이지 않고 바꾸는 !override)
        f=$(ls backups/rollback-*.compose.yml 2>/dev/null | head -1)
        { [ -n "$f" ] && grep -q '^    ports: !override$' "$f" && grep -qx '      - "127.0.0.1:8080:80/tcp"' "$f" \
          && grep -qx '      - "127.0.0.1:8000:8000/tcp"' "$f"; } || { ok=0; why="$why 롤백용 포트 파일이 없거나 다름"; }
        grep -q "포트 연결: frontend 127.0.0.1:8080:80/tcp → 127.0.0.1:8080:8080/tcp" "$state/out" \
          || { ok=0; why="$why 전후 포트 연결 기록 없음"; } ;;
    esac
  done
  # 모든 사례 공통: 운영 볼륨은 읽기 전용으로만 마운트, 백업 DB 검사 컨테이너에는 운영 볼륨을 마운트하지 않음
  grep -o -- "-v moajwo_moajwo-data:[^ ]*" "$STUB_LOG" | grep -qv ":/data:ro$" && { ok=0; why="$why 운영 볼륨을 쓰기 가능으로 마운트"; }
  grep "moajwo.step=backup-check" "$STUB_LOG" | grep -q "moajwo_moajwo-data" && { ok=0; why="$why 백업 검사에 운영 볼륨 마운트"; }
  local calls
  calls=$(awk '/moajwo.step=/ { match($0, /moajwo.step=[a-z-]+/); print substr($0, RSTART + 12, RLENGTH - 12); next }
               /--entrypoint python/ { print "revision-check"; next }
               /compose (stop|start|up)/ { match($0, /compose (stop|start|up)/); print substr($0, RSTART + 8, RLENGTH - 8) }' "$STUB_LOG" | tr '\n' ' ')
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
run_case "검증한ID로만전환"         ok                  "$APPROVED" 0 "실행 중 이미지 = 3단계에서 검증한 이미지" CAND_BUILD GATE_CALLED RETAG_THEN_UP VERIFIED DONE NO_START
run_case "이미지검증실패_정지없음"  gate_fail           "$APPROVED" 1 "이미지 검증 실패 \(종료 코드 1\) — 기존 서비스·latest 태그는 그대로" CAND_BUILD GATE_CALLED NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "후보ID조회실패"           cand_id_fail        "$APPROVED" 1 "후보 backend 이미지 ID를 읽을 수 없음"  NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "이미지이름조회실패"       name_fail           "$APPROVED" 1 "backend의 Compose 이미지 이름을 읽을 수 없음" NO_RETAG NO_STOP NO_BACKUP NO_SWITCH NO_START
run_case "latest태그실패_되돌리고재개" tag_fail          "$APPROVED" 1 "검증한 이미지에 latest 태그를 붙일 수 없음" VERIFIED RETAG_RESTORED NO_SWITCH NO_DONE START
run_case "전환후이미지가검증과다름" post_image_mismatch "$APPROVED" 1 "전환 후 backend 이미지\(other0000000\)가 검증한 이미지\(cand00000000\)와 다름" NO_DONE
echo "== 결과: 통과 $PASS · 실패 $FAILN"
[ "$FAILN" -eq 0 ]
