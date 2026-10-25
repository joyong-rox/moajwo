import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
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
    recovery,
    review_api,
    validation_api,
)
from app.auth import current_user
from app.db import get_session
from app.output_slot import OutputBusy
from app.runlease import RunUncertain

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """기동할 때: 담당 프로세스가 끝난 결과 실행을 근거가 있을 때만 정리한다 (app/recovery.py — 살아 있는 실행·완성된
    결과 파일은 건드리지 않음). 어떤 오류도 기동을 막지 않는다 — 정리하지 못했으면 '미완료'로 기록하고 다시 볼
    예약을 둔다."""
    recovery.startup()
    yield


app = FastAPI(title="Moajwo", version="0.1.0", lifespan=lifespan)


@app.exception_handler(RunUncertain)
async def run_uncertain(_: Request, exc: RunUncertain) -> JSONResponse:
    """결과 파일은 저장했지만 완료 기록을 확인하지 못함(또는 실패 기록을 남기지 못함) — 실패가 아니다. 다시 실행을
    권하지 않고 실행 기록에서 확인하게 한다. 서버는 곧 결과 파일과 기록을 맞춰 보고 확정한다 (app/recovery.py)."""
    recovery.schedule(30)
    body = {"detail": {"code": "run_uncertain", "run_id": exc.run_id, "message": str(exc)}}
    return JSONResponse(body, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


@app.exception_handler(OutputBusy)
async def output_busy(_: Request, exc: OutputBusy) -> JSONResponse:
    """결과 만들기 차례가 없음 — 시작하지 않았다 (기록·파일 없음). 다시 누르는 것은 사용자가 정한다."""
    body = {"detail": {"code": "output_busy", "message": str(exc)}}
    return JSONResponse(body, status_code=status.HTTP_503_SERVICE_UNAVAILABLE, headers={"Retry-After": "10"})


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
