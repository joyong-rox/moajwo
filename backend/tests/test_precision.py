"""C-02: 숫자 정밀도 계약 (docs/NUMBERS.md) — 합계·평균·최솟값·최댓값을 정확한 수로 계산하고,
담을 수 없는 경계를 알린다.

- 정수는 정수 그대로 더한다 (2^53을 넘어도 끝자리까지). 소수는 원본에 적힌 자릿수로 더한다 (0.1 + 0.2 = 0.3).
- 평균은 정확한 합 ÷ 유효한 숫자 개수를 배정밀도로 돌려준다.
- 2^53을 넘는 정수가 있으면 화면(브라우저)이 끝자리를 정확히 보이지 못한다고 알리고,
  Excel에는 16자리 이상을 글자로 적는다.
- xlsx 숫자 칸은 배정밀도라 2^53을 넘는 정수를 담지 못한다 (쓰는 순간 바뀜) — 그런 값은 CSV·글자 칸으로만 들어온다.
- 결측·비공개 표기는 0으로 바꾸지 않고 뺀다. 음수와 0은 유효한 숫자다.
"""

import io
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import owner  # noqa: F401
from tests.test_analysis import POP, _body, _confirm, _file, _run
from tests.test_auth import _client, _provision_user, app_real_login  # noqa: F401

MAP = {"시도": "sido", "인구": "population"}
CRLF = chr(13) + chr(10)
BIG = 2**53 + 1  # 9007199254740993 — 배정밀도로 바꾸면 ...992가 된다


@pytest.fixture(scope="module")
def pr(app_real_login, owner) -> TestClient:  # noqa: F811
    _provision_user(owner, app_real_login, "pr-user1", "pass-pr-user1", "PR-USER1")
    return _client(app_real_login, "pr-user1", "pass-pr-user1")


def _rows(values: list[Any]) -> list[list[Any]]:
    regions = ["서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시"]
    return [["시도", "인구"], *[[regions[i], v] for i, v in enumerate(values)]]


def _csv(c: TestClient, name: str, values: list[Any]) -> int:
    """글자(CSV)로 올린다 — xlsx 숫자 칸은 2^53을 넘는 정수를 담지 못한다."""
    rows = [[POP], *_rows(values)]  # xlsx 도우미처럼 기간·단위를 알리는 제목 줄
    text = CRLF.join(",".join(str(x) for x in row) for row in rows).encode("utf-8")
    res = c.post("/files", files=[("files", (name, text, "text/csv"))])
    assert res.status_code == 200, res.text
    fid = res.json()[0]["id"]
    _confirm(c, fid, MAP)
    return fid


def _whole(c: TestClient, name: str, values: list[Any], **body: Any) -> dict:
    fid = _csv(c, name, values) if name.endswith(".csv") else _file(c, name, _rows(values), MAP)
    r = _run(c, _body([fid], **body))
    [g] = r["groups"]
    return {**g, "id": r["id"], "warnings": r["detail"]["conditions"]["warnings"]}


def _excel(c: TestClient, rid: int) -> dict[str, list[list[Any]]]:
    wb = openpyxl.load_workbook(io.BytesIO(c.get(f"/analysis/runs/{rid}/download").content))
    return {ws.title: [[cell for cell in row] for row in ws.iter_rows()] for ws in wb.worksheets}


def test_decimals_add_as_written(pr: TestClient) -> None:
    g = _whole(pr, "p_decimal.xlsx", [0.1, 0.2])
    assert g["sum"] == 0.3 and g["mean"] == 0.15  # 0.30000000000000004가 아님
    g = _whole(pr, "p_decimal2.xlsx", [10.4, 20.4])
    assert g["sum"] == 30.8  # 배정밀도 합은 30.799999999999997


def test_a_bin_boundary_is_not_missed_by_binary_error(pr: TestClient) -> None:
    low = {"label": "0.3 이하", "max": 0.3, "max_inclusive": True}
    bins = {"on": "sum", "ranges": [low, {"label": "초과", "min": 0.3, "min_inclusive": False}]}
    g = _whole(pr, "p_bins.xlsx", [0.1, 0.2], bins=bins)
    assert g["bin"] == "0.3 이하"


def test_integers_beyond_2_53_are_exact_and_flagged(pr: TestClient) -> None:
    g = _whole(pr, "p_big.csv", [BIG, 1])
    assert g["sum"] == BIG + 1 == 9007199254740994 and isinstance(g["sum"], int)
    assert g["max"] == BIG and g["min"] == 1
    assert any("2^53" in w for w in g["warnings"])
    sheets = _excel(pr, g["id"])
    result = sheets["결과"][1]
    assert result[0].value == "9007199254740994" and result[0].data_type == "s"  # 글자로 자릿수 보존
    detail = [row[-1] for row in sheets["근거 행"][1:]]
    assert sorted(str(c.value) for c in detail) == ["1", "9007199254740993"]


def test_sixteen_digit_values_keep_their_digits_in_excel(pr: TestClient) -> None:
    g = _whole(pr, "p_16.xlsx", [1234567890123456, 4])
    assert g["sum"] == 1234567890123460 and not any("2^53" in w for w in g["warnings"])  # 2^53 안 — 화면도 정확
    cell = _excel(pr, g["id"])["결과"][1][0]
    assert cell.value == "1234567890123460" and cell.data_type == "s"  # Excel 숫자 칸은 15자리까지만 정확


def test_one_won_differences_survive(pr: TestClient) -> None:
    g = _whole(pr, "p_won.xlsx", [1_000_000_000_001, 999_999_999_999, 500])
    assert g["sum"] == 2_000_000_000_500 and g["min"] == 500 and g["max"] == 1_000_000_000_001


def test_negative_zero_missing_and_hidden_values(pr: TestClient) -> None:
    g = _whole(pr, "p_mixed.xlsx", [-5, 0, "-", "x", 10])
    assert (g["sum"], g["count"], g["min"], g["max"]) == (5, 3, -5, 10)
    assert g["partial"] and g["problem_rows"] == 2  # 결측·비공개 표기는 0으로 바꾸지 않고 뺀다
