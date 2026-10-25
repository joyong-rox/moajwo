"""결과 만들기의 예기치 않은 실패를 알리는 문구와 서버 기록 (app/failures.py) — 원문(셀 값·SQL·매개변수·경로) 없이."""

import errno
import logging

import pytest
from sqlalchemy.exc import DataError

from app.failures import failure_reason, log_failure

PHONE = "010-1234-5678"


class OwnError(Exception):
    pass


def test_reason_keeps_only_the_kind_of_the_failure() -> None:
    db = DataError("INSERT INTO runs (error) VALUES (%(e)s)", {"e": PHONE}, Exception(f"bad value {PHONE}"))
    assert failure_reason(db) == "DB 오류 (Exception)"
    disk = OSError(errno.ENOSPC, "No space left on device", "/srv/moajwo/outputs/결과.xlsx")
    assert failure_reason(disk) == "파일 오류 (No space left on device)"  # 경로(filename)는 넣지 않는다
    assert failure_reason(ValueError(f"{PHONE} cannot be used in worksheets.")) == "예기치 않은 오류 (ValueError)"
    # 앱이 직접 만든 문구는 그대로
    assert failure_reason(OwnError("변경 기록 불일치: 칠한 셀 1개, 기록 2개"), (OwnError,)).startswith("변경 기록")


def test_server_log_has_the_kind_and_place_but_not_the_message(caplog: pytest.LogCaptureFixture) -> None:
    def cell() -> None:
        raise ValueError(f"담당 {PHONE} 셀 원문")

    try:
        try:
            cell()
        except ValueError as inner:
            raise RuntimeError(f"감싼 오류 {PHONE}") from inner
    except RuntimeError as exc:
        with caplog.at_level(logging.ERROR):
            log_failure(logging.getLogger("tests.failures"), "integration", 7, exc)
    assert "integration run 7 failed: RuntimeError <- ValueError at " in caplog.text
    assert "test_failures.py:" in caplog.text and " cell" in caplog.text  # 코드 위치 (파일·줄·함수)
    assert PHONE not in caplog.text and "원문" not in caplog.text and "감싼" not in caplog.text
