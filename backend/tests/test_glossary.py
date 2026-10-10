from datetime import date
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.glossary import AliasEntry, Entry, Glossary, period_end
from app.validation import ValidationParams, validate
from tests.test_validation import GOOD_MERGES, GOOD_ROWS, make_input, make_xlsx

AT = period_end("2026-09")

# 마이그레이션 예시와 같은 항목 (7자리 기관코드, 자치단체는 시도 2자리·시군구 3자리)
EXAMPLES = [
    Entry("1741000", "행정안전부", "중앙행정기관", valid_from=date(2017, 7, 26)),
    Entry("1360000", "기상청", "중앙행정기관", valid_from=date(1990, 12, 27)),
    Entry("1760000", "인사혁신처", "중앙행정기관", valid_from=date(2014, 11, 19)),
    Entry("1052000", "기획예산처", "중앙행정기관", valid_from=date(2025, 12, 19)),
    Entry("1053000", "재정경제부", "중앙행정기관", valid_from=date(2026, 1, 2), previous_codes=("1051000",)),
    Entry("1051000", "기획재정부", "중앙행정기관", valid_from=date(2008, 2, 29), valid_to=date(2025, 12, 31)),
    Entry("6110000", "서울특별시", "광역자치단체", "11", None, date(1988, 4, 23)),
    Entry("3150000", "서울특별시 강서구", "기초자치단체", "11", "500", date(1977, 9, 1)),
    Entry("3220000", "서울특별시 강남구", "기초자치단체", "11", "680", date(2000, 1, 1)),
    Entry("6260000", "부산광역시", "광역자치단체", "26", None, date(1995, 1, 1)),
    Entry("3360000", "부산광역시 강서구", "기초자치단체", "26", "440", date(1989, 1, 1)),
    Entry("6130000", "전남광주통합특별시", "광역자치단체", "12", None, date(2026, 5, 21)),
    Entry("6290000", "광주광역시", "광역자치단체", "29", None, date(1995, 1, 1), date(2026, 6, 30)),
]


def glossary(**aliases: str) -> Glossary:
    return Glossary(EXAMPLES, {k: [AliasEntry(v)] for k, v in aliases.items()})


@pytest.mark.parametrize(
    ("name", "prefer", "status", "code"),
    [
        ("행정안전부", "org", "resolved", "1741000"),
        ("기상청", "org", "resolved", "1360000"),
        ("서울특별시", "region", "resolved", "6110000"),
        ("서울", "region", "resolved", "6110000"),  # 시도 약칭 (시도코드 11에서 생성)
        ("서울시", "region", "resolved", "6110000"),
        ("서울 강서구", "region", "resolved", "3150000"),  # 시도·시군구 코드로 상위 시도를 찾아 생성
        ("부산광역시 강서구", "region", "resolved", "3360000"),
        ("강서구", "region", "ambiguous", None),  # 서울·부산
        ("없는기관이름123", "org", "unknown", None),
    ],
)
def test_resolve(name: str, prefer: str, status: str, code: str | None) -> None:
    r = glossary().resolve(name, AT, prefer)  # type: ignore[arg-type]
    assert r.status == status
    assert (r.ref.code if r.status == "resolved" else None) == code


def test_region_code_lookup() -> None:
    g = glossary()
    assert g.by_region_code("11").code == "6110000"
    assert g.by_region_code("11500").code == "3150000"
    assert g.by_region_code("1150052000").code == "3150000"  # 10자리 행정동 코드는 앞 5자리(시도+시군구)
    assert g.by_region_code("29", AT).code == "6290000"  # 종료됐어도 유일하면 연결 (확인 대상에서 표시)
    assert g.by_region_code("99") is None


def test_period_aware_entries() -> None:
    g = glossary()
    r = g.resolve("기획재정부", AT, "org")
    assert r.ref.code == "1051000" and "2025-12-31까지만 유효" in (r.note or "")
    assert g.resolve("기획재정부", period_end("2025-09"), "org").note is None
    assert "까지만 유효" in (g.resolve("광주광역시", AT, "region").note or "")
    assert "부터 유효" in (g.resolve("전남광주통합특별시", period_end("2026-04"), "region").note or "")


def test_suggestions_and_successors() -> None:
    g = glossary()
    assert g.resolve("행정안전 부처", AT, "org").candidates[0].code == "1741000"
    old = g.resolve("기획재정부", AT, "org").ref
    assert g.replacements(old, AT)[0].code == "1053000"  # 이전코드로 연결된 항목이 먼저


def test_alias_with_validity_period() -> None:
    g = Glossary(
        EXAMPLES,
        {
            "행안부": [AliasEntry("1741000")],
            "재정부처": [AliasEntry("1051000", None, date(2025, 12, 31)), AliasEntry("1052000", date(2026, 1, 1))],
        },
    )
    assert g.resolve("행 안 부", AT, "org").ref.code == "1741000"
    assert g.resolve("재정부처", date(2025, 6, 30), "org").ref.code == "1051000"
    assert g.resolve("재정부처", AT, "org").ref.code == "1052000"


# ---------------------------------------------------------------- 검증 연결


def _org_file(tmp_path: Path, name: str, orgs: list[str], file_id: int = 1):
    rows = [["기관명", "건수"], *[[o, i + 1] for i, o in enumerate(orgs)]]
    return make_input(make_xlsx(tmp_path / name, rows), file_id)


def test_required_entities_compared_by_code(tmp_path: Path) -> None:
    f = _org_file(tmp_path, "a.xlsx", ["행안부", "기상청"])
    params = ValidationParams(
        expected_period="2026-09", required_entities=["행정안전부", "기상청"], entity_field="org_name"
    )
    blocked = validate([f], params, glossary=glossary())
    assert [b.expected for b in blocked.blocks if b.code == "required_entity_missing"] == ["행정안전부"]
    assert any(e.value == "행안부" and e.status == "unknown" for e in blocked.entities)
    ok = validate([f], params, glossary=glossary(행안부="1741000"))
    assert [b for b in ok.blocks if b.code == "required_entity_missing"] == []


def test_region_code_column_matches_entity(tmp_path: Path) -> None:
    rows = [["지역코드", "건수"], ["11500", 3], ["26440", 4]]  # 데이터의 지역코드 열 (시도+시군구 5자리)
    f = make_input(make_xlsx(tmp_path / "codes.xlsx", rows))
    params = ValidationParams(
        required_entities=["서울특별시 강서구", "부산 강서구", "서울특별시 강남구"], entity_field="sigungu"
    )
    missing = [
        b.expected for b in validate([f], params, glossary=glossary()).blocks if b.code == "required_entity_missing"
    ]
    assert missing == ["서울특별시 강남구"]


def test_entity_notes_flag_ended_names(tmp_path: Path) -> None:
    f = _org_file(tmp_path, "b.xlsx", ["기획재정부", "기상청", "행정안전부미래국"])
    result = validate([f], ValidationParams(expected_period="2026-09"), glossary=glossary())
    notes = {e.value: e for e in result.entities}
    assert notes["기획재정부"].status == "abolished" and notes["기획재정부"].candidates[0]["code"] == "1053000"
    assert notes["행정안전부미래국"].status == "unknown" and notes["행정안전부미래국"].candidates
    assert "기상청" not in notes
    assert result.status in ("passed", "passed_with_warnings")  # 확인 대상은 상태에 영향 없음


def test_mom_matches_short_and_full_region_names(tmp_path: Path) -> None:
    cur_rows = [r[:] for r in GOOD_ROWS]
    cur_rows[2][0] = "서울"
    prev_rows = [r[:] for r in GOOD_ROWS]
    prev_rows[0][0] = "2026년 8월 시군구별 인구 현황"
    prev_rows[3][2] = 300000
    cur = make_input(make_xlsx(tmp_path / "cur.xlsx", cur_rows, GOOD_MERGES), 1)
    prev = make_input(make_xlsx(tmp_path / "prev.xlsx", prev_rows, GOOD_MERGES), 2)
    params = ValidationParams(expected_period="2026-09")
    assert not [w for w in validate([cur], params, baseline=[prev]).warnings if w.code == "mom_outlier"]
    mom = [w for w in validate([cur], params, baseline=[prev], glossary=glossary()).warnings if w.code == "mom_outlier"]
    assert [(w.row, w.expected, w.actual) for w in mom] == [(4, 300000, 551442)]


# ---------------------------------------------------------------- API (업로드 포함)


@pytest.fixture(scope="module")
def client():
    from app.db import Base, engine
    from app.main import app

    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


def _upload(client: TestClient, name: str, data: bytes) -> dict:
    res = client.post("/glossary/import", files=[("file", (name, data, "application/octet-stream"))])
    assert res.status_code == 200, res.text
    return res.json()


CSV_OK = (
    "코드,정식명칭,유형,시도코드,시군구코드,시작일,종료일,이전코드,별칭,메모\n"
    "1741000,행정안전부,중앙행정기관,,,2017-07-26,,,행안부; 행자부,\n"
    "1051000,기획재정부,중앙행정기관,,,2008-02-29,2025-12-31,,기재부,\n"
    "1053000,재정경제부,중앙행정기관,,,2026-01-02,,1051000,,\n"
    "6110000,서울특별시,광역자치단체,11,,,,,,\n"
    "3150000,서울특별시 강서구,기초자치단체,11,500,,,,,\n"
)


def test_import_csv_and_resolve(client: TestClient) -> None:
    report = _upload(client, "사전.csv", CSV_OK.encode("cp949"))
    assert report["applied"] and (report["inserted"], report["aliases_added"], report["errors"]) == (5, 3, [])
    entries = {e["code"]: e for e in client.get("/glossary/entries").json()}
    assert (entries["3150000"]["sido_code"], entries["3150000"]["sigungu_code"]) == ("11", "500")
    assert entries["1741000"]["aliases"] == ["행안부", "행자부"]
    res = client.get("/glossary/resolve", params={"name": "행자부", "period": "2026-09"}).json()
    assert res["status"] == "resolved" and res["ref"]["code"] == "1741000"
    res = client.get("/glossary/resolve", params={"name": "서울 강서구", "prefer": "region"}).json()
    assert res["ref"]["code"] == "3150000"
    regions = client.get("/glossary/entries", params={"scope": "region"}).json()
    assert {e["code"] for e in regions} == {"6110000", "3150000"}

    again = _upload(client, "사전.csv", CSV_OK.encode("utf-8-sig"))
    assert (again["inserted"], again["updated"], again["aliases_added"]) == (0, 5, 0)


def test_import_rejects_whole_file_on_error(client: TestClient) -> None:
    before = len(client.get("/glossary/entries").json())
    bad = (
        "코드,정식명칭,시도코드,시군구코드,시작일,종료일\n"
        "1360000,기상청,,,1990-12-27,\n"
        "11500,서울 강서구,11,500,,\n"  # 7자리가 아님
        "3150001,어딘가,,500,,\n"  # 시도코드 없이 시군구코드
        "1360000,기상청 중복,,,,\n"  # 같은 코드 중복
        "1760000,인사혁신처,,,2026-02-01,2026-01-01\n"  # 기간 역전
        "1111111,이상한시도,1A,,,\n"  # 시도코드 형식
    )
    report = _upload(client, "bad.csv", bad.encode("utf-8"))
    assert not report["applied"] and report["inserted"] == 0
    assert [e["row"] for e in report["errors"]] == [3, 4, 5, 6, 7]
    assert len(client.get("/glossary/entries").json()) == before


def test_import_xlsx_numeric_codes_and_warnings(client: TestClient, tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["코드", "정식명칭", "시도코드", "시군구코드", "별칭"])
    ws.append(["3360000", "부산광역시 강서구", 26, 440, "부산 강서"])  # 숫자 셀
    ws.append(["3999999", "가상구", 11, 510, None])  # 행정동 코드표에 없는 조합 → 경고
    ws.append(["3220000", "강남", 11, 680, None])  # 이름이 코드표와 다름 → 경고
    ws.append(["3150009", "서울 강서구 중복", 11, 500, None])  # 이미 등록된 3150000과 같은 시군구 → 경고
    ws.append(["4100000", "가상 군", 41, 50, None])  # 엑셀에서 050 → 50으로 바뀐 경우 앞자리 0 채움
    path = tmp_path / "region.xlsx"
    wb.save(path)
    report = _upload(client, "region.xlsx", path.read_bytes())
    assert report["applied"] and report["inserted"] == 5, report
    assert sorted({w["row"] for w in report["warnings"]}) == [3, 4, 5, 6]
    entries = {e["code"]: e for e in client.get("/glossary/entries", params={"scope": "region"}).json()}
    assert entries["4100000"]["sigungu_code"] == "050"


def test_template_export_roundtrip_and_aliases(client: TestClient) -> None:
    template = client.get("/glossary/template.csv")
    assert template.status_code == 200 and template.text.startswith("﻿코드,정식명칭,유형,시도코드,시군구코드")
    exported = client.get("/glossary/export.csv")
    assert "1741000" in exported.text and "행안부; 행자부" in exported.text
    roundtrip = _upload(client, "export.csv", exported.content)
    assert roundtrip["applied"] and roundtrip["errors"] == [] and roundtrip["inserted"] == 0

    created = client.post("/glossary/aliases", json={"alias": "안전부", "target_code": "1741000"})
    assert created.status_code == 201 and created.json()["target_name"] == "행정안전부"
    assert client.post("/glossary/aliases", json={"alias": "안 전부", "target_code": "1741000"}).status_code == 409
    assert client.post("/glossary/aliases", json={"alias": "x", "target_code": "9999999"}).status_code == 422
    assert client.post("/glossary/aliases", json={"alias": "x", "target_code": "11500"}).status_code == 422

    entry = next(
        e for e in client.get("/glossary/entries", params={"q": "행정안전부"}).json() if e["code"] == "1741000"
    )
    assert client.delete(f"/glossary/entries/{entry['id']}").status_code == 204
    assert all(a["target_code"] != "1741000" for a in client.get("/glossary/aliases").json())
    info = client.get("/glossary/info").json()
    assert info["region_code_table_date"] == "2026-09-30" and info["region_entries"] >= 2


def test_sido_short_names_common_forms() -> None:
    from app.glossary import sido_short_names

    assert sido_short_names("충청북도") == {"충청북", "충북"}
    assert sido_short_names("경상남도") == {"경상남", "경남"}
    assert sido_short_names("세종특별자치시") == {"세종", "세종시"}
    # '제주시'는 실제 기초자치단체 이름이라 도(특별자치도)에는 '…시' 약칭을 만들지 않는다
    assert sido_short_names("제주특별자치도") == {"제주"}


def test_region_shorts_resolve_with_base_entries() -> None:
    """기본 광역 항목 + 코드가 채워진 기초 항목이면 '세종', '충북', '서울 중구'를 해석한다."""
    entries = [
        Entry("5690000", "세종특별자치시", "광역자치단체", "36", None),
        Entry("6430000", "충청북도", "광역자치단체", "43", None),
        Entry("6110000", "서울특별시", "광역자치단체", "11", None),
        Entry("3010000", "서울특별시 중구", "기초자치단체", "11", "140"),
    ]
    g = Glossary(entries, {})
    for name, code in [("세종", "5690000"), ("충북", "6430000"), ("서울 중구", "3010000")]:
        res = g.resolve(name, AT, prefer="region")
        assert (res.status, res.ref.code if res.ref else None) == ("resolved", code), name


def test_infer_region_codes_from_admin_table() -> None:
    from app.admin_codes import infer_region_codes

    assert infer_region_codes("서울특별시 강남구") == ("11", "680")
    assert infer_region_codes("서울특별시") == ("11", None)
    assert infer_region_codes("전남광주통합특별시 목포시") == ("12", "110")
    assert infer_region_codes("광주광역시") == ("29", None)  # 말소 코드도 찾는다
    assert infer_region_codes("행정안전부") is None


def test_import_fills_missing_region_codes_and_keeps_existing(client: TestClient) -> None:
    csv = "코드,정식명칭,유형\n3010000,서울특별시 중구,기초자치단체\n".encode()
    report = client.post("/glossary/import", files=[("file", ("g.csv", csv, "text/csv"))]).json()
    assert report["applied"] and any("행정동 코드표로" in w["message"] for w in report["warnings"])
    entry = next(e for e in client.get("/glossary/entries", params={"q": "3010000"}).json() if e["code"] == "3010000")
    assert (entry["sido_code"], entry["sigungu_code"]) == ("11", "140")
    # 코드표에 없는 이름으로 다시 올려도(빈 칸) 이미 있는 코드는 지우지 않는다
    csv = "코드,정식명칭,유형\n3010000,서울 중구청,기초자치단체\n".encode()
    assert client.post("/glossary/import", files=[("file", ("g.csv", csv, "text/csv"))]).json()["applied"]
    entry = next(e for e in client.get("/glossary/entries", params={"q": "3010000"}).json() if e["code"] == "3010000")
    assert (entry["sido_code"], entry["sigungu_code"]) == ("11", "140")
    client.delete(f"/glossary/entries/{entry['id']}")


REORG = [
    Entry("6280000", "인천광역시", "광역자치단체", "28"),
    Entry("3500000", "인천광역시 동구", "기초자치단체", "28", "140", valid_to=date(2026, 6, 30)),
    Entry("3490000", "인천광역시 중구", "기초자치단체", "28", "110", valid_to=date(2026, 6, 30)),
    Entry("3501000", "인천광역시 제물포구", "기초자치단체", "28", "125", valid_from=date(2026, 7, 1)),
    Entry("3491000", "인천광역시 영종구", "기초자치단체", "28", "155", valid_from=date(2026, 7, 1)),
]
REORG_ALIASES = {
    "인천동구": [AliasEntry("3501000", date(2026, 7, 1), base=True)],
    "인천중구": [
        AliasEntry("3501000", date(2026, 7, 1), base=True),
        AliasEntry("3491000", date(2026, 7, 1), base=True),
    ],
}


def test_reorganized_names_follow_reference_date() -> None:
    g = Glossary(REORG, REORG_ALIASES)
    before = g.resolve("인천 동구", period_end("2025-04"), "region")
    after = g.resolve("인천 동구", period_end("2026-08"), "region")
    assert (before.status, before.ref.code) == ("resolved", "3500000")
    assert (after.status, after.ref.code, after.note) == ("resolved", "3501000", None)
    split = g.resolve("인천 중구", period_end("2026-08"), "region")
    assert split.status == "ambiguous" and {c.code for c in split.candidates} == {"3501000", "3491000"}


def test_user_alias_and_valid_name_beat_base_alias() -> None:
    """사용자가 등록한 별칭, 기준일에 유효한 정식명칭은 기본 별칭(개편 승계)보다 먼저다."""
    user = {"인천동구": [*REORG_ALIASES["인천동구"], AliasEntry("3491000")]}
    g = Glossary(REORG, user)
    assert g.resolve("인천 동구", period_end("2026-08"), "region").ref.code == "3491000"
    current_old_name = [*REORG[:1], Entry("3500000", "인천광역시 동구", "기초자치단체", "28", "140"), *REORG[2:]]
    g = Glossary(current_old_name, REORG_ALIASES)  # 사용자가 옛 이름을 현행 항목으로 올렸으면 그 항목
    assert g.resolve("인천광역시 동구", period_end("2026-08"), "region").ref.code == "3500000"


def test_date_values_are_not_names() -> None:
    g = glossary()
    for value in ("2026-08", "2026년", "2026.08.31", "2026년 8월"):
        res = g.resolve(value, AT, "region")
        assert (res.status, res.candidates) == ("unknown", []), value


def test_reference_period_inferred_from_file(tmp_path: Path) -> None:
    """기준 기간이 없으면 자료의 기간(파일명 8자리 날짜 포함)으로 '광주'·'전남' 같은 옛 이름을 해석한다."""
    entries = [
        Entry("6290000", "광주광역시", "광역자치단체", "29", valid_to=date(2026, 6, 30)),
        Entry("6460000", "전라남도", "광역자치단체", "46", valid_to=date(2026, 6, 30)),
        Entry("6130000", "전남광주통합특별시", "광역자치단체", "12", valid_from=date(2026, 7, 1)),
    ]
    rows = [["지역", "관용"], ["광주", 1], ["전남", 2]]
    path = make_xlsx(tmp_path / "수소차_20240331.xlsx", rows)
    result = validate([make_input(path)], ValidationParams(), glossary=Glossary(entries, {}))
    assert result.entities == []
    assert any("자료의 기간(2024-03)" in n for n in result.notes)
    # 기준 기간을 주면 그 기간이 먼저다 (통합 후 기준이면 옛 이름으로 표시)
    result = validate([make_input(path)], ValidationParams(expected_period="2026-08"), glossary=Glossary(entries, {}))
    assert {e.value for e in result.entities} == {"광주", "전남"}


def test_export_leaves_out_base_aliases(client: TestClient) -> None:
    """기본 별칭(개편 승계, 유효기간 있음)은 내보내지 않는다 — 다시 올리면 기간 없는 별칭이 되기 때문."""
    from app.db import SessionLocal
    from app.models import GlossaryAlias

    csv = "코드,정식명칭,유형\n3501000,인천광역시 제물포구,기초자치단체\n".encode()
    assert client.post("/glossary/import", files=[("file", ("g.csv", csv, "text/csv"))]).json()["applied"]
    with SessionLocal() as db:
        db.add(
            GlossaryAlias(
                alias="인천 동구",
                normalized="인천동구",
                target_code="3501000",
                valid_from=date(2026, 7, 1),
                source="base",
            )
        )
        db.commit()
    assert "인천 동구" not in client.get("/glossary/export.csv").text
    entry = next(e for e in client.get("/glossary/entries", params={"q": "3501000"}).json() if e["code"] == "3501000")
    assert "인천 동구" in entry["aliases"]  # 목록 화면에는 보인다
    client.delete(f"/glossary/entries/{entry['id']}")
