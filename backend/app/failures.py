"""결과 만들기(통합·묶기)의 예기치 않은 실패를 알리고 남기는 방법 — 원문(셀 값·SQL·매개변수·경로)을 넣지 않는다.

openpyxl·DB 드라이버의 예외 메시지에는 셀 값(개인정보 포함)·SQL 문장·매개변수가 그대로 들어 있다
(예: IllegalCharacterError '<셀 값> cannot be used in worksheets.'). 응답·실행 기록에는 앱이 직접 만든 문구만 그대로
쓰고, 그 밖에는 예외 종류만 쓴다. 서버 기록에도 메시지를 넣지 않고 예외 종류와 코드 위치(파일·줄·함수)만 남긴다 —
응답·실행 기록과 서버 기록은 실행 번호로 잇는다. 셀 값을 가려서 넣지도 않는다 (가림이 제어 문자가 섞인 값까지
안전하게 가린다고 보지 않음).
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError


def failure_reason(exc: BaseException, own: tuple[type[BaseException], ...] = ()) -> str:
    """응답·실행 기록에 쓸 실패 이유. own: 문구를 앱이 직접 만드는 예외 (그대로 쓴다)."""
    if isinstance(exc, own):
        return str(exc)
    if isinstance(exc, SQLAlchemyError):  # SQL 문장·매개변수 없이 종류·SQLSTATE만 (app/main.py와 같은 원칙)
        orig = getattr(exc, "orig", None)
        state = getattr(orig, "sqlstate", None)
        return f"DB 오류 ({type(orig or exc).__name__}{f' {state}' if state else ''})"
    if isinstance(exc, OSError) and exc.strerror:  # 운영체제 이유만 (경로는 filename에 따로 — 넣지 않음)
        return f"파일 오류 ({exc.strerror})"
    return f"예기치 않은 오류 ({type(exc).__name__})"


def _where(exc: BaseException, frames: int) -> str:
    """코드 위치: 예외가 지나온 마지막 몇 단계의 파일 이름·줄·함수 (서버 경로·값 없음)."""
    tb = traceback.extract_tb(exc.__traceback__)[-frames:]
    return " > ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in tb) or "-"


def log_failure(log: logging.Logger, what: str, run_id: int, exc: BaseException) -> None:
    """서버 기록: 예외 종류(원인 사슬)와 코드 위치만 — logger.exception은 메시지(원문)까지 남기므로 쓰지 않는다.
    다른 예외로 감싼 경우 처음 원인이 난 위치도 남긴다."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not c for c in chain) and len(chain) < 5:
        chain.append(current)
        current = current.__cause__ or current.__context__
    kinds = " <- ".join(type(e).__name__ for e in chain)
    origin = f" (원인 위치: {_where(chain[-1], 3)})" if len(chain) > 1 else ""
    log.error("%s run %s failed: %s at %s%s", what, run_id, kinds, _where(exc, 8), origin)
