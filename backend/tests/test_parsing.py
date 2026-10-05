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


def _gov_stats(tmp_path: Path) -> Path:
    """정부 통계 양식: 제목 / 단위 / 2단 헤더 / 전국·시도 계 / 동명 시군구 / 결측 표기 / 하단 주석."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "시군구별"
    rows = [
        ["시군구별 인구 현황", None, None, None, None],
        ["(단위: 명, %)", None, None, None, None],
        ["행정구역", None, "인구", None, "증감률"],
        ["시도", "시군구", "2025", "2026", None],
        ["전국", None, "51,217,221", "51,159,889", "-0.1"],
        ["서울특별시", "계", "9,386,034", "9,331,828", "-0.6"],
        [None, "강서구", "556,325", "551,442", "-0.9"],
        [None, "강남구", "556,330", "559,016", "0.5"],
        ["부산광역시", "계", "3,293,362", "3,266,598", "-0.8"],
        [None, "강서구", "143,215", "145,902", "1.9"],
        [None, "중구", "38,862", "37,511", "x"],
        ["인천광역시", "중구", "160,111", "-", "…"],
        ["자료: 행정안전부 주민등록인구통계", None, None, None, None],
        ["주: 1) 2026년은 9월 말 기준", None, None, None, None],
        ["※ 외국인 제외", None, None, None, None],
    ]
    for r in rows:
        ws.append(r)
    for rng in ("A1:E1", "A2:E2", "A3:B3", "C3:D3", "E3:E4", "A6:A8", "A9:A11", "A13:E13"):
        ws.merge_cells(rng)
    path = tmp_path / "gov.xlsx"
    wb.save(path)
    return path


def test_parse_gov_stats(tmp_path: Path) -> None:
    sheet = parse_file(_gov_stats(tmp_path)).sheets[0]
    assert (sheet.header_row, sheet.header_row_end) == (3, 4)
    assert sheet.columns == ["행정구역_시도", "행정구역_시군구", "인구_2025", "인구_2026", "증감률"]
    assert (sheet.data_rows, sheet.subtotal_rows, sheet.note_rows, sheet.empty_rows) == (5, 3, 3, 0)
    assert sheet.missing_cells == 3

    kinds = [(r["row"], r["kind"]) for r in sheet.preview]
    assert kinds[0] == (5, "subtotal")  # 전국
    assert [k for _, k in kinds[-3:]] == ["note", "note", "note"]
    # 동명 시군구는 병합된 시도 값으로 구분된다
    gangseo = [r["cells"][:2] for r in sheet.preview if r["cells"][1] == "강서구"]
    assert gangseo == [["서울특별시", "강서구"], ["부산광역시", "강서구"]]
    incheon = next(r for r in sheet.preview if r["cells"][0] == "인천광역시")
    assert incheon["cells"][3:] == ["-", "…"] and incheon["missing"] == [3, 4]


def test_single_header_with_duplicate_names_not_merged_into_data(tmp_path: Path) -> None:
    """헤더에 같은 이름이 이어져도 아래 행이 금액 데이터면 2단 헤더로 보지 않는다."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["지역", "금액", "금액"])
    ws.append(["서울", 1000, 2000])
    ws.append(["부산", 3000, 4000])
    path = tmp_path / "dup.xlsx"
    wb.save(path)
    sheet = parse_file(path).sheets[0]
    assert (sheet.header_row, sheet.header_row_end, sheet.data_rows) == (1, 1, 2)
    assert sheet.columns == ["지역", "금액", "금액_2"]


@pytest.mark.parametrize(
    "text",
    ["자료: 통계청", "출처 : 기상청", "주: 1) 잠정치", "주1) 잠정치", "※ 외국인 제외", "* 추정", "(단위: 명)"],
)
def test_note_rows(text: str) -> None:
    from app.parsing import classify_row

    assert classify_row([text, text, text]) == "note"
    assert classify_row([text, None, None]) == "note"


def test_note_prefix_needs_single_value() -> None:
    from app.parsing import classify_row

    # 첫 칸이 '주'로 시작해도 다른 값이 있으면 데이터 행 (예: 주소 열)
    assert classify_row(["주: 서울", "홍길동", 100]) == "data"
    assert classify_row(["주식회사 한빛", 100]) == "data"


@pytest.mark.parametrize("group_first", [True, False])
def test_two_level_header_with_total_column(tmp_path: Path, group_first: bool) -> None:
    """'인구' 아래 '남/여/계' 하위 헤더. 하위 헤더가 고유 텍스트가 더 많아도 위 그룹 행과 합친다."""
    wb = openpyxl.Workbook()
    ws = wb.active
    if not group_first:
        ws.append(["2026년 성별 인구", None, None, None])
        ws.merge_cells("A1:D1")
    top = ws.max_row + 1 if not group_first else 1
    ws.append(["시군구", "인구", None, None])
    ws.append([None, "남", "여", "계"])
    ws.merge_cells(f"B{top}:D{top}")
    ws.merge_cells(f"A{top}:A{top + 1}")
    ws.append(["강서구", 270000, 281442, 551442])
    ws.append(["강남구", 268000, 291016, 559016])
    path = tmp_path / "two.xlsx"
    wb.save(path)
    sheet = parse_file(path).sheets[0]
    assert (sheet.header_row, sheet.header_row_end) == (top, top + 1)
    assert sheet.columns == ["시군구", "인구_남", "인구_여", "인구_계"]
    assert (sheet.data_rows, sheet.subtotal_rows) == (2, 0)
