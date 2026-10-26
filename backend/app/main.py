import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DataError, DBAPIError, SQLAlchemyError
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
    keyjoin_api,
    mapping_review,
    recovery,
    review_api,
    validation_api,
)
from app.auth import current_user
from app.config import API_DOCS
from app.db import db_failure, get_session
from app.output_slot import OutputBusy
from app.runlease import RunUncertain

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


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """기동할 때: 담당 프로세스가 끝난 결과 실행을 근거가 있을 때만 정리한다 (app/recovery.py — 살아 있는 실행·완성된
    결과 파일은 건드리지 않음). 어떤 오류도 기동을 막지 않는다 — 정리하지 못했으면 '미완료'로 기록하고 다시 볼
    예약을 둔다."""
    recovery.startup()
    yield


app = FastAPI(
    title="Moajwo",
    version="0.1.0",
    # 운영에서는 문서·스키마를 열지 않는다 (config.API_DOCS)
    docs_url="/docs" if API_DOCS else None,
    redoc_url="/redoc" if API_DOCS else None,
    openapi_url="/openapi.json" if API_DOCS else None,
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """요청 형식 오류(422): 필드 위치·오류 종류·안내는 그대로 두고, 보낸 값(input)은 되돌려 보내지 않는다
    (기본 응답은 본문 전체를 담아 비밀번호·이메일·전화번호가 응답·중간 기록에 남았다)."""
    errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": jsonable_encoder(errors)})


DB_UNREACHABLE = "데이터베이스에 연결하지 못했습니다 — 잠시 뒤 다시 시도하세요."
DB_BUSY = "데이터베이스가 다른 작업으로 바빠 처리하지 못했습니다 — 잠시 뒤 다시 시도하세요."
# 쓰기 요청은 커밋됐는지 모를 수 있다 (커밋 응답 전에 연결이 끊기는 경우 등) — '그대로입니다'라고 단정하지 않는다
WRITE_UNCERTAIN = (
    " 이 요청이 반영됐는지는 확인하지 못했습니다 — 화면을 새로 고쳐 반영 여부를 확인한 뒤 필요하면 다시 하세요."
)
READ_METHODS = {"GET", "HEAD", "OPTIONS"}
NUL_REFUSED = "저장할 글자에 NUL(0x00) 문자가 있어 처리하지 못했습니다 — 그 문자를 지운 뒤 다시 하세요. [VALUE_HAS_NUL]"


def _nul_refused(exc: BaseException) -> bool:
    """PostgreSQL 드라이버(psycopg)가 NUL이 든 글자를 보내지 않고 낸 오류 — SQLSTATE가 없는 DataError."""
    orig = getattr(exc, "orig", None)
    return isinstance(exc, DataError) and getattr(orig, "sqlstate", None) is None and "NUL (0x00)" in str(orig)


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
    - 단, PostgreSQL이 NUL(0x00)이 든 글자를 받지 않은 경우는 입력 오류(422)로 알린다 — 앱이 쓰기 전에 거절하는
      경로(파일에서 온 이름·값, app/name_limits.py)에 없는 입력의 안전망이다. SQLite는 그 글자를 저장하므로 이 경로들은
      두 DB 차이가 남는다.
    - 주소·비밀번호·SQL·매개변수는 응답·기록에 넣지 않는다 (오류 종류·SQLSTATE만).
    """
    kind = db_failure(exc)
    if kind is None and _nul_refused(exc):
        logger.warning("database refused NUL: %s", _db_error_name(exc))
        detail = NUL_REFUSED + (WRITE_UNCERTAIN if request.method not in READ_METHODS else "")
        return JSONResponse({"detail": detail}, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
    if kind is None:
        raise exc
    logger.warning("database %s: %s", kind, _db_error_name(exc))
    detail = DB_UNREACHABLE if kind == "unreachable" else DB_BUSY
    if request.method not in READ_METHODS:
        detail += WRITE_UNCERTAIN
    return JSONResponse({"detail": detail}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        headers={"Retry-After": "10"})  # fmt: skip


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
    keyjoin_api.router,
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
