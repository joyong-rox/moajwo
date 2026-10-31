"""D-02: 기관코드와 조직코드로 대상을 구분하는지 — 같은 이름만으로 합치지 않고, 실제 중복은 그대로 확인받는다.

시연(통일부 '운영지원과' 두 행: 조직코드 1250370·680건, 1250663·400건)과 필수 사례(같은 코드 반복, 코드 결측,
다른 파일의 같은 대상, 조직코드를 매핑하지 않은 시트)를 엔진으로 확인한다.
"""

import io

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import analysis
from tests.test_accounts import owner  # noqa: F401
from tests.test_analysis import _file, _preview, _run
from tests.test_auth import _client, _provision_user, app_real_login  # noqa: F401
from tests.test_mail_statistics import evaluate

COLS = ["통계일자", "기관코드", "조직코드", "조직 명", "발신Mail건수"]


def _status(ev):
    return [(t.status, t.repeated["rows"] if t.repeated else 0) for t in ev.targets]


def test_same_name_with_different_org_codes_is_two_targets():
    rows = [["2026-10-20", 1250000, 1250370, "운영지원과", 680], ["2026-10-20", 1250000, 1250663, "운영지원과", 400]]
    ev = evaluate([(COLS, rows, {})], "mail_sent_count")
    assert _status(ev) == [("ok", 0)]
    assert analysis.compute(ev)["groups"][0]["sum"] == 1080


def test_the_same_org_code_twice_still_needs_confirmation():
    rows = [["2026-10-20", 1250000, 1250370, "운영지원과", 680], ["2026-10-20", 1250000, 1250370, "운영지원과", 400]]
    ev = evaluate([(COLS, rows, {})], "mail_sent_count")
    assert _status(ev) == [("needs_input", 2)]


def test_missing_codes_fall_back_to_the_other_identity_values():
    same = [["2026-10-20", 1250000, None, "운영지원과", 680], ["2026-10-20", 1250000, None, "운영지원과", 400]]
    assert _status(evaluate([(COLS, same, {})], "mail_sent_count")) == [("needs_input", 2)]
    other = [["2026-10-20", 1250000, None, "운영지원과", 680], ["2026-10-20", 1250000, None, "기획조정실", 400]]
    assert _status(evaluate([(COLS, other, {})], "mail_sent_count")) == [("ok", 0)]


def test_the_same_org_code_in_two_files_is_an_overlap():
    a = [["2026-10-20", 1250000, 1250370, "운영지원과", 680]]
    b = [["2026-10-20", 1250000, 1250370, "운영지원과", 680]]
    ev = evaluate([(COLS, a, {}), (COLS, b, {})], "mail_sent_count")
    assert any("같은 대상" in p for p in ev.problems)


def test_an_unmapped_org_code_column_keeps_the_repeat():
    rows = [["2026-10-20", 1250000, 1250370, "운영지원과", 680], ["2026-10-20", 1250000, 1250663, "운영지원과", 400]]
    ev = evaluate([(COLS, rows, {"조직코드": None})], "mail_sent_count")  # 시연처럼 '해당 없음'으로 확정
    assert _status(ev) == [("needs_input", 2)]


# ---------------------------------------------------------------- 실제 업로드·확정·대상 확인·실행 (API)

DEMO = {"통계일자": "base_date", "기관코드": "org_code", "조직코드": None, "조직 명": "organization_name",
        "발신Mail건수": "mail_sent_count"}  # fmt: skip
TWO = [COLS, ["2026-10-20", 1250000, 1250370, "운영지원과", 680], ["2026-10-20", 1250000, 1250663, "운영지원과", 400]]


@pytest.fixture(scope="module")
def oc(app_real_login, owner) -> TestClient:  # noqa: F811
    _provision_user(owner, app_real_login, "oc-user1", "pass-oc-user1", "OC-USER1")
    return _client(app_real_login, "oc-user1", "pass-oc-user1")


def _body(fid: int, **target) -> dict:
    return {"field_key": "mail_sent_count", "targets": [{"file_id": fid, "sheet_id": "s0", "unit": "건", **target}]}


def test_repeat_message_points_to_the_org_code_column_left_unmapped(oc: TestClient) -> None:
    fid = _file(oc, "oc_demo.xlsx", TWO, DEMO, title=None)
    reason = " ".join(_preview(oc, _body(fid))["targets"][0]["reasons"])
    assert "반복" in reason and "'조직코드' 열 → '조직코드'" in reason


def test_confirmed_different_records_keep_their_evidence(oc: TestClient) -> None:
    fid = _file(oc, "oc_records.xlsx", TWO, DEMO, title=None)
    r = _run(oc, _body(fid, rows_are_records=True))
    assert r["groups"][0]["sum"] == 1080
    assert r["detail"]["conditions"]["records"] == [f"#{fid} 현황 (같은 대상 1개 2행 — 값 같음 0, 다름 1)"]


def test_mapping_the_org_code_needs_no_confirmation(oc: TestClient) -> None:
    fid = _file(oc, "oc_mapped.xlsx", TWO, {**DEMO, "조직코드": "organization_code"}, title=None)
    pv = _preview(oc, _body(fid))
    assert pv["targets"][0]["status"] == "ok" and not pv["problems"]


def test_leading_zero_and_long_codes_stay_as_written(oc: TestClient) -> None:
    long_code = "123456789012345678"  # 15자리를 넘는 식별자 — 숫자로 바꾸면 끝자리가 바뀐다
    rows = [
        COLS,
        ["2026-10-20", "0012000", "0012345", "운영지원과", 10],
        ["2026-10-20", "0012000", long_code, "기획조정실", 20],
    ]
    fid = _file(oc, "oc_zero.xlsx", rows, {**DEMO, "조직코드": "organization_code"}, title=None)
    r = _run(oc, {**_body(fid), "group_by": ["organization_code"]})
    assert sorted(g["keys"][0] for g in r["groups"]) == ["0012345", long_code]
    plan = oc.post("/bundle/plan", json={"file_ids": [fid]}).json()
    res = oc.post("/bundle/run", json={"file_ids": [fid], "expected_basis": plan["basis"]})
    assert res.status_code == 201, res.text
    wb = openpyxl.load_workbook(io.BytesIO(oc.get(f"/runs/{res.json()['id']}/download").content))
    cells = [c for row in wb.worksheets[0].iter_rows() for c in row if c.value in ("0012000", "0012345", long_code)]
    assert len(cells) == 4 and {c.data_type for c in cells} == {"s"}  # 글자 칸 그대로 (선행 0·자릿수 보존)
