#!/usr/bin/env bash
# PostgreSQL TLS 검증 — 격리 러너(또는 로컬) 안의 일회용 PostgreSQL 17(자체 서명 CA, 이름 localhost, 암호화 연결·SCRAM만)에
# 1) psycopg 매트릭스(유효 인증서 판 + 만료 인증서 판, 판정은 pg_tls_judge — 원인별)와
# 2) 실제 진입 경로(앱·app.dbcheck·Alembic·app.dbcopy — pg_tls_paths.py)를 확인한다. docs/EXTERNAL_DB.md 3-2의 근거.
# 실제 Neon·Render·운영 DB에는 연결하지 않는다. 비밀값은 이 실행 안에서 만든 일회용 값뿐이고 출력에는 주소·비밀번호를 남기지 않는다.
# 만료 인증서는 시험용 CA가 과거 유효 기간으로 서명한다(ossl ca -startdate/-enddate) — 시스템 시각은 바꾸지 않는다.
# 사용: bash diagnostics/ci/pg_tls_check.sh   (저장소 루트에서; OUT=out PGBIN=/usr/lib/postgresql/17/bin PGPORT_TLS=55433 PYTHON=python3)
set -euo pipefail
OUT=${OUT:-out}
# Git Bash(MSYS)는 "/CN=…" 인자를 Windows 경로로 바꾸므로 그 인자만 변환에서 뺀다 (리눅스에서는 영향 없음)
ossl() { MSYS2_ARG_CONV_EXCL="/CN" openssl "$@"; }
mkdir -p "$OUT"
PGBIN=${PGBIN:-/usr/lib/postgresql/17/bin}
PORT=${PGPORT_TLS:-55433}
PYTHON=${PYTHON:-python3}
WORK=$(mktemp -d)
CERTS=$WORK/certs
DATA=$WORK/data
mkdir -p "$CERTS"
# Windows(Git Bash)에서는 PostgreSQL·python이 네이티브라 설정 파일·환경 변수의 경로를 Windows 모양(C:/…)으로 준다
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) winpath() { cygpath -m "$1"; } ;; *) winpath() { echo "$1"; } ;; esac
cleanup() { "$PGBIN/pg_ctl" -D "$DATA" -m fast stop > /dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

echo "== 1) CA·서버 인증서 (이름은 localhost만 — 127.0.0.1을 넣지 않아 verify-full의 호스트 이름 검사가 드러난다)"
ossl req -x509 -newkey rsa:2048 -nodes -keyout "$CERTS/ca.key" -out "$CERTS/ca.crt" -days 2 -subj "/CN=moajwo-ci-test-ca" 2> /dev/null
ossl req -newkey rsa:2048 -nodes -keyout "$CERTS/server.key" -out "$CERTS/server.csr" -subj "/CN=localhost" 2> /dev/null
printf 'subjectAltName=DNS:localhost\n' > "$CERTS/san.cnf"
ossl x509 -req -in "$CERTS/server.csr" -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial -out "$CERTS/server.crt" -days 2 \
  -extfile "$CERTS/san.cnf" 2> /dev/null
ossl req -x509 -newkey rsa:2048 -nodes -keyout "$CERTS/other.key" -out "$CERTS/other.crt" -days 2 -subj "/CN=moajwo-ci-other-ca" 2> /dev/null
# 만료 서버 인증서: 같은 CA, 같은 이름, 유효 기간 2024-01-01 ~ 2024-01-02 (ossl ca — 시스템 시각 그대로)
CADIR=$WORK/ca
mkdir -p "$CADIR/newcerts"
: > "$CADIR/index.txt"
echo 1000 > "$CADIR/serial"
cat > "$CADIR/ca.cnf" <<EOF
[ ca ]
default_ca = test_ca
[ test_ca ]
dir = $(winpath "$CADIR")
database = \$dir/index.txt
new_certs_dir = \$dir/newcerts
serial = \$dir/serial
certificate = $(winpath "$CERTS/ca.crt")
private_key = $(winpath "$CERTS/ca.key")
default_md = sha256
policy = loose
x509_extensions = server_ext
copy_extensions = none
unique_subject = no
[ loose ]
commonName = supplied
[ server_ext ]
subjectAltName = DNS:localhost
basicConstraints = CA:FALSE
EOF
ossl ca -batch -config "$CADIR/ca.cnf" -in "$CERTS/server.csr" -out "$CERTS/server-expired.crt" \
  -startdate 20240101000000Z -enddate 20240102000000Z -notext 2> /dev/null
chmod 600 "$CERTS/server.key"
{ echo "server:"; ossl x509 -in "$CERTS/server.crt" -noout -subject -dates -ext subjectAltName; echo "server-expired:";
  ossl x509 -in "$CERTS/server-expired.crt" -noout -subject -dates -ext subjectAltName; } | tee "$OUT/pg-tls-server-cert.txt"

echo "== 2) 서버: TLS 켜고 암호화 연결(hostssl)만 허용, SCRAM-SHA-256"
PWFILE=$WORK/pw
printf 'ci-throwaway-tls\n' > "$PWFILE"
"$PGBIN/initdb" -D "$DATA" -U postgres --auth=scram-sha-256 --pwfile="$PWFILE" > "$OUT/pg-tls-initdb.txt"
cat >> "$DATA/postgresql.conf" <<EOF
ssl = on
ssl_cert_file = '$(winpath "$CERTS/server.crt")'
ssl_key_file = '$(winpath "$CERTS/server.key")'
ssl_min_protocol_version = 'TLSv1.2'
password_encryption = scram-sha-256
lc_messages = 'C'
port = $PORT
listen_addresses = '127.0.0.1'
EOF
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) ;; *) echo "unix_socket_directories = '$WORK'" >> "$DATA/postgresql.conf" ;; esac
cat > "$DATA/pg_hba.conf" <<EOF
# 암호화 연결만 — 비암호(hostnossl)는 거절
hostssl   all all 127.0.0.1/32 scram-sha-256
hostnossl all all 127.0.0.1/32 reject
EOF
"$PGBIN/pg_ctl" -D "$DATA" -l "$OUT/pg-tls-server.log" -w start > /dev/null
"$PGBIN/postgres" --version | tee "$OUT/pg-tls-server-version.txt"
export PGTLS_PORT=$PORT PGTLS_CA=$(winpath "$CERTS/ca.crt") PGTLS_OTHER=$(winpath "$CERTS/other.crt") PGTLS_PASSWORD=ci-throwaway-tls

echo "== 3) 매트릭스 — 유효한 서버 인증서 (판정: 원인별)"
rc=0
PGTLS_PHASE=valid "$PYTHON" diagnostics/ci/pg_tls_matrix.py "$OUT" || rc=$?

echo "== 4) 실제 진입 경로 (앱·dbcheck·Alembic·dbcopy) — 정상 1건 + 호스트 이름 불일치 거절 1건"
PGTLS_BACKEND=$(winpath "${PGTLS_BACKEND:-$PWD/backend}") PGTLS_PYTHON="$PYTHON" "$PYTHON" diagnostics/ci/pg_tls_paths.py "$OUT" || rc=$?

echo "== 5) 매트릭스 — 만료된 서버 인증서 (같은 CA·이름, 유효 기간 지남)"
"$PGBIN/pg_ctl" -D "$DATA" -m fast stop > /dev/null
CUR=$(winpath "$CERTS/server.crt")
EXP=$(winpath "$CERTS/server-expired.crt")
sed -i "s|ssl_cert_file = '$CUR'|ssl_cert_file = '$EXP'|" "$DATA/postgresql.conf"
grep -q "server-expired" "$DATA/postgresql.conf" || { echo "[중단] 만료 인증서로 바꾸지 못함"; exit 1; }
"$PGBIN/pg_ctl" -D "$DATA" -l "$OUT/pg-tls-server.log" -w start > /dev/null
PGTLS_PHASE=expired "$PYTHON" diagnostics/ci/pg_tls_matrix.py "$OUT" || rc=$?
exit $rc
