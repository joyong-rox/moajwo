"""검토 완료는 한 번만 — 화면에서 '검토 완료'를 빠르게 두 번 누르면 요청 두 개가 거의 동시에 온다.

재현 (5장 중복 제출 확인, 격리 서버): 두 요청이 모두 200이었다. 둘 다 '검토 중'을 읽은 뒤 차례로 완료한 것이다
(결과는 검토 1건 완료라 같았지만, '이 형식 기억하기'를 켜면 학습이 두 번 겹칠 수 있다).
순서는 시간(sleep)이 아니라 이벤트로 정한다. SQLite·PostgreSQL 실행 모두.
- 차례 완료: 두 DB 세션이 같은 검토를 '검토 중'으로 읽고, 첫 세션이 완료한 뒤 두 번째 세션이 완료를 시도한다.
- 실제 경합: 두 요청(각자 연결)이 동시에 완료를 보내고, 첫 요청이 '완료'로 바꾼 뒤 아직 커밋하지 않은 동안 두 번째
  요청이 같은 행을 바꾸려고 들어온다 (PostgreSQL은 그 요청이 행 잠금을 기다리는 것까지 확인한 뒤 첫 요청을 진행).
- 중간 실패: 완료를 차지한 직후·학습 도중의 예외는 완료 상태·학습을 남기지 않고, 다시 하면 한 번 성공한다.
"""

import csv
import io
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text

from app import db as app_db
from app import review as review_module
from app.models import (
    GlossaryAlias,
    LearnedAlias,
    MappingApproval,
    MappingDecision,
    PromotionCandidate,
    ReviewItem,
    ReviewSession,
)
from app.review import ReviewError, complete
from tests.test_accounts import owner  # noqa: F401
from tests.test_auth import _client, _provision_user, app_real_login  # noqa: F401

ROWS = [["구분", "집행액"], ["사업A", "100"], ["사업B", "200"]]


def _learnable(tag: str) -> dict[str, str]:
    """표준 사전에 없는 열 이름 — '이 형식 기억하기'로 실제로 학습되는 이름.
    시험마다 달라야 앞 시험이 학습한 별칭으로 자동 매핑되지 않는다."""
    return {f"{tag} 추진 구분값": "department", f"{tag} 집행 실적값": "expenditure"}


@pytest.fixture(scope="module")
def user(app_real_login, owner) -> TestClient:  # noqa: F811
    _provision_user(owner, app_real_login, "once-user", "pass-once-user", "ONCE")
    return _client(app_real_login, "once-user", "pass-once-user")


def _ready_review(c: TestClient, name: str, remember: bool = False) -> int:
    return _ready(c, name, remember)[0]


def _ready(c: TestClient, name: str, remember: bool = False, learn: str = "") -> tuple[int, int]:
    """검토 완료할 수 있는 검토를 만든다. learn(열 이름 앞말)이 있으면 열 매핑을 미리 확정하지 않고 검토에서
    '확인 필요' 매핑을 승인한다 — 완료 때 '이 형식 기억하기'가 이 사용자의 별칭을 새로 만드는 경로
    (미리 확정하면 그때 이미 학습됨)."""
    learnable = bool(learn)
    keys = _learnable(learn) if learnable else {"구분": "department", "집행액": "expenditure"}
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows([list(keys), *ROWS[1:]])
    res = c.post("/files", files=[("files", (name, buf.getvalue().encode("utf-8"), "text/csv"))])
    assert res.status_code == 200, res.text
    fid = res.json()[0]["id"]
    if not learnable:
        pv = c.get(f"/files/{fid}/preview").json()
        ids = {col["name"]: col["column_id"] for col in pv["columns"]}
        items = [{"sheet_id": pv["sheet_id"], "column_id": ids[n], "field_key": k} for n, k in keys.items()]
        assert c.put(f"/files/{fid}/mapping/decisions", json={"decisions": items}).status_code == 200
    res = c.post("/reviews", json={"file_ids": [fid]})
    assert res.status_code == 201, res.text
    review = res.json()
    for item in review["items"]:
        if learnable and item["category"] == "pending" and item["kind"] == "mapping":
            body = {"decision": "approved", "choice": keys[item["column"]]}
            assert c.post(f"/reviews/{review['id']}/items/{item['id']}/decision", json=body).status_code == 200
    if not c.get(f"/reviews/{review['id']}").json()["can_complete"]:  # 승인으로 검사 기준이 바뀌면 다시 검사
        res = c.post(f"/reviews/{review['id']}/recheck", json={})
        assert res.status_code == 201, res.text
        review = res.json()
    if remember:
        assert c.patch(f"/reviews/{review['id']}", json={"remember_format": True}).status_code == 200
    assert c.get(f"/reviews/{review['id']}").json()["can_complete"], review
    return review["id"], fid


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


# ---------------------------------------------------------------- 실제 경합 · 중간 실패


def _rows(query: Any) -> list[tuple]:
    with app_db.SessionLocal() as s:
        return [tuple(r) for r in s.execute(query)]


def _state(rid: int, fid: int, uid: int) -> dict[str, list[tuple]]:
    """완료가 바꿀 수 있는 것 전부: 검토 상태·완료 시각, 이 사용자의 학습 별칭(출처·시각 포함), 용어사전 별칭,
    승인·승격 근거, 검토 항목 결정, 이 파일의 매핑 확정."""
    return {
        "review": _rows(select(ReviewSession.status, ReviewSession.completed_at).where(ReviewSession.id == rid)),
        "learned": _rows(select(LearnedAlias.__table__).where(LearnedAlias.user_id == uid).order_by(LearnedAlias.id)),
        "glossary_aliases": _rows(select(GlossaryAlias.__table__).order_by(GlossaryAlias.id)),
        "approvals": _rows(select(MappingApproval.__table__).order_by(MappingApproval.id)),
        "candidates": _rows(select(PromotionCandidate.__table__).order_by(PromotionCandidate.id)),
        "items": _rows(
            select(ReviewItem.id, ReviewItem.decision, ReviewItem.choice)
            .where(ReviewItem.session_id == rid)
            .order_by(ReviewItem.id)
        ),
        "decisions": _rows(
            select(MappingDecision.__table__).where(MappingDecision.source_file_id == fid).order_by(MappingDecision.id)
        ),
    }


def _changed(before: dict[str, list[tuple]], after: dict[str, list[tuple]]) -> set[str]:
    return {k for k in before if before[k] != after[k]}


def _counting(calls: list[str], real: Any) -> Any:
    """학습 호출을 센다 — 같은 값으로 두 번 학습하면 행 내용만으로는 보이지 않는다."""

    def counted(*args: Any, **kwargs: Any) -> str | None:
        calls.append(args[3])
        return real(*args, **kwargs)

    return counted


def _waiting_update(conn: Any) -> int:
    """다른 연결이 review_sessions 갱신에서 행 잠금을 기다리는 중인지 (PostgreSQL)."""
    query = (
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock' "
        "AND query ILIKE 'UPDATE review_sessions%'"
    )
    return conn.execute(text(query)).scalar() or 0


def test_two_concurrent_completes_finish_once_and_learn_once(app_real_login, user, monkeypatch) -> None:  # noqa: F811
    rid, fid = _ready(user, "once-race.csv", remember=True, learn="경합")
    uid = user.get("/auth/me").json()["id"]
    before = _state(rid, fid, uid)
    claimed, entered, second_updating = threading.Event(), threading.Event(), threading.Event()
    calls: list[str] = []
    counted = _counting(calls, review_module.learn_column)

    def gated(*args: Any, **kwargs: Any) -> str | None:
        if not claimed.is_set():  # 첫 학습 = 완료를 차지한 요청 (아직 커밋 전) — 두 번째 요청이 들어올 때까지 멈춘다
            claimed.set()
            assert entered.wait(60)
        return counted(*args, **kwargs)

    monkeypatch.setattr(review_module, "learn_column", gated)

    def on_execute(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        # 첫 요청이 멈춘 동안 같은 행을 바꾸려는 문장 = 두 번째 요청의 완료 차지
        claim = statement.lstrip().upper().startswith("UPDATE REVIEW_SESSIONS")
        if claim and claimed.is_set() and not entered.is_set():
            second_updating.set()

    event.listen(app_db.engine, "before_cursor_execute", on_execute)
    second_client = _client(app_real_login, "once-user", "pass-once-user")  # 각자 세션·연결
    results: dict[str, Any] = {}

    def send(name: str, c: TestClient) -> None:
        res = c.post(f"/reviews/{rid}/complete")
        results[name] = (res.status_code, res.json())

    first = threading.Thread(target=send, args=("first", user))
    second = threading.Thread(target=send, args=("second", second_client))
    try:
        first.start()
        assert claimed.wait(60)
        second.start()
        assert second_updating.wait(60)
        if app_db.engine.dialect.name == "postgresql":  # 실제로 행 잠금을 기다리는지 상태로 확인
            with app_db.engine.connect() as probe:
                deadline = time.monotonic() + 60
                while not _waiting_update(probe) and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert _waiting_update(probe) == 1
        entered.set()
        first.join(120)
        second.join(120)
    finally:
        entered.set()
        event.remove(app_db.engine, "before_cursor_execute", on_execute)
    assert results["first"][0] == 200, results
    assert results["second"][0] == 409 and "이미 검토가 끝난" in results["second"][1]["detail"], results
    assert results["first"][1]["learned"]["columns"] == 2
    after = _state(rid, fid, uid)
    assert _changed(before, after) == {"review", "learned"}  # 그 밖(용어사전·승인·승격·항목·확정)은 그대로
    assert after["review"][0][0] == "ready" and len(after["learned"]) == len(before["learned"]) + 2
    assert sorted(calls) == sorted(_learnable("경합"))  # 학습은 한 번만 (두 열 × 1회)


@pytest.mark.parametrize("fail_on", [1, 2], ids=["right-after-claim", "during-learning"])
def test_failure_after_claim_leaves_nothing_and_a_retry_completes_once(app_real_login, user, monkeypatch, fail_on):  # noqa: F811
    c = TestClient(app_real_login, raise_server_exceptions=False)
    assert c.post("/auth/login", json={"username": "once-user", "password": "pass-once-user"}).status_code == 200
    rid, fid = _ready(c, f"once-fail-{fail_on}.csv", remember=True, learn=f"실패{fail_on}")
    uid = c.get("/auth/me").json()["id"]
    before = _state(rid, fid, uid)
    real = review_module.learn_column
    seen: list[str] = []

    def boom(*args: Any, **kwargs: Any) -> str | None:
        seen.append(args[3])
        if len(seen) == fail_on:  # 1: 완료를 차지한 직후, 첫 학습 전 · 2: 첫 열을 학습(flush)한 뒤
            raise RuntimeError("시험용 실패")
        return real(*args, **kwargs)

    monkeypatch.setattr(review_module, "learn_column", boom)
    assert c.post(f"/reviews/{rid}/complete").status_code == 500
    assert _state(rid, fid, uid) == before  # 완료 상태·학습·다른 기록 모두 남지 않음
    assert c.get(f"/reviews/{rid}").json()["status"] == "in_review"
    calls: list[str] = []
    monkeypatch.setattr(review_module, "learn_column", _counting(calls, real))
    retry = c.post(f"/reviews/{rid}/complete")
    assert retry.status_code == 200 and retry.json()["learned"]["columns"] == 2
    assert c.post(f"/reviews/{rid}/complete").status_code == 409
    after = _state(rid, fid, uid)
    assert _changed(before, after) == {"review", "learned"} and len(after["learned"]) == len(before["learned"]) + 2
    assert sorted(calls) == sorted(_learnable(f"실패{fail_on}"))
