"""검토 목록 (GET /reviews, /reviews/counts) — 결과를 만들기 전의 수동 검토도 찾아 이어갈 수 있게 하는 조회 전용 목록.

볼 수 있는 범위는 단건 조회와 같다: 일반 사용자는 자기 검토만, 최고 관리자는 전체(남의 검토는 조회만).
파일 이름은 화면 표시용으로 개인정보 형식을 가린다. 상태 조건을 먼저 걸고 번호 순으로 나눠 주며(next_before),
'다시 검사 필요'처럼 계산해야 아는 상태도 같은 원칙으로 끝까지 찾을 수 있다. 목록·건수 조회는 쓰지 않는다.
"""

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from tests.test_accounts import owner  # noqa: F401
from tests.test_auth import _client, app_real_login  # noqa: F401

ROWS = [["구분", "집행액"], ["사업A", "100"], ["사업B", "200"]]


@pytest.fixture(scope="module")
def people(app_real_login, owner) -> dict[str, TestClient]:  # noqa: F811
    made = {"owner": owner}
    for name in ("rl-user1", "rl-user2", "rl-user3"):
        res = owner.post("/users", json={"username": name, "display_name": name.upper(), "status": "active",
                                         "password": f"pass-{name}"})  # fmt: skip
        assert res.status_code == 201, res.text
        made[name] = _client(app_real_login, name, f"pass-{name}")
    return made


def _file(c: TestClient, name: str) -> int:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(ROWS)
    res = c.post("/files", files=[("files", (name, buf.getvalue().encode("utf-8"), "text/csv"))])
    assert res.status_code == 200, res.text
    fid = res.json()[0]["id"]
    pv = c.get(f"/files/{fid}/preview").json()
    ids = {col["name"]: col["column_id"] for col in pv["columns"]}
    items = [{"sheet_id": pv["sheet_id"], "column_id": ids[n], "field_key": k}
             for n, k in (("구분", "department"), ("집행액", "expenditure"))]  # fmt: skip
    assert c.put(f"/files/{fid}/mapping/decisions", json={"decisions": items}).status_code == 200
    return fid


def _review(c: TestClient, fid: int) -> dict:
    res = c.post("/reviews", json={"file_ids": [fid]})
    assert res.status_code == 201, res.text
    return res.json()


def _pages(c: TestClient, query: str = "") -> list[list[dict]]:
    """next_before를 따라 끝까지 받은 쪽들 (쪽마다 항목 목록)."""
    pages, before = [], None
    while True:
        params = "&".join(x for x in (query, f"before={before}" if before else "") if x)
        res = c.get(f"/reviews?{params}")
        assert res.status_code == 200, res.text
        body = res.json()
        pages.append(body["items"])
        before = body["next_before"]
        if before is None:
            return pages
        assert len(pages) < 200


def _all(c: TestClient, query: str = "") -> list[dict]:
    return [r for page in _pages(c, query) for r in page]


def test_list_shows_own_reviews_with_status_runs_and_latest(people) -> None:
    a = people["rl-user1"]
    fid = _file(a, "목록_진행중.csv")
    pending = _review(a, fid)
    done = _review(a, _file(a, "목록_완료.csv"))
    assert a.post(f"/reviews/{done['id']}/complete").status_code == 200
    run = a.post(f"/reviews/{done['id']}/integrate", json={"include_unmapped": True}).json()
    old = _review(a, _file(a, "목록_대체.csv"))
    new = a.post(f"/reviews/{old['id']}/recheck", json={"replace": {}}).json()

    rows = {r["id"]: r for r in _all(a)}
    assert {pending["id"], done["id"], new["id"]} <= set(rows) and old["id"] not in rows  # 대체된 검토는 기본 제외
    assert list(rows) == sorted(rows, reverse=True)  # 최신 순
    assert rows[pending["id"]]["status"] == "in_review" and rows[pending["id"]]["status_label"] == "검토 중"
    assert rows[pending["id"]]["file_names"] == ["목록_진행중.csv"] and rows[pending["id"]]["run_count"] == 0
    assert (rows[done["id"]]["run_count"], rows[done["id"]]["last_run_id"]) == (1, run["id"])
    assert all(r["can_edit"] and not r["needs_recheck"] for r in rows.values())

    with_old = {r["id"]: r for r in _all(a, "include_superseded=true")}
    assert with_old[old["id"]]["status"] == "superseded" and with_old[old["id"]]["latest_id"] == new["id"]
    assert [r["id"] for r in _all(a, "filter=ready")] == [done["id"]]
    assert a.get("/reviews?filter=nope").status_code == 422


def test_list_follows_view_permission(people) -> None:
    a, b, owner_client = people["rl-user1"], people["rl-user2"], people["owner"]
    mine = _review(b, _file(b, "다른사용자.csv"))
    assert mine["id"] in [r["id"] for r in _all(b)]
    for query in ("", "filter=open", "filter=recheck", "include_superseded=true"):
        assert mine["id"] not in [r["id"] for r in _all(a, query)]  # 남의 검토는 어떤 조건의 목록에도 없다
    assert a.get(f"/reviews/{mine['id']}").status_code == 404
    rows = {r["id"]: r for r in _all(owner_client)}
    assert rows[mine["id"]]["can_edit"] is False and rows[mine["id"]]["created_by_name"] == "RL-USER2"
    assert owner_client.post(f"/reviews/{mine['id']}/complete").status_code == 403  # 조회 전용은 그대로
    assert TestClient(owner_client.app).get("/reviews").status_code == 401
    assert TestClient(owner_client.app).get("/reviews/counts").status_code == 401


def test_list_and_review_mask_personal_data_in_file_names(people) -> None:
    a = people["rl-user1"]
    review = _review(a, _file(a, "담당 kim@example.com 자료.csv"))
    (row,) = [r for r in _all(a) if r["id"] == review["id"]]
    assert "kim@example.com" not in json.dumps(row, ensure_ascii=False) and row["file_names"][0].endswith("자료.csv")
    (open_row,) = [r for r in _all(a, "filter=open") if r["id"] == review["id"]]
    assert "kim@example.com" not in json.dumps(open_row, ensure_ascii=False)
    got = a.get(f"/reviews/{review['id']}").json()
    assert "kim@example.com" not in json.dumps(got["files"], ensure_ascii=False)
    assert all("kim@example.com" not in (i["file_name"] or "") for i in got["items"])


def test_list_marks_reviews_that_need_a_recheck(people) -> None:
    from app.db import SessionLocal
    from app.models import ReviewSession

    a = people["rl-user1"]
    review = _review(a, _file(a, "목록_이전버전.csv"))
    with SessionLocal() as db:  # 검사 기준을 기록하기 전 버전의 검토
        session = db.get(ReviewSession, review["id"])
        params = json.loads(session.params)
        params.pop("check_basis")
        session.params = json.dumps(params, ensure_ascii=False)
        db.commit()
    (row,) = [r for r in _all(a) if r["id"] == review["id"]]
    assert row["needs_recheck"] is True
    assert review["id"] in [r["id"] for r in _all(a, "filter=recheck")]


def _decide(c: TestClient, fid: int, column: str, key: str | None) -> None:
    pv = c.get(f"/files/{fid}/preview").json()
    ids = {col["name"]: col["column_id"] for col in pv["columns"]}
    item = {"sheet_id": pv["sheet_id"], "column_id": ids[column], "field_key": key}
    assert c.put(f"/files/{fid}/mapping/decisions", json={"decisions": [item]}).status_code == 200


def _one(c: TestClient, rid: int) -> dict:
    (row,) = [r for r in _all(c) if r["id"] == rid]
    return row


@pytest.fixture(scope="module")
def old_and_recent(people) -> dict:
    """오래된 검토 1개(검토 중 + 다시 검사 필요) 뒤에 완료한 검토 5개 — 최신 쪽이 모두 완료인 상태."""
    c = people["rl-user3"]
    old_file = _file(c, "페이지_오래된.csv")
    old = _review(c, old_file)
    _decide(c, old_file, "구분", None)  # 검사한 뒤 확정을 바꿈 → 다시 검사 필요
    ready_file = _file(c, "페이지_완료.csv")
    ready = []
    for _ in range(5):
        r = _review(c, ready_file)
        assert c.post(f"/reviews/{r['id']}/complete").status_code == 200
        ready.append(r["id"])
    return {"old": old["id"], "ready": ready}


def test_list_filters_before_paging_so_old_reviews_are_reachable(people, old_and_recent) -> None:
    c, old, ready = people["rl-user3"], old_and_recent["old"], old_and_recent["ready"]
    first = c.get("/reviews?limit=2").json()
    assert [r["id"] for r in first["items"]] == ready[::-1][:2] and first["next_before"] == ready[-2]
    pages = _pages(c, "limit=2")  # 끝까지: 빠짐·겹침 없이 최신 순
    ids = [r["id"] for page in pages for r in page]
    assert ids == sorted([old, *ready], reverse=True) and len(pages) == 3

    # 최신 2개가 모두 완료여도 '검토 중'·'다시 검사 필요'는 조건을 먼저 걸어 오래된 검토를 바로 찾는다
    assert c.get("/reviews?filter=open&limit=2").json() == {"items": [_one(c, old)], "next_before": None}
    recheck = c.get("/reviews?filter=recheck&limit=2").json()
    assert [r["id"] for r in recheck["items"]] == [old] and recheck["next_before"] is None
    assert recheck["items"][0]["needs_recheck"] is True and recheck["items"][0]["status"] == "in_review"
    assert [r["id"] for r in _all(c, "filter=ready&limit=2")] == ready[::-1]
    for bad in ("filter=nope", "limit=0", "limit=101", "before=0"):
        assert c.get(f"/reviews?{bad}").status_code == 422, bad


def test_recheck_filter_continues_past_the_scan_budget(people, old_and_recent, monkeypatch) -> None:
    from app import review_api

    monkeypatch.setattr(review_api, "RECHECK_SCAN", 2)  # 한 번에 2개만 살펴본다
    c, old, ready = people["rl-user3"], old_and_recent["old"], old_and_recent["ready"]
    first = c.get("/reviews?filter=recheck").json()
    assert first == {"items": [], "next_before": ready[-2]}  # 비어 있어도 '없음'이 아니라 이어서 찾을 곳이 있다
    pages = _pages(c, "filter=recheck")
    assert [r["id"] for page in pages for r in page] == [old] and len(pages) == 3


def test_counts_cover_every_visible_review_not_only_a_page(people, old_and_recent) -> None:
    c, ready = people["rl-user3"], old_and_recent["ready"]
    expected = {"total": 6, "live": 6, "open": 1, "ready": 5, "superseded": 0, "recheck": 1}
    assert c.get("/reviews/counts").json() == expected
    child = c.post(f"/reviews/{ready[0]}/recheck", json={"replace": {}}).json()  # 완료 1개를 다시 검사 → 대체
    counts = c.get("/reviews/counts").json()
    assert counts == {"total": 7, "live": 6, "open": 2, "ready": 4, "superseded": 1, "recheck": 1}
    every = [r["id"] for r in _all(c, "include_superseded=true&limit=3")]
    assert len(every) == len(set(every)) == counts["total"] and ready[0] in every and child["id"] in every
    assert ready[0] not in [r["id"] for r in _all(c, "limit=3")]

    from app.db import SessionLocal
    from app.models import ReviewSession, User

    with SessionLocal() as db:
        other = db.scalar(select(User.id).where(User.username == "rl-user2"))
        theirs = db.scalar(select(func.count()).select_from(ReviewSession).where(ReviewSession.created_by == other))
        everyone = db.scalar(select(func.count()).select_from(ReviewSession))
    assert people["rl-user2"].get("/reviews/counts").json()["total"] == theirs  # 자기 검토만 센다
    assert people["owner"].get("/reviews/counts").json()["total"] == everyone  # 최고 관리자는 전체


def test_list_and_counts_read_only_without_models_or_parsing(people, old_and_recent, monkeypatch) -> None:
    from app import review
    from app.db import engine

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("목록 조회가 모델·원본 파싱을 불렀습니다")

    monkeypatch.setattr(review, "get_engine", forbidden)
    monkeypatch.setattr(review, "load_parsed", forbidden)
    writes: list[str] = []

    def watch(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        if statement.lstrip().split(" ", 1)[0].upper() in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", watch)
    try:
        for c in (people["rl-user3"], people["owner"]):
            for query in ("", "filter=open", "filter=ready", "filter=recheck", "include_superseded=true", "limit=1"):
                assert c.get(f"/reviews?{query}").status_code == 200
            assert c.get("/reviews/counts").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", watch)
    assert writes == []
