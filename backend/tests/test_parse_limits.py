"""파일 읽기 한도 (환경별 메모리 예산 — docs/MEMORY.md): 칸 수·한 칸 글자 수·동시에 읽는 파일 수.

한도를 넘는 입력은 메모리를 크게 쓰기 전에 거절하고, 읽기 차례를 얻지 못한 업로드는 기록·원본을 남기지 않는다
(입력 오류가 아니므로 '읽지 못한 파일'로 두지 않음). 시험은 한도만 낮춰 작은 파일로 본다.
"""

import csv
import io
import threading
import time

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import parsing
from app.config import DATA_DIR
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import SourceFile

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


def _csv(rows: list[list]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("utf-8")


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _state() -> tuple[int, list[str]]:
    with SessionLocal() as db:
        count = db.scalar(select(func.count(SourceFile.id)))
    return count, sorted(p.name for p in (DATA_DIR / "uploads").glob("*"))


ROWS = [["시도", "인구"], *[["서울특별시", i] for i in range(50)]]  # 102칸


def test_limits_are_reported_for_the_screen(client: TestClient) -> None:
    body = client.get("/files/limits").json()
    assert body["max_file_cells"] == parsing.MAX_FILE_CELLS and body["max_cell_chars"] == 32_767
    assert body["max_upload_mib"] >= 1 and body["max_xlsx_uncompressed_mib"] >= 1 and body["parse_slots"] >= 1


def test_too_many_cells_are_not_read(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parsing, "MAX_FILE_CELLS", 100)
    analyzed: list[str] = []
    real = parsing.analyze_sheet
    monkeypatch.setattr(parsing, "analyze_sheet", lambda *a, **k: analyzed.append(a[0]) or real(*a, **k))
    for name, data, ctype in (("많은칸.csv", _csv(ROWS), "text/csv"), ("많은칸.xlsx", _xlsx(ROWS), XLSX)):
        body = client.post("/files", files=[("files", (name, data, ctype))]).json()[0]
        assert "칸이 너무 많아 읽지 않았습니다" in body["error"] and "한도 100칸" in body["error"], body
    assert analyzed == []  # 행을 정리·분석하기 전에 멈춘다
    monkeypatch.setattr(parsing, "MAX_FILE_CELLS", 102)  # 경계: 한도와 같으면 읽는다
    body = client.post("/files", files=[("files", ("경계.csv", _csv(ROWS), "text/csv"))]).json()[0]
    assert body["error"] is None and body["data_rows"] == 50


def test_a_csv_cell_longer_than_excel_allows_is_not_read(client: TestClient) -> None:
    rows = [["시도", "설명"], ["서울특별시", "가" * 32_767], ["부산광역시", "나" * 32_768]]
    body = client.post("/files", files=[("files", ("긴칸.csv", _csv(rows), "text/csv"))]).json()[0]
    assert "3행에 32,767자가 넘는 칸" in body["error"], body


def test_a_busy_upload_leaves_no_record_or_file(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parsing, "PARSE_WAIT_S", 0.2)
    before = _state()
    assert parsing._PARSE_SLOTS.acquire(timeout=5)  # 다른 요청이 읽는 중인 것처럼 차례를 모두 잡아 둔다
    try:
        slots = parsing.PARSE_SLOTS
        for _ in range(slots - 1):
            assert parsing._PARSE_SLOTS.acquire(timeout=5)
        body = client.post("/files", files=[("files", ("대기.csv", _csv(ROWS), "text/csv"))]).json()[0]
    finally:
        for _ in range(slots):
            parsing._PARSE_SLOTS.release()
    assert body["id"] is None and "다른 파일을 읽는 중" in body["error"], body
    assert _state() == before  # 기록·원본 모두 남기지 않는다 — 다시 올리면 된다
    body = client.post("/files", files=[("files", ("대기.csv", _csv(ROWS), "text/csv"))]).json()[0]
    assert body["error"] is None  # 차례가 비면 그대로 읽는다


def test_reading_an_uploaded_file_while_busy_is_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fid = client.post("/files", files=[("files", ("나중.csv", _csv(ROWS), "text/csv"))]).json()[0]["id"]
    monkeypatch.setattr(parsing, "PARSE_WAIT_S", 0.2)
    slots = parsing.PARSE_SLOTS
    for _ in range(slots):
        assert parsing._PARSE_SLOTS.acquire(timeout=5)
    try:
        res = client.post(f"/files/{fid}/mapping?use_llm=false")
    finally:
        for _ in range(slots):
            parsing._PARSE_SLOTS.release()
    assert res.status_code == 503 and res.headers["retry-after"] == "10" and "다른 파일을 읽는 중" in res.text
    assert client.post(f"/files/{fid}/mapping?use_llm=false").status_code == 200


def test_different_files_are_not_parsed_at_the_same_time(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 파일 중복 읽기만이 아니라 서로 다른 파일도 동시에 PARSE_SLOTS개까지만 읽는다."""
    paths = []
    for i in range(3):
        path = tmp_path / f"f{i}.csv"
        path.write_bytes(_csv(ROWS))
        paths.append(path)
    running, peak = 0, 0
    lock = threading.Lock()
    real = parsing.analyze_sheet

    def slow(*a, **k):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.2)
        with lock:
            running -= 1
        return real(*a, **k)

    monkeypatch.setattr(parsing, "analyze_sheet", slow)
    threads = [threading.Thread(target=parsing.parse_file, args=(p, p.name)) for p in paths]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert peak == parsing.PARSE_SLOTS
