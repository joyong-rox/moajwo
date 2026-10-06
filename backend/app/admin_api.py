"""관리 API: 표준 사전 승격 심사(관리자·소유자), 전사 표준 사전, 설정(소유자).

결과 데이터(실행 기록·결과 파일·변경 기록)를 바꾸는 API는 여기에도 없다.
"""

import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import ManagerDep, OwnerDep
from app.db import get_session
from app.mapping import get_engine
from app.models import AppSetting, Automation, MappingApproval, PromotionCandidate, StandardTerm, User
from app.promotion import PROMOTION_THRESHOLD, PromotionError, approve, reject
from app.timefmt import utc_to_kst

router = APIRouter(tags=["admin"])

BASELINE_KEY = "manual_baseline_minutes"


# ---------------------------------------------------------------- 승격 심사


class CandidateOut(BaseModel):
    id: int
    column_name: str
    normalized: str
    field_key: str
    field_label: str | None
    approver_count: int
    approvers: list[str]
    status: str
    created_at: datetime
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    review_note: str | None


def _names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return {i: n for i, n in db.execute(select(User.id, User.display_name).where(User.id.in_(ids)))}


def candidate_out(db: Session, c: PromotionCandidate) -> CandidateOut:
    labels = {f.key: f.label for f in get_engine().fields}
    approver_ids = list(
        db.scalars(
            select(MappingApproval.user_id).where(
                MappingApproval.normalized == c.normalized, MappingApproval.field_key == c.field_key
            )
        )
    )
    names = _names(db, set(approver_ids) | ({c.reviewed_by} if c.reviewed_by else set()))
    return CandidateOut(
        id=c.id,
        column_name=c.column_name,
        normalized=c.normalized,
        field_key=c.field_key,
        field_label=labels.get(c.field_key),
        approver_count=c.approver_count,
        approvers=[names.get(i, f"#{i}") for i in approver_ids],
        status=c.status,
        created_at=utc_to_kst(c.created_at),
        reviewed_by_name=names.get(c.reviewed_by) if c.reviewed_by else None,
        reviewed_at=utc_to_kst(c.reviewed_at),
        review_note=c.review_note,
    )


class PromotionList(BaseModel):
    threshold: int
    items: list[CandidateOut]


@router.get("/admin/promotions", response_model=PromotionList)
def list_promotions(
    _: ManagerDep,
    db: Annotated[Session, Depends(get_session)],
    status_: Annotated[Literal["pending", "approved", "rejected", "all"], Query(alias="status")] = "pending",
) -> PromotionList:
    stmt = select(PromotionCandidate).order_by(PromotionCandidate.id.desc())
    if status_ != "all":
        stmt = stmt.where(PromotionCandidate.status == status_)
    return PromotionList(threshold=PROMOTION_THRESHOLD, items=[candidate_out(db, c) for c in db.scalars(stmt)])


class ReviewNoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _candidate(db: Session, candidate_id: int) -> PromotionCandidate:
    c = db.get(PromotionCandidate, candidate_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "승격 후보를 찾을 수 없습니다.")
    return c


@router.post("/admin/promotions/{candidate_id}/approve", response_model=CandidateOut)
def approve_promotion(
    candidate_id: int, body: ReviewNoteIn, actor: ManagerDep, db: Annotated[Session, Depends(get_session)]
) -> CandidateOut:
    """승인하면 전사 표준 사전(standard_terms)에 반영되어 모든 사용자의 매핑 1단계에 적용된다."""
    c = _candidate(db, candidate_id)
    try:
        approve(db, get_engine(), c, actor.id, body.note)
    except PromotionError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return candidate_out(db, c)


@router.post("/admin/promotions/{candidate_id}/reject", response_model=CandidateOut)
def reject_promotion(
    candidate_id: int, body: ReviewNoteIn, actor: ManagerDep, db: Annotated[Session, Depends(get_session)]
) -> CandidateOut:
    c = _candidate(db, candidate_id)
    try:
        reject(db, c, actor.id, body.note)
    except PromotionError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return candidate_out(db, c)


# ---------------------------------------------------------------- 전사 표준 사전


class StandardTermOut(BaseModel):
    id: int
    term: str
    normalized: str
    field_key: str
    field_label: str | None
    approved_by_name: str | None
    created_at: datetime


@router.get("/admin/standard-terms", response_model=list[StandardTermOut])
def list_standard_terms(_: ManagerDep, db: Annotated[Session, Depends(get_session)]) -> list[StandardTermOut]:
    labels = {f.key: f.label for f in get_engine().fields}
    terms = list(db.scalars(select(StandardTerm).order_by(StandardTerm.id.desc())))
    names = _names(db, {t.approved_by for t in terms if t.approved_by})
    return [
        StandardTermOut(
            id=t.id,
            term=t.term,
            normalized=t.normalized,
            field_key=t.field_key,
            field_label=labels.get(t.field_key),
            approved_by_name=names.get(t.approved_by) if t.approved_by else None,
            created_at=utc_to_kst(t.created_at),
        )
        for t in terms
    ]


@router.delete("/admin/standard-terms/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_standard_term(term_id: int, _: ManagerDep, db: Annotated[Session, Depends(get_session)]) -> Response:
    """전사 표준 사전에서 뺀다 (이미 만든 결과 파일·기록은 그대로)."""
    term = db.get(StandardTerm, term_id)
    if term is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "표준 사전 항목을 찾을 수 없습니다.")
    db.delete(term)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- 설정 (소유자)


def get_setting(db: Session, key: str) -> object | None:
    row = db.get(AppSetting, key)
    return json.loads(row.value) if row is not None else None


def baseline_minutes(db: Session) -> float | None:
    value = get_setting(db, BASELINE_KEY)
    return float(value) if isinstance(value, int | float) else None


class AutomationBaselineOut(BaseModel):
    id: int
    name: str
    owner_name: str | None
    manual_baseline_minutes: float | None


class SettingsOut(BaseModel):
    manual_baseline_minutes: float | None  # 실행 1건을 사람이 직접 할 때 걸리는 시간 (분)
    automations: list[AutomationBaselineOut]  # 자동화별로 다르게 정한 기준 (없으면 기본값)
    updated_at: datetime | None
    updated_by_name: str | None


def settings_out(db: Session) -> SettingsOut:
    row = db.get(AppSetting, BASELINE_KEY)
    automations = list(db.scalars(select(Automation).order_by(Automation.id)))
    names = _names(
        db, {a.owner_id for a in automations if a.owner_id} | ({row.updated_by} if row and row.updated_by else set())
    )
    return SettingsOut(
        manual_baseline_minutes=baseline_minutes(db),
        automations=[
            AutomationBaselineOut(
                id=a.id,
                name=a.name,
                owner_name=names.get(a.owner_id) if a.owner_id else None,
                manual_baseline_minutes=a.manual_baseline_minutes,
            )
            for a in automations
        ],
        updated_at=utc_to_kst(row.updated_at) if row is not None else None,
        updated_by_name=names.get(row.updated_by) if row is not None and row.updated_by else None,
    )


@router.get("/settings", response_model=SettingsOut)
def read_settings(_: OwnerDep, db: Annotated[Session, Depends(get_session)]) -> SettingsOut:
    return settings_out(db)


class BaselineIn(BaseModel):
    # 0 초과 ~ 7일. None = 정하지 않음 (절약 시간을 계산하지 않음)
    manual_baseline_minutes: float | None = Field(default=None, gt=0, le=10080)


@router.put("/settings", response_model=SettingsOut)
def update_settings(body: BaselineIn, actor: OwnerDep, db: Annotated[Session, Depends(get_session)]) -> SettingsOut:
    row = db.get(AppSetting, BASELINE_KEY)
    if body.manual_baseline_minutes is None:
        if row is not None:
            db.delete(row)
    else:
        if row is None:
            row = AppSetting(key=BASELINE_KEY, value="null")
            db.add(row)
        row.value = json.dumps(body.manual_baseline_minutes)
        row.updated_by, row.updated_at = actor.id, datetime.now(UTC)
    db.commit()
    return settings_out(db)


@router.put("/settings/automations/{automation_id}", response_model=SettingsOut)
def update_automation_baseline(
    automation_id: int, body: BaselineIn, _: OwnerDep, db: Annotated[Session, Depends(get_session)]
) -> SettingsOut:
    automation = db.get(Automation, automation_id)
    if automation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "자동화를 찾을 수 없습니다.")
    automation.manual_baseline_minutes = body.manual_baseline_minutes
    db.commit()
    return settings_out(db)
