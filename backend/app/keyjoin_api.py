"""공통 key 연결 API: 연결 확인(plan — 읽기 전용)과 결과 엑셀 만들기(run — 확인한 구성 그대로일 때만).
실행 기록·내려받기는 기존 /runs*, 시트·열 목록은 기존 GET /files/{id}/preview를 쓴다."""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, CurrentUserDep
from app.db import get_session
from app.integration_api import RunOut, runs_out
from app.keyjoin import MODE, REQUEST_LOOKBACK, KeyJoinError, KeyJoinIn, KeyJoinRunIn, build_plan, make
from app.models import IntegrationRun
from app.output_slot import output_slot

router = APIRouter(prefix="/keyjoin", tags=["keyjoin"])


def _conflict(code: str, message: str, problems: list[str] | None = None, run_id: int | None = None) -> HTTPException:
    detail: dict[str, Any] = {"message": message, "problems": problems or [], "code": code}
    if run_id is not None:
        detail["run_id"] = run_id
    return HTTPException(status.HTTP_409_CONFLICT, detail)


@router.post("/plan")
def keyjoin_plan(body: KeyJoinIn, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep) -> dict:
    """입력·key 열·가져올 열·범위로 결과 열 이름, 한도, key 문제, 연결 수, 미리보기를 확인한다. 파일을 만들지 않는다.
    입력 전체를 읽어 key 인덱스를 만들므로 결과 만들기 차례를 쥔다 (없으면 OutputBusy — app/output_slot.py)."""
    with output_slot():
        plan = build_plan(db, user, body)
        db.rollback()
        return plan.out()


@router.post("/run", response_model=RunOut, status_code=status.HTTP_201_CREATED)
def keyjoin_run(
    body: KeyJoinRunIn, response: Response, db: Annotated[Session, Depends(get_session)], user: CurrentUserDep
) -> RunOut:
    """확인한 구성 그대로일 때만 결과 엑셀을 만든다 (201). 같은 요청 번호(request_id)의 재전송은 새로 만들지 않고
    그 실행을 200으로 돌려준다. 결과 만들기 차례를 계획 확인부터 쥔다 — 없으면 실행 기록을 만들기 전에 OutputBusy."""
    with output_slot():
        return _keyjoin_run(body, response, db, user)


def _find_request(db: Session, user: CurrentUser, request_id: str) -> tuple[IntegrationRun, dict[str, Any]] | None:
    """이 사용자의 최근 실행에서 같은 요청 번호의 key 연결 실행 (요약 JSON의 request_id)."""
    stmt = select(IntegrationRun).where(IntegrationRun.user_id == user.id)
    for run in db.scalars(stmt.order_by(IntegrationRun.id.desc()).limit(REQUEST_LOOKBACK)):
        try:
            summary = json.loads(run.summary or "{}")
        except ValueError:
            continue
        if isinstance(summary, dict) and summary.get("mode") == MODE and summary.get("request_id") == request_id:
            return run, summary
    return None


def _keyjoin_run(body: KeyJoinRunIn, response: Response, db: Session, user: CurrentUser) -> RunOut:
    found = _find_request(db, user, body.request_id)  # 싼 검사 — 파싱 전
    if found is not None:
        run, summary = found
        if run.status == "failed":
            raise _conflict(
                "KEYJOIN_REQUEST_USED",
                "이 요청 번호의 실행은 실패로 끝났습니다 — 다시 확인한 뒤 새로 만드세요.",
                run_id=run.id,
            )
        if summary.get("basis") != body.expected_basis:
            raise _conflict(
                "KEYJOIN_REQUEST_MISMATCH",
                "같은 요청 번호로 다른 설정을 보냈습니다 — 연결을 다시 확인한 뒤 새로 만드세요.",
                run_id=run.id,
            )
        response.status_code = status.HTTP_200_OK  # 같은 요청의 재전송 — 새 실행 없음
        return runs_out(db, [run])[0]
    plan = build_plan(db, user, body, keep_values=True)
    db.rollback()
    if plan.basis != body.expected_basis:
        raise _conflict(
            "KEYJOIN_BASIS_CHANGED", "연결 확인 뒤 파일·시트·열·key·범위·이름이 바뀌었습니다 — 다시 확인하세요."
        )
    if not plan.ready:
        raise _conflict("KEYJOIN_NOT_READY", "만들 수 없습니다 — 먼저 해결하세요.", [p.text for p in plan.blocks])
    try:
        run = make(db, user, plan, body.request_id)
    except KeyJoinError as exc:
        raise _conflict("KEYJOIN_FAILED", str(exc), run_id=exc.run_id) from exc
    return runs_out(db, [run])[0]
