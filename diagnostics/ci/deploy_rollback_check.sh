#!/usr/bin/env bash
# 배포·롤백 리허설 (격리 러너 전용 — 운영 PC·운영 Docker 엔진에서 돌리지 않는다).
# 같은 Compose 프로젝트(moajwo)·서비스·데이터 볼륨으로: 이전(main) 기동·합성 자료 → 후보 반영(scripts/deploy.sh 그대로)
# → 이전 이미지 + 이전 포트로 롤백(docs/DEPLOY.md 8장 명령 그대로) → 다시 후보 반영. 단계마다 웹·프록시 health·로그인·이전 결과
# 다시 내려받기·DB 행 수·원본 해시·볼륨 목록·frontend 포트 연결·실행 사용자를 남긴다. 마지막에 승인 SHA와 트리가 다르면
# deploy.sh가 멈추는지(서비스 그대로) 본다. 비밀번호는 실행 때 만들고 출력하지 않는다. 합성 자료만 쓴다.
# 사용법: bash deploy_rollback_check.sh <출력 폴더> <이전 main SHA> <후보 SHA>   (저장소 루트에서)
set -uo pipefail
OUT=$(mkdir -p "$1" && cd "$1" && pwd); OLD=$2; NEW=$3
HERE=$(cd "$(dirname "$0")" && pwd)
RES="$OUT/results.tsv"; : > "$RES"
res() { printf '%s\t%s\t%s\n' "$1" "$2" "$3" | tee -a "$RES"; }
export PROBE_PW="Rh-$(openssl rand -hex 12)"
# deploy.sh는 Git Bash의 'pwd -W'(Windows 경로)로 백업 폴더를 마운트한다 — 리눅스 러너에서는 그대로의 경로를 쓰게 한다
pwd() { if [ "${1:-}" = "-W" ]; then builtin pwd; else builtin pwd "$@"; fi; }
export -f pwd

snapshot() {  # snapshot <단계>
  local stage=$1
  res "$stage.web" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/)" "GET / (frontend)"
  res "$stage.proxy_health" "$(curl -s http://127.0.0.1:8080/api/health)" "GET /api/health (frontend → backend)"
  res "$stage.backend_health" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health)" "GET 127.0.0.1:8000/health"
  res "$stage.api" "$(python3 "$HERE/deploy_probe.py" check http://127.0.0.1:8080 "$OUT/seed.json" "$stage")" "같은 계정 로그인·실행 기록·이전 결과 다시 내려받기"
  res "$stage.db" "$(docker compose exec -T backend python -c "import sqlite3; c = sqlite3.connect('file:/app/data/moajwo.db?mode=ro', uri=True); print({t: c.execute('select count(*) from ' + t).fetchone()[0] for t in ('users', 'source_files', 'runs', 'mapping_decisions')}, c.execute('select version_num from alembic_version').fetchone()[0])" 2>&1 | tail -1)" "행 수·리비전 (읽기 전용)"
  docker compose exec -T backend sh -c 'cd /app/data && sha256sum uploads/* outputs/* | sort -k2' > "$OUT/files-$stage.txt" 2>&1
  res "$stage.files" "$(sha256sum "$OUT/files-$stage.txt" | cut -c1-16) ($(wc -l < "$OUT/files-$stage.txt")개)" "원본·결과 파일 해시 목록"
  res "$stage.volumes" "$(docker volume ls --format '{{.Name}}' | grep -i moajwo | tr '\n' ' ')" "moajwo 볼륨 (새 빈 볼륨 없음)"
  res "$stage.frontend" "$(docker inspect moajwo-frontend-1 --format '{{.Image}} {{json .HostConfig.PortBindings}}' | cut -c8-19,72-) uid=$(docker compose exec -T frontend id -u 2>/dev/null)" "frontend 이미지·포트 연결·실행 사용자"
  res "$stage.backend_image" "$(docker inspect moajwo-backend-1 --format '{{.Image}}' | cut -c8-19)" "backend 이미지"
}

# ---------------------------------------------------------------- 1. 이전 버전 (main)
git checkout -q --detach "$OLD"
docker compose up -d --build --wait > "$OUT/old-up.log" 2>&1
res old.up $? "docker compose up -d --build --wait ($(git rev-parse --short HEAD))"
res seed "$(python3 "$HERE/deploy_probe.py" seed http://127.0.0.1:8080 "$OUT/seed.json")" "최초 소유자·합성 CSV·매핑 확정·묶기 결과"
snapshot old

# ---------------------------------------------------------------- 2. 후보 반영 (deploy.sh 그대로)
# 러너 환경 맞춤: 백업 컨테이너(backend 이미지의 uid 1000)가 호스트의 backups/(러너 사용자 소유)에 쓸 수 있게 한다.
# 운영 PC(Docker Desktop)의 바인드 마운트에는 필요 없다 — 1회차는 이 권한 때문에 백업에서 멈추고 기존 서비스를 재개했다.
mkdir -p backups && chmod 777 backups
git checkout -q --detach "$NEW"
bash scripts/deploy.sh "$NEW" 0019_analysis_runs > "$OUT/deploy-1.out" 2>&1
res deploy1.exit $? "scripts/deploy.sh $NEW 0019_analysis_runs"
grep -E "포트 연결|배포 단위|전환 완료|반영 전 포트" "$OUT/deploy-1.out" | sed 's/^/  /' >> "$RES"
snapshot new

# ---------------------------------------------------------------- 3. 롤백 (이전 이미지 + 이전 포트, DB 복원·downgrade 없음 — 8장)
RBF=$(ls backups/rollback-*.compose.yml | head -1)
TS=$(basename "$RBF" .compose.yml); TS=${TS#rollback-}
TAG="rollback-$TS"
cp "$RBF" "$OUT/"
docker compose stop -t 30 frontend backend > "$OUT/rollback.log" 2>&1
docker tag "moajwo-backend:$TAG" moajwo-backend:latest && docker tag "moajwo-frontend:$TAG" moajwo-frontend:latest
RB="-f docker-compose.yml -f $RBF"
docker compose $RB config > "$OUT/rollback-config.yml" 2>&1
res rollback.config "$(python3 - "$OUT/rollback-config.yml" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
out = []
for svc in ("backend", "frontend"):
    m = re.search(rf"^  {svc}:\n(.*?)(?=^  \S|\Z)", text, re.S | re.M)
    body = m.group(1) if m else ""
    targets = re.findall(r"target: (\d+)", body)
    published = re.findall(r'published: "?(\d+)', body)
    out.append(f"{svc}: 대상 {targets} 공개 {published}")
print(" · ".join(out))
PY
)" "docker compose \$RB config — 서비스마다 하나, frontend는 80"
docker compose $RB up -d --no-build --wait >> "$OUT/rollback.log" 2>&1
res rollback.up $? "docker compose \$RB up -d --no-build --wait"
snapshot rollback

# ---------------------------------------------------------------- 4. 다시 후보
bash scripts/deploy.sh "$NEW" 0019_analysis_runs > "$OUT/deploy-2.out" 2>&1
res deploy2.exit $? "scripts/deploy.sh $NEW 0019_analysis_runs (롤백 뒤 다시)"
grep -E "포트 연결|배포 단위|전환 완료" "$OUT/deploy-2.out" | sed 's/^/  /' >> "$RES"
snapshot new_again

# ---------------------------------------------------------------- 5. 승인 SHA와 트리가 다르면 멈춤 (서비스 그대로)
BEFORE=$(docker inspect moajwo-frontend-1 moajwo-backend-1 --format '{{.Id}}' | tr '\n' ' ')
bash scripts/deploy.sh "$OLD" 0019_analysis_runs > "$OUT/deploy-mismatch.out" 2>&1
CODE=$?
AFTER=$(docker inspect moajwo-frontend-1 moajwo-backend-1 --format '{{.Id}}' | tr '\n' ' ')
res mismatch "exit=$CODE 컨테이너 그대로=$([ "$BEFORE" = "$AFTER" ] && echo yes || echo no)" "$(grep -o '내용이 승인한 커밋.*' "$OUT/deploy-mismatch.out" | head -1)"
cp -r backups "$OUT/backups-meta" 2>/dev/null; rm -f "$OUT"/backups-meta/*.tgz   # 압축 백업(합성 자료)은 올리지 않음
docker compose logs --no-color > "$OUT/compose.log" 2>&1
column -t -s $'\t' "$RES" > "$OUT/results.txt"
