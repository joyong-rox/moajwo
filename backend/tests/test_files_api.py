import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import DATA_DIR
from app.db import Base, engine
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


def test_upload_multiple(client: TestClient) -> None:
    csv_bytes = '이름,금액\n김,"1,000"\n합계,"1,000"\n'.encode("euc-kr")
    res = client.post(
        "/files",
        files=[
            ("files", ("members.xls", (FIXTURES / "members.xls").read_bytes(), "application/vnd.ms-excel")),
            ("files", ("거래.csv", csv_bytes, "text/csv")),
            ("files", ("memo.txt", b"hello", "text/plain")),
        ],
    )
    assert res.status_code == 200
    xls, csv, txt = res.json()

    assert xls["error"] is None and xls["sheet_count"] == 1 and xls["data_rows"] == 4
    assert xls["sheets"][0]["header_row"] == 3
    assert csv["data_rows"] == 1 and csv["encoding"] == "euc-kr"
    assert csv["sheets"][0]["name"] == "거래"  # 저장용 UUID가 아닌 원래 파일명
    assert txt["error"] and txt["id"] is None


def test_original_saved_read_only_with_new_name(client: TestClient) -> None:
    data = (FIXTURES / "members.xls").read_bytes()
    res = client.post("/files", files=[("files", ("../../evil.xls", data, "application/vnd.ms-excel"))])
    body = res.json()[0]
    assert body["original_name"] == "evil.xls"

    stored = list((DATA_DIR / "uploads").glob("*.xls"))
    assert stored, "원본이 uploads 아래에 저장되어야 한다"
    for path in stored:
        assert path.read_bytes() == data
        assert not os.access(path, os.W_OK)
        assert path.name != "evil.xls"


def test_mapping_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import files
    from app.mapping import MappingEngine, load_dictionary

    calls: list[str] = []

    def fake_llm(system: str, user: str, schema: dict) -> dict:
        calls.append(user)
        return {"key": "none", "confidence": 0, "reason": "해당 없음"}

    monkeypatch.setattr(files, "get_engine", lambda: MappingEngine(load_dictionary(), None, fake_llm))
    up = client.post(
        "/files",
        files=[("files", ("members.xls", (FIXTURES / "members.xls").read_bytes(), "application/vnd.ms-excel"))],
    )
    file_id = up.json()[0]["id"]

    res = client.post(f"/files/{file_id}/mapping")
    assert res.status_code == 200
    body = res.json()
    cols = {c["column"]: c for c in body["sheets"][0]["columns"]}
    assert set(cols) == {"지역", "이름", "가입일", "회비"}
    assert cols["지역"]["field_key"] == "region" and cols["지역"]["status"] == "auto"
    assert cols["지역"]["samples"] == ["서울", "부산"]
    assert body["embedding"] == "skipped" and body["llm"] == "ok"
    assert all(len(c["candidates"]) == 3 for c in cols.values())
    # LLM에는 미해결 열만 간다
    assert len(calls) == sum(1 for c in cols.values() if c["method"] == "llm")

    assert client.post("/files/999999/mapping").status_code == 404


def test_corrupt_file_reports_error(client: TestClient) -> None:
    res = client.post("/files", files=[("files", ("broken.xlsx", b"not a zip", "application/octet-stream"))])
    body = res.json()[0]
    assert body["error"] and body["id"] is not None
