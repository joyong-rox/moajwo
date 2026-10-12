"""행정구역 개편 기초자치단체 기본 항목 + 개편 전 이름 → 승계 항목 기본 별칭

- 2018년 이후 폐지·신설된 기초자치단체 129개를 기본 항목(source='base')으로 넣는다.
  인천 남구→미추홀구(2018-07-01), 강원도→강원특별자치도(2023-06-11), 경북 군위군→대구(2023-07-01),
  전라북도→전북특별자치도(2024-01-18), 인천 중구·동구·서구→제물포구·영종구·서해구·검단구,
  광주광역시·전라남도 시군구→전남광주통합특별시(2026-07-01).
  7자리 코드: 행정안전부 행정표준코드 기관코드 전체자료(2026-10-02). 존속 기간: 행정동 코드표 시행일 기준
  (폐지 항목은 시행 전날까지, 신설 항목은 시행일부터). 시도·시군구 코드: 행정동 코드표(KIKcd_H, 2026-09-30).
- 폐지 항목의 정식명칭과 지역 약칭('인천 동구', '광주 동구', '전남 목포시')을 승계 항목에 잇는 기본 별칭을
  시행일부터 유효하게 넣는다. 기준일이 개편 전이면 옛 항목, 개편 후면 승계 항목으로 해석된다.
  나뉜 구역(인천 중구 → 제물포구·영종구, 서구 → 서해구·검단구)은 후보를 모두 보여주도록 둘 다 잇는다.
- 이미 같은 코드나 같은 이름의 현행 항목이 있으면 넣지 않는다 (사용자가 올린 항목 우선).
  기본 별칭은 해석 순서에서 사용자 별칭·기준일에 유효한 정식명칭보다 뒤에 쓴다 (app/glossary.py).

Revision ID: 0012_reorg_base_entries
Revises: 0011_sido_base_entries
Create Date: 2026-10-10

"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0012_reorg_base_entries"
down_revision: str | None = "0011_sido_base_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "base"
NOTE = "기본 항목 (행정구역 개편, 행정표준코드 기관코드·행정동 코드표 기준)"
# (7자리 코드, 정식명칭, 시작일, 종료일(그날까지), 이전코드)
ENTRIES = [
    ("3490000", "인천광역시 중구", None, date(2026, 6, 30), None),
    ("3491000", "인천광역시 영종구", date(2026, 7, 1), None, "3490000"),
    ("3500000", "인천광역시 동구", None, date(2026, 6, 30), None),
    ("3501000", "인천광역시 제물포구", date(2026, 7, 1), None, "3490000,3500000"),
    ("3510000", "인천광역시 남구", None, date(2018, 6, 30), None),
    ("3510500", "인천광역시 미추홀구", date(2018, 7, 1), None, "3510000"),
    ("3560000", "인천광역시 서구", None, date(2026, 6, 30), None),
    ("3561000", "인천광역시 서해구", date(2026, 7, 1), None, "3560000"),
    ("3565000", "인천광역시 검단구", date(2026, 7, 1), None, "3560000"),
    ("3590000", "광주광역시 동구", None, date(2026, 6, 30), None),
    ("3600000", "광주광역시 서구", None, date(2026, 6, 30), None),
    ("3610000", "광주광역시 남구", None, date(2026, 6, 30), None),
    ("3620000", "광주광역시 북구", None, date(2026, 6, 30), None),
    ("3630000", "광주광역시 광산구", None, date(2026, 6, 30), None),
    ("4180000", "강원도 춘천시", None, date(2023, 6, 10), None),
    ("4181000", "강원특별자치도 춘천시", date(2023, 6, 11), None, "4180000"),
    ("4190000", "강원도 원주시", None, date(2023, 6, 10), None),
    ("4191000", "강원특별자치도 원주시", date(2023, 6, 11), None, "4190000"),
    ("4200000", "강원도 강릉시", None, date(2023, 6, 10), None),
    ("4201000", "강원특별자치도 강릉시", date(2023, 6, 11), None, "4200000"),
    ("4210000", "강원도 동해시", None, date(2023, 6, 10), None),
    ("4211000", "강원특별자치도 동해시", date(2023, 6, 11), None, "4210000"),
    ("4220000", "강원도 태백시", None, date(2023, 6, 10), None),
    ("4221000", "강원특별자치도 태백시", date(2023, 6, 11), None, "4220000"),
    ("4230000", "강원도 속초시", None, date(2023, 6, 10), None),
    ("4231000", "강원특별자치도 속초시", date(2023, 6, 11), None, "4230000"),
    ("4240000", "강원도 삼척시", None, date(2023, 6, 10), None),
    ("4241000", "강원특별자치도 삼척시", date(2023, 6, 11), None, "4240000"),
    ("4250000", "강원도 홍천군", None, date(2023, 6, 10), None),
    ("4251000", "강원특별자치도 홍천군", date(2023, 6, 11), None, "4250000"),
    ("4260000", "강원도 횡성군", None, date(2023, 6, 10), None),
    ("4261000", "강원특별자치도 횡성군", date(2023, 6, 11), None, "4260000"),
    ("4270000", "강원도 영월군", None, date(2023, 6, 10), None),
    ("4271000", "강원특별자치도 영월군", date(2023, 6, 11), None, "4270000"),
    ("4280000", "강원도 평창군", None, date(2023, 6, 10), None),
    ("4281000", "강원특별자치도 평창군", date(2023, 6, 11), None, "4280000"),
    ("4290000", "강원도 정선군", None, date(2023, 6, 10), None),
    ("4291000", "강원특별자치도 정선군", date(2023, 6, 11), None, "4290000"),
    ("4300000", "강원도 철원군", None, date(2023, 6, 10), None),
    ("4301000", "강원특별자치도 철원군", date(2023, 6, 11), None, "4300000"),
    ("4310000", "강원도 화천군", None, date(2023, 6, 10), None),
    ("4311000", "강원특별자치도 화천군", date(2023, 6, 11), None, "4310000"),
    ("4320000", "강원도 양구군", None, date(2023, 6, 10), None),
    ("4321000", "강원특별자치도 양구군", date(2023, 6, 11), None, "4320000"),
    ("4330000", "강원도 인제군", None, date(2023, 6, 10), None),
    ("4331000", "강원특별자치도 인제군", date(2023, 6, 11), None, "4330000"),
    ("4340000", "강원도 고성군", None, date(2023, 6, 10), None),
    ("4341000", "강원특별자치도 고성군", date(2023, 6, 11), None, "4340000"),
    ("4350000", "강원도 양양군", None, date(2023, 6, 10), None),
    ("4351000", "강원특별자치도 양양군", date(2023, 6, 11), None, "4350000"),
    ("4640000", "전라북도 전주시", None, date(2024, 1, 17), None),
    ("4641000", "전북특별자치도 전주시", date(2024, 1, 18), None, "4640000"),
    ("4670000", "전라북도 군산시", None, date(2024, 1, 17), None),
    ("4671000", "전북특별자치도 군산시", date(2024, 1, 18), None, "4670000"),
    ("4680000", "전라북도 익산시", None, date(2024, 1, 17), None),
    ("4681000", "전북특별자치도 익산시", date(2024, 1, 18), None, "4680000"),
    ("4690000", "전라북도 정읍시", None, date(2024, 1, 17), None),
    ("4691000", "전북특별자치도 정읍시", date(2024, 1, 18), None, "4690000"),
    ("4700000", "전라북도 남원시", None, date(2024, 1, 17), None),
    ("4701000", "전북특별자치도 남원시", date(2024, 1, 18), None, "4700000"),
    ("4710000", "전라북도 김제시", None, date(2024, 1, 17), None),
    ("4711000", "전북특별자치도 김제시", date(2024, 1, 18), None, "4710000"),
    ("4720000", "전라북도 완주군", None, date(2024, 1, 17), None),
    ("4721000", "전북특별자치도 완주군", date(2024, 1, 18), None, "4720000"),
    ("4730000", "전라북도 진안군", None, date(2024, 1, 17), None),
    ("4731000", "전북특별자치도 진안군", date(2024, 1, 18), None, "4730000"),
    ("4740000", "전라북도 무주군", None, date(2024, 1, 17), None),
    ("4741000", "전북특별자치도 무주군", date(2024, 1, 18), None, "4740000"),
    ("4750000", "전라북도 장수군", None, date(2024, 1, 17), None),
    ("4751000", "전북특별자치도 장수군", date(2024, 1, 18), None, "4750000"),
    ("4760000", "전라북도 임실군", None, date(2024, 1, 17), None),
    ("4761000", "전북특별자치도 임실군", date(2024, 1, 18), None, "4760000"),
    ("4770000", "전라북도 순창군", None, date(2024, 1, 17), None),
    ("4771000", "전북특별자치도 순창군", date(2024, 1, 18), None, "4770000"),
    ("4780000", "전라북도 고창군", None, date(2024, 1, 17), None),
    ("4781000", "전북특별자치도 고창군", date(2024, 1, 18), None, "4780000"),
    ("4790000", "전라북도 부안군", None, date(2024, 1, 17), None),
    ("4791000", "전북특별자치도 부안군", date(2024, 1, 18), None, "4790000"),
    ("4800000", "전라남도 목포시", None, date(2026, 6, 30), None),
    ("4810000", "전라남도 여수시", None, date(2026, 6, 30), None),
    ("4820000", "전라남도 순천시", None, date(2026, 6, 30), None),
    ("4830000", "전라남도 나주시", None, date(2026, 6, 30), None),
    ("4840000", "전라남도 광양시", None, date(2026, 6, 30), None),
    ("4850000", "전라남도 담양군", None, date(2026, 6, 30), None),
    ("4860000", "전라남도 곡성군", None, date(2026, 6, 30), None),
    ("4870000", "전라남도 구례군", None, date(2026, 6, 30), None),
    ("4880000", "전라남도 고흥군", None, date(2026, 6, 30), None),
    ("4890000", "전라남도 보성군", None, date(2026, 6, 30), None),
    ("4900000", "전라남도 화순군", None, date(2026, 6, 30), None),
    ("4910000", "전라남도 장흥군", None, date(2026, 6, 30), None),
    ("4920000", "전라남도 강진군", None, date(2026, 6, 30), None),
    ("4930000", "전라남도 해남군", None, date(2026, 6, 30), None),
    ("4940000", "전라남도 영암군", None, date(2026, 6, 30), None),
    ("4950000", "전라남도 무안군", None, date(2026, 6, 30), None),
    ("4960000", "전라남도 함평군", None, date(2026, 6, 30), None),
    ("4970000", "전라남도 영광군", None, date(2026, 6, 30), None),
    ("4980000", "전라남도 장성군", None, date(2026, 6, 30), None),
    ("4990000", "전라남도 완도군", None, date(2026, 6, 30), None),
    ("5000000", "전라남도 진도군", None, date(2026, 6, 30), None),
    ("5010000", "전라남도 신안군", None, date(2026, 6, 30), None),
    ("5140000", "경상북도 군위군", None, date(2023, 6, 30), None),
    ("5141000", "대구광역시 군위군", date(2023, 7, 1), None, "5140000"),
    ("5780000", "전남광주통합특별시 목포시", date(2026, 7, 1), None, "4800000"),
    ("5785000", "전남광주통합특별시 여수시", date(2026, 7, 1), None, "4810000"),
    ("5790000", "전남광주통합특별시 순천시", date(2026, 7, 1), None, "4820000"),
    ("5795000", "전남광주통합특별시 나주시", date(2026, 7, 1), None, "4830000"),
    ("5800000", "전남광주통합특별시 광양시", date(2026, 7, 1), None, "4840000"),
    ("5805000", "전남광주통합특별시 동구", date(2026, 7, 1), None, "3590000"),
    ("5810000", "전남광주통합특별시 서구", date(2026, 7, 1), None, "3600000"),
    ("5815000", "전남광주통합특별시 남구", date(2026, 7, 1), None, "3610000"),
    ("5820000", "전남광주통합특별시 북구", date(2026, 7, 1), None, "3620000"),
    ("5825000", "전남광주통합특별시 광산구", date(2026, 7, 1), None, "3630000"),
    ("5830000", "전남광주통합특별시 담양군", date(2026, 7, 1), None, "4850000"),
    ("5835000", "전남광주통합특별시 곡성군", date(2026, 7, 1), None, "4860000"),
    ("5840000", "전남광주통합특별시 구례군", date(2026, 7, 1), None, "4870000"),
    ("5845000", "전남광주통합특별시 고흥군", date(2026, 7, 1), None, "4880000"),
    ("5850000", "전남광주통합특별시 보성군", date(2026, 7, 1), None, "4890000"),
    ("5855000", "전남광주통합특별시 화순군", date(2026, 7, 1), None, "4900000"),
    ("5860000", "전남광주통합특별시 장흥군", date(2026, 7, 1), None, "4910000"),
    ("5865000", "전남광주통합특별시 강진군", date(2026, 7, 1), None, "4920000"),
    ("5870000", "전남광주통합특별시 해남군", date(2026, 7, 1), None, "4930000"),
    ("5875000", "전남광주통합특별시 영암군", date(2026, 7, 1), None, "4940000"),
    ("5880000", "전남광주통합특별시 무안군", date(2026, 7, 1), None, "4950000"),
    ("5885000", "전남광주통합특별시 함평군", date(2026, 7, 1), None, "4960000"),
    ("5890000", "전남광주통합특별시 영광군", date(2026, 7, 1), None, "4970000"),
    ("5895000", "전남광주통합특별시 장성군", date(2026, 7, 1), None, "4980000"),
    ("5900000", "전남광주통합특별시 완도군", date(2026, 7, 1), None, "4990000"),
    ("5905000", "전남광주통합특별시 진도군", date(2026, 7, 1), None, "5000000"),
    ("5910000", "전남광주통합특별시 신안군", date(2026, 7, 1), None, "5010000"),
]

# 폐지 항목 → 승계 항목 (둘 이상이면 나뉜 구역: 그 이름은 '모호'로 후보를 모두 보여준다)
SUCCESSORS = {
    "3490000": (
        "3501000",
        "3491000",
    ),
    "3500000": ("3501000",),
    "3510000": ("3510500",),
    "3560000": (
        "3561000",
        "3565000",
    ),
    "3590000": ("5805000",),
    "3600000": ("5810000",),
    "3610000": ("5815000",),
    "3620000": ("5820000",),
    "3630000": ("5825000",),
    "4180000": ("4181000",),
    "4190000": ("4191000",),
    "4200000": ("4201000",),
    "4210000": ("4211000",),
    "4220000": ("4221000",),
    "4230000": ("4231000",),
    "4240000": ("4241000",),
    "4250000": ("4251000",),
    "4260000": ("4261000",),
    "4270000": ("4271000",),
    "4280000": ("4281000",),
    "4290000": ("4291000",),
    "4300000": ("4301000",),
    "4310000": ("4311000",),
    "4320000": ("4321000",),
    "4330000": ("4331000",),
    "4340000": ("4341000",),
    "4350000": ("4351000",),
    "4640000": ("4641000",),
    "4670000": ("4671000",),
    "4680000": ("4681000",),
    "4690000": ("4691000",),
    "4700000": ("4701000",),
    "4710000": ("4711000",),
    "4720000": ("4721000",),
    "4730000": ("4731000",),
    "4740000": ("4741000",),
    "4750000": ("4751000",),
    "4760000": ("4761000",),
    "4770000": ("4771000",),
    "4780000": ("4781000",),
    "4790000": ("4791000",),
    "4800000": ("5780000",),
    "4810000": ("5785000",),
    "4820000": ("5790000",),
    "4830000": ("5795000",),
    "4840000": ("5800000",),
    "4850000": ("5830000",),
    "4860000": ("5835000",),
    "4870000": ("5840000",),
    "4880000": ("5845000",),
    "4890000": ("5850000",),
    "4900000": ("5855000",),
    "4910000": ("5860000",),
    "4920000": ("5865000",),
    "4930000": ("5870000",),
    "4940000": ("5875000",),
    "4950000": ("5880000",),
    "4960000": ("5885000",),
    "4970000": ("5890000",),
    "4980000": ("5895000",),
    "4990000": ("5900000",),
    "5000000": ("5905000",),
    "5010000": ("5910000",),
    "5140000": ("5141000",),
}

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
                "type_label": "기초자치단체",
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

    # 개편 전 이름 → 승계 항목 (승계 항목의 시작일부터). 사전에 없는 승계 항목에는 잇지 않는다
    present = {r.code for r in bind.execute(sa.select(entries.c.code)).all()}
    existing = {
        (r.normalized, r.target_code, r.valid_from)
        for r in bind.execute(sa.select(aliases.c.normalized, aliases.c.target_code, aliases.c.valid_from)).all()
    }
    names = {code: name for code, name, *_ in ENTRIES}
    starts = {code: valid_from for code, _, valid_from, *_ in ENTRIES}
    new_aliases = []
    for old, targets in SUCCESSORS.items():
        sido, leaf = names[old].split(" ", 1)
        forms = [names[old], *sorted(f"{short} {leaf}" for short in sido_short_names(sido))]
        for target in targets:
            if target not in present:
                continue
            for form in forms:
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
    # 이 마이그레이션이 넣은 기본 별칭·항목만 지운다 (0011의 광역 기본 항목은 그대로)
    targets = sorted({t for ts in SUCCESSORS.values() for t in ts})
    op.execute(aliases.delete().where(aliases.c.source == SOURCE, aliases.c.target_code.in_(targets)))
    # 사용자 별칭(source가 base가 아닌)이 가리키는 항목은 남긴다 (지우면 별칭이 대상 없이 남는다)
    user_refs = sa.select(aliases.c.target_code).where(aliases.c.source != SOURCE)
    codes = [e[0] for e in ENTRIES]
    op.execute(
        entries.delete().where(entries.c.source == SOURCE, entries.c.code.in_(codes), entries.c.code.not_in(user_refs))
    )
