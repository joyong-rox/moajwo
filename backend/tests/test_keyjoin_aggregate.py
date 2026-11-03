"""key별 집계 후 연결(app/keyjoin.py aggregate): 기대값은 제품 함수를 부르지 않고 손으로 센 상수.
집계 입력만 같은 key의 여러 행을 허용하고(집계 없는 입력의 중복은 기존대로 차단), 행 수 ≠ 값 있는 수(빈 칸·결측 표기는
세지 않음), 합계·최댓값은 값이 없으면 빈 칸(0 아님), 날짜 최댓값은 실제 날짜 비교, 숫자가 아닌 값·숫자/날짜 섞임은 차단,
미연결 key의 빈 칸과 실제 0의 구분, 출처 시트는 기여한 원본 행을 모두, 확인 결과·요약의 검산값, 요청 형식(422),
집계 설정 변경 → 확인 결과·요청 해시 무효, 저장한 작업에 집계 설정 저장·복원. 합성 자료만 쓴다."""

import io
import uuid
from datetime import datetime
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import owner  # noqa: F401
from tests.test_auth import _client, _provision_user, app_real_login  # noqa: F401
from tests.test_keyjoin import _body, _csv, _grid, _input, _plan, _run, _state, _texts, _upload, _xlsx_bytes

A_ROWS = [["사번", "부서"], ["001", "총무"], ["002", "기획"], ["003", "정보"], ["004", "영업"]]
D = datetime
# 이력형 자료: 같은 사번이 여러 행, 빈 칸·결측 표기('-')·0이 섞여 있다 (원본 행 번호 2~6).
# 'x'·'X'도 결측 표기라 메모 값으로 쓰지 않는다
L_ROWS = [
    ["사번", "건수", "일자", "메모"],
    ["001", 2, D(2026, 1, 5), "메모A"],
    ["001", 3, D(2026, 2, 1), None],
    ["002", None, None, "메모B"],
    ["003", 0, D(2026, 3, 3), "-"],
    ["003", 5, None, "메모C"],
]
AGG = [
    {"func": "count_rows", "column_id": None, "out_name": "이력건수"},
    {"func": "count_values", "column_id": "c1", "out_name": "건수_있는수"},
    {"func": "sum", "column_id": "c1", "out_name": "건수_합"},
    {"func": "max", "column_id": "c2", "out_name": "최근일"},
    {"func": "count_values", "column_id": "c3", "out_name": None},  # 기본 이름: '메모 값 있는 수'
]
HEADER = ["사번", "부서", "이력건수", "건수_있는수", "건수_합", "최근일", "메모 값 있는 수"]
# 손으로 센 기대값: 001 = 2행·값 2·합 5·최근 2026-02-01·메모 1(메모A만), 002 = 1행·값 0·합 없음·날짜 없음·메모 1,
# 003 = 2행·값 2(0도 값)·합 5·최근 2026-03-03·메모 1('-'는 결측 표기라 세지 않음), 004 = 이력 없음(미연결 — 모두 빈 칸)
EXPECTED = [
    ("001", "총무", 2, 2, 5, D(2026, 2, 1), 1),
    ("002", "기획", 1, 0, None, None, 1),
    ("003", "정보", 2, 2, 5, D(2026, 3, 3), 1),
    ("004", "영업", None, None, None, None, None),
]
EXPECTED_SOURCES = [[1, 2, "2·3 (2행)"], [2, 3, "4 (1행)"], [3, 4, "5·6 (2행)"], [4, 5, None]]
# 확인 결과의 검산값: 결과 행 가운데 이력에 있는 key(001·002·003)로 — 행 수 합 5, 값 있는 수 합 4, 건수 합 10,
# 최근일 전체 최댓값 2026-03-03, 메모 값 있는 수 합 3. 값 없는 key: 건수_합·최근일 각 1(002).
# 0인 key: 건수_있는수 1(002)
EXPECTED_STATS = [
    {"total": 5, "blank_keys": 0, "zero_keys": 0},
    {"total": 4, "blank_keys": 0, "zero_keys": 1},
    {"total": 10, "blank_keys": 1, "zero_keys": 0},
    {"total": "2026-03-03T00:00:00", "blank_keys": 1, "zero_keys": 0},
    {"total": 3, "blank_keys": 0, "zero_keys": 0},
]


def _agg_input(fid: int, items: list[dict[str, Any]] | None = None, key: str = "c0") -> dict[str, Any]:
    return {"file_id": fid, "sheet_id": "s0", "key_columns": [key], "columns": [], "aggregate": {"items": items or AGG}}


def _make(c: TestClient, body: dict[str, Any]) -> tuple[dict[str, Any], openpyxl.Workbook, dict[str, Any]]:
    plan = _plan(c, body)
    assert plan["ready"], plan["problems"]
    res = _run(c, body, plan["basis"])
    assert res.status_code == 201, res.text
    dl = c.get(f"/runs/{res.json()['id']}/download")
    assert dl.status_code == 200
    return res.json(), openpyxl.load_workbook(io.BytesIO(dl.content)), plan


def _rows(wb: openpyxl.Workbook) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in _grid(wb["연결 결과"])[1:]]


def _codes(plan: dict[str, Any], level: str = "problems") -> list[str]:
    return [p["code"] for p in plan[level]]


@pytest.fixture(scope="module")
def kja(app_real_login, owner) -> dict[str, Any]:  # noqa: F811
    _provision_user(owner, app_real_login, "kja-user1", "pass-kja-user1", "KJA1")
    u = _client(app_real_login, "kja-user1", "pass-kja-user1")
    a = _csv(u, "A.csv", A_ROWS)
    lg = _upload(u, "L.xlsx", _xlsx_bytes({"이력": L_ROWS}))
    return {"u": u, "a": a, "l": lg, "body": _body([_input(a, cols=("c1",)), _agg_input(lg)])}


def test_aggregate_counts_sums_max_and_blanks_cell_by_cell(kja) -> None:
    u = kja["u"]
    plan = _plan(u, kja["body"])
    assert plan["ready"] and plan["policy"] == "keyjoin-policy-3" and plan["header"] == HEADER
    assert plan["result"] == {"rows": 4, "key_columns": 1, "columns": 7, "union": 4, "intersection": 3, "excluded": 0}
    lg = plan["inputs"][1]
    assert lg["picked"] == [] and lg["keys"]["indexed"] == 3 and lg["keys"]["duplicate_keys"] == 0
    agg = lg["aggregate"]
    assert agg["source_rows"] == 5 and agg["keys_after"] == 3
    assert [i["label"] for i in agg["items"]] == ["행 수", "값 있는 수", "합계", "최댓값", "값 있는 수"]
    assert [i["out_name"] for i in agg["items"]] == HEADER[2:]
    assert agg["items"][0]["column"] is None and agg["items"][2]["column"] == {"letter": "B", "name": "건수"}
    assert [{k: i[k] for k in ("total", "blank_keys", "zero_keys")} for i in agg["items"]] == EXPECTED_STATS
    assert "KEYJOIN_AGGREGATED" in _codes(plan, "warnings") and "KEYJOIN_KEY_DUPLICATE" not in _codes(plan)
    note = next(w["text"] for w in plan["warnings"] if w["code"] == "KEYJOIN_AGGREGATED")
    assert "원본 5행 → key 3개" in note and "빈 칸(0 아님)" in note
    assert plan["preview"]["rows"][0]["cells"] == ["총무", 2, 2, 5, "2026-02-01T00:00:00", 1]
    assert plan["preview"]["rows"][1]["cells"] == ["기획", 1, 0, None, None, 1]
    before = _state()
    run, wb, _ = _make(u, kja["body"])
    ws = wb["연결 결과"]
    assert [c.value for c in ws[1]] == HEADER and _rows(wb) == EXPECTED
    for r in ws.iter_rows(min_row=2):
        assert r[0].data_type == "s"
    assert _grid(wb["출처"])[0] == [
        "결과 행",
        f"입력 1 · #{kja['a']} · 1번째 시트 원본 행",
        f"입력 2 · #{kja['l']} · 1번째 시트 원본 행",
    ]
    assert _grid(wb["출처"])[1:] == EXPECTED_SOURCES
    text = _texts(wb)
    assert (
        "선택한 규칙으로 집계한 뒤 연결" in text
        and "집계 검산" in text
        and "검산값 10" in text
        and "값 없는 key 1" in text
    )
    assert "첫 행을 고르거나 자동으로 합치지 않았습니다" in text
    s = run["summary"]
    assert s["inputs"][1]["aggregate"][2] == {
        "func": "sum", "column_id": "c1", "out_name": "건수_합", "total": 10, "blank_keys": 1, "zero_keys": 0,
    }  # fmt: skip
    assert s["inputs"][1]["indexed"] == 3 and s["inputs"][1]["data_rows"] == 5 and run["row_count"] == 4
    assert _state()["runs"] == before["runs"] + 1


def test_duplicate_keys_still_block_without_aggregate(kja) -> None:
    u = kja["u"]
    plan = _plan(u, _body([_input(kja["a"], cols=("c1",)), _input(kja["l"], cols=("c1",))]))
    assert not plan["ready"] and "KEYJOIN_KEY_DUPLICATE" in _codes(plan)
    assert plan["inputs"][1]["aggregate"] is None and plan["inputs"][1]["keys"]["duplicate_keys"] == 2


def test_aggregate_refuses_unparseable_and_mixed_values(kja) -> None:
    u = kja["u"]
    before = _state()
    bad = _upload(
        u, "L2.xlsx", _xlsx_bytes({"이력": [L_ROWS[0], ["001", "abc", D(2026, 1, 1), ""], ["001", 2, 7, ""]]})
    )
    plan = _plan(u, _body([_input(kja["a"], cols=("c1",)), _agg_input(bad)]))
    assert not plan["ready"]
    codes = _codes(plan)
    assert "KEYJOIN_AGG_TYPE" in codes and "KEYJOIN_AGG_MIXED" in codes
    typ = next(p for p in plan["problems"] if p["code"] == "KEYJOIN_AGG_TYPE")
    assert (
        typ["column"] == "B"
        and typ["rows"] == [2]
        and "숫자가 아닌 값이 1칸" in typ["text"]
        and "abc" not in typ["text"]
    )
    mixed = next(p for p in plan["problems"] if p["code"] == "KEYJOIN_AGG_MIXED")
    assert mixed["column"] == "C" and mixed["rows"] == [3] and "섞여" in mixed["text"]
    # 날짜 열의 합계는 숫자가 아니라 차단, 글자 열의 최댓값도 차단
    items = [{"func": "sum", "column_id": "c2", "out_name": None}, {"func": "max", "column_id": "c3", "out_name": None}]
    plan2 = _plan(u, _body([_input(kja["a"], cols=("c1",)), _agg_input(kja["l"], items)]))
    assert not plan2["ready"] and _codes(plan2).count("KEYJOIN_AGG_TYPE") == 2
    assert plan2["header"][2:] == ["일자 합계", "메모 최댓값"]
    assert _state() == before  # 차단된 확인은 실행 기록·결과 파일을 남기지 않는다


def test_aggregate_request_rules_are_422_before_any_read(kja) -> None:
    u, a, lg = kja["u"], kja["a"], kja["l"]
    before = _state()

    def bad(inputs: list[dict[str, Any]], code: str) -> None:
        res = u.post("/keyjoin/plan", json=_body(inputs))
        assert res.status_code == 422 and code in res.text, res.text

    with_cols = {**_agg_input(lg), "columns": [{"column_id": "c3", "out_name": None}]}
    bad([_input(a), with_cols], "KEYJOIN_AGG_WITH_COLUMNS")
    bad(
        [_input(a), _agg_input(lg, [{"func": "count_rows", "column_id": "c1", "out_name": None}])], "KEYJOIN_AGG_COLUMN"
    )
    bad([_input(a), _agg_input(lg, [{"func": "sum", "column_id": None, "out_name": None}])], "KEYJOIN_AGG_COLUMN")
    bad([_input(a), _agg_input(lg, [{"func": "sum", "column_id": "c0", "out_name": None}])], "KEYJOIN_AGG_KEY_COLUMN")
    dup = [{"func": "sum", "column_id": "c1", "out_name": "a"}, {"func": "sum", "column_id": "c1", "out_name": "b"}]
    bad([_input(a), _agg_input(lg, dup)], "KEYJOIN_AGG_DUPLICATE")
    bad([_input(a), _agg_input(lg, [{"func": "sum", "column_id": "c1", "out_name": " "}])], "KEYJOIN_NAME_BLANK")
    bad([_input(a), _agg_input(lg, [{"func": "max", "column_id": "c9", "out_name": None}])], "KEYJOIN_COLUMN_UNKNOWN")
    too_many = [{"func": "count_values", "column_id": "c1", "out_name": str(n)} for n in range(51)]
    assert u.post("/keyjoin/plan", json=_body([_input(a), _agg_input(lg, too_many)])).status_code == 422
    assert (
        u.post(
            "/keyjoin/plan",
            json=_body([_input(a), _agg_input(lg, [{"func": "avg", "column_id": "c1", "out_name": None}])]),
        ).status_code
        == 422
    )
    assert _state() == before


def test_changing_the_aggregation_invalidates_the_basis_and_the_request_hash(kja) -> None:
    u = kja["u"]
    plan = _plan(u, kja["body"])
    changed = _body([_input(kja["a"], cols=("c1",)), _agg_input(kja["l"], AGG[:4])])
    plan2 = _plan(u, changed)
    assert plan2["basis"] != plan["basis"] and plan2["result"]["columns"] == 6
    before = _state()
    stale = _run(u, changed, plan["basis"])
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "KEYJOIN_BASIS_CHANGED"
    rid = uuid.uuid4().hex
    assert _run(u, kja["body"], plan["basis"], rid).status_code == 201
    other = _run(u, changed, plan2["basis"], rid)  # 같은 요청 번호에 다른 집계 설정 → 내용 해시가 다르다
    assert other.status_code == 409 and other.json()["detail"]["code"] == "KEYJOIN_REQUEST_MISMATCH"
    assert _state()["runs"] == before["runs"] + 1


def test_scope_all_keeps_unlinked_cells_blank_not_zero(kja) -> None:
    u = kja["u"]
    extra = _upload(u, "L3.xlsx", _xlsx_bytes({"이력": [*L_ROWS, ["009", 4, D(2026, 4, 4), "메모W"]]}))
    body = _body([_input(kja["a"], cols=("c1",)), _agg_input(extra)], scope="all")
    _run_, wb, plan = _make(u, body)
    rows = _rows(wb)
    assert rows[:4] == EXPECTED and rows[4] == ("009", None, 1, 1, 4, D(2026, 4, 4), 1)
    assert plan["inputs"][0]["keys"]["unlinked"] == 1 and plan["inputs"][1]["keys"]["unlinked"] == 1
    blanks = sum(1 for r in rows for v in r if v is None)
    zeros = sum(1 for r in rows for v in r if v == 0)
    assert (blanks, zeros) == (8, 1)  # 004의 5칸 + 002의 2칸 + 009의 부서 = 8 빈 칸, 실제 0은 002의 건수_있는수 하나


def test_saved_job_keeps_the_aggregation_and_restores_it_on_new_files(kja) -> None:
    u = kja["u"]
    res = u.post("/keyjoin/jobs", json={"name": "이력 집계", "request": kja["body"]})
    assert res.status_code == 201, res.text
    job = res.json()
    saved = job["inputs"][1]["aggregate"]
    assert job["inputs"][0]["aggregate"] is None and len(saved) == 5
    assert saved[0] == {"func": "count_rows", "name": None, "letter": None, "out_name": "이력건수"}
    assert saved[2] == {"func": "sum", "name": "건수", "letter": "B", "out_name": "건수_합"}
    assert saved[4] == {"func": "count_values", "name": "메모", "letter": "D", "out_name": None}
    # 다음 달 자료: 열 순서가 바뀐 사본(메모·일자·건수·사번) — 집계 열도 이름으로 다시 맞춘다
    l2 = _upload(u, "L-2월.xlsx", _xlsx_bytes({"이력": [[r[3], r[2], r[1], r[0]] for r in L_ROWS]}))
    m = u.post(f"/keyjoin/jobs/{job['id']}/match", json={"file_ids": [kja["a"], l2]}).json()
    assert m["ready"], m["blocked"]
    req = m["request"]
    assert req["inputs"][1]["columns"] == [] and req["inputs"][1]["key_columns"] == ["c3"]
    assert req["inputs"][1]["aggregate"]["items"] == [
        {"func": "count_rows", "column_id": None, "out_name": "이력건수"},
        {"func": "count_values", "column_id": "c2", "out_name": "건수_있는수"},
        {"func": "sum", "column_id": "c2", "out_name": "건수_합"},
        {"func": "max", "column_id": "c1", "out_name": "최근일"},
        {"func": "count_values", "column_id": "c0", "out_name": None},
    ]
    kinds = [c["kind"] for c in m["roles"][1]["columns"]]
    assert kinds == ["key", "agg", "agg", "agg", "agg"] and all(
        c["status"] == "moved" for c in m["roles"][1]["columns"]
    )
    _run_, wb, _ = _make(u, req)
    assert _rows(wb) == EXPECTED
