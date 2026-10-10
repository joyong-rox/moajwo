"""glossary entries (user managed, 7-digit codes) with a few examples

Revision ID: 0005_glossary_entries
Revises: 0004_glossary
Create Date: 2026-10-05

용어사전은 사용자가 업로드해 관리한다. 항목은 7자리 기관코드 하나로 식별하고,
자치단체는 시도 2자리·시군구 3자리 코드를 속성으로 둔다.
처음에는 공식 자료(행정기관코드 2026-10-02)에서 확인한 중앙부처·자치단체 몇 개만 예시로 넣는다.
"""

import re
import unicodedata
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0005_glossary_entries"
down_revision: str | None = "0004_glossary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (code, name, type_label, sido_code, sigungu_code, valid_from, valid_to, previous_codes)
EXAMPLES = [
    ("1741000", "행정안전부", "중앙행정기관", None, None, date(2017, 7, 26), None, None),
    ("1360000", "기상청", "중앙행정기관", None, None, date(1990, 12, 27), None, None),
    ("1760000", "인사혁신처", "중앙행정기관", None, None, date(2014, 11, 19), None, None),
    ("1052000", "기획예산처", "중앙행정기관", None, None, date(2025, 12, 19), None, None),
    ("1053000", "재정경제부", "중앙행정기관", None, None, date(2026, 1, 2), None, "1051000"),
    ("1051000", "기획재정부", "중앙행정기관", None, None, date(2008, 2, 29), date(2025, 12, 31), None),
    ("6110000", "서울특별시", "광역자치단체", "11", None, date(1988, 4, 23), None, None),
    ("3150000", "서울특별시 강서구", "기초자치단체", "11", "500", date(1977, 9, 1), None, None),
    ("3220000", "서울특별시 강남구", "기초자치단체", "11", "680", date(2000, 1, 1), None, None),
    ("6260000", "부산광역시", "광역자치단체", "26", None, date(1995, 1, 1), None, None),
    ("3360000", "부산광역시 강서구", "기초자치단체", "26", "440", date(1989, 1, 1), None, None),
    ("6130000", "전남광주통합특별시", "광역자치단체", "12", None, date(2026, 5, 21), None, None),
    ("6290000", "광주광역시", "광역자치단체", "29", None, date(1995, 1, 1), date(2026, 6, 30), None),
    ("6460000", "전라남도", "광역자치단체", "46", None, date(1988, 4, 23), date(2026, 6, 30), None),
]
EXAMPLE_ALIASES = [("행안부", "1741000")]


def _norm(text: str) -> str:
    return re.sub(r"[\s·ㆍ]+", "", unicodedata.normalize("NFKC", text)).lower()


def upgrade() -> None:
    entries = op.create_table(
        "glossary_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=7), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized", sa.String(length=255), nullable=False),
        sa.Column("type_label", sa.String(length=64), nullable=True),
        sa.Column("sido_code", sa.String(length=2), nullable=True),
        sa.Column("sigungu_code", sa.String(length=3), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("previous_codes", sa.String(length=500), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_glossary_entries_normalized", "glossary_entries", ["normalized"])
    op.bulk_insert(
        entries,
        [
            {
                "code": code,
                "name": name,
                "normalized": _norm(name),
                "type_label": type_label,
                "sido_code": sido,
                "sigungu_code": sigungu,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "previous_codes": previous,
                "note": "예시",
                "source": "example",
            }
            for code, name, type_label, sido, sigungu, valid_from, valid_to, previous in EXAMPLES
        ],
    )
    aliases = sa.table(
        "glossary_aliases",
        sa.column("alias", sa.String),
        sa.column("normalized", sa.String),
        sa.column("target_code", sa.String),
        sa.column("note", sa.String),
        sa.column("source", sa.String),
    )
    op.bulk_insert(
        aliases,
        [
            {"alias": a, "normalized": _norm(a), "target_code": c, "note": "예시", "source": "example"}
            for a, c in EXAMPLE_ALIASES
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM glossary_aliases WHERE source = 'example'")
    op.drop_index("ix_glossary_entries_normalized", table_name="glossary_entries")
    op.drop_table("glossary_entries")
