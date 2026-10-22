"""C-03: 합계 검사가 일부 하위 값만 읽었거나 검사를 건너뛴 경우 '통과'로 보이지 않게 경고한다.

- 비공개 표기('x' 등)가 하위 값에 있으면 합계를 확인하지 못한다 → 경고 (참고 메모만 남기던 것)
- 읽지 못한 하위 값(글자)을 빼고 나머지만 더해 합계와 같으면 → 경고 (아무 표시 없이 통과하던 것)
- 읽은 값의 합도 합계와 다르면 → 지금처럼 차단 (읽지 못한 값 개수를 함께 적음)
결측·비공개 표기는 0으로 보지 않는다.
"""

from pathlib import Path

from app.validation import ValidationParams, validate
from tests.test_validation import make_input, make_xlsx

TITLE = ["2026년 9월 서울특별시 시군구별 인구 현황 (단위: 명, 세대)"]
HEAD = ["시도", "시군구", "인구", "세대수"]


def _run(tmp_path: Path, rows: list[list]):
    return validate([make_input(make_xlsx(tmp_path / "s.xlsx", [TITLE, HEAD, *rows]))], ValidationParams())


def test_complete_sums_stay_quiet(tmp_path: Path) -> None:
    rows = [["서울특별시", "계", 300, 30], ["서울특별시", "종로구", 100, 10], ["서울특별시", "중구", 200, 20]]
    result = _run(tmp_path, rows)
    assert not result.blocks and not [w for w in result.warnings if w.code == "sum_incomplete"]


def test_a_hidden_lower_value_is_a_warning_not_a_silent_pass(tmp_path: Path) -> None:
    rows = [["서울특별시", "계", 300, 30], ["서울특별시", "종로구", 100, "x"], ["서울특별시", "중구", 200, 20]]
    result = _run(tmp_path, rows)
    [w] = [w for w in result.warnings if w.code == "sum_incomplete"]
    assert w.column == "세대수" and "비공개·결측 표기('x') 1개" in w.message
    assert "합계를 확인하지 못했습니다" in w.message
    assert result.status == "passed_with_warnings" and not result.blocks
    assert not [n for n in result.notes if "합계 검사 생략" in n]  # 메모에만 남기지 않는다


def test_an_unread_value_that_would_change_the_sum_is_not_hidden(tmp_path: Path) -> None:
    # '12명'을 빼면 100 + 200 = 300으로 합계와 같다 — 그 값까지 넣으면 312라 맞는지 알 수 없다
    rows = [
        ["서울특별시", "계", 300, 30],
        ["서울특별시", "종로구", 100, 10],
        ["서울특별시", "중구", 200, 20],
        ["서울특별시", "용산구", "12명", 0],
    ]
    result = _run(tmp_path, rows)
    [w] = [w for w in result.warnings if w.code == "sum_incomplete"]
    assert w.column == "인구" and "1개 값(6행 '12명')을 숫자로 읽지 못해 나머지 2행만 더해 비교했습니다" in w.message
    assert (w.expected, w.actual) == (300, 300) and not result.blocks


def test_a_real_mismatch_still_blocks(tmp_path: Path) -> None:
    rows = [
        ["서울특별시", "계", 350, 30],
        ["서울특별시", "종로구", 100, 10],
        ["서울특별시", "중구", 200, 20],
        ["서울특별시", "용산구", "12명", 0],
    ]
    result = _run(tmp_path, rows)
    assert [b.code for b in result.blocks] == ["sum_mismatch"] and "읽지 못한" not in result.blocks[0].message
    assert "글자로 남아 합계에 넣지 않은 하위 값 1개" in result.blocks[0].message
    assert not [w for w in result.warnings if w.code == "sum_incomplete"]
