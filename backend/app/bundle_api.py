"""파일별 시트로 묶기 API: 결과 구성 확인(계산·저장 없음)과 결과 엑셀 만들기.
기존 통합(검토 → 하나의 통합표) API는 그대로다."""

from contextlib import nullcontext
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import CurrentUserDep
from app.bundle import BundleError, BundleIn, BundleRunIn, build_plan, make
from app.db import get_session
from app.integration_api import RunOut, runs_out
from app.output_slot import output_slot

router = APIRouter(prefix="/bundle", tags=["bundle"])


@router.post("/plan")
def bundle_plan(body: BundleIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> dict:
    """결과 시트 이름·순서·원본, 한도, (켰으면) 통계 요약의 연결·포함 범위를 확인한다. 파일을 만들지 않는다.
    통계 요약을 켰으면 평가가 메모리를 크게 써서 결과 만들기 차례를 쥔다 (없으면 OutputBusy — app/output_slot.py)."""
    with output_slot() if body.summary is not None else nullcontext():
        plan = build_plan(db, user, body)
        db.rollback()
        return plan.out()


@router.post("/run", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def bundle_run(body: BundleRunIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> RunOut:
    """확인한 구성 그대로일 때만 결과 엑셀을 만든다 (실행할 때마다 새 파일, 원본은 바꾸지 않음).
    결과 만들기 차례를 계획 확인부터 쥔다 — 없으면 실행 기록을 만들기 전에 OutputBusy (app/output_slot.py)."""
    with output_slot():
        return _bundle_run(body, db, user)


def _bundle_run(body: BundleRunIn, db: Session, user: CurrentUserDep) -> RunOut:
    plan = build_plan(db, user, body)
    db.rollback()
    if plan.basis != body.expected_basis:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": "결과 구성 확인 뒤 파일·시트·매핑·요약 조건이 바뀌었습니다 — 결과 구성을 다시 확인하세요.",
                "problems": [],
            },
        )
    if not plan.ready:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"message": "만들 수 없습니다 — 먼저 해결하세요.", "problems": plan.problems}
        )
    try:
        run = make(db, user, plan)
    except BundleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"message": str(exc), "problems": []}) from exc
    return runs_out(db, [run])[0]
