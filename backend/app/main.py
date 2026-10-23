import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError
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
from app.auth import current_user
from app.db import get_session

logger = logging.getLogger(__name__)

app = FastAPI(title="Moajwo", version="0.1.0")

DB_UNAVAILABLE = "데이터베이스에 연결하지 못했습니다 — 잠시 뒤 다시 시도하세요. 계정·자료는 그대로입니다."


@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
async def database_unavailable(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """DB에 닿지 못한 요청 (외부 DB 휴면에서 깨지 못함·연결 끊김·인증 실패 등)은 503으로 알린다.

    비밀번호 오류·계정 없음·최초 설정 필요로 보이지 않게 한다. 주소·비밀번호·SQL은 응답·기록에 넣지 않는다.
    """
    logger.warning("database unavailable: %s", type(getattr(exc, "orig", exc)).__name__)
    return JSONResponse({"detail": DB_UNAVAILABLE}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        headers={"Retry-After": "10"})  # fmt: skip


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
