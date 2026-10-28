#!/usr/bin/env bash
# 로컬 격리 frontend: 빌드본을 vite preview로 127.0.0.1:<포트>에 열고 /api를 격리 backend로 넘긴다 (nginx와 같은 경로 구성).
# 사용법: iso_frontend.sh <frontend 폴더(vite.config.ts 위치)> <빌드 폴더> <포트> <backend 포트>
set -euo pipefail
FE="$1"; DIST="$2"; PORT="$3"; BPORT="$4"
export VITE_API_PROXY_TARGET="http://127.0.0.1:$BPORT"
cd "$FE"
echo "[iso-fe] dist=$DIST port=$PORT -> backend $BPORT"
exec npx.cmd vite preview --outDir "$(cygpath -w "$DIST")" --port "$PORT" --strictPort --host 127.0.0.1
