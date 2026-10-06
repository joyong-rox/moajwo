import io
import sqlite3
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.config import DATA_DIR

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LLM_REASON = "강수량 열은 금액으로 보입니다"


def _xlsx(rows: list[list], merges: tuple[str, ...] = ()) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "현황"
    for r in rows:
        ws.append(r)
    for m in merges:
        ws.merge_cells(m)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def client():
    from app import files, review, validation_api
    from app.db import Base, engine
    from app.main import app
    from app.mapping import MappingEngine, load_dictionary

    def fake_llm(system: str, user: str, schema: dict) -> dict[str, Any]:
        return {"key": "amount", "confidence": 0.7, "reason": LLM_REASON}

    mapping_engine = MappingEngine(load_dictionary(), None, fake_llm)
    patch = pytest.MonkeyPatch()
    for module in (files, validation_api, review):
        patch.setattr(module, "get_engine", lambda: mapping_engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        glossary_csv = "코드,정식명칭,유형,별칭\n1741000,행정안전부,중앙행정기관,행안부\n1360000,기상청,중앙행정기관,\n"
        res = c.post("/glossary/import", files=[("file", ("g.csv", glossary_csv.encode("utf-8"), "text/csv"))])
        assert res.json()["applied"], res.text
        yield c
    patch.undo()


def _upload(client: TestClient, name: str, data: bytes) -> int:
    return client.post("/files", files=[("files", (name, data, XLSX))]).json()[0]["id"]


ROWS = [
    ["기관명", "부서", "집행 실적액", "강수량", "예산액(백만원)", "연락처"],
    ["행안부", "재정과", "1,000", 1.5, 100, "010-1234-5678"],
    ["기상청", None, "2,000", 2.5, 200, None],
    ["기상청본청", "기획과", "3,000", 3.5, 300, None],
    ["합계", None, 6000, None, 999, None],  # 예산액 합계 불일치 (차단)
]


def _create(client: TestClient, file_ids: list[int], **params: Any) -> dict:
    res = client.post("/reviews", json={"file_ids": file_ids, **params})
    assert res.status_code == 201, res.text
    return res.json()


def _items(review: dict, **match: Any) -> list[dict]:
    return [i for i in review["items"] if all(i.get(k) == v for k, v in match.items())]


def test_review_items_categories_and_reasons(client: TestClient) -> None:
    fid = _upload(client, "a.xlsx", _xlsx(ROWS, ("B3:B3",)))
    review = _create(client, [fid])

    blocks = _items(review, category="block")
    assert [b["kind"] for b in blocks] == ["sum_mismatch"]
    assert (blocks[0]["before"], blocks[0]["after"]) == ("999", "600 (하위 행 합계)")

    pending = {(i["kind"], i["before"]) for i in _items(review, category="pending")}
    assert ("mapping", "집행 실적액") in pending  # 유사도 확인 대상
    assert ("mapping", "강수량") in pending  # LLM 판정
    assert ("entity", "기상청본청") in pending  # 용어사전에 없음
    assert ("pii", "개인정보 의심 1건") in pending

    auto = {(i["kind"], i["before"], i["after"]) for i in _items(review, category="auto")}
    assert ("mapping", "예산액(백만원)", "예산액") in auto
    assert ("number", '"1,000"', "1,000 외 2칸") in auto
    assert ("entity", "행안부", "행정안전부 (1741000)") in auto

    # 위치에는 내부 key(org_name)가 아니라 한국어 표준 항목 이름을 쓴다
    assert {i["column"] for i in _items(review, kind="entity")} == {"기관명"}

    # 근거 문장: LLM 판정만 LLM 생성 문장, 나머지는 규칙 템플릿
    llm = _items(review, kind="mapping", before="강수량")[0]
    assert llm["reason_source"] == "llm" and llm["reason"] == LLM_REASON
    assert all(i["reason_source"] == "rule" for i in review["items"] if i is not llm)
    similar = _items(review, kind="mapping", before="집행 실적액")[0]
    assert similar["reason"].startswith("유사도 ") and "확인 기준" in similar["reason"]

    # 점수: 차단이 있으면 59점 이하, 요약은 통합 불가
    assert review["score"] <= 59 and review["summary"].startswith("통합 불가")
    assert review["counts"]["block"] == 1 and not review["can_complete"]


def _db() -> sqlite3.Connection:
    return sqlite3.connect(DATA_DIR / "moajwo.db")


def test_decisions_are_persisted_to_backend_state(client: TestClient) -> None:
    fid = _upload(client, "b.xlsx", _xlsx(ROWS))
    review = _create(client, [fid])
    rid = review["id"]
    open_before = review["counts"]["pending_open"]

    def decide(item: dict, decision: str, choice: str | None = None) -> dict:
        res = client.post(f"/reviews/{rid}/items/{item['id']}/decision", json={"decision": decision, "choice": choice})
        assert res.status_code == 200, res.text
        return res.json()

    similar = _items(review, kind="mapping", before="집행 실적액")[0]
    review = decide(similar, "approved", "expenditure")
    llm = _items(review, kind="mapping", before="강수량")[0]
    review = decide(llm, "rejected")
    entity = _items(review, kind="entity", before="기상청본청")[0]
    candidate = entity["payload"]["candidates"][0]["code"]
    review = decide(entity, "approved", candidate)
    pii = _items(review, kind="pii")[0]
    review = decide(pii, "rejected")

    assert review["counts"]["pending_open"] == open_before - 4
    assert review["files"][0]["excluded"] is True  # 개인정보 거부 → 이번 통합에서 제외
    with _db() as con:
        rows = dict(
            con.execute(
                "select column_name, field_key from mapping_decisions where source_file_id = ?", (fid,)
            ).fetchall()
        )
        ack = con.execute("select pii_acknowledged_at from source_files where id = ?", (fid,)).fetchone()[0]
        saved = dict(con.execute("select id, decision from review_items where session_id = ?", (rid,)).fetchall())
        learned = con.execute("select count(*) from learned_aliases where alias = '집행 실적액'").fetchone()[0]
    assert rows == {"집행 실적액": "expenditure", "강수량": None}  # 승인 → 확정, 거부 → '해당 없음'
    assert ack is None
    assert saved[similar["id"]] == "approved" and saved[llm["id"]] == "rejected" and saved[pii["id"]] == "rejected"
    assert learned == 0  # '이 형식 기억하기'를 하지 않았으므로 학습하지 않음
    entity_after = _items(review, kind="entity", before="기상청본청")[0]
    assert entity_after["choice"] == candidate and entity_after["decision"] == "approved"

    # 승인으로 다시 바꾸면 개인정보 확인이 기록되고 제외가 풀린다
    review = decide(_items(review, kind="pii")[0], "approved")
    assert review["files"][0]["excluded"] is False
    with _db() as con:
        assert con.execute("select pii_acknowledged_at from source_files where id = ?", (fid,)).fetchone()[0]

    # 잘못된 요청
    auto_item = _items(review, category="auto")[0]
    assert (
        client.post(f"/reviews/{rid}/items/{auto_item['id']}/decision", json={"decision": "approved"}).status_code
        == 409
    )
    bad_choice = {"decision": "approved", "choice": "nope"}
    assert client.post(f"/reviews/{rid}/items/{similar['id']}/decision", json=bad_choice).status_code == 409
    assert client.post(f"/reviews/{rid}/complete").status_code == 409  # 차단이 남아 있음
    assert client.get("/reviews/999999").status_code == 404


def test_remember_format_learns_on_complete(client: TestClient) -> None:
    rows = [r[:] for r in ROWS[:-1]]  # 합계 행 제외 → 차단 없음
    fid = _upload(client, "c.xlsx", _xlsx(rows))
    review = _create(client, [fid])
    rid = review["id"]
    assert review["counts"]["block"] == 0
    for item in _items(review, category="pending"):
        if item["kind"] == "mapping":
            choice = item["payload"]["field_key"] or item["payload"]["candidates"][0]["key"]
            body = {"decision": "approved", "choice": choice}
        elif item["kind"] == "entity":
            body = {"decision": "approved", "choice": item["payload"]["candidates"][0]["code"]}
        else:
            body = {"decision": "approved"}
        assert client.post(f"/reviews/{rid}/items/{item['id']}/decision", json=body).status_code == 200

    review = client.patch(f"/reviews/{rid}", json={"remember_format": True}).json()
    assert review["remember_format"] is True and review["can_complete"]
    done = client.post(f"/reviews/{rid}/complete").json()
    assert done["status"] == "ready" and done["summary"].startswith("검토 완료")
    assert done["learned"]["columns"] >= 1 and done["learned"]["names"] == 1
    with _db() as con:
        assert con.execute("select count(*) from learned_aliases where alias = '집행 실적액'").fetchone()[0] == 1
        assert con.execute("select count(*) from glossary_aliases where alias = '기상청본청'").fetchone()[0] == 1
    # 끝난 세션은 바꿀 수 없다
    assert client.patch(f"/reviews/{rid}", json={"remember_format": False}).status_code == 409
    assert client.post(f"/reviews/{rid}/complete").status_code == 409


# 다시 검사 테스트용: 앞선 테스트가 '기억하기'로 학습한 이름과 겹치지 않게 한다
ROWS2 = [[("집행 실적 금액" if c == "집행 실적액" else c) for c in ROWS[0]]] + [
    [("기상청 본부" if c == "기상청본청" else c) for c in row] for row in ROWS[1:]
]


def _decide(client: TestClient, rid: int, item: dict, decision: str, choice: str | None = None) -> dict:
    res = client.post(f"/reviews/{rid}/items/{item['id']}/decision", json={"decision": decision, "choice": choice})
    assert res.status_code == 200, res.text
    return res.json()


def test_recheck_carries_decisions(client: TestClient) -> None:
    fid = _upload(client, "d.xlsx", _xlsx(ROWS2))
    first = _create(client, [fid])
    rid = first["id"]
    entity = _items(first, kind="entity", before="기상청 본부")[0]
    code = entity["payload"]["candidates"][0]["code"]
    _decide(client, rid, entity, "approved", code)
    _decide(client, rid, _items(first, kind="pii")[0], "rejected")
    _decide(client, rid, _items(first, kind="mapping", before="집행 실적 금액")[0], "approved", "expenditure")
    client.patch(f"/reviews/{rid}", json={"remember_format": True})

    res = client.post(f"/reviews/{rid}/recheck", json={})
    assert res.status_code == 201, res.text
    second = res.json()
    assert second["parent_id"] == rid and second["status"] == "in_review"
    assert second["remember_format"] is True
    assert second["files"][0]["excluded"] is True  # 개인정보 거부로 제외한 상태를 이어받음
    entity2 = _items(second, kind="entity", before="기상청 본부")[0]
    assert (entity2["decision"], entity2["choice"]) == ("approved", code)
    assert _items(second, kind="pii")[0]["decision"] == "rejected"
    mapping2 = _items(second, kind="mapping", before="집행 실적 금액")[0]
    assert mapping2["category"] == "auto" and mapping2["after"] == "집행액"  # 확정이 이미 반영됨
    assert [h["id"] for h in second["history"]] == [rid, second["id"]]
    assert second["changes"]["parent_id"] == rid

    old = client.get(f"/reviews/{rid}").json()
    assert old["status"] == "superseded" and old["child_id"] == second["id"] and not old["can_complete"]
    assert old["summary"].startswith("다시 검사로 대체된")
    item = _items(old, category="pending")[0]
    assert client.post(f"/reviews/{rid}/items/{item['id']}/decision", json={"decision": "approved"}).status_code == 409
    assert client.post(f"/reviews/{rid}/recheck", json={}).status_code == 409  # 대체된 세션은 다시 검사 불가


def test_recheck_with_fixed_file(client: TestClient) -> None:
    bad_id = _upload(client, "e_bad.xlsx", _xlsx(ROWS2))
    first = _create(client, [bad_id])
    assert first["counts"]["block"] == 1
    _decide(client, first["id"], _items(first, kind="pii")[0], "approved")
    _decide(client, first["id"], _items(first, kind="mapping", before="집행 실적 금액")[0], "approved", "expenditure")

    fixed_rows = [r[:] for r in ROWS2]
    fixed_rows[-1][4] = 600  # 합계를 고친 파일
    fixed_id = _upload(client, "e_fixed.xlsx", _xlsx(fixed_rows))
    assert client.post(f"/reviews/{first['id']}/recheck", json={"replace": {"999999": fixed_id}}).status_code == 409

    second = client.post(f"/reviews/{first['id']}/recheck", json={"replace": {str(bad_id): fixed_id}}).json()
    assert [f["id"] for f in second["files"]] == [fixed_id]
    assert second["counts"]["block"] == 0
    changes = second["changes"]
    assert changes["blocks"] == [1, 0] and changes["score"][1] > changes["score"][0]
    assert [r["kind"] for r in changes["resolved"] if r["category"] == "block"] == ["sum_mismatch"]
    assert changes["replaced_files"] == {str(bad_id): fixed_id}
    # 고친 파일은 형식이 같아 열 매핑 확정을 이어받고, 개인정보 확인은 새로 받는다
    mapping = _items(second, kind="mapping", before="집행 실적 금액")[0]
    assert mapping["category"] == "auto" and mapping["after"] == "집행액"
    assert _items(second, kind="pii")[0]["decision"] is None
    # '기억하기'로 완료하면 이전 검사에서 확정해 이어받은 매핑도 학습한다
    client.patch(f"/reviews/{second['id']}", json={"remember_format": True})
    _decide(client, second["id"], _items(second, kind="pii")[0], "approved")
    for left in _items(client.get(f"/reviews/{second['id']}").json(), category="pending", decision=None):
        _decide(client, second["id"], left, "rejected")
    done = client.post(f"/reviews/{second['id']}/complete").json()
    assert done["status"] == "ready" and done["learned"]["columns"] >= 1
    with _db() as con:
        assert con.execute("select count(*) from learned_aliases where alias = '집행 실적 금액'").fetchone()[0] == 1
