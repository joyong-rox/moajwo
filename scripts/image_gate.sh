#!/usr/bin/env bash
# 반영 전 이미지 검증 (docs/DEPLOY.md 11장). 사용법: bash scripts/image_gate.sh <backend 이미지 ID> <frontend 이미지 ID> <기록 폴더>
# deploy.sh 3단계가 후보 이미지(ID)로 부른다. 혼자 돌려도 운영 서비스·데이터 볼륨·latest 태그를 바꾸지 않는다.
#   1) 완성 이미지 목록: 후보 이미지 안의 OS·OS 패키지·Python·의존성·CA 묶음·psycopg 내장 라이브러리를 읽어(scripts/image_inventory.py,
#      네트워크·볼륨 없는 일회용 컨테이너) 검증한 목록(deploy/expected-images/<서비스>.json — CI에서 검증한 완성 이미지에서 만든 것)과
#      대조한다. 기반 이미지를 digest로 고정해도 빌드 중 apt·apk·pip로 받는 것은 빌드 시점에 따라 달라질 수 있어, 다르면 멈춘다.
#   2) 격리 기동 점검(scripts/image_smoke.py): 기동·DB 연결·CA·TLS·핵심 취합·내려받기 — 1이 같을 때만.
# 하나라도 실패하면 종료 코드 1. 기록: <기록 폴더>/{backend,frontend}.inventory.json · *.compare.txt · smoke.json
set -uo pipefail
B="${1:-}" F="${2:-}" OUT="${3:-}"
[ -n "$B" ] && [ -n "$F" ] && [ -n "$OUT" ] || { echo "사용법: bash scripts/image_gate.sh <backend 이미지> <frontend 이미지> <기록 폴더>"; exit 2; }
EXP="${DEPLOY_EXPECTED_DIR:-deploy/expected-images}"
PY=""
for c in "${PYTHON:-}" python python3; do   # Windows의 python3는 스토어 안내용 가짜일 수 있어 판으로 확인한다
  [ -n "$c" ] && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null && { PY=$c; break; }
done
[ -n "$PY" ] || { echo "[검증 실패] Python 3.10 이상을 찾을 수 없음 (PYTHON 환경 변수로 지정 가능)"; exit 1; }
mkdir -p "$OUT" || { echo "[검증 실패] 기록 폴더를 만들 수 없음: $OUT"; exit 1; }
echo "[검증] 후보 backend $B · frontend $F · 검증 목록 $EXP · 기록 $OUT"
rc=0
for k in backend frontend; do
  img=$B
  [ "$k" = frontend ] && img=$F
  if [ ! -s "$EXP/$k.json" ]; then
    echo "[검증 실패] 검증한 목록이 없음: $EXP/$k.json"
    rc=1
    continue
  fi
  if ! "$PY" -X utf8 scripts/image_inventory.py collect "$k" "$img" "$OUT/$k.inventory.json"; then
    echo "[검증 실패] $k 완성 이미지 목록을 읽을 수 없음"
    rc=1
    continue
  fi
  "$PY" -X utf8 scripts/image_inventory.py compare "$EXP/$k.json" "$OUT/$k.inventory.json" > "$OUT/$k.compare.txt" 2>&1
  r=$?
  cat "$OUT/$k.compare.txt"
  [ "$r" -eq 0 ] || { echo "[검증 실패] $k: 완성 이미지가 검증한 이미지와 실행 환경이 다름 — CI 검증을 다시 하고 검증 목록을 갱신해야 함"; rc=1; }
done
[ "$rc" -eq 0 ] || { echo "[검증 실패] 완성 이미지 목록 대조 — 격리 기동 점검은 하지 않음"; exit 1; }
"$PY" -X utf8 scripts/image_smoke.py "$B" "$F" "$OUT/smoke.json" || { echo "[검증 실패] 격리 기동 점검 — $OUT/smoke.json"; exit 1; }
echo "[검증 통과] 완성 이미지 목록 = 검증한 목록 (backend·frontend) · 격리 기동 점검 통과 — 기록 $OUT"
