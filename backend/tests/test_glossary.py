from datetime import date
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.glossary import AliasEntry, Entry, Glossary, period_end
from app.validation import ValidationParams, validate
from tests.test_validation import GOOD_MERGES, GOOD_ROWS, make_input, make_xlsx

AT = period_end("2026-09")

# 마이그레이션 예시와 같은 항목 (테스트는 마이그레이션 없이 직접 만든다)
EXAMPLES = [
    Entry("org", "1741000", "행정안전부", "중앙행정기관", None, date(2017, 7, 26)),
    Entry("org", "1360000", "기상청", "중앙행정기관", None, date(1990, 12, 27)),
    Entry("org", "1760000", "인사혁신처", "중앙행정기관", None, date(2014, 11, 19)),
    Entry("org", "1052000", "기획예산처", "중앙행정기관", None, date(2025, 12, 19)),
    Entry("org", "1053000", "재정경제부", "중앙행정기관", None, date(2026, 1, 2), None, ("1051000",)),
    Entry("org", "1051000", "기획재정부", "중앙행정기관", None, date(2008, 2, 29), date(2025, 12, 31)),
    Entry("region", "11", "서울특별시", "시도", None, date(1988, 4, 23)),
    Entry("region", "11500", "서울특별시 강서구", "시군구", "11", date(1988, 4, 23)),
    Entry("region", "11680", "서울특별시 강남구", "시군구", "11", date(1988, 4, 23)),
    Entry("region", "26", "부산광역시", "시도", None, date(1995, 1, 1)),
    Entry("region", "26440", "부산광역시 강서구", "시군구", "26", date(1995, 1, 1)),
    Entry("region", "12", "전남광주통합특별시", "시도", None, date(2026, 7, 1)),
    Entry("region", "29", "광주광역시", "시도", None, date(1995, 1, 1), date(2026, 6, 30)),
]


def glossary(**aliases: str) -> Glossary:
    return Glossary(EXAMPLES, {k: [AliasEntry("org", v, None, None)] for k, v in aliases.items()})


@pytest.mark.parametrize(
    ("name", "prefer", "status", "code"),
    [
        ("행정안전부", "org", "resolved", "1741000"),
        ("기상청", "org", "resolved", "1360000"),
        ("서울", "region", "resolved", "11"),  # 시도 약칭 자동 생성
        ("서울시", "region", "resolved", "11"),
        ("서울 강서구", "region", "resolved", "11500"),
        ("서울특별시 강서구", "region", "resolved", "11500"),
        ("부산광역시 강서구", "region", "resolved", "26440"),
        ("전남광주통합특별시", "region", "resolved", "12"),
        ("강서구", "region", "ambiguous", None),  # 서울·부산
        ("없는기관이름123", "org", "unknown", None),
    ],
)
def test_resolve(name: str, prefer: str, status: str, code: str | None) -> None:
    r = glossary().resolve(name, AT, prefer)  # type: ignore[arg-type]
    assert r.status == status
    assert (r.ref.code if r.status == "resolved" else None) == code


def test_period_aware_entries() -> None:
    g = glossary()
    r = g.resolve("기획재정부", AT, "org")
    assert r.ref.code == "1051000" and "2025-12-31까지만 유효" in (r.note or "")
    assert g.resolve("기획재정부", period_end("2025-09"), "org").note is None
    assert "까지만 유효" in (g.resolve("광주광역시", AT, "region").note or "")
    assert g.resolve("광주광역시", period_end("2026-05"), "region").note is None
    assert "부터 유효" in (g.resolve("전남광주통합특별시", period_end("2026-05"), "region").note or "")


def test_suggestions_and_successors() -> None:
    g = glossary()
    assert g.resolve("행정안전 부처", AT, "org").candidates[0].code == "1741000"
    old = g.resolve("기획재정부", AT, "org").ref
    assert g.replacements(old, AT)[0].code == "1053000"  # 이전코드로 연결된 항목이 먼저


def test_alias_with_validity_period() -> None:
    g = Glossary(
        EXAMPLES,
        {
            "행안부": [AliasEntry("org", "1741000", None, None)],
            "재정부처": [
                AliasEntry("org", "1051000", None, date(2025, 12, 31)),
                AliasEntry("org", "1052000", date(2026, 1, 1), None),
            ],
        },
    )
    assert g.resolve("행 안 부", AT, "org").ref.code == "1741000"
    assert g.resolve("재정부처", date(2025, 6, 30), "org").ref.code == "1051000"
    assert g.resolve("재정부처", AT, "org").ref.code == "1052000"


# ---------------------------------------------------------------- 검증 연결


def _org_file(tmp_path: Path, name: str, orgs: list[str], file_id: int = 1):
    rows = [["기관명", "건수"], *[[o, i + 1] for i, o in enumerate(orgs)]]
    return make_input(make_xlsx(tmp_path / name, rows), file_id)


def test_required_entities_compared_by_glossary_code(tmp_path: Path) -> None:
    f = _org_file(tmp_path, "a.xlsx", ["행안부", "기상청"])
    params = ValidationParams(
        expected_period="2026-09", required_entities=["행정안전부", "기상청"], entity_field="org_name"
    )
    blocked = validate([f], params, glossary=glossary())
    assert [b.expected for b in blocked.blocks if b.code == "required_entity_missing"] == ["행정안전부"]
    assert any(e.value == "행안부" and e.status == "unknown" for e in blocked.entities)
    ok = validate([f], params, glossary=glossary(행안부="1741000"))
    assert [b for b in ok.blocks if b.code == "required_entity_missing"] == []


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
    "구분,코드,정식명칭,유형,상위코드,시작일,종료일,이전코드,별칭,메모\n"
    "기관,1741000,행정안전부,중앙행정기관,,2017-07-26,,,행안부; 행자부,\n"
    "기관,1051000,기획재정부,중앙행정기관,,2008-02-29,2025-12-31,,기재부,\n"
    "기관,1053000,재정경제부,중앙행정기관,,2026-01-02,,1051000,,\n"
    "지역,11,서울특별시,,,,,,,\n"
    "지역,11500,서울특별시 강서구,,,,,,,\n"
)


def test_import_csv_and_resolve(client: TestClient) -> None:
    report = _upload(client, "사전.csv", CSV_OK.encode("cp949"))
    assert report["applied"] and (report["inserted"], report["aliases_added"], report["errors"]) == (5, 3, [])
    entries = {e["code"]: e for e in client.get("/glossary/entries").json()}
    assert entries["11500"]["parent_code"] == "11" and entries["11500"]["type"] == "시군구"  # 자동 채움
    assert entries["1741000"]["aliases"] == ["행안부", "행자부"]
    res = client.get("/glossary/resolve", params={"name": "행자부", "period": "2026-09"}).json()
    assert res["status"] == "resolved" and res["ref"]["code"] == "1741000"
    res = client.get("/glossary/resolve", params={"name": "서울 강서구", "prefer": "region"}).json()
    assert res["ref"]["code"] == "11500"

    # 같은 코드를 다시 올리면 갱신 (별칭은 중복 추가하지 않음)
    again = _upload(client, "사전.csv", CSV_OK.replace("서울특별시 강서구", "서울특별시 강서구").encode("utf-8-sig"))
    assert (again["inserted"], again["updated"], again["aliases_added"]) == (0, 5, 0)


def test_import_rejects_whole_file_on_error(client: TestClient) -> None:
    before = len(client.get("/glossary/entries").json())
    bad = (
        "구분,코드,정식명칭,시작일,종료일\n"
        "기관,1360000,기상청,1990-12-27,\n"
        "지역,1150052000,서울 강서구 등촌1동,,\n"  # 지역은 2·5자리만
        "부서,9,무엇,,\n"  # 구분 오류
        "기관,1360000,기상청 중복,,\n"  # 같은 코드 중복
        "기관,1760000,인사혁신처,2026-02-01,2026-01-01\n"  # 기간 역전
    )
    report = _upload(client, "bad.csv", bad.encode("utf-8"))
    assert not report["applied"] and report["inserted"] == 0
    assert sorted(e["row"] for e in report["errors"]) == [3, 4, 5, 6]
    assert len(client.get("/glossary/entries").json()) == before  # 하나도 반영되지 않음


def test_import_xlsx_with_region_code_warnings(client: TestClient, tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["구분", "코드", "정식명칭", "별칭"])
    ws.append(["지역", "26440", "부산광역시 강서구", "부산 강서"])
    ws.append(["지역", "11510", "가상구", None])  # 행정동 코드표에 없음 → 경고
    ws.append(["지역", "11680", "강남", None])  # 이름이 코드표와 다름 → 경고
    path = tmp_path / "region.xlsx"
    wb.save(path)
    report = _upload(client, "region.xlsx", path.read_bytes())
    assert report["applied"] and report["inserted"] == 3
    assert sorted(w["row"] for w in report["warnings"]) == [3, 4]


def test_template_export_roundtrip_and_aliases(client: TestClient) -> None:
    template = client.get("/glossary/template.csv")
    assert template.status_code == 200 and template.text.startswith("﻿구분,코드,정식명칭")
    exported = client.get("/glossary/export.csv")
    assert "1741000" in exported.text and "행안부; 행자부" in exported.text
    roundtrip = _upload(client, "export.csv", exported.content)
    assert roundtrip["applied"] and roundtrip["errors"] == [] and roundtrip["inserted"] == 0

    created = client.post("/glossary/aliases", json={"alias": "안전부", "kind": "org", "target_code": "1741000"})
    assert created.status_code == 201 and created.json()["target_name"] == "행정안전부"
    dup = {"alias": "안 전부", "kind": "org", "target_code": "1741000"}
    assert client.post("/glossary/aliases", json=dup).status_code == 409
    missing = {"alias": "x", "kind": "org", "target_code": "9999999"}
    assert client.post("/glossary/aliases", json=missing).status_code == 422

    entry = next(
        e for e in client.get("/glossary/entries", params={"q": "행정안전부"}).json() if e["code"] == "1741000"
    )
    assert client.delete(f"/glossary/entries/{entry['id']}").status_code == 204
    assert all(a["target_code"] != "1741000" for a in client.get("/glossary/aliases").json())  # 별칭도 함께 삭제
    info = client.get("/glossary/info").json()
    assert info["region_code_table_date"] == "2026-09-30" and info["entries"] >= 6
