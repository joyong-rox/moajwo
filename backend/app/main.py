import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import files, glossary_api, validation_api
from app.db import get_session

logger = logging.getLogger(__name__)

app = FastAPI(title="Moajwo", version="0.1.0")
app.include_router(files.router)
app.include_router(files.mapping_router)
app.include_router(validation_api.router)
app.include_router(glossary_api.router)


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
