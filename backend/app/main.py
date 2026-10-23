import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
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
from app.db import db_failure, get_session

logger = logging.getLogger(__name__)

app = FastAPI(title="Moajwo", version="0.1.0")

DB_UNREACHABLE = "데이터베이스에 연결하지 못했습니다 — 잠시 뒤 다시 시도하세요."
DB_BUSY = "데이터베이스가 다른 작업으로 바빠 처리하지 못했습니다 — 잠시 뒤 다시 시도하세요."
# 쓰기 요청은 커밋됐는지 모를 수 있다 (커밋 응답 전에 연결이 끊기는 경우 등) — '그대로입니다'라고 단정하지 않는다
WRITE_UNCERTAIN = (
    " 이 요청이 반영됐는지는 확인하지 못했습니다 — 화면을 새로 고쳐 반영 여부를 확인한 뒤 필요하면 다시 하세요."
)
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _db_error_name(exc: BaseException) -> str:
    orig = getattr(exc, "orig", None)
    state = getattr(orig, "sqlstate", None)
    return type(orig or exc).__name__ + (f" {state}" if state else "")


@app.exception_handler(DBAPIError)
@app.exception_handler(SQLAlchemyTimeoutError)
async def database_failure(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """DB에 닿지 못했거나(외부 DB 휴면에서 깨지 못함·연결 끊김·인증 실패) 바빠서(연결 풀 대기 초과·잠금 대기
    초과) 처리하지 못한 요청은 503으로 알린다 — 비밀번호 오류·계정 없음·최초 설정 필요로 보이지 않게.

    - 쓰기 요청은 반영 여부를 확인하라고 안내한다. 서버도 화면도 자동으로 다시 보내지 않는다.
    - 데이터·코드·스키마 오류는 연결 장애로 감추지 않는다 — 그대로 500.
    - 주소·비밀번호·SQL·매개변수는 응답·기록에 넣지 않는다 (오류 종류·SQLSTATE만).
    """
    kind = db_failure(exc)
    if kind is None:
        raise exc
    logger.warning("database %s: %s", kind, _db_error_name(exc))
    detail = DB_UNREACHABLE if kind == "unreachable" else DB_BUSY
    if request.method not in READ_METHODS:
        detail += WRITE_UNCERTAIN
    return JSONResponse({"detail": detail}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
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
    except SQLAlchemyError as exc:
        logger.warning("health check: database %s: %s", db_failure(exc) or "error", _db_error_name(exc))
        database = "error"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok", "database": database}
