import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
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
    review_api,
    validation_api,
)
from app.auth import current_user
from app.db import get_session

logger = logging.getLogger(__name__)


class QueryValueMask(logging.Filter):
    """접근 기록(uvicorn.access)의 주소에서 쿼리 값을 뺀다 — 이름 검색어(q) 등이 기록에 남지 않게 (키는 남긴다)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str) and "?" in args[2]:
            path, _, query = args[2].partition("?")
            keys = [part.split("=", 1)[0] for part in query.split("&") if part]
            record.args = (args[0], args[1], f"{path}?" + "&".join(f"{k}=…" for k in keys), *args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(QueryValueMask())

app = FastAPI(title="Moajwo", version="0.1.0")


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """요청 형식 오류(422): 필드 위치·오류 종류·안내는 그대로 두고, 보낸 값(input)은 되돌려 보내지 않는다
    (기본 응답은 본문 전체를 담아 비밀번호·이메일·전화번호가 응답·중간 기록에 남았다)."""
    errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": jsonable_encoder(errors)})


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
