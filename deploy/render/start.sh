#!/bin/sh
set -eu
: "${MOAJWO_PREVIEW:?Set MOAJWO_PREVIEW=1 for the disposable review service}"
: "${MOAJWO_PREVIEW_OWNER_PASSWORD:?Set the private review owner password in Render}"
test "$MOAJWO_PREVIEW" = "1"
alembic upgrade head
python bootstrap.py
exec uvicorn preview_app:app --host 0.0.0.0 --port "${PORT:-10000}" --workers 1
