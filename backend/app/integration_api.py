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

from app import recovery
from app.auth import CurrentUser, CurrentUserDep, ensure_own, ensure_view
from app.config import DATA_DIR
from app.db import get_session
from app.integration import KST, IntegrationError, integrate
from app.models import Automation, IntegrationRun, ReviewSession, RunChange, User
from app.pii import mask_nested, mask_text
from app.timefmt import stored_kst, utc_to_kst

router = APIRouter(tags=["integration"])

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


STATUS_LABEL = {"done": "완료", "review": "검토", "failed": "실패", "running": "실행 중", "check": "결과 확인 필요"}
# 결과 파일 이름·경로를 보이는 상태 — 실행 중인 행의 경로는 아직 만들기 전의 계획이라 보이지 않는다
SHOW_FILE = ("done", "check")
DOWNLOAD_BLOCKED = {
    "running": "아직 결과 파일을 만드는 중입니다 — 잠시 뒤 실행 기록을 새로 고친 뒤 내려받으세요.",
    "check": "결과를 확정하지 못한 실행이라 내려받을 수 없습니다 — 관리자가 결과를 확인해야 합니다.",
    "failed": "실패한 실행이라 결과 파일이 없습니다.",
    "review": "검토가 끝나지 않은 실행이라 결과 파일이 없습니다.",
}


def _masked_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """실행 요약의 결과 열 이름(표준 항목이 없는 열은 원래 이름)에 든 개인정보를 가린다."""
    # 키는 고정된 이름이라 그대로 두고, 값(파일·시트·열 이름, 뺀 열 목록 등 중첩 글자)만 가린다
    return mask_nested(summary)  # type: ignore[return-value]


def _masked_change(change: "ChangeOut") -> "ChangeOut":
    return change.model_copy(
        update={
            "column": mask_text(change.column),
            "source_column": mask_text(change.source_column),
            "before": mask_text(change.before) if change.before else change.before,
            "after": mask_text(change.after) if change.after else change.after,
            "reason": mask_text(change.reason),
        }
    )


class RunOut(BaseModel):
    id: int
    review_session_id: int | None
    status: str
    status_label: str  # 완료 / 검토 / 실패
    trigger: str  # manual / automation
    automation_id: int | None
    automation_name: str | None
    user_id: int | None  # 실행한 사용자
    user_name: str | None
    input_file_count: int
    file_name: str | None
    output_path: str | None  # 결과 파일 경로 (data/outputs/…)
    row_count: int
    change_count: int
    warning_count: int
    summary: dict[str, Any]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None  # 시작부터 종료까지 (검토 대기 중이면 없음)


def _aware(value: datetime) -> datetime:
    """SQLite는 시간대 없이 돌려주므로 저장할 때 쓴 KST로 본다."""
    return value if value.tzinfo is not None else value.replace(tzinfo=KST)


def run_out(run: IntegrationRun, automation_name: str | None = None, user_name: str | None = None) -> RunOut:
    duration = None
    if run.started_at is not None and run.finished_at is not None:
        duration = max(0, round((_aware(run.finished_at) - _aware(run.started_at)).total_seconds() * 1000))
    return RunOut(
        id=run.id,
        review_session_id=run.review_session_id,
        status=run.status,
        status_label=STATUS_LABEL.get(run.status, run.status),
        trigger=run.trigger,
        automation_id=run.automation_id,
        automation_name=automation_name,
        user_id=run.user_id,
        user_name=user_name,
        input_file_count=run.input_file_count,
        file_name=Path(run.output_path).name if run.output_path and run.status in SHOW_FILE else None,
        output_path=f"data/{run.output_path}" if run.output_path and run.status in SHOW_FILE else None,
        row_count=run.row_count,
        change_count=run.change_count,
        warning_count=run.warning_count,
        summary=_masked_summary(json.loads(run.summary or "{}")),
        error=run.error,
        created_at=utc_to_kst(run.created_at),  # DB 기본값(UTC)
        started_at=stored_kst(run.started_at),  # datetime.now(KST)로 저장
        finished_at=stored_kst(run.finished_at),
        duration_ms=duration,
    )


def runs_out(db: Session, runs: list[IntegrationRun]) -> list[RunOut]:
    ids = {r.automation_id for r in runs if r.automation_id is not None}
    names: dict[int, str] = {}
    if ids:
        names = {i: n for i, n in db.execute(select(Automation.id, Automation.name).where(Automation.id.in_(ids)))}
    user_ids = {r.user_id for r in runs if r.user_id is not None}
    users: dict[int, str] = {}
    if user_ids:
        users = {i: n for i, n in db.execute(select(User.id, User.display_name).where(User.id.in_(user_ids)))}
    out = [
        run_out(
            r,
            names.get(r.automation_id) if r.automation_id else None,
            users.get(r.user_id) if r.user_id else None,
        )
        for r in runs
    ]
    for o in out:
        if o.status == "review" and o.review_session_id is not None:
            o.review_session_id = _latest_session(db, o.review_session_id)
    return out


def _latest_session(db: Session, session_id: int) -> int:
    """검토 대기 실행은 다시 검사한 최신 검토를 가리킨다 (이전 검토는 대체됨)."""
    for _ in range(100):
        child = db.scalar(select(ReviewSession.id).where(ReviewSession.parent_id == session_id))
        if child is None:
            break
        session_id = child
    return session_id


class IntegrateIn(BaseModel):
    # 표준 항목이 없는('해당 없음') 열도 원래 이름으로 넣을지. 비우면 자동화 설정(없으면 넣지 않음)
    include_unmapped: bool | None = None


@router.post("/reviews/{review_id}/integrate", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def integrate_review(
    review_id: int,
    db: Annotated[Session, Depends(get_session)],
    user: CurrentUserDep,
    body: IntegrateIn | None = None,
) -> RunOut:
    """검토가 끝난 세션으로 새 결과 엑셀을 만든다. 실행할 때마다 새 파일을 만든다 (원본은 덮어쓰지 않음).
    검토를 만든 사람만 실행한다."""
    session = db.get(ReviewSession, review_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "검토 세션을 찾을 수 없습니다.")
    ensure_own(user, session.created_by, "검토 세션")
    try:
        run = integrate(db, session, body.include_unmapped if body else None)
    except IntegrationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return runs_out(db, [run])[0]


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    db: Annotated[Session, Depends(get_session)],
    user: CurrentUserDep,
    review_id: int | None = None,
    automation_id: int | None = None,
    user_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunOut]:
    """실행 기록 (최신 순). 일반 사용자는 자기 실행만, 관리자·소유자는 전체(user_id로 거를 수 있음)."""
    stmt = select(IntegrationRun).order_by(IntegrationRun.id.desc()).limit(limit)
    if not user.is_manager:
        stmt = stmt.where(IntegrationRun.user_id == user.id)
    elif user_id is not None:
        stmt = stmt.where(IntegrationRun.user_id == user_id)
    if review_id is not None:
        stmt = stmt.where(IntegrationRun.review_session_id == review_id)
    if automation_id is not None:
        stmt = stmt.where(IntegrationRun.automation_id == automation_id)
    runs = list(db.scalars(stmt))
    recovery.notice(runs)  # 담당이 끝난 실행이 보이면 정리를 예약만 한다 (이 요청은 아무것도 바꾸지 않음)
    return runs_out(db, runs)


def _get(db: Session, run_id: int, user: CurrentUser) -> IntegrationRun:
    run = db.get(IntegrationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "통합 실행을 찾을 수 없습니다.")
    ensure_view(user, run.user_id, "통합 실행")
    return run


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> RunOut:
    return runs_out(db, [_get(db, run_id, user)])[0]


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
    user: CurrentUserDep,
    kind: str | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> ChangePage:
    _get(db, run_id, user)
    where = [RunChange.run_id == run_id] + ([RunChange.kind == kind] if kind else [])
    total = db.scalar(select(func.count(RunChange.id)).where(*where)) or 0
    rows = db.scalars(select(RunChange).where(*where).order_by(RunChange.id).offset(offset).limit(limit))
    return ChangePage(
        total=total,
        items=[_masked_change(ChangeOut.model_validate(r, from_attributes=True)) for r in rows],
    )


@router.get("/runs/{run_id}/download")
def download_run(run_id: int, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> FileResponse:
    run = _get(db, run_id, user)
    if run.status != "done" or not run.output_path:
        raise HTTPException(status.HTTP_409_CONFLICT, DOWNLOAD_BLOCKED.get(run.status, "결과 파일이 없습니다."))
    path = (DATA_DIR / run.output_path).resolve()
    if not path.is_relative_to((DATA_DIR / "outputs").resolve()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "결과 파일을 찾을 수 없습니다.")
    if not path.is_file():  # 기록은 있는데 파일이 없다 (외부 DB 서버의 재배포·재시작 등 — 원본 쪽과 같은 이유)
        raise HTTPException(
            status.HTTP_410_GONE,
            "결과 파일이 서버에 없습니다 — 서버 재배포·재시작으로 지워졌을 수 있습니다. "
            "실행 기록·변경 내역은 그대로이며, 원본을 다시 올려 다시 실행하면 새 결과 파일을 만듭니다.",
        )
    return FileResponse(path, media_type=XLSX_MEDIA, filename=path.name)
