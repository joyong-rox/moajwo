"""U-03: 통계 결과에 표준 항목 이름('건수')뿐 아니라 계산한 원본 열('발신Mail건수')을
화면·Excel·실행 기록에 함께 남기는지.

원본 열 이름이 파일마다 다르면 모두 적고 확인을 안내하며, 가린 뒤 같아진 서로 다른 이름은 '(가림 구분 n)'으로 나눈다
(원래 이름은 내보내지 않음). 이 목록이 생기기 전에 저장된 결과는 저장된 입력으로 보여 준다.
"""

import io
import json
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import owner  # noqa: F401
from tests.test_analysis import _body, _db, _file, _preview, _run, _upload, _xlsx
from tests.test_auth import _client, app_real_login  # noqa: F401

MAP = {"시도": "sido", "발신Mail건수": "count"}
MAP2 = {"시도": "sido", "발신메일건수": "count"}  # 이름만 다른 같은 지표


@pytest.fixture(scope="module")
def sc(app_real_login, owner) -> TestClient:  # noqa: F811
    res = owner.post("/users", json={"username": "sc-user1", "display_name": "SC-USER1", "status": "active",
                                     "password": "pass-sc-user1"})  # fmt: skip
    assert res.status_code == 201, res.text
    return _client(app_real_login, "sc-user1", "pass-sc-user1")


def _count_body(fids: list[int], **kw: Any) -> dict[str, Any]:
    return {**_body(fids, **kw), "field_key": "count"}


def _sheet(content: bytes, name: str) -> list[list[Any]]:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    return [list(r) for r in wb[name].iter_rows(values_only=True)]


def test_analysis_names_the_source_column_everywhere(sc: TestClient) -> None:
    c = sc
    fid = _file(c, "sc_mail.xlsx", [["시도", "발신Mail건수"], ["서울특별시", 10], ["부산광역시", 20]], MAP)
    expect = [{"column": "발신Mail건수", "sheets": 1, "files": [fid]}]
    assert _preview(c, _count_body([fid]))["source_columns"] == expect  # 계산 전 대상 확인에서도 보임
    r = _run(c, _count_body([fid]))
    assert r["detail"]["conditions"]["field_label"] == "건수"
    assert r["detail"]["conditions"]["source_columns"] == expect
    past = {x["id"]: x for x in c.get("/analysis/runs").json()}
    assert past[r["id"]]["source_columns"] == ["발신Mail건수"]  # 지난 결과 목록
    cond = {row[0]: row[1] for row in _sheet(c.get(f"/analysis/runs/{r['id']}/download").content, "조건") if row[0]}
    assert cond["분석 항목 (표준 항목)"] == "건수"
    assert cond["원본 열 (계산한 지표)"] == "발신Mail건수 (시트 1개)"


def test_different_source_names_are_all_listed_with_a_check_note(sc: TestClient) -> None:
    c = sc
    a = _file(c, "sc_a.xlsx", [["시도", "발신Mail건수"], ["서울특별시", 10]], MAP)
    b = _file(c, "sc_b.xlsx", [["시도", "발신메일건수"], ["부산광역시", 20]], MAP2)
    r = _run(c, _count_body([a, b]))
    assert r["detail"]["conditions"]["source_columns"] == [
        {"column": "발신Mail건수", "sheets": 1, "files": [a]},
        {"column": "발신메일건수", "sheets": 1, "files": [b]},
    ]
    cond = {row[0]: row[1] for row in _sheet(c.get(f"/analysis/runs/{r['id']}/download").content, "조건") if row[0]}
    text = cond["원본 열 (계산한 지표)"]
    assert text.startswith("발신Mail건수 (시트 1개) · 발신메일건수 (시트 1개)") and "같은 지표인지" in text


def test_source_names_that_mask_the_same_are_numbered_without_raw_text(sc: TestClient) -> None:
    c = sc
    fids = []
    for raw, region in (("kim1@example.com", "서울특별시"), ("kim2@example.com", "부산광역시")):
        fid = _upload(c, f"sc_{raw[:4]}.xlsx", _xlsx([["시도", f"건수 {raw}"], [region, 10]]))
        # 미리보기의 열 이름은 가려져 오므로 열 번호로 확정한다
        items = [{"sheet_id": "s0", "column_id": "c0", "field_key": "sido"}, {"sheet_id": "s0", "column_id": "c1",
                                                                               "field_key": "count"}]  # fmt: skip
        assert c.put(f"/files/{fid}/mapping/decisions", json={"decisions": items}).status_code == 200
        fids.append(fid)
    pv = _preview(c, _count_body(fids))
    assert [x["column"] for x in pv["source_columns"]] == [
        "건수 k***@example.com (가림 구분 1)",
        "건수 k***@example.com (가림 구분 2)",
    ]
    assert "kim1@" not in json.dumps(pv, ensure_ascii=False) and "kim2@" not in json.dumps(pv, ensure_ascii=False)


def test_results_saved_before_the_list_show_columns_from_their_inputs(sc: TestClient) -> None:
    c = sc
    fid = _file(c, "sc_old.xlsx", [["시도", "발신Mail건수"], ["서울특별시", 10]], MAP)
    r = _run(c, _count_body([fid]))
    cond = json.loads(_db("SELECT conditions FROM analysis_runs WHERE id = ?", r["id"])[0][0])
    del cond["source_columns"]  # 이 목록을 저장하기 전에 만든 결과처럼
    stored = json.dumps(cond, ensure_ascii=False)
    _db("UPDATE analysis_runs SET conditions = ? WHERE id = ?", stored, r["id"])  # 앱과 같은 DB (SQLite·PostgreSQL)
    sheet = {row[0]: row[1] for row in _sheet(c.get(f"/analysis/runs/{r['id']}/download").content, "조건") if row[0]}
    assert sheet["원본 열 (계산한 지표)"] == "발신Mail건수 (시트 1개)"
    shown = c.get(f"/analysis/runs/{r['id']}").json()["conditions"]["source_columns"]  # 화면도 같은 규칙
    assert shown == [{"column": "발신Mail건수", "sheets": 1, "files": [fid]}]
    stored = json.loads(_db("SELECT conditions FROM analysis_runs WHERE id = ?", r["id"])[0][0])
    assert "source_columns" not in stored  # 보여 줄 때만 채우고 저장값은 그대로


def test_bundle_summary_names_the_source_column_in_plan_excel_and_run(sc: TestClient) -> None:
    c = sc
    a = _file(c, "sc_bd_a.xlsx", [["시도", "발신Mail건수"], ["서울특별시", 10], ["부산광역시", 20]], MAP)
    b = _file(c, "sc_bd_b.xlsx", [["시도", "발신메일건수"], ["대구광역시", 5]], MAP2)
    body = {"file_ids": [a, b], "summary": {"items": [{"field_key": "count", "aggregates": ["sum", "count"]}]}}
    plan = c.post("/bundle/plan", json=body).json()
    item = plan["summary"]["items"][0]
    assert item["field_label"] == "건수"
    assert [x["column"] for x in item["source_columns"]] == ["발신Mail건수", "발신메일건수"]
    res = c.post("/bundle/run", json={**body, "expected_basis": plan["basis"]})
    assert res.status_code == 201, res.text
    rows = _sheet(c.get(f"/runs/{res.json()['id']}/download").content, "통계 요약")
    at = next(i for i, r in enumerate(rows) if r[0] == "항목: 건수")
    assert rows[at + 1][0] == "원본 열 (계산한 지표)"
    assert rows[at + 1][1].startswith("발신Mail건수 (시트 1개) · 발신메일건수 (시트 1개)")
    saved = c.get(f"/runs/{res.json()['id']}").json()["summary"]["summary_items"][0]
    assert [x["column"] for x in saved["source_columns"]] == ["발신Mail건수", "발신메일건수"]
    assert saved["field_label"] == "건수"
