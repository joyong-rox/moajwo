"""최고 관리자 관리 화면(/moajwoAdmin)용 API: 관리 현황 통계, 관리 이력.

집계 기준 (화면 도움말과 같은 내용):
- 기간: 시작일 00:00 ~ 종료일 24:00, 한국 시간(KST, UTC+9). 기본값은 이번 달 1일 ~ 오늘.
- 사용자 수: 지금 시점의 계정 상태별 수 (기간과 무관). 최고 관리자 계정도 '이용 중'에 포함한다.
- 업로드 파일 수: 기간 안에 올린 원본 파일 (같은 파일을 다시 올리면 따로 센다).
- 통합 실행: 기간 안에 시작한 실행 기록(직접 검토·자동화 모두)의 상태별 수 — 홈 대시보드의 '이번 달 실행 횟수'와
  같은 시작 시각 기준(dashboard._started).
- 계정 상태와 관계없이 모든 기록을 센다 (검증용 계정 등 이용 중지 계정의 기록도 포함). 그중 이용 중이 아닌 계정과
  계정 도입 전(소유자 없음) 기록의 수를 따로 알려 준다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import STATUS_LABEL, STATUSES, OwnerDep, role_label
from app.dashboard import _started
from app.db import get_session
from app.integration import KST
from app.models import AccountEvent, IntegrationRun, SourceFile, User
from app.timefmt import utc_to_kst

router = APIRouter(prefix="/admin", tags=["admin-console"])

MAX_DAYS = 366


class StatusCount(BaseModel):
    status: str
    label: str
    count: int


class PeriodCount(BaseModel):
    total: int
    inactive_accounts: int  # 그중 지금 이용 중이 아닌 계정(승인 대기·반려·이용 중지)의 기록
    no_owner: int  # 그중 계정 도입 전 기록(소유자 없음)


class RunCounts(PeriodCount):
    done: int
    failed: int
    review: int  # 검토 대기 (확인할 항목이 남아 결과 파일을 아직 만들지 않음)
    running: int


class OverviewOut(BaseModel):
    start: date
    end: date
    timezone: str
    generated_at: datetime
    users: list[StatusCount]
    owners: int
    uploads: PeriodCount
    runs: RunCounts


def _period(start: date | None, end: date | None) -> tuple[date, date, datetime, datetime]:
    today = datetime.now(KST).date()
    start = start or today.replace(day=1)
    end = end or today
    if start > end:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "시작일이 종료일보다 늦습니다.")
    if (end - start).days >= MAX_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"기간은 최대 {MAX_DAYS}일까지 볼 수 있습니다.")
    lo = datetime(start.year, start.month, start.day, tzinfo=KST)
    hi = datetime(end.year, end.month, end.day, tzinfo=KST) + timedelta(days=1)
    return start, end, lo, hi


@router.get("/overview", response_model=OverviewOut)
def overview(
    _: OwnerDep,
    db: Annotated[Session, Depends(get_session)],
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
) -> OverviewOut:
    start, end, lo, hi = _period(start, end)
    by_status = dict(db.execute(select(User.status, func.count(User.id)).group_by(User.status)).all())
    users = [StatusCount(status=s, label=STATUS_LABEL[s], count=by_status.get(s, 0)) for s in STATUSES]
    owners = db.scalar(select(func.count(User.id)).where(User.role == "owner")) or 0
    inactive = set(db.scalars(select(User.id).where(User.status != "active")))

    uploads = PeriodCount(total=0, inactive_accounts=0, no_owner=0)
    for uploaded_at, owner in db.execute(select(SourceFile.uploaded_at, SourceFile.uploaded_by)):
        at = utc_to_kst(uploaded_at)
        if at is None or not lo <= at < hi:
            continue
        uploads.total += 1
        uploads.no_owner += owner is None
        uploads.inactive_accounts += owner in inactive

    runs = RunCounts(total=0, inactive_accounts=0, no_owner=0, done=0, failed=0, review=0, running=0)
    for run in db.scalars(select(IntegrationRun)):
        if not lo <= _started(run) < hi:
            continue
        runs.total += 1
        runs.no_owner += run.user_id is None
        runs.inactive_accounts += run.user_id in inactive
        if run.status in ("done", "failed", "review", "running"):
            setattr(runs, run.status, getattr(runs, run.status) + 1)
    return OverviewOut(
        start=start,
        end=end,
        timezone="한국 시간(KST, UTC+9)",
        generated_at=datetime.now(UTC).astimezone(KST),
        users=users,
        owners=owners,
        uploads=uploads,
        runs=runs,
    )


ACTION_LABEL = {
    "setup": "최초 설정",
    "signup": "가입 신청",
    "create": "계정 추가",
    "approve": "가입 승인",
    "reject": "가입 반려",
    "suspend": "이용 중지",
    "resume": "이용 재개",
    "password_reset": "비밀번호 초기화",
    "role_migrated": "역할 전환(관리자→일반)",
    "migration_check": "전환 확인 필요",
}
RESULT_LABEL = {"ok": "반영", "denied": "반영 안 됨"}


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    actor_name: str | None
    target_id: int | None
    target_name: str
    action: str
    action_label: str
    before_status: str | None
    after_status: str | None
    before_label: str | None
    after_label: str | None
    before_role: str | None
    after_role: str | None
    role_change: str | None
    reason: str | None
    result: str
    result_label: str


@router.get("/audit", response_model=list[AuditOut])
def audit(
    _: OwnerDep,
    db: Annotated[Session, Depends(get_session)],
    target_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[AuditOut]:
    """관리 이력 (최신 순). 비밀번호·쿠키·토큰은 기록하지 않는다."""
    stmt = select(AccountEvent).order_by(AccountEvent.id.desc()).limit(limit)
    if target_id is not None:
        stmt = stmt.where(AccountEvent.target_id == target_id)
    out = []
    for e in db.scalars(stmt):
        role_change = None
        if e.before_role != e.after_role and e.after_role is not None:
            role_change = (
                f"{role_label(e.before_role)} → {role_label(e.after_role)}"
                if e.before_role
                else role_label(e.after_role)
            )
        out.append(
            AuditOut(
                id=e.id,
                created_at=utc_to_kst(e.created_at),
                actor_name=e.actor_name,
                target_id=e.target_id,
                target_name=e.target_name,
                action=e.action,
                action_label=ACTION_LABEL.get(e.action, e.action),
                before_status=e.before_status,
                after_status=e.after_status,
                before_label=STATUS_LABEL.get(e.before_status or "", e.before_status),
                after_label=STATUS_LABEL.get(e.after_status or "", e.after_status),
                before_role=e.before_role,
                after_role=e.after_role,
                role_change=role_change,
                reason=e.reason,
                result=e.result,
                result_label=RESULT_LABEL.get(e.result, e.result),
            )
        )
    return out
