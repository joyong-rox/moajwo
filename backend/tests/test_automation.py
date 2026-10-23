import io
from datetime import UTC, datetime
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.config import DATA_DIR
from app.timefmt import KST
from tests.conftest import db_connect

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

HEADER = ["기관명", "담당 업무 구분", "집행액", "추진 실적 메모", "세부 사업 번호", "시도"]
SEPT = [
    HEADER,
    ["행안부", "재정", "1,000", "정상", "S-1", "서울"],
    ["기상청", "총무", 2500, "검토중", "S-2", "부산광역시"],
    ["국가유산청", "기획", 3000, "정상", "S-3", "서울특별시"],
    ["유산청", "기획", 400, "정상", "S-4", "서울특별시"],
]
OCT = [
    HEADER + ["신규 관리 항목"],
    ["행안부", "재정", "1,200", "정상", "S-1", "서울", "A"],
    ["기상청", "총무", 2600, "완료", "S-2", "부산광역시", "B"],
    ["국가유산청", "기획", 3100, "정상", "S-3", "서울특별시", "C"],
    ["유산청", "기획", 500, "정상", "S-4", "서울특별시", "D"],
]


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "현황"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def client():
    from app import automation, files, integration, review, validation_api
    from app.db import Base, engine
    from app.main import app
    from app.mapping import MappingEngine, load_dictionary

    def no_llm(system: str, user: str, schema: dict) -> dict[str, Any]:
        return {"key": "none", "confidence": 0, "reason": "해당 없음"}

    mapping_engine = MappingEngine(load_dictionary(), None, no_llm)
    patch = pytest.MonkeyPatch()
    for module in (files, validation_api, review, integration, automation):
        patch.setattr(module, "get_engine", lambda: mapping_engine)
    Base.metadata.create_all(engine)
    with db_connect() as con:
        before_codes = {r[0] for r in con.execute("select code from glossary_entries")}
        before_aliases = {r[0] for r in con.execute("select id from glossary_aliases")}
    with TestClient(app) as c:
        glossary_csv = (
            "코드,정식명칭,유형,시도코드,시군구코드,별칭\n"
            "1741000,행정안전부,중앙행정기관,,,행안부\n"
            "1360000,기상청,중앙행정기관,,,\n"
            "1550000,국가유산청,중앙행정기관,,,\n"
            "6110000,서울특별시,광역자치단체,11,,\n"
            "6260000,부산광역시,광역자치단체,26,,\n"
        )
        res = c.post("/glossary/import", files=[("file", ("g.csv", glossary_csv.encode("utf-8"), "text/csv"))])
        assert res.json()["applied"], res.text
        yield c
    patch.undo()
    # 같은 테스트 DB를 쓰는 다른 모듈(용어사전 테스트)에 영향이 없도록 이 모듈이 넣은 용어만 지운다
    with db_connect() as con:
        for (alias_id,) in con.execute("select id from glossary_aliases").fetchall():
            if alias_id not in before_aliases:
                con.execute("delete from glossary_aliases where id = ?", (alias_id,))
        for (code,) in con.execute("select code from glossary_entries").fetchall():
            if code not in before_codes:
                con.execute("delete from glossary_entries where code = ?", (code,))


def _upload(client: TestClient, name: str, rows: list[list]) -> int:
    return client.post("/files", files=[("files", (name, _xlsx(rows), XLSX))]).json()[0]["id"]


def _approve_all(client: TestClient, review: dict) -> dict:
    for item in [i for i in review["items"] if i["category"] == "pending" and i["decision"] is None]:
        candidates = item["payload"].get("candidates") or []
        if item["kind"] == "mapping":
            choice = item["payload"].get("field_key")
        else:
            choice = candidates[0].get("code") if candidates else None
        decision = "approved" if (choice or item["kind"] == "pii") else "rejected"
        res = client.post(
            f"/reviews/{review['id']}/items/{item['id']}/decision", json={"decision": decision, "choice": choice}
        )
        assert res.status_code == 200, res.text
        review = res.json()
    return review


def _manual_run(client: TestClient, fid: int) -> tuple[dict, dict]:
    review = client.post("/reviews", json={"file_ids": [fid]}).json()
    first = review
    _approve_all(client, review)
    assert client.post(f"/reviews/{review['id']}/complete").status_code == 200
    res = client.post(f"/reviews/{review['id']}/integrate")
    assert res.status_code == 201, res.text
    return first, res.json()


def test_saved_automation_skips_most_review(client: TestClient) -> None:
    first, run = _manual_run(client, _upload(client, "9월.xlsx", SEPT))
    first_pending = first["counts"]["pending_open"]
    assert first_pending >= 3  # 사전에 없는 열 이름·처음 보는 기관명은 처음엔 확인이 필요하다
    assert run["trigger"] == "manual" and run["status_label"] == "완료"

    res = client.post(f"/runs/{run['id']}/automation", json={"name": "월간 집행 취합"})
    assert res.status_code == 201, res.text
    saved = res.json()
    rules = {r["column"]: r["field_key"] for r in saved["column_rules"]}
    assert set(rules) == set(HEADER) and rules["기관명"] == "org_name"
    assert "국가유산청" not in saved["entity_rules"]  # 용어사전에 그대로 있는 이름은 규칙이 필요 없다
    assert "유산청" in saved["entity_rules"]  # 검토에서 확인한 이름 연결은 규칙으로 저장
    assert saved["output_format"]["columns"] == run["summary"]["columns"]
    # 입력 파일은 저장하지 않는다
    assert "file_ids" not in saved["validation"] and "baseline_file_ids" not in saved["validation"]

    # 같은 이름으로 다시 저장할 수 없다
    assert client.post(f"/runs/{run['id']}/automation", json={"name": "월간 집행 취합"}).status_code == 409

    # 두 번째 실행: 새 열 1개만 확인 필요
    oct_id = _upload(client, "10월.xlsx", OCT)
    res = client.post(f"/automations/{saved['id']}/run", json={"file_ids": [oct_id]})
    assert res.status_code == 201, res.text
    second = res.json()
    assert second["counts"]["pending_open"] == 1 < first_pending
    assert second["run"]["status"] == "review" and second["run"]["status_label"] == "검토"
    assert second["run"]["trigger"] == "automation" and second["run"]["automation_name"] == "월간 집행 취합"
    assert second["run"]["input_file_count"] == 1 and second["run"]["duration_ms"] is None

    review = client.get(f"/reviews/{second['review_id']}").json()
    assert review["automation_name"] == "월간 집행 취합"
    pending = [i for i in review["items"] if i["category"] == "pending"]
    assert [i["column"] for i in pending] == ["신규 관리 항목"]
    assert "새 열" in pending[0]["reason"]
    ruled = [i for i in review["items"] if i["kind"] == "mapping" and i["column"] == "담당 업무 구분"]
    assert ruled[0]["category"] == "auto" and "월간 집행 취합" in ruled[0]["reason"]
    named = [i for i in review["items"] if i["kind"] == "entity" and i["before"] == "유산청"]
    assert named[0]["category"] == "auto" and "월간 집행 취합" in named[0]["reason"]

    # 다시 검사해도 같은 자동화 규칙으로 검사한다
    res = client.post(f"/reviews/{review['id']}/recheck", json={})
    assert res.status_code in (200, 201), res.text
    review = res.json()
    assert review["automation_name"] == "월간 집행 취합" and review["counts"]["pending_open"] == 1

    # 새 열을 확인하고 통합하면 같은 실행 기록이 완료로 바뀐다
    _approve_all(client, review)
    assert client.post(f"/reviews/{review['id']}/complete").status_code == 200
    done = client.post(f"/reviews/{review['id']}/integrate").json()
    assert done["id"] == second["run"]["id"] and done["status_label"] == "완료"
    assert done["duration_ms"] is not None and done["output_path"].startswith("data/outputs/")
    # 출력 형식: 저장한 열 순서를 따르고 새 열은 뒤에 붙는다
    columns = done["summary"]["columns"]
    assert columns[: len(saved["output_format"]["columns"])] == saved["output_format"]["columns"]


def test_automation_run_without_new_columns_completes(client: TestClient) -> None:
    _, run = _manual_run(client, _upload(client, "9월b.xlsx", SEPT))
    automation = client.post(f"/runs/{run['id']}/automation", json={"name": "열 그대로"}).json()
    same = [SEPT[0]] + [[*r[:2], 9000, *r[3:]] for r in SEPT[1:]]
    fid = _upload(client, "10월b.xlsx", same)
    res = client.post(f"/automations/{automation['id']}/run", json={"file_ids": [fid]}).json()
    assert res["counts"]["pending_open"] == 0
    out = res["run"]
    assert out["status"] == "done" and out["status_label"] == "완료" and out["input_file_count"] == 1
    assert out["duration_ms"] is not None and out["duration_ms"] >= 0
    assert (DATA_DIR / out["output_path"].removeprefix("data/")).is_file()

    history = client.get("/runs", params={"automation_id": automation["id"]}).json()
    assert [r["id"] for r in history] == [out["id"]]
    listed = {a["id"]: a for a in client.get("/automations").json()}
    assert listed[automation["id"]]["run_count"] == 1
    assert listed[automation["id"]]["last_run"]["id"] == out["id"]

    # 자동화를 지워도 실행 기록과 결과 파일은 남는다
    assert client.delete(f"/automations/{automation['id']}").status_code == 204
    kept = client.get(f"/runs/{out['id']}").json()
    assert kept["status"] == "done" and kept["automation_id"] is None and kept["trigger"] == "automation"
    assert client.get(f"/automations/{automation['id']}").status_code == 404


def test_automation_rejects_bad_requests(client: TestClient) -> None:
    review = client.post("/reviews", json={"file_ids": [_upload(client, "9월c.xlsx", SEPT)]}).json()
    with db_connect() as con:
        con.execute(
            "insert into runs (review_session_id, trigger, status, input_file_count, file_ids, row_count, "
            "change_count, warning_count, summary) values (?, 'manual', 'failed', 1, '[]', 0, 0, 0, '{}')",
            (review["id"],),
        )
        failed_id = con.execute("select max(id) from runs").fetchone()[0]
    assert client.post(f"/runs/{failed_id}/automation", json={"name": "실패 저장"}).status_code == 409
    assert client.post("/runs/999999/automation", json={"name": "없음"}).status_code == 404
    assert client.post("/automations/999999/run", json={"file_ids": [1]}).status_code == 404

    _, run = _manual_run(client, _upload(client, "9월d.xlsx", SEPT))
    automation = client.post(f"/runs/{run['id']}/automation", json={"name": "검사용"}).json()
    res = client.post(f"/automations/{automation['id']}/run", json={"file_ids": [999999]})
    assert res.status_code == 422
    assert client.post(f"/automations/{automation['id']}/run", json={"file_ids": []}).status_code == 422


def test_automation_keeps_include_unmapped_choice(client: TestClient) -> None:
    review = client.post("/reviews", json={"file_ids": [_upload(client, "9월e.xlsx", SEPT)]}).json()
    _approve_all(client, review)
    assert client.post(f"/reviews/{review['id']}/complete").status_code == 200
    run = client.post(f"/reviews/{review['id']}/integrate", json={"include_unmapped": True}).json()
    unmapped = run["summary"]["unmapped_columns"]
    assert unmapped  # 거부한 열(해당 없음)이 원래 이름으로 들어감
    automation = client.post(f"/runs/{run['id']}/automation", json={"name": "원래 열 포함"}).json()
    assert automation["output_format"]["include_unmapped"] is True

    same = [SEPT[0]] + [[*r[:2], 7000, *r[3:]] for r in SEPT[1:]]
    res = client.post(f"/automations/{automation['id']}/run", json={"file_ids": [_upload(client, "10월e.xlsx", same)]})
    out = res.json()["run"]
    if out["status"] == "review":  # 확인 항목이 남으면 검토 패널 기본값도 자동화 설정을 따른다
        rv = client.get(f"/reviews/{res.json()['review_id']}").json()
        assert rv["include_unmapped_default"] is True
    else:
        assert out["summary"]["include_unmapped"] is True
        assert set(unmapped) <= set(out["summary"]["columns"])


def _kst_wall(value: str | datetime) -> str:
    """datetime.now(KST)로 저장한 시각의 벽시계 — SQLite는 시간대 없는 글자, PostgreSQL은 시간대가 붙은 값."""
    if isinstance(value, str):
        return value.replace(" ", "T")[:19]
    return value.astimezone(KST).replace(tzinfo=None).isoformat()[:19]


def _utc_instant(value: str | datetime) -> datetime:
    """DB 기본값(UTC)으로 저장한 시각의 순간 — SQLite는 시간대 없는 UTC 글자."""
    return datetime.fromisoformat(value).replace(tzinfo=UTC) if isinstance(value, str) else value


def test_screen_times_carry_kst_offset(client: TestClient) -> None:
    """화면에 보내는 시각에는 +09:00을 붙인다. 저장 기준이 달라도 같은 순간을 같은 한국 시간으로 보여 주게."""
    fid = _upload(client, "시각.xlsx", SEPT)
    _, run = _manual_run(client, fid)
    automation = client.post(f"/runs/{run['id']}/automation", json={"name": "시각 확인"}).json()
    with db_connect() as con:
        started, created = con.execute("select started_at, created_at from runs where id = ?", (run["id"],)).fetchone()
        uploaded = con.execute("select uploaded_at from source_files where id = ?", (fid,)).fetchone()[0]
        updated = con.execute("select updated_at from automations where id = ?", (automation["id"],)).fetchone()[0]
    listed = next(r for r in client.get("/runs").json() if r["id"] == run["id"])
    # datetime.now(KST)로 저장한 실행 시작 시각: 그 벽시계 그대로 +09:00 (9시간을 더하지 않음)
    assert listed["started_at"].endswith("+09:00") and listed["started_at"][:19] == _kst_wall(started)
    # DB 기본값(UTC)으로 저장한 시각: 같은 순간을 KST로
    assert listed["created_at"].endswith("+09:00")
    assert datetime.fromisoformat(listed["created_at"]) == _utc_instant(created)
    up = next(f for f in client.get("/files").json() if f["id"] == fid)["uploaded_at"]
    assert up.endswith("+09:00") and datetime.fromisoformat(up) == _utc_instant(uploaded)
    got = client.get(f"/automations/{automation['id']}").json()
    assert got["updated_at"].endswith("+09:00") and got["updated_at"][:19] == _kst_wall(updated)
    assert got["created_at"].endswith("+09:00")
