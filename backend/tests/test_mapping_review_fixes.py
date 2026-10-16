"""PR #4 병합 전 보완: 공통 후보 판단 근거, 공통 검토·일괄 확정의 개인정보 응답, 분석 없는 파일, 동시 최초 분석 저장.

모두 실제 업로드 → 분석 저장 → API 흐름으로 재현한다. 개인정보는 합성 값만 쓴다.
"""

import io
import sqlite3
import threading
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.config import DATA_DIR
from tests.test_accounts import owner  # noqa: F401
from tests.test_auth import _client, app_real_login  # noqa: F401

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EMAIL, EMAIL2, PHONE = "carol@example.com", "dave@example.com", "010-3456-7890"
RAW = (EMAIL, EMAIL2, PHONE, "3456-7890")


def _xlsx(rows: list[list], title: str | None = None, sheet: str = "현황", notes: tuple[str, ...] = ()) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    if title:
        ws.append([title])
    for r in rows:
        ws.append(r)
    for n in notes:
        ws.append([n])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(c: TestClient, name: str, data: bytes) -> int:
    res = c.post("/files", files=[("files", (name, data, XLSX))])
    assert res.status_code == 200 and res.json()[0]["id"], res.text
    return res.json()[0]["id"]


def _analyze(c: TestClient, fid: int) -> dict[str, Any]:
    res = c.post(f"/files/{fid}/mapping", params={"use_llm": "false"})
    assert res.status_code == 200, res.text
    return res.json()


def _db(sql: str, *args: Any) -> list[tuple]:
    with sqlite3.connect(DATA_DIR / "moajwo.db") as con:
        return con.execute(sql, args).fetchall()


def _group(c: TestClient, ids: list[int], name: str) -> dict:
    review = c.post("/mapping/common", json={"file_ids": ids})
    assert review.status_code == 200, review.text
    return next(g for g in review.json()["groups"] if g["name"] == name)


@pytest.fixture(scope="module")
def fx(app_real_login, owner) -> dict[str, TestClient]:  # noqa: F811
    from app import mapping_review
    from app.mapping import MappingEngine, load_dictionary

    engine = MappingEngine(
        load_dictionary(), None, lambda s, u, schema: {"key": "none", "confidence": 0, "reason": "-"}
    )
    patch = pytest.MonkeyPatch()
    patch.setattr(mapping_review, "get_engine", lambda: engine)
    made = {"owner": owner}
    for name in ("mf-user1", "mf-user2"):
        res = owner.post("/users", json={"username": name, "display_name": name.upper(), "status": "active",
                                         "password": "pass-" + name})  # fmt: skip
        assert res.status_code == 201, res.text
        made[name] = _client(app_real_login, name, "pass-" + name)
    yield made
    patch.undo()


# ---------------------------------------------------------------- 1. 공통 후보 판단 근거


def test_flow_and_stock_wording_conflict_is_not_common(fx) -> None:
    """같은 '등록대수'·숫자·같은 단위라도 한 표는 '신규 등록'(흐름) 표이면 공통 후보가 아니다."""
    u = fx["mf-user1"]
    rows = [["기관명", "등록대수"], ["행정안전부", 5], ["기상청", 6]]
    a = _upload(u, "흐름시점A.xlsx", _xlsx(rows, title="자동차 등록 현황 (단위: 대)"))
    b = _upload(u, "흐름시점B.xlsx", _xlsx(rows, title="자동차 신규 등록 현황 (단위: 대)"))
    for fid in (a, b):
        _analyze(u, fid)
    g = _group(u, [a, b], "등록대수")
    assert g["category"] == "divergent", g
    assert any("흐름" in r or "시점" in r for r in g["reasons"]), g["reasons"]
    assert _group(u, [a, b], "기관명")["category"] == "common"  # 정상적인 공통 항목은 유지


def test_quantity_stored_as_text_still_compares_units(fx) -> None:
    """수량 열이 '010'·'020' 같은 글자로 저장돼도 단위(대 / 천대)를 비교한다."""
    u = fx["mf-user1"]
    a = _upload(u, "글자수량A.xlsx", _xlsx([["기관명", "등록대수"], ["행정안전부", "010"], ["기상청", "020"]],
                                         title="자동차 등록대수 (단위: 대)"))  # fmt: skip
    b = _upload(u, "글자수량B.xlsx", _xlsx([["기관명", "등록대수"], ["행정안전부", "010"], ["기상청", "020"]],
                                         title="자동차 등록대수 (단위: 천대)"))  # fmt: skip
    for fid in (a, b):
        _analyze(u, fid)
    g = _group(u, [a, b], "등록대수")
    assert g["category"] == "divergent" and any("단위" in r for r in g["reasons"]), g
    assert _group(u, [a, b], "기관명")["category"] == "common"  # 글자 항목에는 시트 단위를 적용하지 않음


def test_multiple_unit_declarations_are_ambiguous(fx) -> None:
    """제목에 '단위: 대', 주석에 '단위: 천대'처럼 단위 선언이 여럿이면 첫 값을 고르지 않고 불명확으로 본다."""
    u = fx["mf-user1"]
    rows = [["기관명", "등록대수"], ["행정안전부", 5], ["기상청", 6]]
    a = _upload(u, "단위여럿A.xlsx", _xlsx(rows, title="자동차 등록대수 (단위: 대)", notes=("※ 단위: 천대",)))
    b = _upload(u, "단위여럿B.xlsx", _xlsx(rows, title="자동차 등록대수 (단위: 대)"))
    for fid in (a, b):
        _analyze(u, fid)
    g = _group(u, [a, b], "등록대수")
    assert g["category"] == "divergent" and any("단위" in r for r in g["reasons"]), g


def test_common_candidate_lists_checked_evidence(fx) -> None:
    """공통 후보에는 실제로 확인한 근거(같은 표준 항목·값 형태·단위 등)를 함께 준다."""
    u = fx["mf-user1"]
    rows = [["기관명", "집행액"], ["행정안전부", 100], ["기상청", 200]]
    a = _upload(u, "근거A.xlsx", _xlsx(rows, title="예산 집행 (단위: 백만원)"))
    b = _upload(u, "근거B.xlsx", _xlsx(rows, title="예산 집행 (단위: 백만원)"))
    for fid in (a, b):
        _analyze(u, fid)
    g = _group(u, [a, b], "집행액")
    assert g["category"] == "common", g
    joined = " ".join(g["evidence"])
    assert "표준 항목" in joined and "단위" in joined and "백만원" in joined


# ---------------------------------------------------------------- 2. 개인정보 응답


def test_pii_in_title_sheet_and_file_name_not_in_review_or_batch(fx, caplog) -> None:
    import logging

    u = fx["mf-user1"]
    caplog.set_level(logging.DEBUG)
    rows = [["기관명", "등록대수"], ["행정안전부", 5], ["기상청", 6]]
    a = _upload(u, f"개인정보 {EMAIL}.xlsx", _xlsx(rows, title=f"자동차 등록 현황 (단위: 대 문의 {EMAIL})",
                                                    sheet=f"현황 {EMAIL2}"))  # fmt: skip
    b = _upload(u, "개인정보B.xlsx", _xlsx(rows, title=f"자동차 등록 현황 (단위: 대) 담당 {PHONE}"))
    for fid in (a, b):
        _analyze(u, fid)
    review = u.post("/mapping/common", json={"file_ids": [a, b]})
    assert review.status_code == 200
    assert not any(r in review.text for r in RAW), "공통 검토 응답에 개인정보 원문"
    g = next(x for x in review.json()["groups"] if x["name"] == "등록대수")
    assert {m["unit"] for m in g["members"]} == {"대"}  # 정상 단위는 그대로
    member = next(m for m in g["members"] if m["file_id"] == a)
    item = {"file_id": a, "sheet_id": member["sheet_id"], "column_id": member["column_id"], "field_key": "note"}
    plan = u.post("/mapping/batch/preview", json={"items": [item]})
    assert plan.status_code == 200 and not any(r in plan.text for r in RAW)
    stale = {**item, "expected_confirmed": True, "expected_field_key": "budget", "overwrite": False}
    conflict = u.post("/mapping/batch/apply", json={"items": [stale]})
    assert conflict.status_code == 409 and not any(r in conflict.text for r in RAW)
    for path in (f"/files/{a}/mapping/latest", f"/files/{a}/preview"):
        res = u.get(path)
        assert res.status_code == 200 and not any(r in res.text for r in RAW), path
    listed = next(f for f in u.get("/files").json() if f["id"] == a)
    assert EMAIL not in listed["display_name"] and listed["display_name"].endswith(".xlsx")  # 화면 표시용은 가림
    assert listed["original_name"] == f"개인정보 {EMAIL}.xlsx"  # 원래 이름은 바꾸지 않음 (기존 화면·식별 호환)
    sheet_id = u.get(f"/files/{a}/preview").json()["sheet_id"]
    assert u.get(f"/files/{a}/preview", params={"sheet_id": sheet_id}).status_code == 200  # 식별은 sheet_id로
    assert not any(r in caplog.text for r in RAW)


def test_decisions_api_finds_pii_sheet_by_sheet_id(fx) -> None:
    """응답의 시트 이름은 가려서 주므로 확정 저장 API는 sheet_id로 시트를 찾는다 (가린 이름으로는 못 찾음)."""
    u = fx["mf-user1"]
    fid = _upload(u, "시트개인정보.xlsx", _xlsx([["기관명", "세부 내역"], ["행정안전부", "a"]], sheet=f"연락 {EMAIL}"))
    sheet = _analyze(u, fid)["sheets"][0]
    assert EMAIL not in sheet["name"] and sheet["sheet_id"] == "s0"
    wrong = u.put(f"/files/{fid}/mapping/decisions",
                  json={"decisions": [{"sheet": sheet["name"], "column_id": "c1", "field_key": "note"}]})  # fmt: skip
    assert wrong.status_code == 422 and not any(r in wrong.text for r in RAW)
    ok = u.put(f"/files/{fid}/mapping/decisions",
               json={"decisions": [{"sheet_id": "s0", "column_id": "c1", "field_key": "note"}]})  # fmt: skip
    assert ok.status_code == 200, ok.text
    cols = {c["column"]: c for c in u.get(f"/files/{fid}/mapping/latest").json()["sheets"][0]["columns"]}
    assert (cols["세부 내역"]["status"], cols["세부 내역"]["field_key"]) == ("confirmed", "note")


# ---------------------------------------------------------------- 3·4. 확인 화면 정보, 분석 없는 파일


def test_batch_plan_shows_status_and_reason(fx, monkeypatch) -> None:
    """확인 화면(일괄 확정 미리보기)에 열마다 지금 상태와 제안 근거가 그대로 보인다.

    예전 자료 '관리번호'(523·525)는 문자열 유사도만으로 '기관코드 · 확인 필요'가 되는 보고된 오제안이었는데,
    기관코드 제안 검사로 이제 기각된다. '확인 필요' 표시는 정상적으로 확인 필요가 되는 '집행 실적액'으로 보고,
    '관리번호'는 '미해결'과 기각 사유가 그대로 보이는지 본다. 분석은 문자열 유사도만 쓰도록 고정한다
    (기본 엔진은 로컬 Ollama 임베딩이 켜져 있는지에 따라 결과가 달라질 수 있음).
    """
    from app import files
    from app.mapping import MappingEngine, load_dictionary

    monkeypatch.setattr(files, "get_engine", lambda: MappingEngine(load_dictionary(), None, None))
    u = fx["mf-user1"]
    fid = _upload(u, "상태표시.xlsx", _xlsx([["기관명", "집행 실적액", "관리번호"],
                                             ["행정안전부", 100, "523"], ["기상청", 200, "525"]]))  # fmt: skip
    mapping = _analyze(u, fid)
    sheet = u.get(f"/files/{fid}/mapping/latest").json()["sheets"][0]
    cols = {c["column"]: c for c in sheet["columns"]}
    names = ("기관명", "집행 실적액", "관리번호")
    items = [{"file_id": fid, "sheet_id": sheet["sheet_id"], "column_id": cols[n]["column_id"],
              "field_key": cols[n]["field_key"] or "note"} for n in names]  # fmt: skip
    plan = u.post("/mapping/batch/preview", json={"items": items}).json()["items"]
    by = {p["column"]: p for p in plan}
    assert by["기관명"]["current_status"] == "auto"
    assert by["집행 실적액"]["current_status"] == cols["집행 실적액"]["status"] == "review"
    assert by["집행 실적액"]["suggestion_reason"]
    assert by["관리번호"]["current_status"] == cols["관리번호"]["status"] == "unresolved"
    assert "기관코드" in by["관리번호"]["suggestion_reason"]
    assert [c["status"] for c in mapping["sheets"][0]["columns"]] == ["auto", "review", "unresolved"]


def test_latest_without_analysis_shows_saved_decisions_without_writing(fx) -> None:
    """분석 기록 없이 확정값만 있는 파일: 확정값은 보여 주고 나머지는 '미분석'.

    화면을 열어도 아무것도 저장하지 않는다.
    """
    u = fx["mf-user1"]
    fid = _upload(u, "확정만.xlsx", _xlsx([["기관명", "세부 내역"], ["행정안전부", "a"]]))
    res = u.put(f"/files/{fid}/mapping/decisions",
                json={"decisions": [{"sheet": "현황", "column_id": "c1", "field_key": "note"}]})  # fmt: skip
    assert res.status_code == 200
    counts = {
        t: _db(f"select count(*) from {t}")[0][0] for t in ("mapping_analyses", "mapping_decisions", "learned_aliases")
    }
    latest = u.get(f"/files/{fid}/mapping/latest")
    assert latest.status_code == 200, latest.text
    body = latest.json()
    assert body["analyzed"] is False and body["analyzed_at"] is None
    cols = {c["column"]: c for c in body["sheets"][0]["columns"]}
    assert (cols["세부 내역"]["status"], cols["세부 내역"]["field_key"]) == ("confirmed", "note")
    assert cols["기관명"]["status"] == "unanalyzed" and cols["기관명"]["field_key"] is None
    after = {t: _db(f"select count(*) from {t}")[0][0] for t in counts}
    assert after == counts  # 열었다고 분석·확정·학습 기록이 늘지 않음


# ---------------------------------------------------------------- 5. 같은 파일 최초 분석 동시 저장


def test_concurrent_first_analysis_keeps_one_row(fx, monkeypatch) -> None:
    from app import files
    from app.auth import CurrentUser
    from app.db import SessionLocal
    from app.models import MappingAnalysis

    u = fx["mf-user1"]
    fid = _upload(u, "동시분석.xlsx", _xlsx([["기관명", "집행액"], ["행정안전부", 1]]))
    uid = _db("select id from users where username = 'mf-user1'")[0][0]
    me = CurrentUser(uid, "mf-user1", "MF-USER1", "user")
    barrier = threading.Barrier(2)
    real_init = MappingAnalysis.__init__

    def waiting_init(self, *args: Any, **kwargs: Any) -> None:  # 두 요청 모두 '기록 없음'을 본 뒤 저장하게 맞춘다
        barrier.wait(timeout=10)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(MappingAnalysis, "__init__", waiting_init)
    outcomes: list[Any] = []

    def attempt() -> None:
        with SessionLocal() as db:
            try:
                outcomes.append(files.map_file_columns(fid, db, me, use_llm=False).analyzed_at is not None)
            except Exception as exc:  # noqa: BLE001
                outcomes.append(type(exc).__name__)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert outcomes == [True, True], outcomes
    assert _db("select count(*) from mapping_analyses where source_file_id = ? and created_by = ?", fid, uid) == [(1,)]
