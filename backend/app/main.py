import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import (
    admin_api,
    admin_console,
    analysis_api,
    auth_api,
    automation_api,
    bundle_api,
    dashboard,
    files,
    glossary_api,
    integration_api,
    mapping_review,
    review_api,
    validation_api,
)
from app import db as app_db
from app.auth import current_user
from app.db import get_session
from app.recovery import recover_interrupted

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """기동할 때: 결과를 만드는 도중 멈춘 실행·쓰다 만 임시 결과를 정리한다 (app/recovery.py).
    DB에 닿지 못해도 기동은 막지 않는다 — 요청은 DB 상태에 따라 따로 답한다."""
    try:
        with app_db.SessionLocal() as db:
            recover_interrupted(db)
    except SQLAlchemyError as exc:
        logger.warning("startup recovery skipped: %s", type(exc).__name__)
    yield


app = FastAPI(title="Moajwo", version="0.1.0", lifespan=lifespan)
# 로그인 없이 열리는 것은 /health 와 로그인 API(/auth/status·setup·login·logout)뿐이다
app.include_router(auth_api.router)
LOGIN_REQUIRED = [Depends(current_user)]
for router in (
    auth_api.users_router,
    files.router,
    files.mapping_router,
    validation_api.router,
    glossary_api.router,
    review_api.router,
    mapping_review.router,
    analysis_api.router,
    integration_api.router,
    bundle_api.router,
    automation_api.router,
    admin_api.router,
    admin_console.router,
    dashboard.router,
):
    app.include_router(router, dependencies=LOGIN_REQUIRED)


@app.get("/health")
def health(response: Response, session: Annotated[Session, Depends(get_session)]) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
        database = "ok"
    except SQLAlchemyError:
        logger.exception("health check: database unavailable")
        database = "error"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok", "database": database}
