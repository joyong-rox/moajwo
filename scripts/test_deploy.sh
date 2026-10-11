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
echo "docker $*" >> "$STUB_LOG"
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
    build|stop|start) exit 0 ;;
  esac
  exit 0
fi
case "$1" in
  inspect)
    case "$*" in *Health*) echo healthy; exit 0 ;; esac
    if switched; then
      [ "$S" = post_image_fail ] && exit 1
      echo "sha256:new000000000000$2"
    else
      echo "sha256:old000000000000$2"
    fi
    exit 0 ;;
  tag) exit 0 ;;
  ps)                                    # ps --filter volume=… --format {{.Names}}
    [ "$S" = volps_fail ] && exit 1      # 비정상 종료 + 빈 표준출력
    exit 0 ;;                            # 정상 종료 + 빈 결과
  run)
    case "$*" in
      *"--entrypoint python"*)           # 볼륨의 DB 리비전 조회
        [ "$S" = ps_fail_rev_fail ] && exit 1
        echo "0014_old"; exit 0 ;;
    esac
    [ "$S" = backup_fail ] && exit 125   # 백업 one-off 실패
    dir=$(printf '%s\n' "$*" | sed -n 's/.*-v \([^ ]*\):\/backup.*/\1/p')
    arc=$(printf '%s\n' "$*" | grep -o 'moajwo-data-[0-9-]*\.tgz' | head -1)
    printf 'archive' > "$(cygpath -u "$dir")/$arc"
    printf 'revision 0014_old\nintegrity ok\njournal delete\nsha256 abc123\n'
    exit 0 ;;
esac
exit 0
EOF
chmod +x "$STUB/docker"

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
  PATH="$STUB:$PATH" bash scripts/deploy.sh "$sha" > "$state/out" 2>&1
  local rc=$? ok=1 why=""
  [ "$rc" -eq "$want_rc" ] || { ok=0; why="종료 코드 $rc (기대 $want_rc)"; }
  grep -Eq "$want_log" "$state/out" || { ok=0; why="$why 로그 없음: $want_log"; }
  local forbidden
  for forbidden in "$@"; do
    case "$forbidden" in
      NO_BACKUP) grep -q -- "--entrypoint sh" "$STUB_LOG" && { ok=0; why="$why 백업이 실행됨"; } ;;
      NO_SWITCH) grep -q "compose up" "$STUB_LOG" && { ok=0; why="$why 전환(up)이 실행됨"; } ;;
      NO_START) grep -q "compose start" "$STUB_LOG" && { ok=0; why="$why 재시작이 실행됨"; } ;;
      NO_STOP) grep -q "compose stop" "$STUB_LOG" && { ok=0; why="$why 정지가 실행됨"; } ;;
      NO_DONE) grep -q "전환 완료" "$state/out" && { ok=0; why="$why '전환 완료'가 출력됨"; } ;;
      START) grep -q "compose start" "$STUB_LOG" || { ok=0; why="$why 재시작이 실행되지 않음"; } ;;
      DONE) grep -q "전환 완료" "$state/out" || { ok=0; why="$why '전환 완료'가 없음"; } ;;
    esac
  done
  local calls
  calls=$(grep -oE "compose (stop|start|up)|--entrypoint sh|--entrypoint python" "$STUB_LOG" | sed 's/--entrypoint sh/backup/; s/--entrypoint python/revision-check/; s/compose //' | tr '\n' ' ')
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
run_case "백업실패"               backup_fail     "$APPROVED"   1 "백업 명령 실패"                      NO_SWITCH NO_DONE START
run_case "전환후이미지조회실패"   post_image_fail "$APPROVED"   1 "전환 후 backend 이미지를 확인할 수 없음" NO_DONE
run_case "전환후리비전조회실패"   post_rev_fail   "$APPROVED"   1 "전환 후 리비전을 확인할 수 없음"       NO_DONE
run_case "전환후컨테이너조회실패" post_cid_fail   "$APPROVED"   1 "전환 후 backend 컨테이너를 확인할 수 없음" NO_DONE
echo "== 결과: 통과 $PASS · 실패 $FAILN"
[ "$FAILN" -eq 0 ]
