"""사용자가 수량 항목으로 확정한 열의 계산용 값 (구성요소 수준 — 파싱 결과·캐시를 바꾸지 않음, 자동 제안은 제외)."""

import copy
import csv
import io
from pathlib import Path

from app.mapping import ColumnMapping
from app.parsing import parse_file
from app.quantity import QUANTITY_FIELDS, confirmed_quantity_indexes, quantity_sheet, quantity_value
from app.validation import FileInput, MappedColumn, SheetInput, ValidationParams, validate

LONG = "1000000000000000"


def _sheet(tmp_path: Path, rows: list[list[str]]):
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    path = tmp_path / "q.csv"
    path.write_text(buf.getvalue(), encoding="utf-8")
    return parse_file(path, path.name).sheets[0]


def test_quantity_value_keeps_digits_and_leaves_unreadable_text() -> None:
    assert quantity_value("1000000000000001") == 1000000000000001  # float를 거치지 않음
    assert quantity_value("12345678901234567890123") == 12345678901234567890123
    assert quantity_value("1,234") == 1234 and quantity_value("-2.5") == -2.5 and quantity_value("0") == 0
    for text in ("-", "x", "…", "0900", "약 100", ""):  # 결측·비공개 표기, 앞자리 0, 문자 — 그대로 (0으로 바꾸지 않음)
        assert quantity_value(text) == text
    assert quantity_value(None) is None and quantity_value(7) == 7


def test_only_confirmed_quantity_mappings_count() -> None:
    mappings = [
        ColumnMapping("가", [], status="confirmed", field_key="expenditure"),
        ColumnMapping("나", [], status="auto", field_key="expenditure"),  # 자동 제안 — 해석을 바꾸지 않음
        ColumnMapping("다", [], status="confirmed", field_key="org_code"),  # 수량 항목이 아님
        ColumnMapping("라", [], status="confirmed", field_key=None),  # '해당 없음'
        ColumnMapping("마", [], status="review", field_key="budget"),
    ]
    assert confirmed_quantity_indexes(mappings) == frozenset({0})
    assert "expenditure" in QUANTITY_FIELDS and "org_code" not in QUANTITY_FIELDS


def test_quantity_sheet_makes_new_rows_and_keeps_the_original(tmp_path: Path) -> None:
    sheet = _sheet(tmp_path, [["구분", "식별부호"], ["합계", "2000000000000000"], ["사업A", LONG], ["사업B", "100"]])
    before = copy.deepcopy(sheet.body)
    converted = quantity_sheet(sheet, frozenset({1}))
    assert [r["cells"][1] for r in converted.body] == [2000000000000000, int(LONG), 100]
    assert sheet.body == before  # 원래 시트(파싱 결과·캐시와 같은 객체)는 그대로 — 원문 글자
    assert [r["cells"][1] for r in sheet.body] == ["2000000000000000", LONG, "100"]
    assert [r["original"][1] for r in converted.body] == ["2000000000000000", LONG, "100"]  # 원문은 그대로 남음
    assert quantity_sheet(sheet, frozenset()) is sheet


def _validate(sheet, quantity: frozenset[int]) -> object:
    cols = [MappedColumn(1, sheet.columns[1], "expenditure", None)]
    return validate(
        [FileInput(1, "q.csv", [SheetInput(quantity_sheet(sheet, quantity), cols, quantity)])], ValidationParams()
    )


def test_confirmed_quantity_sum_is_checked(tmp_path: Path) -> None:
    sheet = _sheet(
        tmp_path,
        [["구분", "비식별처리비용"], ["합계", "2000000000000000"], ["사업A", LONG], ["사업B", "100"], ["사업C", "200"]],
    )
    assert [r["cells"][1] for r in sheet.body][1] == LONG  # 파서는 원문 보존 ('식별' 이름 + 긴 값)
    result = _validate(sheet, frozenset({1}))
    assert result.status == "blocked" and [b.code for b in result.blocks] == ["sum_mismatch"]
    message = result.blocks[0].message
    assert "1,000,000,000,000,300" in message and "차이 999,999,999,999,700" in message
    unconfirmed = _validate(sheet, frozenset())  # 확정하지 않으면 원문(글자) 그대로 — 계산하지 않음
    assert unconfirmed.status == "passed" and not unconfirmed.blocks


def test_unreadable_value_in_confirmed_quantity_is_reported(tmp_path: Path) -> None:
    sheet = _sheet(
        tmp_path, [["구분", "비식별처리비용"], ["합계", "약 300"], ["사업A", "100"], ["사업B", "200"], ["사업C", "-"]]
    )
    result = _validate(sheet, frozenset({1}))
    assert result.status == "passed_with_warnings"  # 합계 검사를 할 수 없었음을 정상 통과로 처리하지 않음
    (warning,) = [w for w in result.warnings if w.code == "quantity_not_number"]
    assert warning.actual == 1 and "약 300" in warning.message and "합계 검사·계산에서 빠집니다" in warning.message
    assert "'-'" not in warning.message  # 결측 표기는 기존 규칙대로 (경고 대상 아님)
