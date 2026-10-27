"""공통 key 연결의 key 정규형(key_part)·튜플 비교·형식별 '변환 전 값' 회귀 — 파일 업로드 없이 (CSV·xlsx·xls는 파서만).

규칙(app/keyjoin.py): 글자는 그대로(앞자리 0·공백·대소문자 보존), 정수 칸은 10진 문자열, 실수 칸의 정수 값은 정수와 같은
경로, 소수·NaN·날짜·참거짓·16자리 이상 숫자·병합 채움·빈 값·결측 표기는 차단 사유. 합성 자료만 쓴다."""

import csv
import io
import math
from datetime import date, datetime, time
from pathlib import Path

import openpyxl
import pytest

from app.keyjoin import (
    CODE_PRIORITY,
    KEY_EMPTY,
    KEY_PARTIAL,
    KEY_TYPE,
    KINDS,
    _row_code,
    analyze_input,
    key_part,
)
from app.parsing import parse_file


@pytest.mark.parametrize(
    ("original", "cell", "expected"),
    [
        ("001", "001", ("001", "text")),  # CSV 원문 글자: 앞자리 0 보존
        (" 001 ", "001", (" 001 ", "text")),  # 앞뒤 공백도 그대로 (strip 없음)
        ("Abc", "Abc", ("Abc", "text")),
        ("12345678901234567890123", "12345678901234567890123", ("12345678901234567890123", "text")),
        (123, 123, ("123", "int")),  # xlsx 정수 칸 → '123'
        (-5, -5, ("-5", "int")),
        (0, 0, ("0", "int")),
        (123.0, 123, ("123", "int")),  # xls 숫자 칸(실수)의 정수 값 → 정수와 같은 경로
        (1.5, 1.5, (None, "float")),  # 소수
        (float("nan"), None, (None, "float")),
        (float("inf"), None, (None, "float")),
        (True, True, (None, "bool")),
        (False, False, (None, "bool")),
        (datetime(2026, 1, 1), "2026-01-01", (None, "date")),
        (date(2026, 1, 1), "2026-01-01", (None, "date")),
        (time(9, 30), "09:30:00", (None, "date")),
        (10**15, 10**15, (None, "precision")),  # 16자리 정수 칸
        (999_999_999_999_999, 999_999_999_999_999, ("999999999999999", "int")),  # 15자리는 받는다
        (float(2**53), 2**53, (None, "precision")),  # xls 실수 2^53 이상
        (float(2**53 - 2), 2**53 - 2, ("9007199254740990", "int")),  # 16자리 — 2^53 아래지만 자릿수로 차단되지 않는지?
        (None, None, (None, "empty")),
        ("", None, (None, "empty")),
        ("   ", None, (None, "empty")),
        ("-", "-", (None, "missing_mark")),
        ("N/A", "N/A", (None, "missing_mark")),
        (" x ", "x", (None, "missing_mark")),  # 결측 표기는 공백을 뺀 값으로 본다
        (None, "001", (None, "filled")),  # 병합으로 채운 칸 (원문 없음·채운 값 있음)
        ("a\x01b", "a\x01b", (None, "illegal")),
        ("a￾", "a￾", (None, "illegal")),
    ],
)
def test_key_part(original: object, cell: object, expected: tuple) -> None:
    if expected == ("9007199254740990", "int"):  # 16자리 실수는 자릿수(15) 규칙에 걸린다 — 기대값을 바로잡아 둔다
        expected = (None, "precision")
    assert key_part(original, cell) == expected
    assert key_part(original, cell)[1] in KINDS


def test_composite_key_is_compared_as_a_tuple_not_a_joined_string() -> None:
    assert ("a|b", "c") != ("a", "b|c")  # 구분자로 이으면 같아지는 경계가 튜플에서는 다르다
    assert ("001",) != ("1",) and ("123",) == ("123",) and ("K1", "001") != ("001", "K1")


def test_row_code_priority_for_composite_keys() -> None:
    assert _row_code([(KEY_EMPTY, 1)], 2) == (KEY_PARTIAL, 1)  # 일부만 빈 값 → 일부 누락
    assert _row_code([(KEY_EMPTY, 0), (KEY_EMPTY, 1)], 2) == (KEY_EMPTY, 0)  # 모두 빈 값 → 빈 key
    assert _row_code([(KEY_EMPTY, 0), (KEY_TYPE, 1)], 2) == (KEY_TYPE, 1)  # 자료형 사유가 빈 값보다 앞선다
    assert CODE_PRIORITY[-1] == KEY_EMPTY


# ---------------------------------------------------------------- 형식별 '변환 전 값' 회귀 (파서가 바꾼 값이 아닌 원문)


def _csv(tmp_path: Path, rows: list[list[str]], name: str = "k.csv") -> Path:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    path = tmp_path / name
    path.write_text(buf.getvalue(), encoding="utf-8")
    return path


def _xlsx(tmp_path: Path, rows: list[list[object]], name: str = "k.xlsx") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    path = tmp_path / name
    wb.save(path)
    return path


def _xls(tmp_path: Path, rows: list[list[object]], name: str = "k.xls") -> Path:
    xlwt = pytest.importorskip("xlwt")
    wb = xlwt.Workbook(encoding="utf-8")
    ws = wb.add_sheet("k")
    for r, row in enumerate(rows):
        for j, v in enumerate(row):
            if v is not None:
                ws.write(r, j, v)
    wb.save(str(tmp_path / name))
    return tmp_path / name


def _index(path: Path, key_ids: list[int] = [0], pick_ids: list[int] = [1], decisions=None):  # noqa: B006, ANN001 — 읽기만
    sheet = parse_file(path, path.name).sheets[0]
    return analyze_input(sheet, key_ids, pick_ids, set(), {}, keep_all=True, decisions=decisions)


def test_csv_keys_keep_leading_zeros_spaces_and_digits_even_when_the_parser_made_numbers(tmp_path: Path) -> None:
    """같은 열에 '123'(숫자로 바뀜)과 '00123'이 있으면 파서는 열을 글자로 두고 ' 123'의 공백을 지운 값을 cells에 두지만,
    key는 원문(' 123')으로 만든다."""
    ix = _index(_csv(tmp_path, [["id", "v"], ["123", "a"], ["00123", "b"], [" 123", "c"], ["9007199254740993", "d"]]))
    assert list(ix.rows) == [("123",), ("00123",), (" 123",), ("9007199254740993",)]
    assert ix.kinds[0]["text"] == 4 and not ix.int_keys and ix.values[("123",)] == ["a"]


def test_csv_numeric_looking_keys_without_leading_zero_are_still_text_keys(tmp_path: Path) -> None:
    """CSV의 '123'은 원문이 글자라 글자 key('123')다 — xlsx 정수 칸 123과 연결되고 '00123'과는 다르다."""
    ix = _index(_csv(tmp_path, [["id", "v"], ["123", "a"], ["124", "b"]]))
    assert list(ix.rows) == [("123",), ("124",)] and ix.kinds[0]["text"] == 2


def test_xlsx_int_cells_become_decimal_strings_and_text_cells_stay(tmp_path: Path) -> None:
    ix = _index(_xlsx(tmp_path, [["id", "v"], [123, "a"], ["00123", "b"], ["123", "c"], [10**15, "d"]]))
    assert list(ix.rows) == [("123",), ("00123",)]  # '123' 글자 셀은 정수 123과 같은 key → 중복
    assert ix.dups == {("123",): [2, 4]} and ix.int_keys == {("123",)}
    assert ix.rejects["KEYJOIN_KEY_PRECISION"].first == [5] and ix.kinds[0]["precision"] == 1
    assert (ix.kinds[0]["int"], ix.kinds[0]["text"]) == (1, 2)  # 16자리 정수 칸은 int가 아니라 precision


def test_xls_number_cells_are_floats_and_follow_the_integer_path(tmp_path: Path) -> None:
    ix = _index(_xls(tmp_path, [["id", "v"], [123, "a"], [1.5, "b"], ["007", "c"], [2**53, "d"]]))
    assert list(ix.rows) == [("123",), ("007",)]
    assert ix.rejects["KEYJOIN_KEY_TYPE"].first == [3] and ix.rejects["KEYJOIN_KEY_PRECISION"].first == [5]
    assert ix.kinds[0] == dict.fromkeys(KINDS, 0) | {"int": 1, "float": 1, "text": 1, "precision": 1}


def test_merged_cells_and_dates_are_refused_from_the_original_values(tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in [["id", "v"], ["001", 1], [None, 2], [datetime(2026, 1, 1), 3], [True, 4]]:
        ws.append(r)
    ws.merge_cells("A2:A3")
    path = tmp_path / "m.xlsx"
    wb.save(path)
    ix = _index(path)
    assert list(ix.rows) == [("001",)]
    assert ix.rejects["KEYJOIN_KEY_FILLED"].first == [3] and ix.rejects["KEYJOIN_KEY_TYPE"].first == [4, 5]
    assert ix.kinds[0]["filled"] == 1 and ix.kinds[0]["date"] == 1 and ix.kinds[0]["bool"] == 1


def test_values_keep_zero_and_blank_apart_and_long_ints_become_text(tmp_path: Path) -> None:
    ix = _index(_csv(tmp_path, [["id", "v"], ["a", "0"], ["b", ""], ["c", "-"], ["d", "12345678901234567"]]))
    assert [ix.values[(k,)] for k in "abcd"] == [[0], [None], ["-"], ["12345678901234567"]]
    assert ix.long_numbers == 1 and math.isfinite(0)


def test_subtotal_and_note_rows_with_a_key_wait_for_a_decision_and_follow_it(tmp_path: Path) -> None:
    """key 칸이 있는 소계·주석 추정 행은 자동으로 빼지 않고 결정을 기다린다(미결정 — 인덱스에 넣지 않음). 포함하면
    데이터 행처럼 인덱스에 들어가고 제외하면 세기만 한다. 값이 있는 행의 빈 key는 그대로 데이터 행(KEY_EMPTY)."""
    rows = [["id", "v"], ["a", "1"], ["합계", "1"], ["※ 자료: 시험", ""], ["", "9"]]
    ix = _index(_csv(tmp_path, rows))
    assert list(ix.rows) == [("a",)] and ix.data_rows == 2 and ix.skipped == {}
    assert [(r["row"], r["reason"], r["in_key"], r["decision"]) for r in ix.review] == [
        (3, "subtotal", True, None),
        (4, "note", False, None),
    ]
    assert (ix.unresolved_rows, ix.included_rows, ix.excluded_rows) == (2, 0, 0)
    assert ix.rejects[KEY_EMPTY].first == [5]  # 값이 있는 행의 빈 key만 데이터 행
    decided = _index(_csv(tmp_path, rows), decisions=({3}, {4}))
    assert list(decided.rows) == [("a",), ("합계",)] and decided.values[("합계",)] == [1]
    assert (decided.unresolved_rows, decided.included_rows, decided.excluded_rows) == (0, 1, 1)
    assert decided.excluded_first == [4] and [r["decision"] for r in decided.review] == ["include", "exclude"]
