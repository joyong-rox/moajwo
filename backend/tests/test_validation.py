from pathlib import Path
from typing import get_args

import openpyxl
import pytest

from app.mapping import MappingEngine, load_dictionary
from app.parsing import parse_file
from app.pii import PiiReport, find_in_text, scan_grid
from app.validation import (
    BlockCode,
    BlockIssue,
    FileInput,
    MappedColumn,
    SheetInput,
    ValidationParams,
    WarningCode,
    WarningIssue,
    validate,
)

ENGINE = MappingEngine(load_dictionary(), None, None)


def make_xlsx(path: Path, rows: list[list], merges: tuple[str, ...] = (), title: str = "현황") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    for m in merges:
        ws.merge_cells(m)
    wb.save(path)
    return path


def make_input(path: Path, file_id: int = 1, name: str | None = None) -> FileInput:
    parsed = parse_file(path)
    sheets = []
    for sheet in parsed.sheets:
        result = ENGINE.map_columns(sheet.columns, sheet.samples, use_llm=False)
        cols = [
            MappedColumn(i, c.column, c.field_key, c.qualifier)
            for i, c in enumerate(result.columns)
            if c.field_key and c.status in ("confirmed", "auto")
        ]
        sheets.append(SheetInput(sheet, cols))
    report = parsed.pii
    return FileInput(file_id, name or path.name, sheets, report.counts, [], False)


def codes(issues: list) -> list[str]:
    return [i.code for i in issues]


# 정상 시군구 인구표 (2026년 9월)
GOOD_ROWS = [
    ["2026년 9월 시군구별 인구 현황", None, None, None],
    ["시도", "시군구", "인구", "세대수"],
    ["서울특별시", "계", 1107767, 470000],
    [None, "강서구", 551442, 240000],
    [None, "강남구", 556325, 230000],
    ["부산광역시", "계", 145902, 60000],
    [None, "강서구", 145902, 60000],
    ["전국", None, 1253669, 530000],
]
GOOD_MERGES = ("A1:D1", "A3:A5", "A6:A7")


# ---------------------------------------------------------------- 개인정보


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("900101-1234567", [("rrn", "900101-1******")]),
        ("주민번호 9001012234567 입니다", [("rrn", "900101-2******")]),
        ("901301-1234567", []),  # 13월은 생년월일이 아님
        ("010-1234-5678", [("mobile", "010-****-5678")]),
        ("01012345678", [("mobile", "010-****-5678")]),
        ("010 123 4567", [("mobile", "010-****-4567")]),
        ("02-2100-1234", []),  # 일반 전화는 대상 아님
        ("hong.gd@example.go.kr", [("email", "h***@example.go.kr")]),
        ("1150052000", []),  # 행정동 코드
    ],
)
def test_find_pii(text: str, expected: list) -> None:
    assert find_in_text(text) == expected


def test_pii_masks_and_locates_without_raw_values() -> None:
    report = PiiReport()
    scan_grid(
        "명단",
        [["이름", "주민번호", "연락처"], ["홍길동", "800101-1234567", "010-9876-5432"], ["김", 8001012234567, 1]],
        report,
    )
    assert report.counts == {"rrn": 2, "mobile": 1, "email": 0}
    assert [(i.row, i.column) for i in report.items] == [(2, "B"), (2, "C"), (3, "B")]
    joined = repr(report)
    assert "1234567" not in joined and "9876" not in joined


# ---------------------------------------------------------------- 차단: 합계


def test_good_file_has_no_blocks(tmp_path: Path) -> None:
    f = make_input(make_xlsx(tmp_path / "good.xlsx", GOOD_ROWS, GOOD_MERGES))
    result = validate([f], ValidationParams(expected_period="2026-09"))
    assert result.blocks == []
    assert result.status in ("passed", "passed_with_warnings")


def test_sum_mismatch_blocks_with_location(tmp_path: Path) -> None:
    rows = [r[:] for r in GOOD_ROWS]
    rows[2][2] = 1107000  # 서울 계 인구를 틀리게
    f = make_input(make_xlsx(tmp_path / "bad.xlsx", rows, GOOD_MERGES))
    result = validate([f], ValidationParams())
    sums = [b for b in result.blocks if b.code == "sum_mismatch"]
    # 서울 계(하위 합과 다름) + 전국(소계 합과 다름)
    assert [(b.row, b.column) for b in sums] == [(3, "인구"), (8, "인구")]
    assert sums[0].expected == 1107767 and sums[0].actual == 1107000
    assert result.status == "blocked"
    assert all(isinstance(b, BlockIssue) for b in result.blocks)


def test_subtotal_after_group_and_rate_columns_skipped(tmp_path: Path) -> None:
    rows = [
        ["부서", "담당자", "매출액", "집행률(%)"],
        ["영업1팀", "김", 100, 50.0],
        [None, "이", 200, 70.0],
        ["영업1팀 소계", None, 300, 60.0],  # 비율 열은 합산하지 않는다
        ["합계", None, 300, 60.0],
    ]
    f = make_input(make_xlsx(tmp_path / "s.xlsx", rows, ("A2:A3",)))
    assert validate([f], ValidationParams()).blocks == []


def test_rounding_tolerance_and_suppressed_values(tmp_path: Path) -> None:
    rows = [
        ["시도", "금액"],
        ["서울", 10.4],
        ["부산", 20.4],
        ["합계", 30.9],  # 반올림 차이 0.1 ≤ 허용 오차 0.1
    ]
    assert validate([make_input(make_xlsx(tmp_path / "r.xlsx", rows))], ValidationParams()).blocks == []
    rows = [["시도", "인구"], ["서울", 100], ["부산", "x"], ["합계", 500]]
    result = validate([make_input(make_xlsx(tmp_path / "x.xlsx", rows))], ValidationParams())
    assert result.blocks == [] and any("비공개" in n for n in result.notes)


# ---------------------------------------------------------------- 차단: 필수 기관 / 기간


def test_required_entities(tmp_path: Path) -> None:
    f = make_input(make_xlsx(tmp_path / "g.xlsx", GOOD_ROWS, GOOD_MERGES))
    params = ValidationParams(required_entities=["서울특별시", "부산광역시", "인천광역시"], entity_field="sido")
    missing = [b for b in validate([f], params).blocks if b.code == "required_entity_missing"]
    assert [b.expected for b in missing] == ["인천광역시"]


def test_required_entity_from_file_name(tmp_path: Path) -> None:
    rows = [["예산액(백만원)", "집행액(백만원)"], [100, 90]]
    a = make_input(make_xlsx(tmp_path / "행정안전부_예산.xlsx", rows), 1)
    b = make_input(make_xlsx(tmp_path / "기상청_예산.xlsx", rows), 2)
    params = ValidationParams(required_entities=["행정안전부", "기상청", "인사혁신처"])
    assert [x.expected for x in validate([a, b], params).blocks if x.code == "required_entity_missing"] == [
        "인사혁신처"
    ]


def test_period_mismatch(tmp_path: Path) -> None:
    f = make_input(make_xlsx(tmp_path / "g.xlsx", GOOD_ROWS, GOOD_MERGES))
    blocks = validate([f], ValidationParams(expected_period="2026-08")).blocks
    assert codes(blocks) == ["period_mismatch"] and blocks[0].actual == "2026-09"
    assert validate([f], ValidationParams(expected_period="2026")).blocks == []

    rows = [["연도", "월", "기관명", "건수"], [2026, 9, "기상청", 3], [2026, 8, "행정안전부", 4]]
    g = make_input(make_xlsx(tmp_path / "ym.xlsx", rows), 2)
    blocks = validate([g], ValidationParams(expected_period="2026-09")).blocks
    assert [(b.row, b.actual) for b in blocks] == [(3, "2026-08")]


def test_period_mismatch_between_files_without_expected(tmp_path: Path) -> None:
    a = make_input(make_xlsx(tmp_path / "a_2026년 9월.xlsx", [["기관명", "건수"], ["기상청", 1]]), 1)
    b = make_input(make_xlsx(tmp_path / "b_2026년 8월.xlsx", [["기관명", "건수"], ["행정안전부", 2]]), 2)
    blocks = validate([a, b], ValidationParams()).blocks
    assert codes(blocks) == ["period_mismatch"]


# ---------------------------------------------------------------- 경고


def test_mom_outlier_warning(tmp_path: Path) -> None:
    cur = make_input(make_xlsx(tmp_path / "cur.xlsx", GOOD_ROWS, GOOD_MERGES), 1)
    prev_rows = [r[:] for r in GOOD_ROWS]
    prev_rows[0][0] = "2026년 8월 시군구별 인구 현황"
    prev_rows[3][2] = 300000  # 서울 강서구: 300,000 → 551,442 (+84%)
    prev = make_input(make_xlsx(tmp_path / "prev.xlsx", prev_rows, GOOD_MERGES), 2)
    result = validate([cur], ValidationParams(expected_period="2026-09"), baseline=[prev])
    mom = [w for w in result.warnings if w.code == "mom_outlier"]
    assert [(w.row, w.column, w.expected, w.actual) for w in mom] == [(4, "인구", 300000, 551442)]
    # 부산 강서구는 같은 이름이지만 시도가 달라 서울 값과 비교하지 않는다
    assert result.blocks == [] and result.status == "passed_with_warnings"


def test_blank_value_warning(tmp_path: Path) -> None:
    rows = [["기관명", "건수"], ["기상청", 3], ["행정안전부", None], ["인사혁신처", 2]]
    result = validate([make_input(make_xlsx(tmp_path / "b.xlsx", rows))], ValidationParams())
    blanks = [w for w in result.warnings if w.code == "blank_value"]
    assert [(w.column, w.row, w.actual) for w in blanks] == [("건수", 3, 1)]
    assert result.blocks == []


def test_unit_estimate_warnings(tmp_path: Path) -> None:
    a = make_input(make_xlsx(tmp_path / "a.xlsx", [["기관명", "예산액(백만원)"], ["기상청", 3200]]), 1)
    b = make_input(make_xlsx(tmp_path / "b.xlsx", [["기관명", "예산액(천원)"], ["행정안전부", 12500000]]), 2)
    c = make_input(make_xlsx(tmp_path / "c.xlsx", [["기관명", "예산액"], ["인사혁신처", 1850000000]]), 3)
    units = [w for w in validate([a, b, c], ValidationParams()).warnings if w.code == "unit_estimate"]
    messages = " | ".join(w.message for w in units)
    assert "단위가 다릅니다" in messages  # 백만원 vs 천원
    assert "배 다릅니다" in messages  # 단위 없는 c의 값 규모
    assert "단위 표기가 없습니다" in messages


# ---------------------------------------------------------------- 등급 분리


def test_block_and_warning_codes_are_disjoint() -> None:
    assert not set(get_args(BlockCode)) & set(get_args(WarningCode))
    assert set(get_args(BlockCode)) == {"sum_mismatch", "required_entity_missing", "period_mismatch"}
    assert set(get_args(WarningCode)) == {
        "mom_outlier",
        "blank_value",
        "unit_estimate",
        "header_missing",
        "quantity_not_number",
    }


def test_status_precedence_and_separation(tmp_path: Path) -> None:
    rows = [r[:] for r in GOOD_ROWS]
    rows[2][2] = 1  # 합계 불일치 (차단)
    rows[1].append("연락처")
    rows[3].append("010-1111-2222")  # 개인정보 (다른 행의 연락처는 비어 있음 → 공백 경고)
    f = make_input(make_xlsx(tmp_path / "all.xlsx", rows, GOOD_MERGES))
    result = validate([f], ValidationParams())
    assert result.status == "blocked"
    assert all(isinstance(b, BlockIssue) for b in result.blocks)
    assert all(isinstance(w, WarningIssue) for w in result.warnings)
    assert "blank_value" in codes(result.warnings) and "blank_value" not in codes(result.blocks)
    assert result.pii and result.pii[0].counts["mobile"] == 1

    # 차단을 고치면: 개인정보 미확인 → 확인 필요, 확인 후 → 경고 포함 통과
    rows[2][2] = 1107767
    g = make_input(make_xlsx(tmp_path / "fixed.xlsx", rows, GOOD_MERGES))
    assert validate([g], ValidationParams()).status == "pii_confirmation_required"
    g.pii_acknowledged = True
    assert validate([g], ValidationParams()).status == "passed_with_warnings"


def test_validation_does_not_modify_input(tmp_path: Path) -> None:
    path = make_xlsx(tmp_path / "bad.xlsx", [["시도", "인구"], ["서울", 1], ["합계", 5]])
    before = path.read_bytes()
    f = make_input(path)
    body_before = repr(f.sheets[0].sheet.body)
    validate([f], ValidationParams(expected_period="2026-09", required_entities=["부산"]))
    assert path.read_bytes() == before and repr(f.sheets[0].sheet.body) == body_before


def test_preview_and_samples_are_masked_but_body_is_not(tmp_path: Path) -> None:
    from app.pii import mask_text

    assert mask_text("연락 010-9876-5432 / a.b@x.go.kr") == "연락 010-****-5432 / a***@x.go.kr"
    rows = [
        ["기관명", "주민등록번호", "연락처"],
        ["기상청", "800101-1234567", "010-9876-5432"],
        ["행안부", 8501012345678, None],
    ]
    sheet = parse_file(make_xlsx(tmp_path / "p.xlsx", rows)).sheets[0]
    shown = repr(sheet.preview) + repr(sheet.samples)
    assert "1234567" not in shown and "9876" not in shown and "2345678" not in shown
    assert sheet.samples[1] == ["800101-1******", "850101-2******"]
    # 검증용 본문은 원래 값을 유지한다 (파싱 결과 자체를 바꾸지 않음)
    assert sheet.body[0]["cells"][1] == "800101-1234567"


def test_header_missing_warning(tmp_path: Path) -> None:
    """머리글을 못 찾은 표(숫자만 있음)는 검증이 '문제 없음'으로 지나가지 않고 헤더 확인 경고를 낸다."""
    path = make_xlsx(tmp_path / "noheader.xlsx", [[1, 2, 3], [4, 5, 6]])
    result = validate([make_input(path)], ValidationParams())
    assert "header_missing" in codes(result.warnings)
    good = validate([make_input(make_xlsx(tmp_path / "ok.xlsx", GOOD_ROWS, GOOD_MERGES))], ValidationParams())
    assert "header_missing" not in codes(good.warnings)


def test_stock_series_top_total_not_summed(tmp_path: Path) -> None:
    """KOSIS 누적 통계: 맨 위 '합계'(연말 값) 아래가 월 계열이면 월별 합과 비교하지 않는다."""
    rows = [["월별", "인구"], ["합계", 1002889], ["1월", 994549], ["2월", 995905], ["3월", 1002889]]
    result = validate([make_input(make_xlsx(tmp_path / "stock.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" not in codes(result.blocks)
    assert any("누적 자료" in n for n in result.notes)


def test_top_national_total_still_checked(tmp_path: Path) -> None:
    """지역별 표의 맨 위 '전국'은 그대로 합계를 검사한다 (하위 행이 시점 계열이 아님)."""
    rows = [["시도", "인구"], ["전국", 999], ["서울", 100], ["부산", 200]]
    result = validate([make_input(make_xlsx(tmp_path / "nat.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" in codes(result.blocks)


def test_subtotal_group_is_nearest_text_left_of_label(tmp_path: Path) -> None:
    """'2026-08 | 서울 | 계' 행의 그룹은 '서울'(라벨 바로 왼쪽)이다.

    맨 앞 '2026-08'로 묶으면 같은 달 전국 행 전체와 비교하게 된다.
    """
    rows = [
        ["월", "시도명", "시군구", "대수"],
        ["2026-08", "서울", "계", 300],
        ["2026-08", "서울", "강남구", 100],
        ["2026-08", "서울", "강서구", 200],
        ["2026-08", "부산", "계", 50],
        ["2026-08", "부산", "중구", 50],
    ]
    result = validate([make_input(make_xlsx(tmp_path / "month.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" not in codes(result.blocks)
    rows[2][3] = 999  # 서울 계와 하위 합이 다르면 그대로 차단
    result = validate([make_input(make_xlsx(tmp_path / "month2.xlsx", rows))], ValidationParams())
    assert [b.row for b in result.blocks if b.code == "sum_mismatch"] == [2]


def test_national_total_with_single_region_is_skipped(tmp_path: Path) -> None:
    """한 시도 자료에 '전국' 행이 함께 실린 경우: 하위 그룹이 하나뿐이라 전체 합계는 검사하지 않는다."""
    rows = [
        ["시도", "시군구", "대수"],
        ["전국", "합계", 9999],
        ["충남", "계", 300],
        ["충남", "천안시", 100],
        ["충남", "공주시", 200],
    ]
    result = validate([make_input(make_xlsx(tmp_path / "one.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" not in codes(result.blocks)
    assert any("하위 그룹이 하나뿐" in n for n in result.notes)


def test_subtotal_placed_above_its_rows(tmp_path: Path) -> None:
    """병합 셀로 라벨이 '계'만 보이고 소계가 하위 행 위에 놓인 양식 (충청남도 자동차 등록현황 '천안시 계')."""
    rows = [
        ["시도", "시군구", "구", "관용", "자가용"],
        ["충남", "계", "계", 280, 310825],
        ["충남", "천안시", "동남구", 112, 122934],
        ["충남", "천안시", "서북구", 168, 187891],
        ["충남", "공주시", "공주시", 241, 43046],
    ]
    result = validate([make_input(make_xlsx(tmp_path / "above.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" not in codes(result.blocks)
    assert any("위쪽에 놓인 소계" in n for n in result.notes)
    rows[2][3] = 150  # 아래 행과도 맞지 않으면 그대로 차단
    result = validate([make_input(make_xlsx(tmp_path / "above2.xlsx", rows))], ValidationParams())
    assert "sum_mismatch" in codes(result.blocks)
