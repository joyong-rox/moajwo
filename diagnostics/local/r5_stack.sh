#!/usr/bin/env bash
# 5장 두 차례 기능 검증 — 격리 서버 띄우기·끄기 (Docker 없이, 127.0.0.1에만).
#   backend: 최종 후보(wtrc) 코드, dbvenv 파이썬, 8096 · frontend: 후보 빌드본(build-rc)을 vite preview로 8086
# 사용법:
#   r5_stack.sh fe-start                         frontend 띄우기 (한 번)
#   r5_stack.sh be-start sqlite <데이터 폴더>      SQLite 모드 (데이터 폴더의 moajwo.db)
#   r5_stack.sh be-start pg <데이터 폴더> <DB 이름>  외부 DB 모드 (로컬 PostgreSQL 17, TLS, 앱 계정 / 마이그레이션은 소유 계정)
#   r5_stack.sh pg-fresh <DB 이름>                빈 DB 만들기 (같은 이름이 있으면 지움 — 격리 시험 DB만)
#   r5_stack.sh be-stop | fe-stop
set -u
SCR="${R5_WORK:?작업 폴더(R5_WORK) — wtrc·wt36 작업 트리, build-rc2, pwvenv·dbvenv, pgsecret, tools(axe.min.js) 위치}"
PYDB="$SCR/dbvenv/Scripts/python.exe"
pid_on() { netstat -ano | awk -v p="127.0.0.1:$1" '$2 == p && $4 == "LISTENING" {print $5}' | head -1; }
stop_port() {
  local pid; pid=$(pid_on "$1")
  [ -n "$pid" ] && taskkill //PID "$pid" //T //F > /dev/null 2>&1
  for _ in $(seq 1 20); do [ -z "$(pid_on "$1")" ] && return 0; sleep 0.5; done
  echo "포트 $1가 닫히지 않음" >&2; return 1
}
wait_health() {
  for _ in $(seq 1 120); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$1$2")" = 200 ] && return 0
    sleep 1
  done
  echo "health 실패: $1$2" >&2; return 1
}
case "$1" in
  fe-start)
    nohup bash "$SCR/tools/iso_frontend.sh" "$SCR/wtrc/frontend" "$SCR/${BUILD:-build-rc2}" 8086 8096 >> "$SCR/iso/r5-frontend.log" 2>&1 &
    wait_health 8086 / ;;
  fe-stop) stop_port 8086 ;;
  be-start)
    MODE=$2; DATA=$3
    mkdir -p "$DATA"
    if [ "$MODE" = pg ]; then
      export MOAJWO_DB_MODE=external
      DATABASE_URL=$("$PYDB" "$SCR/pgurl.py" moajwo_app "$4" "sslmode=require")
      MOAJWO_MIGRATION_DATABASE_URL=$("$PYDB" "$SCR/pgurl.py" moajwo_owner "$4" "sslmode=require")
      export DATABASE_URL MOAJWO_MIGRATION_DATABASE_URL
    fi
    PY="$PYDB" nohup bash "$SCR/tools/iso_backend.sh" "$SCR/wtrc/backend" "$DATA" 8096 >> "$DATA/../backend-$(basename "$DATA").log" 2>&1 &
    wait_health 8096 /health ;;
  be-stop) stop_port 8096 ;;
  pg-fresh)
    "$PYDB" - "$("$PYDB" "$SCR/pgurl.py" pgadmin postgres "sslmode=require")" "$("$PYDB" "$SCR/pgurl.py" pgadmin "$2" "sslmode=require")" "$2" <<'PY'
import sys
import psycopg
admin, admin_db, name = sys.argv[1:]
with psycopg.connect(admin, autocommit=True) as c:
    c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    c.execute(f'CREATE DATABASE "{name}" OWNER moajwo_owner')
with psycopg.connect(admin_db, autocommit=True) as c:
    c.execute("GRANT USAGE ON SCHEMA public TO moajwo_app")
    c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE moajwo_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO moajwo_app")
    c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE moajwo_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO moajwo_app")
print("fresh", name)
PY
    ;;
  *) echo "알 수 없는 명령: $1" >&2; exit 2 ;;
esac
