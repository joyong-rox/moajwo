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
build backend backend
build frontend frontend
build frontend-render frontend frontend/Dockerfile.render
for n in backend frontend frontend-render; do
  docker image inspect "moajwo-$n:check" > "$OUT/inspect-$n.json" 2>/dev/null || continue
  docker image inspect "moajwo-$n:check" --format \
    '{{.Id}} user={{.Config.User}} ports={{json .Config.ExposedPorts}} cmd={{json .Config.Cmd}} layers={{len .RootFS.Layers}} size={{.Size}}' \
    | sed "s/^/$n /" | tee -a "$OUT/images.txt"
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

# ---------------------------------------------------------------- 메모리 제한 컨테이너의 읽기 한도 (Render 무료와 같은 512MiB)
# 한도는 docs/MEMORY.md의 Render 권장값. 자료는 backend 이미지 안의 openpyxl로 만든다 (러너에 따로 설치하지 않음).
if ! grep -q MOAJWO_MAX_FILE_CELLS backend/app/config.py; then
  res mem "건너뜀" "읽기 한도 설정이 없는 코드 (PR #31 전)"
else
docker run -d --name small --network mnet --memory=512m --memory-swap=512m \
  -e OLLAMA_BASE_URL=http://127.0.0.1:9 -e MOAJWO_MAX_UPLOAD_MIB=10 -e MOAJWO_MAX_XLSX_UNCOMPRESSED_MIB=16 \
  -e MOAJWO_MAX_FILE_CELLS=400000 -e MOAJWO_VIEW_CACHE_MIB=32 -e MOAJWO_PARSE_SLOTS=1 \
  -p 127.0.0.1:18000:8000 moajwo-backend:check > /dev/null
for _ in $(seq 1 60); do
  [ "$(docker inspect --format '{{.State.Health.Status}}' small 2>/dev/null)" = healthy ] && break
  sleep 2
done
docker exec small python - <<'PY' > "$OUT/mem-files.txt" 2>&1
import csv, zipfile
import openpyxl
def xlsx(path, rows):
    wb = openpyxl.Workbook(write_only=True); ws = wb.create_sheet("시트1")
    ws.append(["기준일자", "시도", "시군구", "기관코드", "인구", "세대수", "예산", "비고"])
    for i in range(rows):
        ws.append([f"2026-09-{i % 28 + 1:02d}", "서울특별시", f"시군구{i % 25}", 1100000 + i, 1000 + i, 400 + i % 97, i * 1.5, "합성"])
    wb.save(path)
    with zipfile.ZipFile(path) as z:
        print(path, rows, round(sum(x.file_size for x in z.infolist()) / 2**20, 2), "MiB unpacked")
def csvf(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["기준일자", "시도", "인구", "세대수"])
        for i in range(rows):
            w.writerow([f"2026-09-{i % 28 + 1:02d}", "서울특별시", 1000 + i, 400 + i])
    print(path, rows, rows * 4 + 4, "cells")
xlsx("/tmp/x_under.xlsx", 39000)   # 풀었을 때 약 15MiB (한도 16MiB 아래)
xlsx("/tmp/x_over.xlsx", 52000)    # 약 20MiB (한도 위 — 읽기 전에 거절)
csvf("/tmp/c_under.csv", 99000)    # 39.6만 칸 (한도 40만 아래)
csvf("/tmp/c_over.csv", 101000)    # 40.4만 칸 (한도 위 — 읽는 중에 거절)
PY
for f in x_under.xlsx x_over.xlsx c_under.csv c_over.csv; do docker cp "small:/tmp/$f" "$OUT/$f" > /dev/null; done
S=http://127.0.0.1:18000
JAR="$OUT/.cookies2"; PW="Ci-$(openssl rand -hex 12)"
curl -s -c "$JAR" -o /dev/null -H 'Content-Type: application/json' \
  -d "{\"username\":\"ci-owner\",\"display_name\":\"CI\",\"password\":\"$PW\"}" $S/auth/setup
peak() { docker exec small cat /sys/fs/cgroup/memory.peak 2>/dev/null | awk '{printf "%.0fMiB", $1/1048576}'; }
mem_case() {  # mem_case <이름> <파일...> — 같은 요청에 여러 파일
  local name=$1; shift
  local args=() start
  for f in "$@"; do args+=(-F "files=@$OUT/$f"); done
  start=$(date +%s%3N)
  local body; body=$(curl -s -b "$JAR" "${args[@]}" $S/files)
  local ms=$(( $(date +%s%3N) - start ))
  local summary; summary=$(echo "$body" | python3 -c 'import json,sys
try:
    print("; ".join(f"{x[\"original_name\"]}: " + (x["error"] or f"ok rows={x[\"data_rows\"]}") for x in json.load(sys.stdin)))
except Exception as e:
    print("응답 해석 실패", type(e).__name__)' 2>&1)
  res "mem_$name" "$summary" "${ms}ms, 누적 최대 $(peak), OOMKilled=$(docker inspect --format '{{.State.OOMKilled}}' small)"
}
res mem_baseline "$(peak)" "기동 직후 컨테이너 최대 메모리 (cgroup memory.peak)"
mem_case small_csv ci.csv
mem_case xlsx_under x_under.xlsx
mem_case xlsx_over x_over.xlsx
mem_case csv_under c_under.csv
mem_case csv_over c_over.csv
# 서로 다른 파일 둘을 동시에 올림 (읽기 차례 1개 — 하나는 기다렸다 읽는다)
( mem_case parallel_a x_under.xlsx ) & ( mem_case parallel_b c_under.csv ) & wait
res mem_events "$(docker exec small cat /sys/fs/cgroup/memory.events 2>/dev/null | tr '\n' ' ')" "cgroup memory.events (oom_kill 0이어야 거절로 처리)"
res mem_state "$(docker inspect --format 'running={{.State.Running}} OOMKilled={{.State.OOMKilled}} restarts={{.RestartCount}}' small)" "컨테이너 상태"
docker logs small > "$OUT/small-backend.log" 2>&1
rm -f "$JAR" "$OUT"/x_*.xlsx "$OUT"/c_*.csv
fi

# ---------------------------------------------------------------- 완성 이미지 취약점 (Trivy 0.76.0, 릴리스 체크섬 확인)
TV=0.76.0
TGZ="trivy_${TV}_Linux-64bit.tar.gz"  # 체크섬 파일의 이름 그대로 받아야 sha256sum -c가 찾는다
curl -sSL -o "$TGZ" "https://github.com/aquasecurity/trivy/releases/download/v$TV/$TGZ"
curl -sSL -o trivy.sum "https://github.com/aquasecurity/trivy/releases/download/v$TV/trivy_${TV}_checksums.txt"
if grep "  $TGZ\$" trivy.sum | sha256sum -c - >> "$LOG" 2>&1; then
  tar xzf "$TGZ" trivy
  for n in backend frontend frontend-render; do
    ./trivy image --quiet --format json --scanners vuln --severity HIGH,CRITICAL -o "$OUT/trivy-$n.json" "moajwo-$n:check" 2>> "$LOG"
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
