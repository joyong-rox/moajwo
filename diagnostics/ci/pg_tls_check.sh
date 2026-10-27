#!/usr/bin/env bash
# PostgreSQL TLS 검증 매트릭스 — 격리 러너 안의 일회용 PostgreSQL 17(자체 서명 CA, 이름 localhost, 암호화 연결·SCRAM만)에
# psycopg(backend/requirements.txt 판)로 sslmode·sslrootcert·channel_binding 조합을 붙여 보고 기대와 대조한다
# (docs/EXTERNAL_DB.md 3-2 'verify-full 기본'의 근거). 실제 Neon·Render·운영 DB에는 연결하지 않는다. 비밀값은 이 러너
# 안에서 만든 일회용 값뿐이고 출력에는 주소·비밀번호를 남기지 않는다.
# 사용: bash diagnostics/ci/pg_tls_check.sh   (저장소 루트에서; 환경 변수 OUT=out PGBIN=/usr/lib/postgresql/17/bin PGPORT_TLS=55433)
set -euo pipefail
OUT=${OUT:-out}
mkdir -p "$OUT"
PGBIN=${PGBIN:-/usr/lib/postgresql/17/bin}
PORT=${PGPORT_TLS:-55433}
WORK=$(mktemp -d)
CERTS=$WORK/certs
DATA=$WORK/data
mkdir -p "$CERTS"
cleanup() { "$PGBIN/pg_ctl" -D "$DATA" -m fast stop > /dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

echo "== 1) CA·서버 인증서 (이름은 localhost만 — 127.0.0.1을 넣지 않아 verify-full의 호스트 이름 검사가 드러난다)"
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$CERTS/ca.key" -out "$CERTS/ca.crt" -days 2 -subj "/CN=moajwo-ci-test-ca" 2> /dev/null
openssl req -newkey rsa:2048 -nodes -keyout "$CERTS/server.key" -out "$CERTS/server.csr" -subj "/CN=localhost" 2> /dev/null
printf 'subjectAltName=DNS:localhost\n' > "$CERTS/san.cnf"
openssl x509 -req -in "$CERTS/server.csr" -CA "$CERTS/ca.crt" -CAkey "$CERTS/ca.key" -CAcreateserial -out "$CERTS/server.crt" -days 2 \
  -extfile "$CERTS/san.cnf" 2> /dev/null
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$CERTS/other.key" -out "$CERTS/other.crt" -days 2 -subj "/CN=moajwo-ci-other-ca" 2> /dev/null
chmod 600 "$CERTS/server.key"
openssl x509 -in "$CERTS/server.crt" -noout -subject -ext subjectAltName | tee "$OUT/pg-tls-server-cert.txt"

echo "== 2) 서버: TLS 켜고 암호화 연결(hostssl)만 허용, SCRAM-SHA-256"
PWFILE=$WORK/pw
printf 'ci-throwaway-tls\n' > "$PWFILE"
"$PGBIN/initdb" -D "$DATA" -U postgres --auth=scram-sha-256 --pwfile="$PWFILE" > "$OUT/pg-tls-initdb.txt"
cat >> "$DATA/postgresql.conf" <<EOF
ssl = on
ssl_cert_file = '$CERTS/server.crt'
ssl_key_file = '$CERTS/server.key'
ssl_min_protocol_version = 'TLSv1.2'
password_encryption = scram-sha-256
port = $PORT
listen_addresses = '127.0.0.1'
unix_socket_directories = '$WORK'
EOF
cat > "$DATA/pg_hba.conf" <<EOF
# 암호화 연결만 — 비암호(hostnossl)는 거절
hostssl   all all 127.0.0.1/32 scram-sha-256
hostnossl all all 127.0.0.1/32 reject
EOF
"$PGBIN/pg_ctl" -D "$DATA" -l "$OUT/pg-tls-server.log" -w start > /dev/null
"$PGBIN/postgres" --version | tee "$OUT/pg-tls-server-version.txt"

echo "== 3) 매트릭스 (psycopg)"
PGTLS_PORT=$PORT PGTLS_CA=$CERTS/ca.crt PGTLS_OTHER=$CERTS/other.crt PGTLS_PASSWORD=ci-throwaway-tls \
  python3 diagnostics/ci/pg_tls_matrix.py "$OUT"
