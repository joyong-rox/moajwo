"""검토 완료는 한 번만 — 화면에서 '검토 완료'를 빠르게 두 번 누르면 요청 두 개가 거의 동시에 온다.

재현 (5장 중복 제출 확인, 격리 서버): 두 요청이 모두 200이었다. 둘 다 '검토 중'을 읽은 뒤 차례로 완료한 것이다
(결과는 검토 1건 완료라 같았지만, '이 형식 기억하기'를 켜면 학습이 두 번 겹칠 수 있다).
여기서는 같은 순서를 결정적으로 만든다: 두 DB 세션이 같은 검토를 '검토 중'으로 읽고, 첫 세션이 완료한 뒤
두 번째 세션(읽어 둔 '검토 중'을 들고 있음)이 완료를 시도한다. SQLite·PostgreSQL 실행 모두.
"""

import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db as app_db
from app.models import LearnedAlias, ReviewSession
from app.review import ReviewError, complete
from tests.test_accounts import owner  # noqa: F401
from tests.test_auth import _client, app_real_login  # noqa: F401

ROWS = [["구분", "집행액"], ["사업A", "100"], ["사업B", "200"]]


@pytest.fixture(scope="module")
def user(app_real_login, owner) -> TestClient:  # noqa: F811
    body = {"username": "once-user", "display_name": "ONCE", "status": "active", "password": "pass-once-user"}
    res = owner.post("/users", json=body)
    assert res.status_code == 201, res.text
    return _client(app_real_login, "once-user", "pass-once-user")


def _ready_review(c: TestClient, name: str, remember: bool = False) -> int:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(ROWS)
    res = c.post("/files", files=[("files", (name, buf.getvalue().encode("utf-8"), "text/csv"))])
    assert res.status_code == 200, res.text
    fid = res.json()[0]["id"]
    pv = c.get(f"/files/{fid}/preview").json()
    ids = {col["name"]: col["column_id"] for col in pv["columns"]}
    keys = {"구분": "department", "집행액": "expenditure"}
    items = [{"sheet_id": pv["sheet_id"], "column_id": ids[n], "field_key": k} for n, k in keys.items()]
    assert c.put(f"/files/{fid}/mapping/decisions", json={"decisions": items}).status_code == 200
    res = c.post("/reviews", json={"file_ids": [fid]})
    assert res.status_code == 201, res.text
    review = res.json()
    if remember:
        assert c.patch(f"/reviews/{review['id']}", json={"remember_format": True}).status_code == 200
    assert c.get(f"/reviews/{review['id']}").json()["can_complete"], review
    return review["id"]


def test_second_complete_that_read_in_review_before_the_first_finished_is_refused(user) -> None:
    rid = _ready_review(user, "once-a.csv")
    with app_db.SessionLocal() as first, app_db.SessionLocal() as second:
        a, b = first.get(ReviewSession, rid), second.get(ReviewSession, rid)
        assert a.status == b.status == "in_review"  # 두 요청이 모두 '검토 중'을 읽은 상태
        complete(first, a)
        with pytest.raises(ReviewError, match="이미 검토가 끝난"):
            complete(second, b)
    with app_db.SessionLocal() as s:
        assert s.get(ReviewSession, rid).status == "ready"


def test_second_complete_does_not_learn_again(user) -> None:
    rid = _ready_review(user, "once-b.csv", remember=True)

    def learned_rows(uid: int) -> list[tuple]:
        with app_db.SessionLocal() as s:
            cols = select(LearnedAlias.id, LearnedAlias.normalized, LearnedAlias.field_key)
            return list(s.execute(cols.where(LearnedAlias.user_id == uid)))

    with app_db.SessionLocal() as first, app_db.SessionLocal() as second:
        a, b = first.get(ReviewSession, rid), second.get(ReviewSession, rid)
        uid = a.created_by
        complete(first, a)
        after_first = learned_rows(uid)
        with pytest.raises(ReviewError):
            complete(second, b)
    assert learned_rows(uid) == after_first  # 두 번째 요청은 학습 자료를 바꾸지 않음


def test_api_second_complete_is_409_and_review_stays_ready(user) -> None:
    rid = _ready_review(user, "once-c.csv")
    assert user.post(f"/reviews/{rid}/complete").status_code == 200
    res = user.post(f"/reviews/{rid}/complete")
    assert res.status_code == 409 and "이미 검토가 끝난" in res.json()["detail"]
    assert user.get(f"/reviews/{rid}").json()["status"] == "ready"
