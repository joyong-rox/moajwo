"""통합 실행 API: 결과 엑셀 생성, 실행 내역·변경 내역 조회, 결과 내려받기."""

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.db import get_session
from app.integration import IntegrationError, integrate
from app.models import IntegrationRun, ReviewSession, RunChange

router = APIRouter(tags=["integration"])

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class RunOut(BaseModel):
    id: int
    review_session_id: int | None
    status: str
    file_name: str | None
    row_count: int
    change_count: int
    warning_count: int
    summary: dict[str, Any]
    error: str | None
    created_at: datetime


def run_out(run: IntegrationRun) -> RunOut:
    return RunOut(
        id=run.id,
        review_session_id=run.review_session_id,
        status=run.status,
        file_name=Path(run.output_path).name if run.output_path else None,
        row_count=run.row_count,
        change_count=run.change_count,
        warning_count=run.warning_count,
        summary=json.loads(run.summary or "{}"),
        error=run.error,
        created_at=run.created_at,
    )


@router.post("/reviews/{review_id}/integrate", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def integrate_review(review_id: int, db: Annotated[Session, Depends(get_session)]) -> RunOut:
    """검토가 끝난 세션으로 새 결과 엑셀을 만든다. 실행할 때마다 새 파일을 만든다 (원본은 덮어쓰지 않음)."""
    session = db.get(ReviewSession, review_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "검토 세션을 찾을 수 없습니다.")
    try:
        run = integrate(db, session)
    except IntegrationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return run_out(run)


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    db: Annotated[Session, Depends(get_session)],
    review_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunOut]:
    stmt = select(IntegrationRun).order_by(IntegrationRun.id.desc()).limit(limit)
    if review_id is not None:
        stmt = stmt.where(IntegrationRun.review_session_id == review_id)
    return [run_out(r) for r in db.scalars(stmt)]


def _get(db: Session, run_id: int) -> IntegrationRun:
    run = db.get(IntegrationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "통합 실행을 찾을 수 없습니다.")
    return run


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Annotated[Session, Depends(get_session)]) -> RunOut:
    return run_out(_get(db, run_id))


class ChangeOut(BaseModel):
    id: int
    source_file_id: int | None
    file_name: str
    sheet: str
    source_row: int | None
    output_row: int
    column: str
    source_column: str
    kind: str
    before: str | None
    after: str | None
    reason: str
    confidence: float


class ChangePage(BaseModel):
    total: int
    items: list[ChangeOut]


@router.get("/runs/{run_id}/changes", response_model=ChangePage)
def list_changes(
    run_id: int,
    db: Annotated[Session, Depends(get_session)],
    kind: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> ChangePage:
    _get(db, run_id)
    where = [RunChange.run_id == run_id] + ([RunChange.kind == kind] if kind else [])
    total = db.scalar(select(func.count(RunChange.id)).where(*where)) or 0
    rows = db.scalars(select(RunChange).where(*where).order_by(RunChange.id).offset(offset).limit(limit))
    return ChangePage(
        total=total,
        items=[ChangeOut.model_validate(r, from_attributes=True) for r in rows],
    )


@router.get("/runs/{run_id}/download")
def download_run(run_id: int, db: Annotated[Session, Depends(get_session)]) -> FileResponse:
    run = _get(db, run_id)
    if run.status != "done" or not run.output_path:
        raise HTTPException(status.HTTP_409_CONFLICT, "결과 파일이 없습니다.")
    path = (DATA_DIR / run.output_path).resolve()
    if not path.is_relative_to((DATA_DIR / "outputs").resolve()) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "결과 파일을 찾을 수 없습니다.")
    return FileResponse(path, media_type=XLSX_MEDIA, filename=path.name)
