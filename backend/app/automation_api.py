"""자동화 API: 완료된 실행을 자동화로 저장, 목록·상세·삭제, 새 파일로 다시 실행.

일반 사용자는 자기 자동화만, 관리자·소유자는 전체를 본다. 자동화를 저장한 사람(owner_id)이 그 자동화의 소유자다.
"""

import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, CurrentUserDep, can_view, ensure_own, ensure_view
from app.automation import AutomationError, delete_automation, run_automation, save_from_run
from app.db import get_session
from app.integration_api import RunOut, runs_out
from app.models import Automation, IntegrationRun, SourceFile, User
from app.review import session_items, summarize

router = APIRouter(tags=["automation"])


class AutomationOut(BaseModel):
    id: int
    name: str
    owner_id: int | None
    owner_name: str | None
    manual_baseline_minutes: float | None  # 소유자가 정한 이 자동화의 수동 기준 소요 시간 (없으면 기본값)
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
    owner = db.get(User, a.owner_id) if a.owner_id is not None else None
    return AutomationOut(
        id=a.id,
        name=a.name,
        owner_id=a.owner_id,
        owner_name=owner.display_name if owner is not None else None,
        manual_baseline_minutes=a.manual_baseline_minutes,
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


def _get(db: Session, automation_id: int, user: CurrentUser) -> Automation:
    automation = db.get(Automation, automation_id)
    if automation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "자동화를 찾을 수 없습니다.")
    ensure_view(user, automation.owner_id, "자동화")
    return automation


class SaveIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.post("/runs/{run_id}/automation", response_model=AutomationOut, status_code=status.HTTP_201_CREATED)
def save_automation(
    run_id: int, body: SaveIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> AutomationOut:
    """완료된 실행의 승인된 매핑 규칙·검증 조건·출력 형식을 자동화로 저장한다 (입력 파일은 저장하지 않음)."""
    run = db.get(IntegrationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "실행 기록을 찾을 수 없습니다.")
    ensure_own(user, run.user_id, "실행 기록")
    try:
        automation = save_from_run(db, run, body.name, owner_id=user.id)
    except AutomationError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return automation_out(db, automation)


@router.get("/automations", response_model=list[AutomationOut])
def list_automations(db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> list[AutomationOut]:
    stmt = select(Automation).order_by(Automation.id.desc())
    if not user.is_manager:
        stmt = stmt.where(Automation.owner_id == user.id)
    return [automation_out(db, a) for a in db.scalars(stmt)]


@router.get("/automations/{automation_id}", response_model=AutomationOut)
def get_automation(
    automation_id: int, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> AutomationOut:
    return automation_out(db, _get(db, automation_id, user))


@router.delete("/automations/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_automation(
    automation_id: int, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> Response:
    """자동화만 지운다 (실행 기록·결과 파일은 남김). 저장한 사람 또는 관리자·소유자."""
    delete_automation(db, _get(db, automation_id, user))
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
def run(
    automation_id: int, body: RunIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> AutomationRunOut:
    """새 파일에 저장된 규칙을 바로 적용한다. 확인할 항목이 없으면 결과 엑셀까지 만들고,
    새 열 등 확인할 항목이 있으면 '검토' 상태로 남겨 검토 패널에서 이어서 처리한다.
    실행 기록은 실행한 사용자의 것으로 남는다."""
    automation = _get(db, automation_id, user)
    hidden = [
        fid
        for fid in [*body.file_ids, *body.baseline_file_ids]
        if (record := db.get(SourceFile, fid)) is not None and not can_view(user, record.uploaded_by)
    ]
    if hidden:  # 남의 파일은 없는 파일과 같게 알린다
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"파일을 찾을 수 없습니다: {hidden}")
    try:
        result, session = run_automation(
            db, automation, body.file_ids, body.expected_period, body.baseline_file_ids, user_id=user.id
        )
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
