"""통합 전 사전 검토 API."""

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, CurrentUserDep, ensure_own, ensure_view
from app.db import get_session
from app.files import get_file_for
from app.integration import include_unmapped_default
from app.models import Automation, IntegrationRun, ReviewItem, ReviewSession, SourceFile, User
from app.pii import mask_nested, mask_rrn_number, mask_text
from app.review import (
    INTERNAL_PARAMS,
    ReviewError,
    ReviewPermissionError,
    can_change_file,
    changes,
    complete,
    create_session,
    decide,
    history,
    recheck,
    recheck_reasons,
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
    # 이 항목의 결정이 원본 파일(매핑 확정·개인정보 확인)을 바꾸는데, 검토를 만든 사람이 그 파일을 올린 사람이 아니면
    # False (조회 전용). 화면은 버튼을 끄고 안내하며, 서버는 결정 요청을 403으로 거부한다.
    can_change: bool = True


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
    latest_id: int | None = None  # 대체된 검토면 다시 검사로 이어진 최신 검토
    history: list[dict[str, Any]] = []  # 처음 검사부터 이 세션까지
    changes: dict[str, Any] | None = None  # 이전 검사 대비 변화
    automation_id: int | None = None  # 저장된 자동화로 실행한 검토
    automation_name: str | None = None
    created_by: int | None = None
    created_by_name: str | None = None
    can_edit: bool = True  # 이 검토를 만든 사람만 승인·완료·다시 검사할 수 있다 (다른 사람은 조회만)
    include_unmapped_default: bool = False  # 통합할 때 '표준 항목이 없는 열 포함'의 기본값 (자동화 설정)
    needs_recheck: bool = False  # 검사한 뒤 열 매핑 확정이 바뀌어 이 검사 결과로 완료·통합할 수 없음 (다시 검사 필요)
    recheck_reasons: list[str] = []  # 바뀐 열 ('이전 → 지금', 가림)


def _masked(text: str | None) -> str | None:
    """검토 항목의 열 이름·값을 화면으로 보낼 때 개인정보를 가린다 (DB의 기록은 연결용 원문 그대로)."""
    return mask_text(text) if text else text


def review_out(
    db: Session, session: ReviewSession, learned: dict[str, int] | None = None, user: CurrentUser | None = None
) -> ReviewOut:
    items = session_items(db, session)
    reasons = recheck_reasons(db, session)
    s = summarize(session, items, reasons)
    automation = db.get(Automation, session.automation_id) if session.automation_id is not None else None
    creator = db.get(User, session.created_by) if session.created_by is not None else None
    excluded = set(json.loads(session.excluded_file_ids))
    files = []
    for fid in json.loads(session.file_ids):
        record = db.get(SourceFile, fid)
        name = mask_text(record.original_name) if record else "?"  # 화면 표시용 (연결은 id로)
        files.append(ReviewFileOut(id=fid, name=name, excluded=fid in excluded))
    rrn_files = {
        fid
        for fid in {i.file_id for i in items if i.file_id is not None}
        if (rec := db.get(SourceFile, fid)) is not None
        and rec.pii_summary
        and json.loads(rec.pii_summary).get("counts", {}).get("rrn")
    }

    def shown(i: ReviewItem, text: str | None) -> str | None:
        if not text:
            return text
        return str(mask_rrn_number(text)) if i.file_id in rrn_files else mask_text(text)

    writable = {
        fid: can_change_file(db, fid, session.created_by) for fid in {i.file_id for i in items if i.file_id is not None}
    }
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
                file_name=_masked(i.file_name),
                sheet=_masked(i.sheet),
                row=i.row,
                column=_masked(i.column),
                before=shown(i, i.before),
                after=shown(i, i.after),
                reason=shown(i, i.reason) or "",
                reason_source=i.reason_source,
                payload=mask_nested(json.loads(i.payload), rrn_numbers=i.file_id in rrn_files),
                decision=i.decision,
                choice=i.choice,
                excluded=i.file_id in excluded and i.kind != "pii",
                can_change=writable.get(i.file_id, True) if i.kind in ("mapping", "pii") else True,
            )
            for i in items
        ],
        # 입력 조건(필수 기관 등)의 개인정보 형식도 가린다 (검사 결과가 유효한 확정 상태는 내부 기록이라 내보내지 않음)
        params=mask_nested({k: v for k, v in json.loads(session.params).items() if k not in INTERNAL_PARAMS}),
        validation_run_id=session.validation_run_id,
        created_at=session.created_at,
        completed_at=session.completed_at,
        learned=learned,
        parent_id=session.parent_id,
        child_id=db.scalar(select(ReviewSession.id).where(ReviewSession.parent_id == session.id)),
        latest_id=_latest(db, session) if session.status == "superseded" else None,
        history=history(db, session),
        changes=changes(db, session),
        automation_id=session.automation_id,
        automation_name=automation.name if automation is not None else None,
        created_by=session.created_by,
        created_by_name=creator.display_name if creator is not None else None,
        include_unmapped_default=include_unmapped_default(db, session),
        needs_recheck=s.needs_recheck,
        recheck_reasons=reasons if s.needs_recheck else [],
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


REVIEW_STATUS_LABEL = {"in_review": "검토 중", "ready": "검토 완료", "superseded": "다시 검사로 대체됨"}
LIST_FILE_NAMES = 3  # 목록에 보이는 파일 이름 수 (나머지는 개수로)


class ReviewListItem(BaseModel):
    id: int
    status: str  # in_review / ready / superseded
    status_label: str
    needs_recheck: bool  # 검사 기준과 지금 매핑이 달라 다시 검사해야 완료·통합할 수 있음 (대체된 검토는 False)
    file_count: int
    file_names: list[str]  # 앞의 몇 개만, 개인정보 형식은 가림
    created_at: datetime
    completed_at: datetime | None
    created_by: int | None
    created_by_name: str | None
    automation_name: str | None
    latest_id: int  # 대체된 검토면 다시 검사로 이어진 최신 검토, 아니면 자기 자신
    run_count: int  # 이 검토로 만든 실행 기록 (실패·대기 포함)
    last_run_id: int | None  # 결과 파일이 있는 가장 최근 실행
    can_edit: bool  # 만든 사람만 승인·완료·통합 (최고 관리자도 남의 검토는 조회만)


def _latest(db: Session, session: ReviewSession) -> int:
    """다시 검사로 이어진 마지막 검토 (대체되지 않은 검토는 자기 자신)."""
    current, seen = session.id, {session.id}
    while (child := db.scalar(select(ReviewSession.id).where(ReviewSession.parent_id == current))) is not None:
        if child in seen:  # 순환은 없어야 하지만 끝없이 돌지 않게
            break
        seen.add(child)
        current = child
    return current


@router.get("", response_model=list[ReviewListItem])
def list_reviews(
    db: Annotated[Session, Depends(get_session)],
    user: CurrentUserDep,
    status_filter: Annotated[Literal["in_review", "ready", "superseded"] | None, Query(alias="status")] = None,
    include_superseded: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ReviewListItem]:
    """검토 목록 (최신 순, 조회 전용) — 결과를 아직 만들지 않은 검토도 찾아 이어갈 수 있게 한다.
    볼 수 있는 범위는 단건 조회와 같다: 일반 사용자는 자기 검토, 최고 관리자는 전체 (남의 검토는 can_edit=False)."""
    stmt = select(ReviewSession).order_by(ReviewSession.id.desc()).limit(limit)
    if not user.is_manager:
        stmt = stmt.where(ReviewSession.created_by == user.id)
    if status_filter is not None:
        stmt = stmt.where(ReviewSession.status == status_filter)
    elif not include_superseded:
        stmt = stmt.where(ReviewSession.status != "superseded")
    out = []
    for session in db.scalars(stmt):
        fids = json.loads(session.file_ids)
        names = [mask_text(r.original_name) if (r := db.get(SourceFile, f)) else "?" for f in fids[:LIST_FILE_NAMES]]
        creator = db.get(User, session.created_by) if session.created_by is not None else None
        automation = db.get(Automation, session.automation_id) if session.automation_id is not None else None
        runs = select(IntegrationRun.id).where(IntegrationRun.review_session_id == session.id)
        out.append(
            ReviewListItem(
                id=session.id,
                status=session.status,
                status_label=REVIEW_STATUS_LABEL.get(session.status, session.status),
                needs_recheck=session.status != "superseded" and bool(recheck_reasons(db, session)),
                file_count=len(fids),
                file_names=names,
                created_at=session.created_at,
                completed_at=session.completed_at,
                created_by=session.created_by,
                created_by_name=creator.display_name if creator is not None else None,
                automation_name=automation.name if automation is not None else None,
                latest_id=_latest(db, session) if session.status == "superseded" else session.id,
                run_count=db.scalar(select(func.count()).select_from(runs.subquery())) or 0,
                last_run_id=db.scalar(
                    runs.where(IntegrationRun.status == "done").order_by(IntegrationRun.id.desc()).limit(1)
                ),
                can_edit=session.created_by == user.id or (session.created_by is None and user.is_manager),
            )
        )
    return out


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
    except ReviewPermissionError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
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
    except ReviewPermissionError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ReviewError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return review_out(db, session, user=user)
