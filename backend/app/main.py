import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import (
    admin_api,
    admin_console,
    auth_api,
    automation_api,
    dashboard,
    files,
    glossary_api,
    integration_api,
    review_api,
    validation_api,
)
from app.auth import current_user
from app.db import get_session

logger = logging.getLogger(__name__)

app = FastAPI(title="Moajwo", version="0.1.0")
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
    integration_api.router,
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
