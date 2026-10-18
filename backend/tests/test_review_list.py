"""검토 목록 (GET /reviews) — 결과를 만들기 전의 수동 검토도 찾아 이어갈 수 있게 하는 조회 전용 목록.

볼 수 있는 범위는 단건 조회와 같다: 일반 사용자는 자기 검토만, 최고 관리자는 전체(남의 검토는 조회만).
파일 이름은 화면 표시용으로 개인정보 형식을 가린다.
"""

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from tests.test_accounts import owner  # noqa: F401
from tests.test_auth import _client, app_real_login  # noqa: F401

ROWS = [["구분", "집행액"], ["사업A", "100"], ["사업B", "200"]]


@pytest.fixture(scope="module")
def people(app_real_login, owner) -> dict[str, TestClient]:  # noqa: F811
    made = {"owner": owner}
    for name in ("rl-user1", "rl-user2"):
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


def test_list_shows_own_reviews_with_status_runs_and_latest(people) -> None:
    a = people["rl-user1"]
    fid = _file(a, "목록_진행중.csv")
    pending = _review(a, fid)
    done = _review(a, _file(a, "목록_완료.csv"))
    assert a.post(f"/reviews/{done['id']}/complete").status_code == 200
    run = a.post(f"/reviews/{done['id']}/integrate", json={"include_unmapped": True}).json()
    old = _review(a, _file(a, "목록_대체.csv"))
    new = a.post(f"/reviews/{old['id']}/recheck", json={"replace": {}}).json()

    rows = {r["id"]: r for r in a.get("/reviews").json()}
    assert {pending["id"], done["id"], new["id"]} <= set(rows) and old["id"] not in rows  # 대체된 검토는 기본 제외
    assert list(rows) == sorted(rows, reverse=True)  # 최신 순
    assert rows[pending["id"]]["status"] == "in_review" and rows[pending["id"]]["status_label"] == "검토 중"
    assert rows[pending["id"]]["file_names"] == ["목록_진행중.csv"] and rows[pending["id"]]["run_count"] == 0
    assert (rows[done["id"]]["run_count"], rows[done["id"]]["last_run_id"]) == (1, run["id"])
    assert all(r["can_edit"] and not r["needs_recheck"] for r in rows.values())

    with_old = {r["id"]: r for r in a.get("/reviews?include_superseded=true").json()}
    assert with_old[old["id"]]["status"] == "superseded" and with_old[old["id"]]["latest_id"] == new["id"]
    assert [r["id"] for r in a.get("/reviews?status=ready").json()] == [done["id"]]
    assert a.get("/reviews?status=nope").status_code == 422


def test_list_follows_view_permission(people) -> None:
    a, b, owner_client = people["rl-user1"], people["rl-user2"], people["owner"]
    mine = _review(b, _file(b, "다른사용자.csv"))
    assert mine["id"] in [r["id"] for r in b.get("/reviews").json()]
    assert mine["id"] not in [r["id"] for r in a.get("/reviews").json()]  # 남의 검토는 목록에도 없다
    assert a.get(f"/reviews/{mine['id']}").status_code == 404
    rows = {r["id"]: r for r in owner_client.get("/reviews").json()}
    assert rows[mine["id"]]["can_edit"] is False and rows[mine["id"]]["created_by_name"] == "RL-USER2"
    assert owner_client.post(f"/reviews/{mine['id']}/complete").status_code == 403  # 조회 전용은 그대로
    assert TestClient(owner_client.app).get("/reviews").status_code == 401


def test_list_and_review_mask_personal_data_in_file_names(people) -> None:
    a = people["rl-user1"]
    review = _review(a, _file(a, "담당 kim@example.com 자료.csv"))
    (row,) = [r for r in a.get("/reviews").json() if r["id"] == review["id"]]
    assert "kim@example.com" not in json.dumps(row, ensure_ascii=False) and row["file_names"][0].endswith("자료.csv")
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
    (row,) = [r for r in a.get("/reviews").json() if r["id"] == review["id"]]
    assert row["needs_recheck"] is True
