"""D-01: 합계 항목의 산식 확인 — 같은 이름의 합계라도 자료마다 산식이 다를 수 있어 값으로 본다.

시연 자료의 통일부 '발신건수합계' 셀은 =SUM(F2:I2)로 Mail·외부에 시스템·대용량 발신까지 더하고, 다른 부처는 =E2+F2
(Mail+외부)다. 차이를 입력 오류로 단정하지 않고 어느 산식과 맞는지 근거를 보여 주며, 계산·검사는 막지 않는다.
"""

import io
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import analysis
from app.bundle import ItemIn, SummaryIn, double_count_notes
from app.formulas import check_totals
from app.validation import ValidationParams, validate
from tests.test_accounts import owner  # noqa: F401
from tests.test_analysis import _upload, _xlsx
from tests.test_auth import _client, app_real_login  # noqa: F401
from tests.test_mail_statistics import evaluate
from tests.test_validation import make_input, make_xlsx

# 통일부처럼: 발신건수합계 = 대용량 + 시스템 + Mail + 외부
UNIF = ["통계일자", "기관코드", "조직코드", "조직 명", "대용량파일전송발신수", "시스템발신수"]
UNIF += ["발신Mail건수", "발신외부건수", "발신건수합계"]
UNIF_ROWS = [
    ["2026-10-20", 1250000, 1250370, "운영지원과", 3, 10, 600, 80, 693],
    ["2026-10-20", 1250000, 1250663, "운영지원과", 1, 4, 350, 50, 405],
]
# 다른 부처처럼: 발신건수합계 = Mail + 외부
PLAIN = ["날짜", "기관코드", "조직코드", "조직명", "발신Mail건수", "발신외부건수", "발신건수합계"]
PLAIN_ROWS = [
    ["2026-10-20", 1741000, 1741001, "기획조정실", 100, 20, 120],
    ["2026-10-20", 1741000, 1741002, "운영지원과", 50, 5, 55],
]
UNIF_COLS = {"mail_large_file_sent_count": 4, "mail_system_sent_count": 5, "mail_sent_count": 6,
             "mail_external_sent_count": 7, "mail_sent_total_count": 8}  # fmt: skip


def test_check_totals_names_the_formula_the_values_follow() -> None:
    [check] = [c for c in check_totals(list(enumerate(UNIF_ROWS, 2)), UNIF_COLS) if c.total == "mail_sent_total_count"]
    assert check.checked == 2 and [row for row, _ in check.differ] == [2, 3] and check.difference == 18
    assert check.alternative == ("mail_all_sent_total_count", 2)


def test_missing_parts_and_unreadable_cells_are_not_guessed() -> None:
    # 구성 항목 열이 하나라도 없으면 그 합계는 보지 않는다
    assert check_totals([(2, [1, 2, 3])], {"mail_sent_total_count": 2, "mail_sent_count": 0}) == []
    # 숫자로 읽을 수 없는 칸이 있는 행은 비교하지 않는다 (0으로 보지 않음)
    cols = {"mail_sent_count": 0, "mail_external_sent_count": 1, "mail_sent_total_count": 2}
    [check] = check_totals([(2, [100, "-", 999]), (3, [50, 5, 55])], cols)
    assert check.checked == 1 and not check.differ


def test_validation_warns_only_where_the_formula_differs(tmp_path: Path) -> None:
    unif = make_input(make_xlsx(tmp_path / "unif.xlsx", [UNIF, *UNIF_ROWS]), 1)
    plain = make_input(make_xlsx(tmp_path / "plain.xlsx", [PLAIN, *PLAIN_ROWS]), 2)
    result = validate([unif, plain], ValidationParams())
    found = [w for w in result.warnings if w.code == "formula_mismatch"]
    assert [w.file_id for w in found] == [1]  # 산식이 맞는 자료는 경고하지 않는다
    assert "2행이 다릅니다" in found[0].message and "'발신전체건수합계'(으)로 확정할지" in found[0].message
    assert (found[0].expected, found[0].actual) == (1080, 1098)  # 구성 항목 합 → 합계 열 값 (차이 18)
    assert result.status != "blocked"  # 경고일 뿐 통합을 막지 않는다


def test_analysis_warns_but_computes_the_confirmed_values() -> None:
    ev = evaluate([(UNIF, UNIF_ROWS, {}), (PLAIN, PLAIN_ROWS, {})], "mail_sent_total_count")
    notes = [w for w in ev.warnings if "산식" in w]
    assert len(notes) == 1 and "#1" in notes[0] and "'발신전체건수합계'(으)로 확정할지" in notes[0]
    assert analysis.compute(ev)["groups"][0]["sum"] == 693 + 405 + 120 + 55  # 확정한 값 그대로 (고치지 않음)


def test_confirming_the_wider_total_for_that_file_matches_its_formula() -> None:
    # 파일별 사용자 확정으로 통일부 열만 '발신전체건수합계'로 — 그 산식과 맞으므로 알릴 것이 없다
    ev = evaluate([(UNIF, UNIF_ROWS, {"발신건수합계": "mail_all_sent_total_count"})], "mail_all_sent_total_count")
    assert not [w for w in ev.warnings if "산식" in w]
    assert not [w for w in evaluate([(PLAIN, PLAIN_ROWS, {})], "mail_sent_total_count").warnings if "산식" in w]


def test_summary_notes_double_counting_of_a_total_and_its_parts() -> None:
    items = [ItemIn(field_key="mail_sent_total_count", aggregates=["sum"]), ItemIn(field_key="mail_sent_count",
                                                                                   aggregates=["sum"])]  # fmt: skip
    [note] = double_count_notes(SummaryIn(items=items))
    assert "'발신Mail건수'" in note and "두 번" in note
    nested = [ItemIn(field_key="mail_all_sent_total_count", aggregates=["sum"]),
              ItemIn(field_key="mail_sent_total_count", aggregates=["sum"])]  # fmt: skip
    assert any("'발신건수합계'" in n for n in double_count_notes(SummaryIn(items=nested)))  # 합계 안의 합계
    alone = [ItemIn(field_key="mail_sent_count", aggregates=["sum"]), ItemIn(field_key="mail_received_count",
                                                                             aggregates=["sum"])]  # fmt: skip
    assert double_count_notes(SummaryIn(items=alone)) == []


@pytest.fixture(scope="module")
def fm(app_real_login, owner) -> TestClient:  # noqa: F811
    res = owner.post("/users", json={"username": "fm-user1", "display_name": "FM-USER1", "status": "active",
                                     "password": "pass-fm-user1"})  # fmt: skip
    assert res.status_code == 201, res.text
    return _client(app_real_login, "fm-user1", "pass-fm-user1")


def _confirm_all(c: TestClient, fid: int) -> None:
    """자동 제안을 그대로 확정한다 (시연처럼 모든 열을 확정 — 분석은 확정한 매핑만 쓴다)."""
    cols = c.post(f"/files/{fid}/mapping?use_llm=false").json()["sheets"][0]["columns"]
    items = [{"sheet_id": "s0", "column_id": x["column_id"], "field_key": x["field_key"]} for x in cols]
    assert c.put(f"/files/{fid}/mapping/decisions", json={"decisions": items}).status_code == 200


def test_bundle_summary_shows_both_notes_in_the_plan_and_the_sheet(fm: TestClient) -> None:
    c = fm
    sources = (("unif.xlsx", [UNIF, *UNIF_ROWS]), ("plain.xlsx", [PLAIN, *PLAIN_ROWS]))
    fids = [_upload(c, name, _xlsx(rows, title=None)) for name, rows in sources]
    for fid in fids:
        _confirm_all(c, fid)
    links = [{"file_id": f, "sheet_id": "s0", "unit": "건"} for f in fids]
    keys = ("mail_sent_total_count", "mail_sent_count")  # 합계와 그 구성 항목을 함께 고름
    items = [{"field_key": k, "aggregates": ["sum"], "links": links} for k in keys]
    body = {"file_ids": fids, "summary": {"items": items}}
    plan = c.post("/bundle/plan", json=body).json()
    assert any("두 번 셉니다" in w for w in plan["warnings"]), plan["warnings"]
    assert any("산식" in w for w in plan["summary"]["items"][0]["warnings"])
    res = c.post("/bundle/run", json={**body, "expected_basis": plan["basis"]})
    assert res.status_code == 201, res.text
    wb = openpyxl.load_workbook(io.BytesIO(c.get(f"/runs/{res.json()['id']}/download").content))
    notes = [r[1] for r in wb["통계 요약"].iter_rows(values_only=True) if r[0] == "주의"]
    assert any("두 번 셉니다" in n for n in notes) and any("산식" in n for n in notes)
