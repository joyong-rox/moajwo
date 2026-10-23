#!/usr/bin/env bash
# 로컬 격리 backend (Docker 없이): 임시 데이터 폴더, 모델 주소 차단(127.0.0.1:9), 127.0.0.1에만 열기.
# 사용법: iso_backend.sh <코드의 backend 폴더> <데이터 폴더> <포트>
set -euo pipefail
SRC="$1"; DATA="$2"; PORT="$3"
PY="${PY:?backend 의존성을 설치한 파이썬 경로(PY)}"
mkdir -p "$DATA"
export MOAJWO_DATA_DIR="$(cygpath -w "$DATA")"
export OLLAMA_BASE_URL="http://127.0.0.1:9"
export PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1
cd "$SRC"
echo "[iso] code=$(git rev-parse HEAD) data=$MOAJWO_DATA_DIR port=$PORT"
"$PY" -m alembic upgrade head
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
