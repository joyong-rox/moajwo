"""사용자가 수량 항목으로 확정한 열의 계산용 값 (구성요소 수준 — 파싱 결과·캐시를 바꾸지 않음, 자동 제안은 제외)."""

import copy
import csv
import io
from pathlib import Path

from app.check_basis import column_facts, effect_free
from app.mapping import ColumnMapping
from app.parsing import parse_file
from app.quantity import QUANTITY_FIELDS, confirmed_quantity_indexes, quantity_sheet, quantity_value, text_value_columns
from app.validation import CHECKED_FIELDS, FileInput, MappedColumn, SheetInput, ValidationParams, validate

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
    # 확정하지 않고 자동 적용만 된 수량 열: 원문(글자) 그대로 두되, 합계 검사를 못 했음을 경고로 알림 (조용히 통과 아님)
    unconfirmed = _validate(sheet, frozenset())
    assert unconfirmed.status == "passed_with_warnings" and not unconfirmed.blocks
    (warning,) = unconfirmed.warnings
    assert warning.code == "quantity_not_number" and "자동 적용" in warning.message and "확정하세요" in warning.message
    assert warning.actual == 4  # 합계 행 포함, 숫자로 읽을 수 있는데 글자로 보존된 값


def test_unreadable_value_in_confirmed_quantity_is_reported(tmp_path: Path) -> None:
    sheet = _sheet(
        tmp_path, [["구분", "비식별처리비용"], ["합계", "약 300"], ["사업A", "100"], ["사업B", "200"], ["사업C", "-"]]
    )
    result = _validate(sheet, frozenset({1}))
    assert result.status == "passed_with_warnings"  # 합계 검사를 할 수 없었음을 정상 통과로 처리하지 않음
    (warning,) = [w for w in result.warnings if w.code == "quantity_not_number"]
    assert warning.actual == 1 and "약 300" in warning.message and "합계 검사·계산에서 빠집니다" in warning.message
    assert "'-'" not in warning.message  # 결측 표기는 기존 규칙대로 (경고 대상 아님)


def test_text_value_columns_are_the_columns_whose_checks_depend_on_quantity_confirmation(tmp_path: Path) -> None:
    sheet = _sheet(
        tmp_path,
        [["구분", "식별부호", "건수", "메모값", "결측"], ["합계", "2000000000000000", "3", "약 300", "-"],
         ["사업A", LONG, "1", "100", "x"], ["사업B", "100", "2", "200", ""]],
    )  # fmt: skip
    assert text_value_columns(sheet) == ["구분", "식별부호", "메모값"]  # 글자 값이 남은 열 (결측·비공개·빈 칸 제외)
    assert text_value_columns(sheet, numeric_only=True) == ["식별부호"]  # 숫자로 읽을 수 있는 글자가 있는 열


def test_fields_that_checks_read_by_key() -> None:
    """검사가 항목 key로 읽지 않는 항목은 이것뿐이다 (새 검사가 항목을 읽으면 CHECKED_FIELDS와 함께 고친다)."""
    from app.mapping import load_dictionary

    keys = {f.key for f in load_dictionary()}
    assert CHECKED_FIELDS <= keys
    assert keys - CHECKED_FIELDS == {"department", "manager", "note", "vehicle_type", "vehicle_use", "fuel_type"}
    assert {"base_date", "year", "month", "sigungu", "org_name", "population", "total", "expenditure"} <= CHECKED_FIELDS


def test_column_facts(tmp_path: Path) -> None:
    sheet = _sheet(
        tmp_path, [["구분", "식별부호", "건수", "메모"], ["합계", LONG, "3", "가"], ["사업A", "100", "", "나"]]
    )
    facts = column_facts(sheet)
    assert facts["식별부호"] == {"text_values": True, "blank_values": False, "numeric_values": False}  # 원문 보존 글자
    assert facts["건수"] == {"text_values": False, "blank_values": True, "numeric_values": True}
    assert facts["메모"] == {"text_values": True, "blank_values": False, "numeric_values": False}


def test_review_approval_keeps_the_checked_result_only_when_the_check_input_is_unchanged() -> None:
    """검토에서 승인해도 기존 검사 결과를 이어 쓰는 경우 (check_basis.effect_free) — 나머지는 다시 검사해야 한다."""
    text = {"text_values": True, "blank_values": False, "numeric_values": False}
    sheet = {
        "메모": ["unresolved", None, None, "none"],  # 검사에 쓰지 않은 열
        "자료시점": ["review", "base_date", None, "llm"],  # 제안만 있음 (검사에 쓰지 않음)
        "담당": ["auto", "department", None, "learned"],
        "기준": ["auto", "base_date", None, "pattern"],
        "율": ["auto", "ratio", None, "exact"],
        "금액": ["auto", "expenditure", None, "similarity"],
    }
    assert effect_free(sheet, "메모", text, None) is True  # '해당 없음' — 검사 입력 그대로
    assert effect_free(sheet, "메모", text, "note") is True  # 검사가 읽지 않는 항목 + 빈 칸·숫자 없는 글자 열
    assert effect_free(sheet, "자료시점", text, "base_date") is False  # 기간 항목은 기간 검사에 쓰인다
    assert effect_free(sheet, "메모", text, "sigungu") is False  # 기관·지역 항목
    assert effect_free(sheet, "메모", text, "expenditure") is False  # 수량·금액 항목
    assert effect_free(sheet, "메모", {**text, "blank_values": True}, "note") is False  # 빈 칸 → 공백 경고
    assert effect_free(sheet, "메모", {**text, "numeric_values": True}, "note") is False  # 숫자 → 합계·전월·단위 비교
    assert effect_free(sheet, "메모", text, "department") is False  # 같은 항목 열이 둘 → 전월·단위 비교 대상이 바뀜
    assert effect_free(sheet, "기준", text, "base_date") is True  # 검사에 쓴 자동 매핑을 그대로 확정
    assert effect_free(sheet, "기준", text, "year") is False  # 검사에 쓴 매핑과 다른 항목
    assert effect_free(sheet, "기준", text, None) is False
    assert effect_free(sheet, "율", text, "ratio") is False  # 수량 항목 자동 → 확정: 글자 값의 해석이 바뀜
    assert effect_free(sheet, "율", {**text, "text_values": False}, "ratio") is True
    assert effect_free(sheet, "금액", text, "expenditure") is False  # 자동 → 확정: 글자 값이 계산용 값으로 바뀜
    assert effect_free(sheet, "금액", {**text, "text_values": False}, "expenditure") is True
    assert effect_free(sheet, "없는열", text, None) is False
