from pathlib import Path

import openpyxl
import pytest

from app.parsing import ParseError, decode_csv, detect_header_row, normalize_cell, parse_file, to_number

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234", 1234),
        ("1,234,567.5", 1234567.5),
        ("-12,000", -12000),
        ("2 100 000", 2100000),
        ("  750000 ", 750000),
        ("0", 0),
        ("3.14", 3.14),
        ("010 1234 5678", None),  # 전화번호
        ("00123", None),  # 앞자리 0 코드값
        ("12,34", None),  # 잘못된 천 단위 구분
        ("1,234 원", None),
        ("abc", None),
    ],
)
def test_to_number(text: str, expected: object) -> None:
    assert to_number(text) == expected


def test_normalize_cell_blank_to_none() -> None:
    assert normalize_cell("   ") is None
    assert normalize_cell(3.0) == 3


@pytest.mark.parametrize(
    ("raw", "encoding"),
    [
        ("거래처,금액\n한빛,1000\n".encode("euc-kr"), "euc-kr"),
        ("거래처,금액\n똠양꿍,1000\n".encode("cp949"), "cp949"),  # EUC-KR에 없는 글자
        ("﻿거래처,금액\n".encode(), "utf-8-sig"),
    ],
)
def test_decode_csv(raw: bytes, encoding: str) -> None:
    text, used = decode_csv(raw)
    assert text.lstrip("﻿").startswith("거래처")
    assert used == encoding


def test_decode_csv_utf8_without_bom() -> None:
    text, _ = decode_csv(("거래처,금액,비고\n" * 20).encode("utf-8"))
    assert text.startswith("거래처")


def test_detect_header_skips_merged_title() -> None:
    rows = [["제목", "제목", "제목"], [None, None, None], ["이름", "부서", "금액"], ["홍길동", "총무", 100]]
    assert detect_header_row(rows) == 2


def test_detect_header_none_when_no_text() -> None:
    assert detect_header_row([[1, 2], [3, 4]]) is None


def _xlsx(tmp_path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "매출"
    ws.append(["3분기 매출", None, None])
    ws.merge_cells("A1:C1")
    ws.append(["부서", "이름", "금액"])
    ws.append(["영업팀", "김", "1,000"])
    ws.append([None, "이", "2,000"])
    ws.append(["영업팀 소계", None, 3000])
    ws.append([None, None, None])
    ws.append(["총무팀", "박", 500])
    ws.append(["합계", None, 3500])
    ws.append([None, None, None])  # 뒤쪽 빈 행은 세지 않는다
    ws.merge_cells("A3:A4")
    path = tmp_path / "s.xlsx"
    wb.save(path)
    return path


def test_parse_xlsx(tmp_path: Path) -> None:
    path = _xlsx(tmp_path)
    before = path.read_bytes()
    result = parse_file(path)
    sheet = result.sheets[0]
    assert (sheet.header_row, sheet.data_rows, sheet.subtotal_rows, sheet.empty_rows) == (2, 3, 2, 1)
    assert sheet.columns == ["부서", "이름", "금액"]
    assert sheet.preview[1]["cells"] == ["영업팀", "이", 2000]  # 병합 ffill + 숫자 변환
    assert path.read_bytes() == before  # 원본 불변


def test_parse_xls_fixture() -> None:
    result = parse_file(FIXTURES / "members.xls")
    sheet = result.sheets[0]
    assert (sheet.header_row, sheet.data_rows, sheet.subtotal_rows, sheet.empty_rows) == (3, 4, 1, 0)
    assert [r["cells"][0] for r in sheet.preview[:3]] == ["서울", "서울", "서울"]


def test_parse_csv_euc_kr(tmp_path: Path) -> None:
    path = tmp_path / "t.csv"
    lines = ["일자,거래처,금액", '09-01,한빛,"45,000"', ",,", '09-02,가람,"1,200"', '합계,,"46,200"']
    path.write_bytes("\r\n".join(lines).encode("euc-kr"))
    result = parse_file(path)
    sheet = result.sheets[0]
    assert result.encoding == "euc-kr"
    assert (sheet.header_row, sheet.data_rows, sheet.empty_rows, sheet.subtotal_rows) == (1, 2, 1, 1)
    assert sheet.preview[0]["cells"] == ["09-01", "한빛", 45000]


def test_parse_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"not a zip")
    with pytest.raises(ParseError):
        parse_file(path)
