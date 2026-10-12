"""일반구(자치구가 아닌 시 산하 구) 기본 항목

- 행정동 코드표(KIKcd_H, 2026-09-30)의 현존 일반구 39개(고양시 덕양구, 성남시 분당구, 청주시 흥덕구, 천안시 서북구,
  전주시 완산구, 포항시 남구, 창원시 마산합포구 등)와, 2024-01-17까지 쓰인 '전라북도 전주시 완산구·덕진구'를
  기본 항목(source='base', 유형 '일반구')으로 넣는다. '경기 고양시 덕양구', '고양시 덕양구' 같은 이름을 해석한다.
  7자리 코드: 행정안전부 행정표준코드 기관코드 전체자료(2026-10-02)의 하부행정기구 코드.
  시작일: 2018년 이후 생긴 구만 넣는다 (부천시 원미·소사·오정구 2024-01-01 재설치, 화성시 4개 구 2026-02-01,
  전북특별자치도 전환 2024-01-18). 시도·시군구 코드: 행정동 코드표.
- 이미 같은 코드나 같은 이름의 현행 항목이 있으면 넣지 않는다 (사용자가 올린 항목 우선).
- 옛 전주 구 이름 → 전북특별자치도 항목 기본 별칭 (2024-01-18부터, 0012와 같은 방식).

Revision ID: 0013_general_gu_entries
Revises: 0012_reorg_base_entries
Create Date: 2026-10-10

"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0013_general_gu_entries"
down_revision: str | None = "0012_reorg_base_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "base"
TYPE_LABEL = "일반구"
NOTE = "기본 항목 (일반구, 행정표준코드 기관코드·행정동 코드표 기준)"
# (7자리 코드, 정식명칭, 시작일, 종료일(그날까지), 이전코드)
ENTRIES = [
    ("3750000", "경기도 수원시 장안구", None, None, None),
    ("3760000", "경기도 수원시 권선구", None, None, None),
    ("3770000", "경기도 수원시 팔달구", None, None, None),
    ("5610000", "경기도 수원시 영통구", None, None, None),
    ("3790000", "경기도 성남시 수정구", None, None, None),
    ("3800000", "경기도 성남시 중원구", None, None, None),
    ("3810000", "경기도 성남시 분당구", None, None, None),
    ("3840000", "경기도 안양시 만안구", None, None, None),
    ("3850000", "경기도 안양시 동안구", None, None, None),
    ("3871000", "경기도 부천시 원미구", date(2024, 1, 1), None, None),
    ("3881000", "경기도 부천시 소사구", date(2024, 1, 1), None, None),
    ("3891000", "경기도 부천시 오정구", date(2024, 1, 1), None, None),
    ("5550000", "경기도 안산시 상록구", None, None, None),
    ("5560000", "경기도 안산시 단원구", None, None, None),
    ("3950000", "경기도 고양시 덕양구", None, None, None),
    ("3960100", "경기도 고양시 일산동구", None, None, None),
    ("4100100", "경기도 고양시 일산서구", None, None, None),
    ("5620000", "경기도 용인시 처인구", None, None, None),
    ("5630000", "경기도 용인시 기흥구", None, None, None),
    ("5640000", "경기도 용인시 수지구", None, None, None),
    ("5740000", "경기도 화성시 만세구", date(2026, 2, 1), None, None),
    ("5750000", "경기도 화성시 효행구", date(2026, 2, 1), None, None),
    ("5760000", "경기도 화성시 병점구", date(2026, 2, 1), None, None),
    ("5770000", "경기도 화성시 동탄구", date(2026, 2, 1), None, None),
    ("5720000", "충청북도 청주시 상당구", None, None, None),
    ("5725000", "충청북도 청주시 서원구", None, None, None),
    ("5730000", "충청북도 청주시 흥덕구", None, None, None),
    ("5735000", "충청북도 청주시 청원구", None, None, None),
    ("5650000", "충청남도 천안시 동남구", None, None, None),
    ("5660000", "충청남도 천안시 서북구", None, None, None),
    ("5030000", "경상북도 포항시 남구", None, None, None),
    ("5040000", "경상북도 포항시 북구", None, None, None),
    ("5670123", "경상남도 창원시 의창구", None, None, None),
    ("5670140", "경상남도 창원시 성산구", None, None, None),
    ("5670156", "경상남도 창원시 마산합포구", None, None, None),
    ("5670184", "경상남도 창원시 마산회원구", None, None, None),
    ("5670206", "경상남도 창원시 진해구", None, None, None),
    ("4651000", "전북특별자치도 전주시 완산구", date(2024, 1, 18), None, "4650000"),
    ("4661000", "전북특별자치도 전주시 덕진구", date(2024, 1, 18), None, "4660000"),
    ("4650000", "전라북도 전주시 완산구", None, date(2024, 1, 17), None),
    ("4660000", "전라북도 전주시 덕진구", None, date(2024, 1, 17), None),
]
SUCCESSORS = {"4650000": "4651000", "4660000": "4661000"}

entries = sa.table(
    "glossary_entries",
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
aliases = sa.table(
    "glossary_aliases",
    sa.column("alias", sa.String),
    sa.column("normalized", sa.String),
    sa.column("target_code", sa.String),
    sa.column("valid_from", sa.Date),
    sa.column("valid_to", sa.Date),
    sa.column("note", sa.String),
    sa.column("source", sa.String),
)


def upgrade() -> None:
    from app.admin_codes import infer_region_codes
    from app.glossary import normalize_name, sido_short_names

    bind = op.get_bind()
    rows = bind.execute(sa.select(entries.c.code, entries.c.normalized, entries.c.valid_to)).all()
    codes = {r.code for r in rows}
    current_names = {r.normalized for r in rows if r.valid_to is None}
    new = []
    for code, name, valid_from, valid_to, previous in ENTRIES:
        normalized = normalize_name(name)
        if code in codes or (valid_to is None and normalized in current_names):
            continue
        sido, sigungu = infer_region_codes(name) or (None, None)
        new.append(
            {
                "code": code,
                "name": name,
                "normalized": normalized,
                "type_label": TYPE_LABEL,
                "sido_code": sido,
                "sigungu_code": sigungu,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "previous_codes": previous,
                "note": NOTE,
                "source": SOURCE,
            }
        )
    if new:
        op.bulk_insert(entries, new)

    present = {r.code for r in bind.execute(sa.select(entries.c.code)).all()}
    existing = {
        (r.normalized, r.target_code, r.valid_from)
        for r in bind.execute(sa.select(aliases.c.normalized, aliases.c.target_code, aliases.c.valid_from)).all()
    }
    names = {code: name for code, name, *_ in ENTRIES}
    starts = {code: valid_from for code, _, valid_from, *_ in ENTRIES}
    new_aliases = []
    for old, target in SUCCESSORS.items():
        if target not in present:
            continue
        sido, leaf = names[old].split(" ", 1)
        for form in [names[old], *sorted(f"{short} {leaf}" for short in sido_short_names(sido))]:
            key = (normalize_name(form), target, starts[target])
            if key in existing:
                continue
            existing.add(key)
            new_aliases.append(
                {
                    "alias": form,
                    "normalized": key[0],
                    "target_code": target,
                    "valid_from": starts[target],
                    "valid_to": None,
                    "note": f"행정구역 개편: {names[old]} → {names[target]}",
                    "source": SOURCE,
                }
            )
    if new_aliases:
        op.bulk_insert(aliases, new_aliases)


def downgrade() -> None:
    # 이 마이그레이션이 넣은 기본 항목·별칭만 지운다 (0011·0012 기본 데이터는 그대로)
    targets = list(SUCCESSORS.values())
    op.execute(aliases.delete().where(aliases.c.source == SOURCE, aliases.c.target_code.in_(targets)))
    # 사용자 별칭(source가 base가 아닌)이 가리키는 항목은 남긴다 (지우면 별칭이 대상 없이 남는다)
    user_refs = sa.select(aliases.c.target_code).where(aliases.c.source != SOURCE)
    codes = [e[0] for e in ENTRIES]
    op.execute(
        entries.delete().where(entries.c.source == SOURCE, entries.c.code.in_(codes), entries.c.code.not_in(user_refs))
    )
