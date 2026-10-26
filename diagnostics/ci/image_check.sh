#!/usr/bin/env bash
# 배포 이미지 확인 (격리 러너 전용 — 운영 PC·운영 Docker 엔진에서 돌리지 않는다).
# 지금 checkout한 커밋으로 backend·frontend·Render frontend 이미지를 만들고, 실행 사용자·포트·pid/임시 경로 권한·health·
# 정적 파일·API 프록시·업로드·내려받기·설치 도구 제거 뒤 라이브러리·Alembic을 확인한 뒤, 완성 이미지를 Trivy로 검사한다.
# 메모리 제한(512MiB, Render 무료와 같은 크기) 컨테이너에서 읽기 한도 경계도 잰다. 판정 없이 측정을 OUT에 남긴다.
# 계정 비밀번호는 실행 때 만들고 출력하지 않는다. 합성 자료만 쓴다. 외부(Render) 서비스로 요청하지 않는다.
set -uo pipefail
OUT="${1:-out}"
mkdir -p "$OUT"
LOG="$OUT/check.log"
RES="$OUT/results.tsv"
: > "$RES"
log() { echo "[check] $*" | tee -a "$LOG"; }
res() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" | tee -a "$RES"; }  # 항목 · 결과 · 근거

SHA=$(git rev-parse HEAD)
log "commit $SHA ($(git log -1 --format=%s))"
docker version --format 'docker {{.Server.Version}}' | tee -a "$LOG"

# ---------------------------------------------------------------- 빌드
build() {  # build <이름> <컨텍스트> [Dockerfile]
  local name=$1 ctx=$2 file=${3:-}
  local start=$(date +%s)
  if docker build ${file:+-f "$file"} -t "moajwo-$name:check" "$ctx" > "$OUT/build-$name.log" 2>&1; then
    res "build_$name" ok "$(( $(date +%s) - start ))s"
  else
    res "build_$name" FAIL "build-$name.log"; tail -30 "$OUT/build-$name.log"
  fi
}
# 기반 이미지를 먼저 받아 둔다 — 빌드가 같은 판을 쓰고, 아래에서 그 판의 digest·패키지를 읽는다
for base in nginx:1.30-alpine node:24.22.1-alpine python:3.11-slim; do docker pull -q "$base" >> "$LOG" 2>&1; done
build backend backend
build frontend frontend
build frontend-render frontend frontend/Dockerfile.render
grep -ho 'docker.io/library/[a-z0-9.:-]*@sha256:[0-9a-f]*' "$OUT"/build-*.log 2>/dev/null | sort -u > "$OUT/base-digests-used.txt"
for n in backend frontend frontend-render; do
  docker image inspect "moajwo-$n:check" > "$OUT/inspect-$n.json" 2>/dev/null || continue
  docker image inspect "moajwo-$n:check" --format \
    '{{.Id}} user={{.Config.User}} ports={{json .Config.ExposedPorts}} cmd={{json .Config.Cmd}} layers={{len .RootFS.Layers}} size={{.Size}}' \
    | sed "s/^/$n /" | tee -a "$OUT/images.txt"
done

# ---------------------------------------------------------------- 기반 이미지·설치 패키지 (두 nginx 최종 이미지 — 로컬 frontend·Render)
# 기반 태그는 떠도는 태그라 빌드 때 받은 판의 digest를 남긴다. 패키지는 기반 nginx 이미지 대비 바뀐 것만 따로 (apk info -v)
for base in nginx:1.30-alpine node:24.22.1-alpine python:3.11-slim; do
  docker image inspect "$base" --format "$base {{.Id}} {{json .RepoDigests}}" >> "$OUT/bases.txt" 2>&1
done
res base_nginx "$(grep '^nginx:' "$OUT/bases.txt" | cut -d' ' -f3- | cut -c1-120)" "기반 nginx:1.30-alpine digest (bases.txt)"
docker run --rm --entrypoint apk nginx:1.30-alpine info -v 2>/dev/null | sort > "$OUT/apk-base-nginx.txt"
for n in frontend frontend-render; do
  docker run --rm --entrypoint apk "moajwo-$n:check" info -v 2>/dev/null | sort > "$OUT/apk-$n.txt"
  res "image_$n" "$(docker image inspect "moajwo-$n:check" --format '{{.Id}}' | cut -c1-19)" "최종 이미지 ID"
  res "apk_brotli_$n" "$(grep -E '^brotli-libs-' "$OUT/apk-$n.txt" | tr '\n' ' ')" \
    "설치된 brotli-libs (기반: $(grep -E '^brotli-libs-' "$OUT/apk-base-nginx.txt" | tr '\n' ' '))"
  res "apk_curl_$n" "$(grep -E '^(curl|libcurl)-[0-9]' "$OUT/apk-$n.txt" | tr '\n' ' ')" \
    "설치된 curl·libcurl (기반: $(grep -E '^(curl|libcurl)-[0-9]' "$OUT/apk-base-nginx.txt" | tr '\n' ' '))"
  res "apk_changes_$n" "$(diff "$OUT/apk-base-nginx.txt" "$OUT/apk-$n.txt" | grep '^[<>]' | tr '\n' ' ')" \
    "기반 nginx 대비 패키지 변경 (< 기반 · > 최종, 비면 변경 없음 — apk-$n.txt)"
done

# ---------------------------------------------------------------- backend
docker network create mnet > /dev/null
docker run -d --name backend --network mnet --network-alias backend \
  -e OLLAMA_BASE_URL=http://127.0.0.1:9 moajwo-backend:check > /dev/null
for _ in $(seq 1 60); do
  [ "$(docker inspect --format '{{.State.Health.Status}}' backend 2>/dev/null)" = healthy ] && break
  sleep 2
done
res backend_health "$(docker inspect --format '{{.State.Health.Status}}' backend)" "HEALTHCHECK /health"
res backend_uid "$(docker exec backend id -u)" "id -u (이미지 USER app)"
res backend_alembic "$(docker exec backend alembic current 2>/dev/null | tail -1)" "기동 때 alembic upgrade head"
if docker exec backend python -c "import pip" 2>/dev/null; then res backend_pip present "pip 남음"; else res backend_pip removed "import pip 실패 (설치 도구 제거)"; fi
res backend_libs "$(docker exec backend python -c 'import openpyxl, xlrd, chardet, rapidfuzz, sqlalchemy, alembic, fastapi, uvicorn, multipart; print("ok")' 2>&1 | tail -1)" "필수 라이브러리 import"
docker exec backend sh -c 'ls -ld /app/data && id' >> "$LOG" 2>&1

# ---------------------------------------------------------------- frontend (compose와 같은 구성: 컨테이너 8080 → 호스트 18080)
docker run -d --name frontend --network mnet -p 127.0.0.1:18080:8080 moajwo-frontend:check > /dev/null
for _ in $(seq 1 30); do curl -sf -o /dev/null http://127.0.0.1:18080/ && break; sleep 1; done
res frontend_uid "$(docker exec frontend id -u)" "id -u (기대 101 nginx)"
res frontend_master "$(docker exec frontend ps -o user,args | awk '/nginx: master/ {print $1}' | head -1)" "nginx master 프로세스 사용자"
res frontend_nginx_t "$(docker exec frontend nginx -t 2>&1 | tail -1)" "nginx -t"
res frontend_pid "$(docker exec frontend sh -c 'ls -l /tmp/nginx.pid | awk "{print \$3}"')" "/tmp/nginx.pid 소유자"
res frontend_cache "$(docker exec frontend sh -c 'stat -c %U /var/cache/nginx/client_temp 2>/dev/null || echo 없음')" "임시 경로 소유자"
docker logs frontend > "$OUT/frontend-start.log" 2>&1
H=http://127.0.0.1:18080
res index "$(curl -s -o "$OUT/index.html" -w '%{http_code}' $H/)" "GET /"
ASSET=$(grep -o '/assets/index-[^"]*\.js' "$OUT/index.html" | head -1)
res static "$(curl -s -o /dev/null -w '%{http_code} %{content_type}' "$H$ASSET")" "GET $ASSET"
curl -s -D "$OUT/headers-index.txt" -o /dev/null $H/
curl -s -D "$OUT/headers-asset.txt" -o /dev/null "$H$ASSET"
res api_proxy "$(curl -s -o /dev/null -w '%{http_code}' $H/api/health)" "GET /api/health (nginx → backend:8000)"
docker exec frontend sh -c 'cd /usr/share/nginx/html && find . -type f -exec sha256sum {} + | sort -k2' > "$OUT/asset-sha256.txt"

# 업로드·내려받기 (nginx 프록시를 거쳐, 새 최초 소유자 계정·합성 CSV)
JAR="$OUT/.cookies"; PW="Ci-$(openssl rand -hex 12)"
curl -s -c "$JAR" -o /dev/null -w '%{http_code}\n' -H 'Content-Type: application/json' \
  -d "{\"username\":\"ci-owner\",\"display_name\":\"CI\",\"password\":\"$PW\"}" $H/api/auth/setup | sed 's/^/setup /' >> "$LOG"
printf '시도,인구\n서울특별시,100\n부산광역시,200\n' > "$OUT/ci.csv"
UP=$(curl -s -b "$JAR" -F "files=@$OUT/ci.csv;type=text/csv" $H/api/files)
FID=$(echo "$UP" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])' 2>/dev/null)
res upload "${FID:+id=$FID}" "POST /api/files (multipart, 프록시)"
PLAN=$(curl -s -b "$JAR" -H 'Content-Type: application/json' -d "{\"file_ids\":[$FID]}" $H/api/bundle/plan)
BASIS=$(echo "$PLAN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["basis"])' 2>/dev/null)
RUN=$(curl -s -b "$JAR" -H 'Content-Type: application/json' -d "{\"file_ids\":[$FID],\"expected_basis\":\"$BASIS\"}" $H/api/bundle/run)
RID=$(echo "$RUN" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])' 2>/dev/null)
res download "$(curl -s -b "$JAR" -o "$OUT/bundle.xlsx" -w '%{http_code} %{content_type} %{size_download}B' $H/api/runs/$RID/download)" "묶기 결과 GET /api/runs/$RID/download"
rm -f "$JAR"

# ---------------------------------------------------------------- Render frontend 이미지 (외부 API로 요청하지 않음)
docker run --rm moajwo-frontend-render:check nginx -t > "$OUT/render-nginx-t.log" 2>&1
res render_nginx_t "$(tail -1 "$OUT/render-nginx-t.log")" "nginx -t (proxy_pass 호스트 이름 확인만)"
docker run -d --name render -p 127.0.0.1:18081:8080 moajwo-frontend-render:check > /dev/null
for _ in $(seq 1 30); do curl -sf -o /dev/null http://127.0.0.1:18081/ && break; sleep 1; done
res render_uid "$(docker exec render id -u)" "id -u"
res render_index "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/)" "GET / (8080)"
docker rm -f render > /dev/null

# Render 앞단 → https API → backend 흐름을 격리로: 같은 이름(moajwo-test-api.onrender.com)의 대역 TLS 앞단을 이 망 안에 두고
# 그 뒤에 위 backend를 둔다 — 실제 Render로 요청하지 않는다. 앞단(nginx.render.conf)은 상류 인증서를 검증하므로(proxy_ssl_verify on)
# 시험용 CA(뿌리 → 중간 → 서버)를 만들어 서버 인증서를 그 CA로 서명하고, 앞단 컨테이너의 신뢰 묶음 파일 자리에 그 뿌리 CA만 든
# 묶음을 읽기 전용으로 덮어 띄운다(이미지 자체는 그대로). 그 다음 신뢰하지 않는 CA·만료·이름 불일치·중간 인증서 누락을 차례로
# 대역 API에 꽂아 앞단이 요청을 전달하지 않는지(502, 오류 기록에 verify 실패, backend에 그 경로의 요청 없음)와, 정상 체인으로
# 돌아오면 로그인·쿠키·헤더·업로드·내려받기가 되는지 본다. 검증을 끄거나 http로 돌아가는 우회는 설정에 없다(nginx -T로 셈).
# 이 대역 시험은 검증 기능 자체의 시험이다 — 실제 eMudhra 차단의 증명은 아래 '공개 검증 호스트' 항목으로 따로 본다.
RT=$(mktemp -d)
chmod 755 "$RT"
mkca() {  # mkca <이름> <CN> [서명 CA 이름] → $RT/<이름>.key·.crt (서명 CA가 없으면 자체 서명 뿌리)
  local n=$1 cn=$2 by=${3:-}
  openssl genrsa -out "$RT/$n.key" 2048 > /dev/null 2>&1
  if [ -z "$by" ]; then
    openssl req -x509 -new -key "$RT/$n.key" -days 2 -subj "/CN=$cn" -addext "basicConstraints=critical,CA:TRUE" \
      -addext "keyUsage=critical,keyCertSign,cRLSign" -out "$RT/$n.crt" > /dev/null 2>&1
  else
    openssl req -new -key "$RT/$n.key" -subj "/CN=$cn" -out "$RT/$n.csr" > /dev/null 2>&1
    printf 'basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n' > "$RT/$n.ext"
    openssl x509 -req -in "$RT/$n.csr" -CA "$RT/$by.crt" -CAkey "$RT/$by.key" -CAcreateserial -days 2 -extfile "$RT/$n.ext" \
      -out "$RT/$n.crt" > /dev/null 2>&1
  fi
}
mkleaf() {  # mkleaf <이름> <CN> <서명 CA 이름> [시작 YYYYMMDDHHMMSSZ 끝 YYYYMMDDHHMMSSZ] → $RT/<이름>.key·.crt (SAN=DNS:CN)
  local n=$1 cn=$2 by=$3 nb=${4:-} na=${5:-}
  openssl genrsa -out "$RT/$n.key" 2048 > /dev/null 2>&1
  openssl req -new -key "$RT/$n.key" -subj "/CN=$cn" -out "$RT/$n.csr" > /dev/null 2>&1
  mkdir -p "$RT/db-$n"; : > "$RT/db-$n/index.txt"; echo 1000 > "$RT/db-$n/serial"
  printf '[ca]\ndefault_ca=d\n[d]\ndir=%s\ndatabase=$dir/index.txt\nserial=$dir/serial\nnew_certs_dir=$dir\ndefault_md=sha256\npolicy=p\nx509_extensions=leaf\ndefault_days=2\nunique_subject=no\n[p]\ncommonName=supplied\n[leaf]\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:%s\n' \
    "$RT/db-$n" "$cn" > "$RT/db-$n/ca.cnf"
  openssl ca -batch -config "$RT/db-$n/ca.cnf" -keyfile "$RT/$by.key" -cert "$RT/$by.crt" -in "$RT/$n.csr" -out "$RT/$n.crt" -notext \
    ${nb:+-startdate "$nb"} ${na:+-enddate "$na"} > /dev/null 2>&1
}
mkca root "Moajwo CI Test Root CA"
mkca inter "Moajwo CI Test Intermediate CA" root
mkca other "Moajwo CI Other Root CA"            # 앞단이 믿지 않는 다른 CA
mkleaf good moajwo-test-api.onrender.com inter                                   # 정상: 중간 CA가 서명, 이름 일치
mkleaf wrongname wrong-name.example.test inter                                   # 이름 불일치
mkleaf expired moajwo-test-api.onrender.com inter 20260101000000Z 20260102000000Z  # 만료
mkleaf untrusted moajwo-test-api.onrender.com other                              # 신뢰하지 않는 CA(자체 서명 뿌리)가 서명
cat "$RT/good.crt" "$RT/inter.crt" > "$RT/good-chain.crt"       # 서버 + 중간 (보통 체인)
cat "$RT/wrongname.crt" "$RT/inter.crt" > "$RT/wrongname-chain.crt"
cat "$RT/expired.crt" "$RT/inter.crt" > "$RT/expired-chain.crt"
cp "$RT/root.crt" "$RT/trust.pem"                               # 앞단에 줄 신뢰 묶음: 시험 뿌리 CA 하나뿐
chmod 644 "$RT"/*.crt "$RT"/*.key "$RT"/*.pem
res tls_chain_depth "$(openssl verify -CAfile "$RT/root.crt" -untrusted "$RT/inter.crt" "$RT/good.crt" 2>&1 | tail -1) · 체인 뿌리→중간→서버 (중간 1)" \
  "시험 체인의 깊이 — nginx proxy_ssl_verify_depth 3(중간 최대 2)은 이 체인과 공개 CA의 보통 체인을 받음"

tls_case() {  # tls_case <이름> <서버 인증서 파일> <키> <설명> → 앞단 /api/<이름> 요청의 상태·앞단 오류 기록·backend 도달 여부
  local name=$1 crt=$2 key=$3 why=$4
  printf 'server { listen 443 ssl; ssl_certificate /t/%s; ssl_certificate_key /t/%s;\n  location / { proxy_pass http://backend:8000; } }\n' "$crt" "$key" > "$RT/tls-$name.conf"
  docker run -d --name api-tls --network mnet --network-alias moajwo-test-api.onrender.com -v "$RT:/t:ro" \
    -v "$RT/tls-$name.conf:/etc/nginx/conf.d/default.conf:ro" nginx:1.30-alpine > /dev/null
  docker run -d --name render-chain --network mnet -p 127.0.0.1:18082:8080 \
    -v "$RT/trust.pem:/etc/ssl/certs/ca-certificates.crt:ro" moajwo-frontend-render:check > /dev/null
  for _ in $(seq 1 30); do curl -sf -o /dev/null http://127.0.0.1:18082/ && break; sleep 1; done
  local code err hit
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:18082/api/health?case=$name"); sleep 1
  code="$code/$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:18082/api/health?case=$name")"   # 다시 시도도 같은 결과여야
  err=$(docker logs render-chain 2>&1 | grep -iE 'upstream SSL|certificate|verify|handshake' | sed -E 's/^[^ ]+ [^ ]+ //' | cut -c1-160 | tail -1)
  hit=$(docker logs backend 2>&1 | grep -c "case=$name")   # backend 접근 기록에 그 경로가 있으면 요청이 전달된 것
  res "tls_$name" "$code · backend 도달 $hit · ${err:-오류 기록 없음}" "$why"
  docker rm -f render-chain api-tls > /dev/null
}
tls_case trusted      good-chain.crt good.key       "신뢰 CA + 올바른 서버 이름 → 200/200, backend 도달 2"
tls_case untrusted    untrusted.crt  untrusted.key  "신뢰하지 않는 CA(자체 서명) → 502/502, 도달 0, verify 실패"
tls_case expired      expired-chain.crt expired.key "만료 인증서 → 502/502, 도달 0"
tls_case wrongname    wrongname-chain.crt wrongname.key "서버 이름 불일치 → 502/502, 도달 0"
tls_case nointer      good.crt       good.key       "필수 중간 인증서 누락(서버 인증서만 보냄) → 502/502, 도달 0"
tls_case recovered    good-chain.crt good.key       "정상 체인으로 복구 → 200/200 (검증을 끄지 않고 복구)"

# 복구된 체인으로 기존 흐름(로그인·쿠키·보안 헤더·업로드·내려받기)이 그대로인지 — 앞단은 시험 뿌리 CA 묶음으로
printf 'server { listen 443 ssl; ssl_certificate /t/good-chain.crt; ssl_certificate_key /t/good.key;\n  location / { proxy_pass http://backend:8000; } }\n' > "$RT/tls.conf"
docker run -d --name api-tls --network mnet --network-alias moajwo-test-api.onrender.com -v "$RT:/t:ro" \
  -v "$RT/tls.conf:/etc/nginx/conf.d/default.conf:ro" nginx:1.30-alpine > /dev/null
docker run -d --name render-chain --network mnet -p 127.0.0.1:18082:8080 \
  -v "$RT/trust.pem:/etc/ssl/certs/ca-certificates.crt:ro" moajwo-frontend-render:check > /dev/null
for _ in $(seq 1 30); do curl -sf -o /dev/null http://127.0.0.1:18082/ && break; sleep 1; done
R=http://127.0.0.1:18082
res render_chain_health "$(curl -s $R/api/health)" "앞단 /api/health → https 대역 API(시험 CA 체인) → backend"
RJ="$RT/cookies"
res render_chain_login "$(curl -s -c "$RJ" -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  -d "{\"username\":\"ci-owner\",\"password\":\"$PW\"}" $R/api/auth/login) · me $(curl -s -b "$RJ" -o /dev/null -w '%{http_code}' $R/api/auth/me)" \
  "로그인(앞단을 거쳐) → 쿠키로 /api/auth/me"
res render_chain_cookie "$(grep -c moajwo "$RJ" 2>/dev/null)개 · $(curl -s -D - -o /dev/null -H 'Content-Type: application/json' -d "{\"username\":\"ci-owner\",\"password\":\"$PW\"}" $R/api/auth/login | grep -i '^set-cookie' | sed 's/=[^;]*;/=<값>;/' | tr -d '\r' | head -1)" \
  "로그인 쿠키 속성 (값은 지움 — Secure는 MOAJWO_COOKIE_SECURE를 켠 Render에서만)"
res render_chain_headers "$(curl -sI $R/ | grep -ciE '^(content-security-policy|x-frame-options|x-content-type-options|referrer-policy):')/4" "GET / 보안 헤더 4종"
res render_chain_api_headers "$(curl -sI $R/api/health | grep -ciE '^(content-security-policy|x-frame-options|x-content-type-options|referrer-policy):')/4" "GET /api/health 보안 헤더 (앞단이 API 응답에 붙이는지)"
UP2=$(curl -s -b "$RJ" -F "files=@$OUT/ci.csv;type=text/csv" $R/api/files)
FID2=$(echo "$UP2" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])' 2>/dev/null)
res render_chain_upload "${FID2:+id=$FID2}" "앞단을 거친 업로드 (multipart)"
res render_chain_download "$(curl -s -b "$RJ" -o /dev/null -w '%{http_code} %{content_type}' $R/api/runs/$RID/download)" "앞단을 거친 내려받기 (앞서 만든 묶기 결과 #$RID)"
res render_upstream_verify "$(docker exec render-chain nginx -T 2>/dev/null | grep -cE '^\s*proxy_ssl_verify\s+on') · off $(docker exec render-chain nginx -T 2>/dev/null | grep -cE '^\s*proxy_ssl_verify\s+off')" \
  "상류 인증서 검증 설정 수 (on 1·off 0이어야 — 끄는 우회 없음)"
res render_upstream_name "$(docker exec render-chain nginx -T 2>/dev/null | grep -E '^\s*proxy_ssl_(name|server_name|verify_depth|trusted_certificate|protocols)' | sed 's/^\s*//' | tr '\n' ' ')" "SNI·검증 이름·깊이·신뢰 묶음·프로토콜"
docker rm -f render-chain api-tls > /dev/null
rm -f "$RJ"

# 공개 검증 호스트 (Render 안내가 가리키는 커뮤니티 시험 호스트 — 손상된 eMudhra 체인을 내놓음): 같은 최종 이미지의 nginx가 자기 OS
# 신뢰 묶음(마운트 없음)으로 그 호스트에 붙으면 verify 실패로 502여야 한다. 반대로 정상 공개 호스트는 200이어야 한다.
# DNS 실패·연결 시간 초과·404는 인증서 거절로 세지 않는다 — 오류 기록의 verify 문구로만 판정한다. 인증 정보 없이 GET 한 번씩.
pubcase() {  # pubcase <이름> <호스트> <설명>
  local name=$1 host=$2 why=$3
  printf 'resolver 1.1.1.1 8.8.8.8 valid=60s ipv6=off;\nserver { listen 8080; location / { set $u https://%s/; proxy_pass $u; proxy_ssl_verify on; proxy_ssl_verify_depth 3; proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt; proxy_ssl_server_name on; proxy_ssl_name %s; proxy_ssl_protocols TLSv1.2 TLSv1.3; proxy_set_header Host %s; proxy_connect_timeout 15s; proxy_read_timeout 20s; } }\n' "$host" "$host" "$host" > "$RT/pub-$name.conf"
  docker run -d --name pub -p 127.0.0.1:18083:8080 -v "$RT/pub-$name.conf:/etc/nginx/conf.d/default.conf:ro" moajwo-frontend-render:check > /dev/null
  sleep 2
  local code err kind
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 40 http://127.0.0.1:18083/)
  err=$(docker logs pub 2>&1 | grep -iE 'upstream SSL|certificate|verify|resolve|timed out|connect\(\)' | sed -E 's/^[^ ]+ [^ ]+ //' | cut -c1-200 | tail -1)
  case "$err" in
    *verify*|*certificate*) kind="verify 실패";;
    *resolve*|*"timed out"*|*"connect()"*) kind="연결·DNS 문제 (인증서 거절로 세지 않음 — 미검증)";;
    *) kind="오류 기록 없음";;
  esac
  res "pub_$name" "$code · $kind · ${err:-}" "$why"
  docker rm -f pub > /dev/null
  docker run --rm moajwo-frontend-render:check sh -c "wget -q -O /dev/null https://$host/ 2>&1; echo exit=\$?" | tail -1 | sed "s/^/wget(busybox, OS CA) /" | tee -a "$LOG"
}
pubcase emudhra-g3b dv-g3b-emudhra.we-have-a-problem.com "손상된 eMudhra 체인 (Render 안내의 시험 호스트) → verify 실패·502이어야"
pubcase emudhra-g3b-aaa dv-g3b-aaa-emudhra.we-have-a-problem.com "손상된 eMudhra 체인 (다른 중간) → verify 실패·502이어야"
pubcase control example.com "대조군 — 정상 공개 체인 → 200 (연결이 되는 러너인지)"

# 신뢰 저장소 조사: 최종 이미지의 OS CA 묶음에 emSign·eMudhra 이름의 인증서가 남았는지(0이어야), 묶음 크기, 패키지 판
for n in frontend-render backend; do
  docker run --rm --entrypoint cat "moajwo-$n:check" /etc/ssl/certs/ca-certificates.crt > "$OUT/ca-bundle-$n.pem" 2>/dev/null
  SUBJ=$(openssl crl2pkcs7 -nocrl -certfile "$OUT/ca-bundle-$n.pem" 2>/dev/null | openssl pkcs7 -print_certs -noout 2>/dev/null | grep '^subject')
  res "ca_bundle_$n" "인증서 $(echo "$SUBJ" | grep -c .)개 · emSign/eMudhra $(echo "$SUBJ" | grep -ciE 'emsign|emudhra')개 · sha256 $(sha256sum "$OUT/ca-bundle-$n.pem" | cut -c1-16)" "OS CA 묶음 /etc/ssl/certs/ca-certificates.crt"
done
res ca_pkg_frontend-render "$(grep -E '^ca-certificates' "$OUT/apk-frontend-render.txt" | tr '\n' ' ')" "Alpine ca-certificates 패키지 판 (기반: $(grep -E '^ca-certificates' "$OUT/apk-base-nginx.txt" | tr '\n' ' '))"
res ca_pkg_backend "$(docker run --rm --entrypoint dpkg-query moajwo-backend:check -W -f '${Package} ${Version}' ca-certificates 2>/dev/null)" "Debian ca-certificates 패키지 판"
res build_versions "$(grep -hoE 'node root certificates [0-9]+ emSign/eMudhra [0-9]+|^#[0-9]+ [0-9.]+ (v[0-9.]+|[0-9]+\.[0-9]+\.[0-9]+)$|pip [0-9.]+ from|pip-vendored certifi [0-9.]+|Python 3\.[0-9.]+|ca-certificates [0-9+a-z~.]+' "$OUT"/build-frontend-render.log "$OUT"/build-backend.log | sed -E 's/^#[0-9]+ [0-9.]+ //' | sort -u | tr '\n' ' ')" "빌드 기록의 node·npm·뿌리 인증서 수·python·pip·pip 내장 certifi·Debian ca-certificates"
rm -rf "$RT"

# ---------------------------------------------------------------- 메모리 제한 컨테이너의 읽기 한도 (Render 무료와 같은 512MiB)
# 한도는 docs/MEMORY.md의 Render 권장값. 자료는 측정 대상이 아닌 일회용 컨테이너(같은 backend 이미지의 openpyxl)에서 만든다
# — 만드는 데 쓴 메모리가 측정 컨테이너의 최대값에 섞이지 않게. 중단 기준: 컨테이너 OOM·재시작이 나면 그 뒤 측정은 믿지 않는다.
if ! grep -q MOAJWO_MAX_FILE_CELLS backend/app/config.py; then
  res mem "건너뜀" "읽기 한도 설정이 없는 코드 (PR #31 전)"
else
docker run -i --name gen --entrypoint python moajwo-backend:check - <<'PY' > "$OUT/mem-files.txt" 2>&1
import csv, os, zipfile
import openpyxl
def xlsx(path, rows):
    wb = openpyxl.Workbook(write_only=True); ws = wb.create_sheet("시트1")
    ws.append(["기준일자", "시도", "시군구", "기관코드", "인구", "세대수", "예산", "비고"])
    for i in range(rows):
        ws.append([f"2026-09-{i % 28 + 1:02d}", "서울특별시", f"시군구{i % 25}", 1100000 + i, 1000 + i, 400 + i % 97, i * 1.5, "합성"])
    wb.save(path)
    with zipfile.ZipFile(path) as z:
        unpacked = sum(x.file_size for x in z.infolist())
    print(path, f"rows={rows} cells={(rows + 1) * 8}", f"unpacked={unpacked / 2**20:.2f}MiB", f"file={os.path.getsize(path) / 2**20:.2f}MiB")
def csvf(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["기준일자", "시도", "인구", "세대수"])
        for i in range(rows):
            w.writerow([f"2026-09-{i % 28 + 1:02d}", "서울특별시", 1000 + i, 400 + i])
    print(path, f"rows={rows} cells={(rows + 1) * 4}")
def big(path, mib):  # 올리기 크기 한도(10MiB) 위 — 저장하는 중에 거절
    line = "2026-09-01,서울특별시,1000,400\n".encode()
    with open(path, "wb") as f:
        f.write("기준일자,시도,인구,세대수\n".encode())
        for _ in range(mib * 2**20 // len(line) + 1):
            f.write(line)
    print(path, f"file={mib}MiB+")
xlsx("/tmp/x_under.xlsx", 39000)   # 31.2만 칸, 풀었을 때 16MiB 아래를 노림 (실제 값은 위 출력)
xlsx("/tmp/x_over.xlsx", 52000)    # 41.6만 칸 · 16MiB 위를 노림 — 읽기 전에 거절
csvf("/tmp/c_under.csv", 99000)    # 39.6만 칸 (한도 40만 아래)
csvf("/tmp/c_over.csv", 101000)    # 40.4만 칸 (한도 위 — 읽는 중에 거절)
big("/tmp/c_big.csv", 11)
PY
for f in x_under.xlsx x_over.xlsx c_under.csv c_over.csv c_big.csv; do docker cp "gen:/tmp/$f" "$OUT/$f" > /dev/null; done
docker rm gen > /dev/null
ls -l "$OUT"/x_*.xlsx "$OUT"/c_*.csv >> "$OUT/mem-files.txt"

docker run -d --name small --network mnet --memory=512m --memory-swap=512m \
  -e OLLAMA_BASE_URL=http://127.0.0.1:9 -e MOAJWO_MAX_UPLOAD_MIB=10 -e MOAJWO_MAX_XLSX_UNCOMPRESSED_MIB=16 \
  -e MOAJWO_MAX_FILE_CELLS=400000 -e MOAJWO_VIEW_CACHE_MIB=32 -e MOAJWO_PARSE_SLOTS=1 \
  -p 127.0.0.1:18000:8000 moajwo-backend:check > /dev/null
for _ in $(seq 1 60); do
  [ "$(docker inspect --format '{{.State.Health.Status}}' small 2>/dev/null)" = healthy ] && break
  sleep 2
done
S=http://127.0.0.1:18000
TMPD=$(mktemp -d)
JAR="$TMPD/cookies"; PW="Ci-$(openssl rand -hex 12)"
curl -s -c "$JAR" -o /dev/null -H 'Content-Type: application/json' \
  -d "{\"username\":\"ci-owner\",\"display_name\":\"CI\",\"password\":\"$PW\"}" $S/auth/setup
# 최대 메모리는 컨테이너 기동부터의 누적 최대다 (memory.peak 초기화는 리눅스 6.12+에서도 쓴 쪽 파일 설명자에만 적용되어
# 다른 읽기에는 누적 값이 보인다 — 2회차에서 확인). 사례를 작은 것부터 돌려 늘어난 만큼을 그 사례의 몫으로 읽는다.
PEAK_MODE=누적
reset_peak() { true; }
peak() { docker exec small cat /sys/fs/cgroup/memory.peak 2>/dev/null | awk '{printf "%.0fMiB", $1/1048576}'; }
current() { docker exec small cat /sys/fs/cgroup/memory.current 2>/dev/null | awk '{printf "%.0fMiB", $1/1048576}'; }
cat > "$TMPD/sum.py" <<'PY'
import json, sys
code, body = sys.argv[1], open(sys.argv[2], encoding="utf-8").read()
try:
    d = json.loads(body)
except ValueError:
    print(f"HTTP {code} 응답 해석 실패"); sys.exit()
if isinstance(d, list):
    parts = []
    for x in d:
        state = ("거절: " + x["error"]) if x.get("error") else f"ok rows={x.get('data_rows')}"
        parts.append(f"{x.get('original_name')} id={x.get('id')} {state}")
    print(f"HTTP {code} " + "; ".join(parts))
elif isinstance(d, dict) and "rows" in d:
    print(f"HTTP {code} 미리보기 rows={len(d['rows'])} total_rows={d.get('total_rows')}")
else:
    print(f"HTTP {code} {str(d.get('detail', d))[:200]}")
PY
state() { docker inspect --format 'OOMKilled={{.State.OOMKilled}} restarts={{.RestartCount}}' small; }
request() {  # request <이름> <설명> <curl 인자...>
  local name=$1 what=$2; shift 2
  local start code ms
  start=$(date +%s%3N)
  code=$(curl -s -o "$TMPD/body-$name.json" -w '%{http_code}' -b "$JAR" "$@")
  ms=$(( $(date +%s%3N) - start ))
  res "mem_$name" "$(python3 "$TMPD/sum.py" "$code" "$TMPD/body-$name.json")" \
    "$what · ${ms}ms · ${PEAK_MODE} 최대 $(peak) · 지금 $(current) · $(state)"
}
upload() { local name=$1 what=$2; shift 2; local args=(); for f in "$@"; do args+=(-F "files=@$OUT/$f"); done; request "$name" "$what" "${args[@]}" $S/files; }
res mem_baseline "$(peak)" "기동 직후 최대 (alembic upgrade 포함) · 지금 $(current)"
reset_peak; upload small_csv "일반 파일" ci.csv
reset_peak; upload xlsx_under "한도 아래 xlsx" x_under.xlsx
reset_peak; upload xlsx_over "한도 위 xlsx" x_over.xlsx
reset_peak; upload csv_under "한도 아래 CSV" c_under.csv
reset_peak; upload csv_over "한도 위 CSV" c_over.csv
reset_peak; upload size_over "올리기 크기 한도 위" c_big.csv
reset_peak; upload multi "한 요청에 두 파일 (차례로 읽음)" x_under.xlsx c_under.csv
# 서로 다른 파일을 두 요청으로 동시에 (읽기 차례 1개 — 하나는 기다렸다 읽는다)
reset_peak; ( upload parallel_a "동시 요청 A" x_under.xlsx ) & ( upload parallel_b "동시 요청 B" c_under.csv ) & wait
XID=$(python3 -c 'import json,sys; print(next(x["id"] for x in json.load(open(sys.argv[1])) if x.get("id") and not x.get("error")))' "$TMPD/body-xlsx_under.json" 2>/dev/null)
if [ -n "$XID" ]; then
  reset_peak; request view_cold "미리보기 — 보기 캐시 비어 있음 (원본을 다시 읽음, 최초 비용)" "$S/files/$XID/preview"
  reset_peak; request view_warm "미리보기 — 보기 캐시 채워짐 (같은 파일 두 번째)" "$S/files/$XID/preview"
else
  res mem_view "건너뜀" "한도 아래 xlsx 업로드가 성공하지 않음"
fi
# 거절 뒤 남는 상태: 기록·원본 파일·매핑·실행 기록 (읽기 전용으로 연다)
LEFT=$(docker exec -i small python - <<'PY2' 2>&1
import os, sqlite3
c = sqlite3.connect("file:/app/data/moajwo.db?mode=ro", uri=True)
n = {t: c.execute(f"select count(*) from {t}").fetchone()[0]
     for t in ("source_files", "runs", "mapping_decisions", "mapping_analyses", "analysis_runs")}
unread = c.execute("select count(*) from source_files where pii_summary is null").fetchone()[0]
print(f"source_files={n['source_files']} (읽지 못함 {unread}) uploads={len(os.listdir('/app/data/uploads'))} "
      + " ".join(f"{k}={v}" for k, v in n.items() if k != "source_files"))
PY2
)
res mem_leftover "$LEFT" "기록 수 = 업로드 폴더 파일 수여야 함 (크기 거절·차례 대기 실패는 둘 다 남기지 않음, 한도 거절은 '읽지 못한 파일'로 남음)"
res mem_events "$(docker exec small cat /sys/fs/cgroup/memory.events 2>/dev/null | tr '\n' ' ')" "cgroup memory.events (oom_kill 0이어야 거절로 처리)"
res mem_state "$(docker inspect --format 'running={{.State.Running}} OOMKilled={{.State.OOMKilled}} restarts={{.RestartCount}}' small)" "컨테이너 상태"
docker logs small > "$OUT/small-backend.log" 2>&1
rm -rf "$TMPD" "$OUT"/x_*.xlsx "$OUT"/c_*.csv
fi

# ---------------------------------------------------------------- 완성 이미지 취약점 (Trivy 0.76.0, 릴리스 체크섬 확인)
TV=0.76.0
TGZ="trivy_${TV}_Linux-64bit.tar.gz"  # 체크섬 파일의 이름 그대로 받아야 sha256sum -c가 찾는다
curl -sSL -o "$TGZ" "https://github.com/aquasecurity/trivy/releases/download/v$TV/$TGZ"
curl -sSL -o trivy.sum "https://github.com/aquasecurity/trivy/releases/download/v$TV/trivy_${TV}_checksums.txt"
if grep "  $TGZ\$" trivy.sum | sha256sum -c - >> "$LOG" 2>&1; then
  tar xzf "$TGZ" trivy
  ./trivy --version | head -1 >> "$LOG"
  # 검사 보고(JSON)에는 취약점 DB 시각이 없다 — DB를 먼저 받고 그 판·시각을 따로 남긴다
  ./trivy image --download-db-only --quiet 2>> "$LOG"
  ./trivy version --format json > "$OUT/trivy-version.json" 2>> "$LOG"
  for n in frontend frontend-render; do  # 대상 CVE는 심각도와 상관없이 찾는다 (HIGH·CRITICAL만 보면 등급이 다를 때 놓침)
    ./trivy image --quiet --skip-db-update --format json --scanners vuln -o "$OUT/trivy-all-$n.json" "moajwo-$n:check" 2>> "$LOG"
    res "trivy_target_$n" "$(python3 - "$OUT/trivy-all-$n.json" "$OUT/trivy-version.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
v = [x for r in d.get("Results", []) for x in r.get("Vulnerabilities") or []]
hit = [f"{x['VulnerabilityID']} {x['PkgName']} {x['InstalledVersion']} (수정 {x.get('FixedVersion') or '-'})" for x in v
       if x["VulnerabilityID"] in ("CVE-2026-111497", "CVE-2026-92392")]
os_ = (d.get("Metadata") or {}).get("OS") or {}
try:
    db = json.load(open(sys.argv[2])).get("VulnerabilityDB") or {}
except (OSError, ValueError):
    db = {}
print(("남음: " + "; ".join(hit)) if hit else "검출 없음", "· 모든 심각도", len({x["VulnerabilityID"] for x in v}), "개",
      "· OS", os_.get("Family", "-"), os_.get("Name", "-"),
      "· 검사 대상", [r.get("Type") for r in d.get("Results", [])], "· DB", (db.get("UpdatedAt") or "-")[:19])
PY
)" "CVE-2026-111497 (brotli-libs)·CVE-2026-92392 (curl·libcurl) — trivy-all-$n.json · DB 시각 trivy-version.json (OS 패키지 대상이 없으면 검사가 안 된 것)"
  done
  for n in backend frontend frontend-render; do
    ./trivy image --quiet --skip-db-update --format json --scanners vuln --severity HIGH,CRITICAL -o "$OUT/trivy-$n.json" "moajwo-$n:check" 2>> "$LOG"
    res "trivy_$n" "$(python3 - "$OUT/trivy-$n.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
v = [x for r in d.get("Results", []) for x in r.get("Vulnerabilities") or []]
fixed = sum(1 for x in v if x.get("FixedVersion"))
print(f"HIGH+CRITICAL {len({x['VulnerabilityID'] for x in v})}개 (항목 {len(v)}, 수정판 있음 {fixed})")
PY
)" "trivy-$n.json"
  done
else
  res trivy "체크섬 불일치" "검사하지 않음"
fi
log "끝"
column -t -s $'\t' "$RES" | tee "$OUT/results.txt"
