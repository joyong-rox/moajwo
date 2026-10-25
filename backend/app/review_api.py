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
    ReviewNameError,
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
    try:
        session = create_session(db, body.model_dump(), user_id=user.id)
    except ReviewNameError as exc:  # 시트·열 이름이 저장 한도를 넘음 — 아무것도 기록하지 않았다
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
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
    pending_count: int  # 아직 결과가 정해지지 않은 실행 (실행 중·결과 확인 필요) — '결과 파일 없음'과 구분
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


ReviewFilter = Literal["all", "open", "recheck", "ready"]
PAGE_MAX = 100  # 한 번에 돌려주는 검토 수 상한 (더 오래된 검토는 next_before로 이어서)
RECHECK_SCAN = 300  # '다시 검사 필요'는 계산해야 알므로 한 번에 살펴보는 검토 수 (못 다 보면 이어서 찾기)


class ReviewPage(BaseModel):
    items: list[ReviewListItem]
    # 이어서 볼 때 before로 넘길 값 — None이면 조건에 맞는 검토를 끝까지 살펴봤다.
    # '다시 검사 필요'는 items가 비어 있어도 값이 있을 수 있다 (아직 살펴보지 않은 더 오래된 검토가 남음).
    next_before: int | None


class ReviewCounts(BaseModel):
    """볼 수 있는 검토 전체의 상태별 건수 (목록에 불러온 범위와 상관없음)."""

    total: int  # 대체된 검토 포함
    live: int  # 대체된 검토 제외
    open: int  # 검토 중
    ready: int  # 검토 완료
    superseded: int
    recheck: int  # 다시 검사 필요 (대체된 검토 제외)


def _visible(stmt: Any, user: CurrentUser) -> Any:
    """볼 수 있는 범위는 단건 조회와 같다: 일반 사용자는 자기 검토, 최고 관리자는 전체."""
    return stmt if user.is_manager else stmt.where(ReviewSession.created_by == user.id)


def _list_item(db: Session, session: ReviewSession, user: CurrentUser, needs_recheck: bool) -> ReviewListItem:
    fids = json.loads(session.file_ids)
    names = [mask_text(r.original_name) if (r := db.get(SourceFile, f)) else "?" for f in fids[:LIST_FILE_NAMES]]
    creator = db.get(User, session.created_by) if session.created_by is not None else None
    automation = db.get(Automation, session.automation_id) if session.automation_id is not None else None
    runs = select(IntegrationRun.id).where(IntegrationRun.review_session_id == session.id)
    return ReviewListItem(
        id=session.id,
        status=session.status,
        status_label=REVIEW_STATUS_LABEL.get(session.status, session.status),
        needs_recheck=needs_recheck,
        file_count=len(fids),
        file_names=names,
        created_at=session.created_at,
        completed_at=session.completed_at,
        created_by=session.created_by,
        created_by_name=creator.display_name if creator is not None else None,
        automation_name=automation.name if automation is not None else None,
        latest_id=_latest(db, session) if session.status == "superseded" else session.id,
        run_count=db.scalar(select(func.count()).select_from(runs.subquery())) or 0,
        last_run_id=db.scalar(runs.where(IntegrationRun.status == "done").order_by(IntegrationRun.id.desc()).limit(1)),
        pending_count=db.scalar(
            select(func.count()).select_from(runs.where(IntegrationRun.status.in_(("running", "check"))).subquery())
        )
        or 0,
        can_edit=session.created_by == user.id or (session.created_by is None and user.is_manager),
    )


def _needs_recheck(db: Session, session: ReviewSession) -> bool:
    return session.status != "superseded" and bool(recheck_reasons(db, session))


@router.get("", response_model=ReviewPage)
def list_reviews(
    db: Annotated[Session, Depends(get_session)],
    user: CurrentUserDep,
    filter_: Annotated[ReviewFilter, Query(alias="filter")] = "all",
    include_superseded: bool = False,
    limit: Annotated[int, Query(ge=1, le=PAGE_MAX)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> ReviewPage:
    """검토 목록 (최신 순, 조회 전용) — 결과를 아직 만들지 않은 검토도 찾아 이어갈 수 있게 한다.

    상태 조건을 먼저 걸고 그 결과를 번호 순으로 나눠 준다 (최신 몇 개를 받은 뒤 거르지 않음).
    before를 주면 그 번호보다 오래된 검토부터 — 응답의 next_before를 그대로 넘기면 빠짐·겹침 없이 이어진다.
    '다시 검사 필요'(filter=recheck)는 저장된 상태가 아니라 계산해야 알므로 최신 순으로 살펴보며 채우고,
    한 번에 RECHECK_SCAN개까지만 본다 (모델 호출·원본 읽기·쓰기 없음 — recheck_reasons).
    include_superseded는 '전체'에만 쓴다 (대체된 검토는 검토 중·완료·다시 검사 필요가 아님).
    """
    stmt = _visible(select(ReviewSession).order_by(ReviewSession.id.desc()), user)
    if before is not None:
        stmt = stmt.where(ReviewSession.id < before)
    if filter_ == "open":
        stmt = stmt.where(ReviewSession.status == "in_review")
    elif filter_ == "ready":
        stmt = stmt.where(ReviewSession.status == "ready")
    elif filter_ == "recheck" or not include_superseded:
        stmt = stmt.where(ReviewSession.status != "superseded")

    if filter_ != "recheck":
        rows = list(db.scalars(stmt.limit(limit + 1)))
        page = rows[:limit]
        return ReviewPage(
            items=[_list_item(db, s, user, _needs_recheck(db, s)) for s in page],
            next_before=page[-1].id if len(rows) > limit else None,
        )

    items: list[ReviewListItem] = []
    rows = list(db.scalars(stmt.limit(RECHECK_SCAN + 1)))
    next_before = None
    for i, session in enumerate(rows[:RECHECK_SCAN]):
        if _needs_recheck(db, session):
            items.append(_list_item(db, session, user, True))
        if len(items) == limit or i == RECHECK_SCAN - 1:
            # 여기서 멈춘다 — 살펴보지 않은 더 오래된 검토가 남았으면 이 번호부터 이어서
            next_before = session.id if i < len(rows) - 1 else None
            break
    return ReviewPage(items=items, next_before=next_before)


@router.get("/counts", response_model=ReviewCounts)
def review_counts(db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> ReviewCounts:
    """상태 카드용 전체 건수 (볼 수 있는 범위 전체, 조회 전용). '다시 검사 필요'는 대체되지 않은 검토마다 계산한다."""
    by_status = dict(
        db.execute(
            _visible(select(ReviewSession.status, func.count()).group_by(ReviewSession.status), user)
        ).all()  # fmt: skip
    )
    live = _visible(select(ReviewSession).where(ReviewSession.status != "superseded"), user)
    total = sum(by_status.values())
    superseded = by_status.get("superseded", 0)
    return ReviewCounts(
        total=total,
        live=total - superseded,
        open=by_status.get("in_review", 0),
        ready=by_status.get("ready", 0),
        superseded=superseded,
        recheck=sum(1 for s in db.scalars(live) if recheck_reasons(db, s)),
    )


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
    except ReviewNameError as exc:  # 바꾼 파일의 시트·열 이름이 저장 한도를 넘음 (이전 검토는 그대로)
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except ReviewError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return review_out(db, session, user=user)
