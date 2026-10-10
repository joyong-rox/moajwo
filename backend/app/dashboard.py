"""홈 화면 대시보드 집계.

범위: 일반 사용자는 자기 기록, 관리자·소유자는 전체.
- 이번 달 실행 횟수: 이번 달(KST)에 시작한 실행 기록 수 (완료·검토·실패 모두, 완료 수는 따로)
- 누적 절약 시간: 완료된 실행마다 max(0, 수동 기준 소요 시간 - 실제 처리 시간)의 합
  · 수동 기준 소요 시간: 소유자가 설정에서 정한 값 (자동화별 값이 있으면 그 값). 정하지 않았으면 계산하지 않는다.
  · 실제 처리 시간: 실행 기록의 시작 → 종료 시각 (지금 설정 기준으로 다시 계산)
- 검증한 행 수: 검증(검토 포함)을 한 번 이상 거친 파일들의 데이터 행 수
  (같은 파일을 다시 검사하거나, 내용이 같은 파일(sha256)을 다시 올려 검사해도 한 번만 센다)
- 통합 전 발견한 오류 건수: 검토에서 찾은 차단 오류 + 검증 경고. 다시 검사에서 이전 검사와 같은 항목은 다시 세지 않는다.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admin_api import baseline_minutes
from app.auth import CurrentUserDep
from app.db import get_session
from app.files import load_parsed
from app.integration import KST
from app.models import Automation, IntegrationRun, ReviewItem, ReviewSession, SourceFile, ValidationRun

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


class DashboardOut(BaseModel):
    scope: str  # mine / all
    month: str  # 예: 2026년 10월
    month_runs: int
    month_done: int
    saved_seconds: float
    saved_runs: int  # 절약 시간에 포함한 완료 실행 수
    runs_without_baseline: int  # 기준 시간이 없어 절약 시간에서 뺀 완료 실행 수
    baseline_minutes: float | None
    validated_rows: int
    validated_files: int
    errors_found: int
    blocks_found: int
    warnings_found: int


def _kst(value: datetime | None, naive_tz=KST) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=naive_tz)


def _started(run: IntegrationRun) -> datetime:
    # started_at은 KST로 저장한다. 없으면 DB 기본값(UTC) created_at
    started = _kst(run.started_at)
    return started if started is not None else _kst(run.created_at, UTC) or datetime.now(KST)


def _savings(db: Session, runs: list[IntegrationRun]) -> tuple[float, int, int, float | None]:
    default = baseline_minutes(db)
    overrides = {a.id: a.manual_baseline_minutes for a in db.scalars(select(Automation))}
    saved, counted, missing = 0.0, 0, 0
    for r in runs:
        start, end = _kst(r.started_at), _kst(r.finished_at)
        if r.status != "done" or start is None or end is None:
            continue
        minutes = overrides.get(r.automation_id) if r.automation_id is not None else None
        minutes = minutes if minutes is not None else default
        if minutes is None:
            missing += 1
            continue
        saved += max(0.0, minutes * 60 - max(0.0, (end - start).total_seconds()))
        counted += 1
    return saved, counted, missing, default


def _validated_rows(db: Session, user_id: int | None) -> tuple[int, int]:
    stmt = select(ValidationRun.params)
    if user_id is not None:
        stmt = stmt.where(ValidationRun.created_by == user_id)
    file_ids: set[int] = set()
    for params in db.scalars(stmt):
        try:
            file_ids.update(int(f) for f in json.loads(params).get("file_ids", []))
        except (ValueError, TypeError, AttributeError):
            continue
    rows = 0
    seen: set[str] = set()  # 내용이 같은 파일(sha256)은 다시 올려도 한 번만 센다
    for fid in sorted(file_ids):
        record = db.get(SourceFile, fid)
        if record is None or record.sha256 in seen:
            continue
        seen.add(record.sha256)
        if record.data_rows is None:  # 행 수를 기록하기 전에 올린 파일은 한 번 읽어 채운다
            try:
                record.data_rows = load_parsed(db, fid)[1].data_rows
                db.commit()
            except HTTPException:
                continue
        rows += record.data_rows or 0
    return rows, len(seen)


def _issue_key(item: ReviewItem, file_map: dict[int, int]) -> tuple[Any, ...]:
    fid = file_map.get(item.file_id, item.file_id) if item.file_id is not None else None
    return (item.category, item.kind, fid, item.sheet, item.row, item.column, item.before)


def _errors(db: Session, user_id: int | None) -> tuple[int, int]:
    stmt = select(ReviewSession)
    if user_id is not None:
        stmt = stmt.where(ReviewSession.created_by == user_id)
    sessions = {s.id: s for s in db.scalars(stmt)}
    if not sessions:
        return 0, 0
    items = db.scalars(
        select(ReviewItem).where(ReviewItem.category.in_(("block", "warning")), ReviewItem.session_id.in_(sessions))
    )
    by_session: dict[int, list[ReviewItem]] = {}
    for item in items:
        by_session.setdefault(item.session_id, []).append(item)
    blocks = warnings = 0
    for sid, session in sessions.items():
        replaced = {int(k): int(v) for k, v in json.loads(session.params).get("replaced_files", {}).items()}
        previous: set[tuple[Any, ...]] = set()
        if session.parent_id is not None:
            previous = {_issue_key(i, replaced) for i in by_session.get(session.parent_id, [])}
        for item in by_session.get(sid, []):
            if _issue_key(item, {}) in previous:
                continue  # 다시 검사에서 이전 검사와 같은 오류
            if item.category == "block":
                blocks += 1
            else:
                warnings += 1
    return blocks, warnings


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(user: CurrentUserDep, db: Annotated[Session, Depends(get_session)]) -> DashboardOut:
    owner = None if user.is_manager else user.id
    stmt = select(IntegrationRun)
    if owner is not None:
        stmt = stmt.where(IntegrationRun.user_id == owner)
    runs = list(db.scalars(stmt))
    now = datetime.now(KST)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_runs = [r for r in runs if _started(r) >= month_start]
    saved, counted, missing, default = _savings(db, runs)
    rows, files = _validated_rows(db, owner)
    blocks, warnings = _errors(db, owner)
    return DashboardOut(
        scope="mine" if owner is not None else "all",
        month=f"{now.year}년 {now.month}월",
        month_runs=len(month_runs),
        month_done=sum(1 for r in month_runs if r.status == "done"),
        saved_seconds=round(saved, 1),
        saved_runs=counted,
        runs_without_baseline=missing,
        baseline_minutes=default,
        validated_rows=rows,
        validated_files=files,
        errors_found=blocks + warnings,
        blocks_found=blocks,
        warnings_found=warnings,
    )
