"""이메일 경계: 한글 등 영문이 아닌 글자 바로 뒤에 붙은 이메일도 탐지·가림이 같은 범위로 처리한다 (합성 문자열만)."""

import pytest

from app.mapping import MappingEngine, load_dictionary
from app.pii import PiiReport, find_in_text, has_pii, mask_text, scan_grid

# (원문, 가린 결과) — 앞뒤 일반 문구와 주소 안의 밑줄·점·더하기·하이픈은 그대로 둔다
EMAILS = [
    ("인구수_kim@example.com", "인구수_***@example.com"),  # 밑줄로 시작하는 로컬 부분 (_kim)
    ("인구수kim@example.com", "인구수k***@example.com"),
    ("kim@example.com", "k***@example.com"),
    ("_kim@example.com", "_***@example.com"),
    ("kim_lee@example.com", "k***@example.com"),
    ("인구수(kim@example.com)", "인구수(k***@example.com)"),
    ("인구수 kim@example.com", "인구수 k***@example.com"),
    ("담당kim.lee+tag@sub.example.co.kr 연락", "담당k***@sub.example.co.kr 연락"),
]
# 이메일이 아닌 이름·수량·코드는 그대로 (탐지도 없음)
PLAIN = [
    "인구수_12345",
    "인구수",
    "1741000",
    "6110000",
    "12345678901234567890",
    "1,234,567",
    "550",
    "인구수@통계",
    "a@b",
]


@pytest.mark.parametrize(("raw", "masked"), EMAILS)
def test_email_detection_and_masking_use_the_same_span(raw: str, masked: str) -> None:
    assert mask_text(raw) == masked
    found = find_in_text(raw)
    assert [k for k, _ in found] == ["email"]
    assert found[0][1] in masked  # 탐지가 돌려주는 가린 값 = 가림 결과의 그 부분
    assert has_pii(raw)
    assert mask_text(masked) == masked and not find_in_text(masked)  # 다시 적용해도 더 훼손하지 않음


@pytest.mark.parametrize("text", PLAIN)
def test_names_codes_and_numbers_are_not_emails(text: str) -> None:
    assert mask_text(text) == text and not find_in_text(text) and not has_pii(text)


def test_phone_and_rrn_rules_are_unchanged() -> None:
    assert mask_text("010-1234-5678") == "010-****-5678"
    assert mask_text("담당010-1234-5678") == "담당010-****-5678"
    assert mask_text("900101-1234567") == "900101-1******"
    assert mask_text("연락처 010-1234-5678 kim@example.com") == "연락처 010-****-5678 k***@example.com"
    assert [k for k, _ in find_in_text("900101-1234567")] == ["rrn"]


def test_upload_scan_finds_emails_glued_to_korean_in_headers_and_cells() -> None:
    report = PiiReport()
    scan_grid(
        "현황",
        [["시도", "인구수_kim@example.com"], ["서울특별시", "담당kim@example.com"], ["부산광역시", 1741000]],
        report,
    )
    assert report.counts["email"] == 2
    assert {(i.row, i.column, i.masked) for i in report.items} == {
        (1, "B", "_***@example.com"),
        (2, "B", "k***@example.com"),
    }


def test_column_names_with_glued_email_are_not_learned_or_promoted() -> None:
    from app.db import Base, SessionLocal
    from app.db import engine as db_engine
    from app.decisions import learn_column
    from app.promotion import record_approval

    Base.metadata.create_all(db_engine)
    engine = MappingEngine(load_dictionary(), None, None)
    with SessionLocal() as db:
        for name in ("인구수_kim@example.com", "인구수kim@example.com"):
            assert (
                learn_column(db, engine, 1, name, "population", None)
                == "열 이름에 개인정보 형식이 있어 이름을 학습하지 않음"
            )
            assert record_approval(db, engine, 1, name, "population") is None
        db.rollback()
