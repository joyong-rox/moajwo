"""파일에서 온 이름의 저장 한도 (app/name_limits.py) — SQLite와 PostgreSQL에서 같은 거절을, 쓰기 전에, 원문 없이.

PostgreSQL은 varchar(255)보다 긴 이름을 쓰는 순간 DB 오류(500)를 냈고 SQLite는 그대로 저장했다. 같은 시험이 두 DB에서
돈다 (MOAJWO_TEST_DATABASE_URL). 길이는 글자 수 — 한글 255자(765바이트)는 두 DB 모두 저장된다.
"""

import io
import json
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from tests.conftest import db_rows

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _name(n: int, head: str) -> str:
    """n자 한글 열 이름 (개인정보 형식 없음)."""
    return head + "가" * (n - len(head))


N254, N255, N256 = _name(254, "인구"), _name(255, "세대"), _name(256, "예산")


@pytest.fixture(scope="module")
def client():
    from app import automation, files, integration, mapping_review, review, validation_api
    from app.main import app
    from app.mapping import MappingEngine, load_dictionary

    def no_llm(system: str, user: str, schema: dict) -> dict[str, Any]:
        return {"key": "none", "confidence": 0, "reason": "-"}

    engine = MappingEngine(load_dictionary(), None, no_llm)
    patch = pytest.MonkeyPatch()
    for module in (files, validation_api, review, mapping_review, integration, automation):
        patch.setattr(module, "get_engine", lambda: engine)
    with TestClient(app) as c:
        glossary = "코드,정식명칭,유형\n1741000,행정안전부,중앙행정기관\n"
        res = c.post("/glossary/import", files=[("file", ("g.csv", glossary.encode("utf-8"), "text/csv"))])
        assert res.json()["applied"], res.text
        yield c
    patch.undo()


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


def _upload(c: TestClient, name: str, data: bytes) -> int:
    res = c.post("/files", files=[("files", (name, data, XLSX))])
    assert res.status_code == 200 and res.json()[0]["id"], res.text
    return res.json()[0]["id"]


def _apply(c: TestClient, fid: int, *cols: tuple[str, str | None]) -> Any:
    items = [
        {"file_id": fid, "sheet_id": "s0", "column_id": cid, "field_key": key, "expected_confirmed": False,
         "expected_field_key": None}
        for cid, key in cols
    ]  # fmt: skip
    return c.post("/mapping/batch/apply", json={"items": items})


def _decided(fid: int) -> list[tuple]:
    return db_rows(
        "select column_name, field_key from mapping_decisions where source_file_id = ? order by length(column_name)",
        fid,
    )


def _count(table: str) -> int:
    return db_rows(f"select count(*) from {table}")[0][0]


def _counts() -> dict[str, int]:
    tables = ("validation_runs", "review_sessions", "review_items", "runs", "mapping_decisions", "learned_aliases")
    return {t: _count(t) for t in tables}


def _ready(c: TestClient, fids: list[int]) -> dict:
    """검토를 만들고 완료한다 (모든 열을 확정했고 이름은 용어사전에 있음)."""
    res = c.post("/reviews", json={"file_ids": fids})
    assert res.status_code == 201, res.text
    review = res.json()
    res = c.post(f"/reviews/{review['id']}/complete")
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------- 확정 (한 열·일괄·공통 검토 승인 = 일괄 적용 API)


def test_names_up_to_the_limit_are_confirmed_and_longer_ones_refused_before_writing(client) -> None:
    fid = _upload(client, "길이.xlsx", _xlsx([["기관명", N254, N255, N256], ["행정안전부", 1, 2, 3]]))
    assert _apply(client, fid, ("c1", "note")).status_code == 200
    assert _apply(client, fid, ("c2", "population")).status_code == 200
    res = _apply(client, fid, ("c3", "amount"))
    assert res.status_code == 409, res.text
    problems = res.json()["detail"]["problems"]
    assert len(problems) == 1 and problems[0].startswith("길이.xlsx / 현황 / D열(머리글 1행): "), problems
    assert "열 이름이 256자로 저장 한도 255자를 넘습니다" in problems[0]
    assert N256[:20] not in res.text  # 이름 원문을 되돌려 보내지 않는다 (위치·글자 수·한도만)
    # 한글 255자(765바이트)도 두 DB에서 그대로 저장된다 — 한도는 글자 수
    assert len(N255.encode("utf-8")) == 765
    assert _decided(fid) == [(N254, "note"), (N255, "population")]


def test_one_long_column_keeps_the_whole_request_unsaved_including_not_applicable(client) -> None:
    fid = _upload(client, "섞임.xlsx", _xlsx([["기관명", "섞임 메모", N256], ["행정안전부", "a", 1]]))
    before = _counts()
    approvals = _count("mapping_approvals")
    # 정상 열 + 긴 열('해당 없음' — 해당 없음도 확정 행을 만든다): 아무것도 저장하지 않는다
    res = _apply(client, fid, ("c1", "note"), ("c2", None))
    assert res.status_code == 409 and len(res.json()["detail"]["problems"]) == 1, res.text
    assert _apply(client, fid, ("c2", None)).status_code == 409
    assert _decided(fid) == [] and _counts() == before and _count("mapping_approvals") == approvals
    # 정상 열만 보내면 저장된다
    assert _apply(client, fid, ("c1", "note")).status_code == 200
    assert _decided(fid) == [("섞임 메모", "note")]


def test_preview_shows_the_long_column_excluded_with_its_place(client) -> None:
    fid = _upload(client, "미리보기.xlsx", _xlsx([["기관명", N256], ["행정안전부", 1]]))
    item = {"file_id": fid, "sheet_id": "s0", "column_id": "c1", "field_key": "note"}
    plan = client.post("/mapping/batch/preview", json={"items": [item]}).json()
    entry = plan["items"][0]
    assert plan["excluded"] == 1 and entry["action"] == "excluded", plan
    assert entry["column"] == "B열(머리글 1행)" and "저장 한도 255자" in entry["reason"]
    assert N256[:20] not in json.dumps(plan, ensure_ascii=False)
    assert _decided(fid) == []


def test_api_decisions_check_the_name_found_by_column_id_too(client) -> None:
    fid = _upload(client, "직접.xlsx", _xlsx([["기관명", "직접 메모", N256], ["행정안전부", "a", 1]]))
    url = f"/files/{fid}/mapping/decisions"
    res = client.put(url, json={"decisions": [{"sheet_id": "s0", "column_id": "c2", "field_key": "note"}]})
    assert res.status_code == 422 and res.json()["detail"].startswith("시트 '현황' · C열(머리글 1행): "), res.text
    assert "열 이름이 256자로 저장 한도 255자를 넘습니다" in res.text and N256[:20] not in res.text
    # 예전 방식(열 이름 글자)도 같은 거절
    res = client.put(url, json={"decisions": [{"sheet": "현황", "column": N256, "field_key": None}]})
    assert res.status_code == 422 and N256[:20] not in res.text
    # 정상 열과 함께 보내도 아무것도 저장하지 않는다
    both = [{"sheet_id": "s0", "column_id": "c1", "field_key": "note"}, {"sheet_id": "s0", "column_id": "c2",
            "field_key": None}]  # fmt: skip
    assert client.put(url, json={"decisions": both}).status_code == 422
    assert _decided(fid) == []


def test_two_level_header_joined_past_the_limit(client) -> None:
    def merged(group: int) -> bytes:  # 그룹 머리글(가로 병합) + 하위 머리글 → '그룹_하위'
        rows = [["기관명", "가" * group, None], [None, "인구", "세대"], ["행정안전부", 1, 2]]
        return _xlsx(rows, ("A1:A2", "B1:C1"))

    ok = _upload(client, "병합252.xlsx", merged(252))  # '그룹_인구' 255자
    assert _apply(client, ok, ("c1", "population"), ("c2", None)).status_code == 200
    assert [len(n) for n, _ in _decided(ok)] == [255, 255]
    long = _upload(client, "병합253.xlsx", merged(253))  # 256자
    res = _apply(client, long, ("c1", "population"))
    assert res.status_code == 409, res.text
    assert "B열(머리글 1~2행)" in res.text and "256자로 저장 한도 255자" in res.text
    assert _decided(long) == []


def test_learning_skips_a_name_whose_key_grows_past_the_limit(client) -> None:
    name = "㍿" * 100  # 100자 — 정규화(NFKC) 키는 '株式会社' × 100 = 400자
    fid = _upload(client, "호환문자.xlsx", _xlsx([["기관명", name], ["행정안전부", "a"]]))
    approvals = _count("mapping_approvals")
    res = client.put(
        f"/files/{fid}/mapping/decisions",
        json={"decisions": [{"sheet_id": "s0", "column_id": "c1", "field_key": "note"}]},
    )
    assert res.status_code == 200 and res.json()["saved"] == 1, res.text
    assert res.json()["not_learned"][0]["reason"] == "열 이름이 길어 학습하지 않음 (저장 한도 255자)"
    assert _decided(fid) == [(name, "note")]  # 확정은 저장된다
    assert db_rows("select count(*) from learned_aliases where source_file_id = ?", fid) == [(0,)]
    assert _count("mapping_approvals") == approvals  # 승격 근거로도 남기지 않음
    # 일괄 적용 경로도 같다 (확정 저장, 학습 0)
    other = _upload(client, "호환문자2.xlsx", _xlsx([["기관명", name], ["행정안전부", "b"]]))
    res = _apply(client, other, ("c1", "note"))
    assert res.status_code == 200 and res.json()["learned"] == 0 and _decided(other) == [(name, "note")], res.text


# ---------------------------------------------------------------- 검토·다시 검사·자동화


def test_review_is_not_created_for_long_names_and_leaves_nothing(client) -> None:
    long = _upload(client, "검토긴이름.xlsx", _xlsx([["기관명", N256], ["행정안전부", 1]]))
    ok = _upload(client, "검토정상.xlsx", _xlsx([["기관명", "인구수"], ["행정안전부", 1]]))
    before = _counts()
    res = client.post("/reviews", json={"file_ids": [long]})
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert "검토를 만들지 않았습니다" in detail and "'검토긴이름.xlsx' · 시트 '현황' · B열(머리글 1행)" in detail
    assert "열 이름 256자 (한도 255자)" in detail and N256[:20] not in res.text
    assert _counts() == before  # 검증 기록·검토·항목을 남기지 않는다
    # 전월 비교 자료에 있어도 같다
    res = client.post("/reviews", json={"file_ids": [ok], "baseline_file_ids": [long]})
    assert res.status_code == 422 and _counts() == before
    # 다시 검사로 긴 이름 파일로 바꾸면 거절하고, 이전 검토는 그대로 (대체되지 않음)
    review = client.post("/reviews", json={"file_ids": [ok]}).json()
    res = client.post(f"/reviews/{review['id']}/recheck", json={"replace": {str(ok): long}})
    assert res.status_code == 422, res.text
    assert client.get(f"/reviews/{review['id']}").json()["status"] == "in_review"


def test_automation_run_on_long_names_records_nothing(client) -> None:
    fid = _upload(client, "자동화원본.xlsx", _xlsx([["기관명", "인구수"], ["행정안전부", 10]]))
    assert _apply(client, fid, ("c0", "org_name"), ("c1", "population")).status_code == 200
    review = _ready(client, [fid])
    run = client.post(f"/reviews/{review['id']}/integrate", json={})
    assert run.status_code == 201, run.text
    saved = client.post(f"/runs/{run.json()['id']}/automation", json={"name": "길이 한도 자동화"})
    assert saved.status_code == 201, saved.text
    long = _upload(client, "자동화긴이름.xlsx", _xlsx([["기관명", "인구수", N256], ["행정안전부", 11, 1]]))
    before = _counts()
    res = client.post(f"/automations/{saved.json()['id']}/run", json={"file_ids": [long]})
    assert res.status_code == 422 and "검토를 만들지 않았습니다" in res.text and N256[:20] not in res.text, res.text
    assert _counts() == before  # 규칙 기록(확정)·검토·실행 기록을 만들지 않는다


def test_entity_files_listed_across_many_files_fit_the_limit(client) -> None:
    names = [f"시도별_통계자료_현황표_{i:02d}_2026년9월분.xlsx" for i in range(12)]  # 이름 약 30자 × 12개
    fids = [_upload(client, n, _xlsx([["기관명", "인구수"], ["가나다라연구기관", i]])) for i, n in enumerate(names)]
    res = client.post("/reviews", json={"file_ids": fids})
    assert res.status_code == 201, res.text  # PostgreSQL에서 이어 붙인 파일 이름(255자 초과)으로 500이 나던 경로
    shown = [i["file_name"] for i in res.json()["items"] if i["kind"] == "entity" and i["before"] == "가나다라연구기관"]
    assert shown and all(len(s) <= 255 for s in shown), shown
    assert shown[0].startswith(names[0]) and shown[0].endswith("개") and " 외 " in shown[0]


# ---------------------------------------------------------------- 통합


def test_integration_refuses_a_result_column_name_over_the_limit_before_a_run(client) -> None:
    second = _name(251, "비고원문")  # 같은 표준 항목의 두 번째 열 → 결과 열 이름 '비고 [<251자>]' = 256자
    fid = _upload(client, "통합긴열.xlsx", _xlsx([["기관명", "메모", second], ["행정안전부", "a", "b"]]))
    assert _apply(client, fid, ("c0", "org_name"), ("c1", "note"), ("c2", "note")).status_code == 200
    review = _ready(client, [fid])
    before = _counts()
    res = client.post(f"/reviews/{review['id']}/integrate", json={})
    assert res.status_code == 409, res.text
    assert "번째 열의 이름이 256자로 저장 한도 255자를 넘어 통합하지 않았습니다" in res.text
    assert second[:20] not in res.text and "INSERT" not in res.text
    assert _counts() == before  # 실행 기록·변경 기록을 만들기 전에 거절


def test_integration_failure_reason_has_no_sql(client, monkeypatch) -> None:
    from sqlalchemy.exc import DataError

    from app import runlease

    fid = _upload(client, "통합실패.xlsx", _xlsx([["기관명", "인구수"], ["행정안전부", 3]]))
    assert _apply(client, fid, ("c0", "org_name"), ("c1", "population")).status_code == 200
    review = _ready(client, [fid])

    def broken(*args: Any, **kwargs: Any) -> None:
        orig = Exception("value too long for type character varying(255)")
        raise DataError('INSERT INTO run_changes ("column") VALUES (%(column)s)', {"column": "x"}, orig)

    monkeypatch.setattr(runlease, "prepare", broken)
    res = client.post(f"/reviews/{review['id']}/integrate", json={})
    monkeypatch.undo()
    assert res.status_code == 409 and "DB 오류 (Exception)" in res.text, res.text
    assert "INSERT" not in res.text and "varying" not in res.text
    error = db_rows("select error from runs order by id desc limit 1")[0][0]
    assert "INSERT" not in error and error.startswith("DB 오류"), error


# ---------------------------------------------------------------- 용어사전


def test_glossary_import_reports_too_long_values_as_row_errors(client) -> None:
    long = "가" * 256
    csv = f"코드,정식명칭,유형,별칭\n9100001,{long},기관,\n9100002,정상기관,기관,{'㍿' * 70}\n"
    res = client.post("/glossary/import", files=[("file", ("길이.csv", csv.encode("utf-8"), "text/csv"))])
    report = res.json()
    assert res.status_code == 200 and report["applied"] is False, res.text
    assert [e["row"] for e in report["errors"]] == [2, 3]
    assert report["errors"][0]["message"].startswith("정식명칭 256자 — 저장 한도 255자를 넘습니다")
    assert report["errors"][1]["message"].startswith("별칭 280자 — 저장 한도 255자를 넘습니다")  # 정규화 키
    assert long not in res.text
    assert db_rows("select count(*) from glossary_entries where code in ('9100001', '9100002')") == [(0,)]
    ok = "코드,정식명칭\n9100003," + "나" * 255 + "\n"
    res = client.post("/glossary/import", files=[("file", ("한도.csv", ok.encode("utf-8"), "text/csv"))])
    assert res.json()["applied"] is True, res.text
    # 직접 추가하는 별칭: 이름은 한도 안이어도 정규화한 키가 넘으면 거절
    res = client.post("/glossary/aliases", json={"alias": "㍿" * 70, "target_code": "1741000"})
    assert res.status_code == 422 and "280자로 저장 한도 255자" in res.text, res.text


# ---------------------------------------------------------------- 한도 검사 전에 SQLite에 쌓인 자료 (그대로 둠)


@pytest.mark.sqlite_only("SQLite에만 한도를 넘는 기존 자료가 있을 수 있음 (PostgreSQL은 쓸 수 없음)")
def test_existing_long_values_in_sqlite_are_left_as_they_are(client) -> None:
    fid = _upload(client, "기존긴이름.xlsx", _xlsx([["기관명", N256], ["행정안전부", 1]]))
    db_rows(
        "insert into mapping_decisions (source_file_id, sheet_name, column_name, field_key) values (?, '현황', ?, ?)",
        fid,
        N256,
        "note",
    )
    assert client.get(f"/files/{fid}/mapping/latest").status_code == 200  # 기존 확정은 그대로 읽힌다
    assert _decided(fid) == [(N256, "note")]
    # 새 확정(지금 상태를 맞게 적고 덮어쓰기를 골라도)은 저장 한도로 거절하고, 이미 있던 행은 지우거나 바꾸지 않는다
    item = {"file_id": fid, "sheet_id": "s0", "column_id": "c1", "field_key": None, "expected_confirmed": True,
            "expected_field_key": "note", "overwrite": True}  # fmt: skip
    res = client.post("/mapping/batch/apply", json={"items": [item]})
    assert res.status_code == 409 and "256자로 저장 한도 255자" in res.text, res.text
    assert _decided(fid) == [(N256, "note")]


@pytest.mark.sqlite_only("한도 검사 전에 SQLite에서 만든 검토 항목 (PostgreSQL에는 생길 수 없음)")
def test_legacy_review_item_with_a_long_name_is_not_confirmed(client) -> None:
    fid = _upload(client, "기존검토.xlsx", _xlsx([["기관명", "옛 메모"], ["행정안전부", "a"]]))
    review = client.post("/reviews", json={"file_ids": [fid]}).json()
    item = next(i for i in review["items"] if i["kind"] == "mapping" and i["column"] == "옛 메모")
    db_rows(
        "update review_items set category = 'pending', decision = null, \"column\" = ? where id = ?", N256, item["id"]
    )
    before = _decided(fid)
    res = client.post(f"/reviews/{review['id']}/items/{item['id']}/decision", json={"decision": "rejected"})
    assert res.status_code == 409 and "256자로 저장 한도 255자" in res.text and N256[:20] not in res.text, res.text
    assert _decided(fid) == before
