"""방위형 자치구 기본 항목 + 세종 중복 표기 기본 별칭

- 서울 중구와 부산·대구·대전·울산의 중구·동구·서구·남구·북구(18개)를 기본 항목(source='base')으로 넣는다.
  시도 없이 '남구'·'중구'만 온 값이 0012의 전남광주통합특별시 구 하나로 잘못 확정되지 않고, 같은 이름의 여러
  항목 → '모호'(사용자 확인)로 판정되게 한다. 7자리 코드: 행정표준코드 기관코드 전체자료(2026-10-02),
  시도·시군구 코드: 행정동 코드표(KIKcd_H, 2026-09-30).
- 시도(세종)와 시군구(세종특별자치시)를 이어 붙인 '세종 세종특별자치시' 같은 표기를 세종특별자치시(5690000)에
  잇는 기본 별칭을 넣는다 (2012-07-01부터).
- 이미 같은 코드나 같은 이름의 현행 항목이 있으면 넣지 않는다 (사용자가 올린 항목 우선).

Revision ID: 0014_direction_gu_sejong
Revises: 0013_general_gu_entries
Create Date: 2026-10-10

"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0014_direction_gu_sejong"
down_revision: str | None = "0013_general_gu_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "base"
NOTE = "기본 항목 (방위형 자치구, 행정표준코드 기관코드·행정동 코드표 기준)"
ENTRIES = [
    ("3010000", "서울특별시 중구"),
    ("3250000", "부산광역시 중구"),
    ("3260000", "부산광역시 서구"),
    ("3270000", "부산광역시 동구"),
    ("3310000", "부산광역시 남구"),
    ("3320000", "부산광역시 북구"),
    ("3410000", "대구광역시 중구"),
    ("3420000", "대구광역시 동구"),
    ("3430000", "대구광역시 서구"),
    ("3440000", "대구광역시 남구"),
    ("3450000", "대구광역시 북구"),
    ("3640000", "대전광역시 동구"),
    ("3650000", "대전광역시 중구"),
    ("3660000", "대전광역시 서구"),
    ("3690000", "울산광역시 중구"),
    ("3700000", "울산광역시 남구"),
    ("3710000", "울산광역시 동구"),
    ("3720000", "울산광역시 북구"),
]
SEJONG = "5690000"
SEJONG_FROM = date(2012, 7, 1)
SEJONG_ALIASES = ["세종 세종특별자치시", "세종시 세종특별자치시", "세종특별자치시 세종특별자치시"]

entries = sa.table(
    "glossary_entries",
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("normalized", sa.String),
    sa.column("type_label", sa.String),
    sa.column("sido_code", sa.String),
    sa.column("sigungu_code", sa.String),
    sa.column("valid_to", sa.Date),
    sa.column("note", sa.String),
    sa.column("source", sa.String),
)
aliases = sa.table(
    "glossary_aliases",
    sa.column("alias", sa.String),
    sa.column("normalized", sa.String),
    sa.column("target_code", sa.String),
    sa.column("valid_from", sa.Date),
    sa.column("note", sa.String),
    sa.column("source", sa.String),
)


def upgrade() -> None:
    from app.admin_codes import infer_region_codes
    from app.glossary import normalize_name

    bind = op.get_bind()
    rows = bind.execute(sa.select(entries.c.code, entries.c.normalized, entries.c.valid_to)).all()
    codes = {r.code for r in rows}
    current_names = {r.normalized for r in rows if r.valid_to is None}
    new = []
    for code, name in ENTRIES:
        normalized = normalize_name(name)
        if code in codes or normalized in current_names:
            continue
        sido, sigungu = infer_region_codes(name) or (None, None)
        new.append(
            {
                "code": code,
                "name": name,
                "normalized": normalized,
                "type_label": "기초자치단체",
                "sido_code": sido,
                "sigungu_code": sigungu,
                "valid_to": None,
                "note": NOTE,
                "source": SOURCE,
            }
        )
    if new:
        op.bulk_insert(entries, new)

    if SEJONG not in {r.code for r in bind.execute(sa.select(entries.c.code)).all()}:
        return  # 세종 항목이 없는 사전(사용자가 지운 경우)에는 별칭을 잇지 않는다
    existing = {
        (r.normalized, r.target_code)
        for r in bind.execute(sa.select(aliases.c.normalized, aliases.c.target_code)).all()
    }
    new_aliases = [
        {
            "alias": alias,
            "normalized": normalize_name(alias),
            "target_code": SEJONG,
            "valid_from": SEJONG_FROM,
            "note": "시도·시군구를 이어 붙인 세종 중복 표기",
            "source": SOURCE,
        }
        for alias in SEJONG_ALIASES
        if (normalize_name(alias), SEJONG) not in existing
    ]
    if new_aliases:
        op.bulk_insert(aliases, new_aliases)


def downgrade() -> None:
    # 이 마이그레이션이 넣은 기본 항목·별칭만 지운다
    from app.glossary import normalize_name

    op.execute(
        aliases.delete().where(
            aliases.c.source == SOURCE,
            aliases.c.target_code == SEJONG,
            aliases.c.normalized.in_([normalize_name(a) for a in SEJONG_ALIASES]),
        )
    )
    # 사용자 별칭(source가 base가 아닌)이 가리키는 항목은 남긴다 (지우면 별칭이 대상 없이 남는다)
    user_refs = sa.select(aliases.c.target_code).where(aliases.c.source != SOURCE)
    codes = [c for c, _ in ENTRIES]
    op.execute(
        entries.delete().where(entries.c.source == SOURCE, entries.c.code.in_(codes), entries.c.code.not_in(user_refs))
    )
