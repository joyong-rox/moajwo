"""광역자치단체 기본 항목 + 비어 있는 시도·시군구 코드 채우기

- 현행 광역 16개(2026-07 광주·전남 통합 반영)와 옛 자료용 폐지 광역 4개를 기본 항목으로 넣는다.
  7자리 코드·존속 기간: 행정안전부 행정표준코드관리시스템 기관코드 전체자료(2026-10-02),
  시도 2자리·시행일: 행정동 코드표(KIKcd_H, 2026-09-30).
- 이미 같은 코드나 같은 이름의 현행 항목이 있으면 넣지 않는다 (사용자가 올린 항목 우선).
- 시도·시군구 코드가 빈 자치단체 항목은 정식명칭으로 행정동 코드표에서 찾아 채운다 (기존 값은 바꾸지 않음).
  '서울', '세종', '서울 중구' 같은 지역 약칭은 이 코드로 만들어진다.

Revision ID: 0011_sido_base_entries
Revises: 0010_users_roles
Create Date: 2026-10-10

"""

import logging
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0011_sido_base_entries"
down_revision: str | None = "0010_users_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "base"
# (7자리 코드, 정식명칭, 시도 2자리, 시작일, 종료일(그날까지), 이전코드)
SIDO = [
    ("6110000", "서울특별시", "11", None, None, None),
    ("6260000", "부산광역시", "26", date(1995, 1, 1), None, None),
    ("6270000", "대구광역시", "27", date(1995, 1, 1), None, None),
    ("6280000", "인천광역시", "28", date(1995, 1, 1), None, None),
    ("6300000", "대전광역시", "30", date(1995, 1, 1), None, None),
    ("6310000", "울산광역시", "31", date(1997, 7, 15), None, None),
    ("5690000", "세종특별자치시", "36", date(2012, 7, 1), None, None),
    ("6410000", "경기도", "41", None, None, None),
    ("6430000", "충청북도", "43", None, None, None),
    ("6440000", "충청남도", "44", None, None, None),
    ("6470000", "경상북도", "47", None, None, None),
    ("6480000", "경상남도", "48", None, None, None),
    ("6500000", "제주특별자치도", "50", date(2006, 7, 1), None, None),
    ("6530000", "강원특별자치도", "51", date(2023, 6, 11), None, "6420000"),
    ("6540000", "전북특별자치도", "52", date(2024, 1, 18), None, "6450000"),
    ("6130000", "전남광주통합특별시", "12", date(2026, 7, 1), None, "6290000,6460000"),
    # 옛 자료('광주', '전남', '강원', '전북')용 폐지 광역 — 기준일이 존속 기간 안이면 이 항목으로 해석된다
    ("6290000", "광주광역시", "29", date(1995, 1, 1), date(2026, 6, 30), None),
    ("6460000", "전라남도", "46", None, date(2026, 6, 30), None),
    ("6420000", "강원도", "42", None, date(2023, 6, 10), None),
    ("6450000", "전라북도", "45", None, date(2024, 1, 17), None),
]

aliases = sa.table("glossary_aliases", sa.column("target_code", sa.String), sa.column("source", sa.String))
entries = sa.table(
    "glossary_entries",
    sa.column("id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("normalized", sa.String),
    sa.column("type_label", sa.String),
    sa.column("sido_code", sa.String),
    sa.column("sigungu_code", sa.String),
    sa.column("valid_from", sa.Date),
    sa.column("valid_to", sa.Date),
    sa.column("previous_codes", sa.String),
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
    for code, name, sido, valid_from, valid_to, previous in SIDO:
        normalized = normalize_name(name)
        if code in codes or (valid_to is None and normalized in current_names):
            continue
        new.append(
            {
                "code": code,
                "name": name,
                "normalized": normalized,
                "type_label": "광역자치단체",
                "sido_code": sido,
                "sigungu_code": None,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "previous_codes": previous,
                "note": "기본 항목 (행정표준코드 기관코드·행정동 코드표 기준)",
                "source": SOURCE,
            }
        )
    if new:
        op.bulk_insert(entries, new)

    # 비어 있는 시도·시군구 코드 채우기: 각 칸이 비어 있을 때만 채우고, 들어 있는 값은 추정값과 달라도 그대로 둔다
    # (다르면 로그로 알린다). 시군구는 시도가 추정과 같을 때만 채운다 (다른 시도의 시군구 번호를 붙이지 않도록).
    conflicts = []
    for entry_id, code, name, sido, sigungu in bind.execute(
        sa.select(entries.c.id, entries.c.code, entries.c.name, entries.c.sido_code, entries.c.sigungu_code).where(
            sa.or_(entries.c.sido_code.is_(None), entries.c.sigungu_code.is_(None))
        )
    ).all():
        found = infer_region_codes(name)
        if found is None:
            continue
        values = {}
        if sido is None:
            values["sido_code"] = found[0]
        if sigungu is None and found[1] is not None and (sido or found[0]) == found[0]:
            values["sigungu_code"] = found[1]
        if (sido is not None and sido != found[0]) or (sigungu is not None and sigungu != found[1]):
            conflicts.append(f"{code} {name}: 저장 {sido}/{sigungu}, 코드표 {found[0]}/{found[1]}")
        if values:
            bind.execute(entries.update().where(entries.c.id == entry_id).values(**values))
    if conflicts:
        logging.getLogger("alembic.runtime.migration").warning(
            "용어사전 시도·시군구 코드가 행정동 코드표와 달라 그대로 두었습니다 (%d건): %s",
            len(conflicts),
            "; ".join(conflicts[:20]),
        )


def downgrade() -> None:
    # 이 마이그레이션의 기본 항목(SIDO 코드)만 지운다 (채운 코드는 올바른 값이라 되돌리지 않음)
    # 사용자 별칭(source가 base가 아닌)이 가리키는 항목은 남긴다 (지우면 별칭이 대상 없이 남는다)
    user_refs = sa.select(aliases.c.target_code).where(aliases.c.source != SOURCE)
    codes = [s[0] for s in SIDO]
    op.execute(
        entries.delete().where(entries.c.source == SOURCE, entries.c.code.in_(codes), entries.c.code.not_in(user_refs))
    )
