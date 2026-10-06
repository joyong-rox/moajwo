"""통합 전 사전 검토 API."""

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, CurrentUserDep, ensure_own, ensure_view
from app.db import get_session
from app.files import get_file_for
from app.models import Automation, ReviewItem, ReviewSession, SourceFile, User
from app.review import (
    ReviewError,
    changes,
    complete,
    create_session,
    decide,
    history,
    recheck,
    session_items,
    summarize,
)
from app.validation_api import ValidationIn

router = APIRouter(prefix="/reviews", tags=["review"])


class ReviewItemOut(BaseModel):
    id: int
    category: str  # block / pending / auto / warning
    kind: str
    file_id: int | None
    file_name: str | None
    sheet: str | None
    row: int | None
    column: str | None
    before: str | None
    after: str | None
    reason: str
    reason_source: str  # rule / llm
    payload: dict[str, Any]
    decision: str | None
    choice: str | None
    excluded: bool


class ReviewFileOut(BaseModel):
    id: int
    name: str
    excluded: bool


class ReviewOut(BaseModel):
    id: int
    status: str
    score: int
    summary: str
    counts: dict[str, int]
    can_complete: bool
    remember_format: bool
    files: list[ReviewFileOut]
    items: list[ReviewItemOut]
    params: dict[str, Any]
    validation_run_id: int | None
    created_at: datetime
    completed_at: datetime | None
    learned: dict[str, int] | None = None
    parent_id: int | None = None
    child_id: int | None = None  # 이 세션을 이어받아 다시 검사한 세션 (대체된 경우)
    history: list[dict[str, Any]] = []  # 처음 검사부터 이 세션까지
    changes: dict[str, Any] | None = None  # 이전 검사 대비 변화
    automation_id: int | None = None  # 저장된 자동화로 실행한 검토
    automation_name: str | None = None
    created_by: int | None = None
    created_by_name: str | None = None
    can_edit: bool = True  # 이 검토를 만든 사람만 승인·완료·다시 검사할 수 있다 (다른 사람은 조회만)


def review_out(
    db: Session, session: ReviewSession, learned: dict[str, int] | None = None, user: CurrentUser | None = None
) -> ReviewOut:
    items = session_items(db, session)
    s = summarize(session, items)
    automation = db.get(Automation, session.automation_id) if session.automation_id is not None else None
    creator = db.get(User, session.created_by) if session.created_by is not None else None
    excluded = set(json.loads(session.excluded_file_ids))
    files = []
    for fid in json.loads(session.file_ids):
        record = db.get(SourceFile, fid)
        files.append(ReviewFileOut(id=fid, name=record.original_name if record else "?", excluded=fid in excluded))
    return ReviewOut(
        id=session.id,
        status=session.status,
        score=s.score,
        summary=s.summary,
        counts={
            "block": s.blocks,
            "pending": s.pending,
            "pending_open": s.pending_open,
            "auto": s.auto,
            "warning": s.warnings,
        },
        can_complete=s.can_complete and session.status == "in_review",
        remember_format=session.remember_format,
        files=files,
        items=[
            ReviewItemOut(
                id=i.id,
                category=i.category,
                kind=i.kind,
                file_id=i.file_id,
                file_name=i.file_name,
                sheet=i.sheet,
                row=i.row,
                column=i.column,
                before=i.before,
                after=i.after,
                reason=i.reason,
                reason_source=i.reason_source,
                payload=json.loads(i.payload),
                decision=i.decision,
                choice=i.choice,
                excluded=i.file_id in excluded and i.kind != "pii",
            )
            for i in items
        ],
        params=json.loads(session.params),
        validation_run_id=session.validation_run_id,
        created_at=session.created_at,
        completed_at=session.completed_at,
        learned=learned,
        parent_id=session.parent_id,
        child_id=db.scalar(select(ReviewSession.id).where(ReviewSession.parent_id == session.id)),
        history=history(db, session),
        changes=changes(db, session),
        automation_id=session.automation_id,
        automation_name=automation.name if automation is not None else None,
        created_by=session.created_by,
        created_by_name=creator.display_name if creator is not None else None,
        can_edit=user is None or session.created_by == user.id or (session.created_by is None and user.is_manager),
    )


def _get(db: Session, review_id: int, user: CurrentUser, own: bool = False) -> ReviewSession:
    """볼 수 있는(own=True면 바꿀 수 있는) 검토. 남의 검토는 일반 사용자에게 없는 것처럼 404."""
    session = db.get(ReviewSession, review_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "검토 세션을 찾을 수 없습니다.")
    (ensure_own if own else ensure_view)(user, session.created_by, "검토 세션")
    return session


@router.post("", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
def create_review(body: ValidationIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> ReviewOut:
    if set(body.file_ids) & set(body.baseline_file_ids):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "같은 파일을 검증 대상과 전월 자료로 함께 쓸 수 없습니다."
        )
    for fid in [*body.file_ids, *body.baseline_file_ids]:
        get_file_for(db, user, fid)
    session = create_session(db, body.model_dump(), user_id=user.id)
    return review_out(db, session, user=user)


@router.get("/{review_id}", response_model=ReviewOut)
def get_review(review_id: int, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> ReviewOut:
    return review_out(db, _get(db, review_id, user), user=user)


class DecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    choice: str | None = None  # 승인 시 연결할 표준 항목 key 또는 7자리 코드


@router.post("/{review_id}/items/{item_id}/decision", response_model=ReviewOut)
def decide_item(
    review_id: int, item_id: int, body: DecisionIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> ReviewOut:
    session = _get(db, review_id, user, own=True)
    item = db.get(ReviewItem, item_id)
    if item is None or item.session_id != session.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "검토 항목을 찾을 수 없습니다.")
    try:
        decide(db, session, item, body.decision, body.choice)
    except ReviewError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return review_out(db, session, user=user)


class ReviewPatch(BaseModel):
    remember_format: bool


@router.patch("/{review_id}", response_model=ReviewOut)
def update_review(
    review_id: int, body: ReviewPatch, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> ReviewOut:
    session = _get(db, review_id, user, own=True)
    if session.status != "in_review":
        raise HTTPException(status.HTTP_409_CONFLICT, "검토가 끝난 세션은 바꿀 수 없습니다.")
    session.remember_format = body.remember_format
    db.commit()
    return review_out(db, session, user=user)


@router.post("/{review_id}/complete", response_model=ReviewOut)
def complete_review(review_id: int, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> ReviewOut:
    session = _get(db, review_id, user, own=True)
    try:
        # 기관·지역 이름은 모두가 쓰는 용어사전에 들어가므로 사전 관리 권한(관리자·소유자)일 때만 학습
        learned = complete(db, session, learn_names=user.is_manager)
    except ReviewError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return review_out(db, session, learned, user=user)


class RecheckIn(BaseModel):
    # 원본을 고쳐 다시 올린 파일로 바꿀 때: {이전 파일 id: 새 파일 id}
    replace: dict[int, int] = {}


@router.post("/{review_id}/recheck", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
def recheck_review(
    review_id: int, body: RecheckIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> ReviewOut:
    """이전 검토를 이어받아 다시 검사한다 (결정·제외·기억하기 선택을 이어받고, 이전 검토는 대체됨)."""
    old = _get(db, review_id, user, own=True)
    for new_id in body.replace.values():
        get_file_for(db, user, new_id)
    try:
        session = recheck(db, old, body.replace)
    except ReviewError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return review_out(db, session, user=user)
