"""자동화 API: 완료된 실행을 자동화로 저장, 목록·상세·삭제, 새 파일로 다시 실행."""

import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.automation import AutomationError, delete_automation, run_automation, save_from_run
from app.db import get_session
from app.integration_api import RunOut, runs_out
from app.models import Automation, IntegrationRun
from app.review import session_items, summarize

router = APIRouter(tags=["automation"])


class AutomationOut(BaseModel):
    id: int
    name: str
    source_run_id: int | None
    column_rules: list[dict[str, Any]]
    entity_rules: dict[str, str | None]
    validation: dict[str, Any]
    output_format: dict[str, Any]
    created_at: datetime
    updated_at: datetime | None
    run_count: int
    last_run: RunOut | None


def automation_out(db: Session, a: Automation) -> AutomationOut:
    count = db.scalar(select(func.count(IntegrationRun.id)).where(IntegrationRun.automation_id == a.id)) or 0
    last = db.scalar(
        select(IntegrationRun).where(IntegrationRun.automation_id == a.id).order_by(IntegrationRun.id.desc()).limit(1)
    )
    return AutomationOut(
        id=a.id,
        name=a.name,
        source_run_id=a.source_run_id,
        column_rules=json.loads(a.column_rules),
        entity_rules=json.loads(a.entity_rules),
        validation=json.loads(a.validation),
        output_format=json.loads(a.output_format),
        created_at=a.created_at,
        updated_at=a.updated_at,
        run_count=count,
        last_run=runs_out(db, [last])[0] if last is not None else None,
    )


def _get(db: Session, automation_id: int) -> Automation:
    automation = db.get(Automation, automation_id)
    if automation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "자동화를 찾을 수 없습니다.")
    return automation


class SaveIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.post("/runs/{run_id}/automation", response_model=AutomationOut, status_code=status.HTTP_201_CREATED)
def save_automation(run_id: int, body: SaveIn, db: Annotated[Session, Depends(get_session)]) -> AutomationOut:
    """완료된 실행의 승인된 매핑 규칙·검증 조건·출력 형식을 자동화로 저장한다 (입력 파일은 저장하지 않음)."""
    run = db.get(IntegrationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "실행 기록을 찾을 수 없습니다.")
    try:
        automation = save_from_run(db, run, body.name)
    except AutomationError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return automation_out(db, automation)


@router.get("/automations", response_model=list[AutomationOut])
def list_automations(db: Annotated[Session, Depends(get_session)]) -> list[AutomationOut]:
    return [automation_out(db, a) for a in db.scalars(select(Automation).order_by(Automation.id.desc()))]


@router.get("/automations/{automation_id}", response_model=AutomationOut)
def get_automation(automation_id: int, db: Annotated[Session, Depends(get_session)]) -> AutomationOut:
    return automation_out(db, _get(db, automation_id))


@router.delete("/automations/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_automation(automation_id: int, db: Annotated[Session, Depends(get_session)]) -> Response:
    """자동화만 지운다 (실행 기록·결과 파일은 남김)."""
    delete_automation(db, _get(db, automation_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class RunIn(BaseModel):
    file_ids: list[int] = Field(min_length=1)
    baseline_file_ids: list[int] = []
    expected_period: str | None = None


class AutomationRunOut(BaseModel):
    run: RunOut
    review_id: int
    counts: dict[str, int]  # 검토 항목 수: block / pending / pending_open / auto / warning
    summary: str


@router.post("/automations/{automation_id}/run", response_model=AutomationRunOut, status_code=status.HTTP_201_CREATED)
def run(automation_id: int, body: RunIn, db: Annotated[Session, Depends(get_session)]) -> AutomationRunOut:
    """새 파일에 저장된 규칙을 바로 적용한다. 확인할 항목이 없으면 결과 엑셀까지 만들고,
    새 열 등 확인할 항목이 있으면 '검토' 상태로 남겨 검토 패널에서 이어서 처리한다."""
    automation = _get(db, automation_id)
    try:
        result, session = run_automation(db, automation, body.file_ids, body.expected_period, body.baseline_file_ids)
    except AutomationError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    s = summarize(session, session_items(db, session))
    return AutomationRunOut(
        run=runs_out(db, [result])[0],
        review_id=session.id,
        counts={
            "block": s.blocks,
            "pending": s.pending,
            "pending_open": s.pending_open,
            "auto": s.auto,
            "warning": s.warnings,
        },
        summary=s.summary,
    )
