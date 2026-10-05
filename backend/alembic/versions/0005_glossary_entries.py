"""glossary entries (user managed) with a few examples

Revision ID: 0005_glossary_entries
Revises: 0004_glossary
Create Date: 2026-10-05

용어사전은 사용자가 업로드해 관리한다. 처음에는 공식 자료에서 확인한 중앙부처·지역 몇 개만 예시로 넣는다.
(기관: 행정기관코드 2026-10-02, 지역: 행정동코드 2026-09-30 기준)
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

# (kind, code, name, type_label, parent_code, valid_from, valid_to)
EXAMPLES = [
    ("org", "1741000", "행정안전부", "중앙행정기관", None, date(2017, 7, 26), None),
    ("org", "1360000", "기상청", "중앙행정기관", None, date(1990, 12, 27), None),
    ("org", "1760000", "인사혁신처", "중앙행정기관", None, date(2014, 11, 19), None),
    ("org", "1052000", "기획예산처", "중앙행정기관", None, date(2025, 12, 19), None),
    ("org", "1053000", "재정경제부", "중앙행정기관", None, date(2026, 1, 2), None),
    ("org", "1051000", "기획재정부", "중앙행정기관", None, date(2008, 2, 29), date(2025, 12, 31)),
    ("region", "11", "서울특별시", "시도", None, date(1988, 4, 23), None),
    ("region", "11500", "서울특별시 강서구", "시군구", "11", date(1988, 4, 23), None),
    ("region", "11680", "서울특별시 강남구", "시군구", "11", date(1988, 4, 23), None),
    ("region", "26", "부산광역시", "시도", None, date(1995, 1, 1), None),
    ("region", "26440", "부산광역시 강서구", "시군구", "26", date(1995, 1, 1), None),
    ("region", "12", "전남광주통합특별시", "시도", None, date(2026, 7, 1), None),
    ("region", "29", "광주광역시", "시도", None, date(1995, 1, 1), date(2026, 6, 30)),
    ("region", "46", "전라남도", "시도", None, date(1988, 4, 23), date(2026, 6, 30)),
]
EXAMPLE_ALIASES = [("행안부", "org", "1741000")]


def _norm(text: str) -> str:
    return re.sub(r"[\s·ㆍ]+", "", unicodedata.normalize("NFKC", text)).lower()


def upgrade() -> None:
    entries = op.create_table(
        "glossary_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized", sa.String(length=255), nullable=False),
        sa.Column("type_label", sa.String(length=64), nullable=True),
        sa.Column("parent_code", sa.String(length=32), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("previous_codes", sa.String(length=500), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "code"),
    )
    op.create_index("ix_glossary_entries_normalized", "glossary_entries", ["normalized"])
    op.bulk_insert(
        entries,
        [
            {
                "kind": kind,
                "code": code,
                "name": name,
                "normalized": _norm(name),
                "type_label": type_label,
                "parent_code": parent,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "note": "예시",
                "source": "example",
            }
            for kind, code, name, type_label, parent, valid_from, valid_to in EXAMPLES
        ],
    )
    aliases = sa.table(
        "glossary_aliases",
        sa.column("alias", sa.String),
        sa.column("normalized", sa.String),
        sa.column("kind", sa.String),
        sa.column("target_code", sa.String),
        sa.column("note", sa.String),
        sa.column("source", sa.String),
    )
    op.bulk_insert(
        aliases,
        [
            {"alias": a, "normalized": _norm(a), "kind": k, "target_code": c, "note": "예시", "source": "example"}
            for a, k, c in EXAMPLE_ALIASES
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM glossary_aliases WHERE source = 'example'")
    op.drop_index("ix_glossary_entries_normalized", table_name="glossary_entries")
    op.drop_table("glossary_entries")
