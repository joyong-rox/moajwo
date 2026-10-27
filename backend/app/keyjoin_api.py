"""공통 key 연결 API: 연결 확인(plan — 읽기 전용)과 결과 엑셀 만들기(run — 확인한 구성 그대로일 때만).
실행 기록·내려받기는 기존 /runs*, 시트·열 목록은 기존 GET /files/{id}/preview를 쓴다."""

import threading
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser, CurrentUserDep
from app.db import get_session
from app.integration_api import RunOut, runs_out
from app.keyjoin import (
    MODE,
    KeyJoinError,
    KeyJoinIn,
    KeyJoinRunIn,
    RequestTaken,
    build_plan,
    check_request,
    make,
    request_hash,
)
from app.models import IntegrationRun
from app.output_slot import OutputBusy, output_slot

router = APIRouter(prefix="/keyjoin", tags=["keyjoin"])

# 이 프로세스에서 만드는 중인 (사용자, 요청 번호). 같은 번호의 동시 둘째는 차례 수와 상관없이 시작하지 않는다 —
# 차례가 2개 이상이면 둘 다 '같은 번호 없음'을 읽고 실행·파일을 둘 만들 수 있기 때문. 다른 프로세스와는 나누지 않는다
# (worker 여럿이면 runs의 요청 번호 고유 색인이 필요 — docs/KEYJOIN.md).
_IN_FLIGHT: set[tuple[int, str]] = set()
_IN_FLIGHT_LOCK = threading.Lock()
IN_FLIGHT_BUSY = (
    "같은 요청 번호의 결과를 이 서버가 이미 만드는 중입니다 — 먼저 보낸 요청의 응답을 기다리세요 "
    "(이 요청은 시작하지 않았고 실행 기록·파일을 만들지 않았습니다)."
)


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
    """확인한 구성 그대로일 때만 결과 엑셀을 만든다 (201). 같은 요청 번호(request_id)는 사용자별로 실행 하나다 —
    DB에서 찾아(최근 몇 건이 아니라 전부, 고유 색인) 요청 내용 해시가 같으면 그 실행을 200으로 돌려주고(완료·실행 중·
    결과 확인 필요 — 상태 그대로, 완료로 바꾸지 않음), 다르면 409, 실패로 끝난 번호는 409. 결과 만들기 차례를 계획
    확인부터 쥔다 — 없으면 실행 기록을 만들기 전에 OutputBusy. 같은 번호가 이 프로세스에서 만들어지는 중이면 둘째는
    503(output_busy)으로 시작하지 않는다. 다른 프로세스가 같은 번호를 동시에 보내도 실행 행을 넣는 순간 고유 색인이
    하나만 남긴다 — 늦은 쪽은 파일을 만들지 않고 그 실행을 돌려받는다."""
    mark = (user.id, body.request_id)
    with _IN_FLIGHT_LOCK:
        if mark in _IN_FLIGHT:
            raise OutputBusy(IN_FLIGHT_BUSY)
        _IN_FLIGHT.add(mark)
    try:
        with output_slot():
            return _keyjoin_run(body, response, db, user)
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.discard(mark)


def _find_request(db: Session, user: CurrentUser, request_id: str) -> IntegrationRun | None:
    """이 사용자의 같은 요청 번호 key 연결 실행 — 고유 색인(user_id, request_kind, request_id)으로 전부에서 찾는다."""
    stmt = select(IntegrationRun).where(
        IntegrationRun.user_id == user.id, IntegrationRun.request_kind == MODE, IntegrationRun.request_id == request_id
    )
    return db.scalars(stmt).first()


def _answer(db: Session, run: IntegrationRun, key: str, response: Response) -> RunOut:
    """같은 번호의 실행이 이미 있을 때: 내용이 같으면 그 실행(200 — 상태 그대로), 다르면·비교할 수 없으면·실패면 409.
    어느 경우든 새 실행·결과 파일을 만들지 않고, 결과 파일이 없어진 완료 실행을 다시 만들지도 않는다(내려받기는 410)."""
    if run.request_hash is None:
        raise _conflict(
            "KEYJOIN_REQUEST_LEGACY",
            "이 요청 번호는 이전 판에서 만든 실행이라 요청 내용을 비교할 수 없습니다 — 연결을 다시 확인한 뒤 새로 "
            "만드세요.",
            run_id=run.id,
        )
    if run.request_hash != key:
        raise _conflict(
            "KEYJOIN_REQUEST_MISMATCH",
            "같은 요청 번호로 다른 설정(파일·시트·key 열·가져올 열·결과 이름·범위·기준 입력)을 보냈습니다 — "
            "연결을 다시 확인한 뒤 새로 만드세요.",
            run_id=run.id,
        )
    if run.status == "failed":
        raise _conflict(
            "KEYJOIN_REQUEST_USED",
            "이 요청 번호의 실행은 실패로 끝났습니다 — 다시 확인한 뒤 새로 만드세요.",
            run_id=run.id,
        )
    response.status_code = status.HTTP_200_OK  # 같은 요청의 재전송 — 새 실행 없음 (완료가 아니면 상태를 그대로 알린다)
    return runs_out(db, [run])[0]


def _keyjoin_run(body: KeyJoinRunIn, response: Response, db: Session, user: CurrentUser) -> RunOut:
    check_request(body)  # 형식 검사(파일·DB 없음)를 재전송 조회보다 먼저 — 재전송도 형식이 맞아야 200
    key = request_hash(body)  # 요청 내용 — 화면이 보낸 expected_basis가 아니라 서버가 계산한 값으로 비교
    found = _find_request(db, user, body.request_id)  # 싼 검사 — 파싱 전 (완료된 같은 요청은 원본을 다시 읽지 않음)
    if found is not None:
        return _answer(db, found, key, response)
    db.rollback()
    plan = build_plan(db, user, body, keep_values=True)
    db.rollback()
    if plan.basis != body.expected_basis:
        raise _conflict(
            "KEYJOIN_BASIS_CHANGED", "연결 확인 뒤 파일·시트·열·key·범위·이름이 바뀌었습니다 — 다시 확인하세요."
        )
    if not plan.ready:
        raise _conflict("KEYJOIN_NOT_READY", "만들 수 없습니다 — 먼저 해결하세요.", [p.text for p in plan.blocks])
    try:
        run = make(db, user, plan, body.request_id, key)
    except RequestTaken:
        found = _find_request(db, user, body.request_id)  # 다른 프로세스가 같은 번호로 먼저 실행 행을 넣었다
        if found is None:
            raise
        return _answer(db, found, key, response)
    except KeyJoinError as exc:
        raise _conflict("KEYJOIN_FAILED", str(exc), run_id=exc.run_id) from exc
    return runs_out(db, [run])[0]
