"""로그인·권한 분리·표준 사전 승격·대시보드.

이 모듈만 실제 로그인(쿠키)으로 검사한다. 사용자마다 별도 TestClient(쿠키 저장소)를 쓴다.
"""

import io
import json
import sqlite3
from collections.abc import Iterator
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.config import DATA_DIR

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PROMO_COLUMN = "세부 추진 내역"  # 기본 표준 사전에 없는 열 이름


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "현황"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ROWS = [
    ["기관명", "집행액", PROMO_COLUMN],
    ["행정안전부", 1000, "정상 추진"],
    ["기상청", 2500, "검토 중"],
]


@pytest.fixture(scope="module")
def app_real_login(logged_in_owner) -> Iterator[Any]:
    """기존 테스트용 로그인 대체를 잠시 끄고 실제 쿠키 로그인으로 검사한다."""
    from app import admin_api, automation, files, integration, review, validation_api
    from app.auth import current_user
    from app.main import app
    from app.mapping import MappingEngine, load_dictionary

    def no_llm(system: str, user: str, schema: dict) -> dict[str, Any]:
        return {"key": "none", "confidence": 0, "reason": "해당 없음"}

    engine = MappingEngine(load_dictionary(), None, no_llm)
    patch = pytest.MonkeyPatch()
    for module in (files, validation_api, review, integration, automation, admin_api):
        patch.setattr(module, "get_engine", lambda: engine)
    override = app.dependency_overrides.pop(current_user)
    yield app
    app.dependency_overrides[current_user] = override
    patch.undo()


def _client(app: Any, username: str | None = None, password: str = "test-password-1") -> TestClient:
    c = TestClient(app)
    if username is not None:
        res = c.post("/auth/login", json={"username": username, "password": password})
        assert res.status_code == 200, res.text
    return c


@pytest.fixture(scope="module")
def accounts(app_real_login) -> dict[str, TestClient]:
    owner = _client(app_real_login, "test-owner")
    made = {"owner": owner}
    for username, role in [
        ("admin1", "admin"),
        ("user1", "user"),
        ("user2", "user"),
        ("user3", "user"),
        ("user4", "user"),
    ]:
        res = owner.post(
            "/users",
            json={"username": username, "display_name": username.upper(), "role": role, "password": "pass-" + username},
        )
        assert res.status_code == 201, res.text
        made[username] = _client(app_real_login, username, "pass-" + username)
    return made


def _upload(c: TestClient, rows: list[list] = ROWS, name: str = "자료.xlsx") -> int:
    res = c.post("/files", files=[("files", (name, _xlsx(rows), XLSX))])
    assert res.status_code == 200, res.text
    return res.json()[0]["id"]


def test_login_required_and_session_lifecycle(app_real_login, accounts) -> None:
    anon = _client(app_real_login)
    for method, path in [("get", "/files"), ("get", "/runs"), ("get", "/dashboard"), ("get", "/users")]:
        assert getattr(anon, method)(path).status_code == 401, path
    assert anon.get("/health").status_code == 200
    assert anon.get("/auth/status").json() == {"setup_required": False, "user": None}
    # 계정이 이미 있으면 최초 설정은 막힌다
    res = anon.post("/auth/setup", json={"username": "evil", "display_name": "x", "password": "password-123"})
    assert res.status_code == 409

    bad = anon.post("/auth/login", json={"username": "user1", "password": "wrong-password"})
    unknown = anon.post("/auth/login", json={"username": "nobody", "password": "wrong-password"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()["detail"] == unknown.json()["detail"]  # 아이디가 있는지 알려주지 않는다

    assert accounts["user1"].get("/auth/status").json()["user"]["username"] == "user1"
    me = accounts["user1"].get("/auth/me").json()
    assert me["role"] == "user" and me["role_label"] == "일반 사용자"
    cookie = accounts["user1"].cookies.get("moajwo_session")
    assert cookie and "user1" not in cookie
    with sqlite3.connect(DATA_DIR / "moajwo.db") as con:
        stored = [r[0] for r in con.execute("select token_hash from auth_sessions")]
        hashes = [r[0] for r in con.execute("select password_hash from users")]
    assert cookie not in stored  # DB에는 토큰 해시만
    assert all(h.startswith("scrypt$") and "pass-" not in h for h in hashes)

    temp = _client(app_real_login, "user4", "pass-user4")
    assert temp.post("/auth/logout").status_code == 204
    assert temp.get("/auth/me").status_code == 401


def test_lockout_after_repeated_failures(app_real_login, accounts) -> None:
    accounts["owner"].post(
        "/users", json={"username": "locky", "display_name": "L", "role": "user", "password": "pass-locky"}
    )
    c = _client(app_real_login)
    for _ in range(5):
        assert c.post("/auth/login", json={"username": "locky", "password": "nope-nope"}).status_code == 401
    res = c.post("/auth/login", json={"username": "locky", "password": "pass-locky"})
    assert res.status_code == 401 and "잠" in res.json()["detail"]


def test_role_gates(accounts) -> None:
    user, admin, owner = accounts["user1"], accounts["admin1"], accounts["owner"]
    # 일반 사용자: 사전 관리·승격 심사·사용자 관리·설정 불가
    assert user.post("/glossary/aliases", json={"alias": "행안", "target_code": "1741000"}).status_code == 403
    assert user.get("/admin/promotions").status_code == 403
    assert user.get("/admin/standard-terms").status_code == 403
    assert user.get("/users").status_code == 403
    assert user.get("/settings").status_code == 403
    assert user.get("/glossary/resolve", params={"name": "행정안전부"}).status_code == 200  # 검증에 쓰는 조회는 가능
    # 관리자: 사전·승격·사용자는 가능, 설정과 관리자·소유자 계정 관리는 불가
    assert admin.get("/admin/promotions").status_code == 200
    assert admin.get("/users").status_code == 200
    assert admin.get("/settings").status_code == 403
    boss = {"username": "boss2", "display_name": "B", "role": "owner", "password": "pass-boss2"}
    res = admin.post("/users", json=boss)
    assert res.status_code == 403
    owner_id = next(u["id"] for u in owner.get("/users").json() if u["username"] == "test-owner")
    assert admin.patch(f"/users/{owner_id}", json={"is_active": False}).status_code == 403
    # 소유자: 설정 가능, 자기 자신 비활성화·마지막 소유자 강등은 불가
    assert owner.get("/settings").status_code == 200
    assert owner.patch(f"/users/{owner_id}", json={"role": "user"}).status_code == 409


def test_users_see_only_their_own_records(accounts) -> None:
    u1, u2, admin = accounts["user1"], accounts["user2"], accounts["admin1"]
    fid = _upload(u1, name="u1자료.xlsx")
    assert fid in [f["id"] for f in u1.get("/files").json()]
    assert fid not in [f["id"] for f in u2.get("/files").json()]
    assert u2.post(f"/files/{fid}/mapping", params={"use_llm": "false"}).status_code == 404
    assert u2.post("/reviews", json={"file_ids": [fid]}).status_code == 404

    review = u1.post("/reviews", json={"file_ids": [fid]}).json()
    for item in [i for i in review["items"] if i["category"] == "pending"]:
        res = u1.post(
            f"/reviews/{review['id']}/items/{item['id']}/decision",
            json={
                "decision": "approved" if item["payload"].get("field_key") else "rejected",
                "choice": item["payload"].get("field_key"),
            },
        )
        assert res.status_code == 200, res.text
    assert u1.post(f"/reviews/{review['id']}/complete").status_code == 200
    run = u1.post(f"/reviews/{review['id']}/integrate").json()
    assert run["status"] == "done" and run["user_name"] == "USER1"

    # 사용자는 자기 실행 기록만
    assert [r["id"] for r in u1.get("/runs").json()] == [run["id"]]
    assert run["id"] not in [r["id"] for r in u2.get("/runs").json()]
    assert u2.get(f"/runs/{run['id']}").status_code == 404
    assert u2.get(f"/runs/{run['id']}/download").status_code == 404
    assert u2.get(f"/reviews/{review['id']}").status_code == 404
    # 관리자는 전체 조회(읽기 전용)
    assert run["id"] in [r["id"] for r in admin.get("/runs").json()]
    assert admin.get(f"/runs/{run['id']}/download").status_code == 200
    seen = admin.get(f"/reviews/{review['id']}").json()
    assert seen["can_edit"] is False and seen["created_by_name"] == "USER1"


def test_admin_cannot_modify_result_data(accounts) -> None:
    u1, admin, owner = accounts["user1"], accounts["admin1"], accounts["owner"]
    run = u1.get("/runs").json()[0]
    for client in (admin, owner):
        for method in ("put", "patch", "delete"):
            assert getattr(client, method)(f"/runs/{run['id']}").status_code == 405
        assert client.post(f"/runs/{run['id']}/changes", json={}).status_code == 405
        assert client.delete(f"/runs/{run['id']}/download").status_code == 405
    # 남의 검토를 대신 승인·완료·통합하지 않는다
    fid = _upload(u1, name="u1자료2.xlsx")
    review = u1.post("/reviews", json={"file_ids": [fid]}).json()
    pending = [i for i in review["items"] if i["category"] == "pending"]
    assert pending
    res = admin.post(f"/reviews/{review['id']}/items/{pending[0]['id']}/decision", json={"decision": "rejected"})
    assert res.status_code == 403
    assert admin.post(f"/reviews/{review['id']}/complete").status_code == 403
    assert admin.post(f"/reviews/{review['id']}/integrate").status_code == 403
    # 결과 파일은 읽기 전용
    path = DATA_DIR / run["output_path"].removeprefix("data/")
    assert path.is_file()
    with pytest.raises(PermissionError):
        path.open("r+b")


def _confirm_promo_column(c: TestClient) -> None:
    fid = _upload(c, name="승격.xlsx")
    res = c.put(
        f"/files/{fid}/mapping/decisions",
        json={"decisions": [{"sheet": "현황", "column": PROMO_COLUMN, "field_key": "note"}]},
    )
    assert res.status_code == 200, res.text


def test_promotion_after_three_users_and_admin_approval(accounts) -> None:
    admin = accounts["admin1"]
    _confirm_promo_column(accounts["user1"])
    _confirm_promo_column(accounts["user1"])  # 같은 사용자가 여러 번 승인해도 1명
    _confirm_promo_column(accounts["user2"])
    assert not [c for c in admin.get("/admin/promotions").json()["items"] if c["column_name"] == PROMO_COLUMN]
    _confirm_promo_column(accounts["user3"])
    queue = admin.get("/admin/promotions").json()
    assert queue["threshold"] == 3
    candidate = next(c for c in queue["items"] if c["column_name"] == PROMO_COLUMN)
    assert candidate["status"] == "pending" and candidate["approver_count"] == 3
    assert sorted(candidate["approvers"]) == ["USER1", "USER2", "USER3"]
    assert candidate["created_at"].endswith("+09:00")  # 화면에는 한국 시간으로

    # 반영 전: 4번째 사용자에게는 아직 자동 적용되지 않는다
    u4 = accounts["user4"]
    fid = _upload(u4, name="u4.xlsx")
    before = u4.post(f"/files/{fid}/mapping", params={"use_llm": "false"}).json()["sheets"][0]["columns"]
    assert next(c for c in before if c["column"] == PROMO_COLUMN)["method"] != "standard"

    res = admin.post(f"/admin/promotions/{candidate['id']}/approve", json={"note": "공통 비고 열"})
    assert res.status_code == 200 and res.json()["status"] == "approved"
    terms = admin.get("/admin/standard-terms").json()
    assert any(t["term"] == PROMO_COLUMN and t["field_key"] == "note" for t in terms)
    assert admin.post(f"/admin/promotions/{candidate['id']}/approve", json={}).status_code == 409  # 이미 처리됨

    after = u4.post(f"/files/{fid}/mapping", params={"use_llm": "false"}).json()["sheets"][0]["columns"]
    promoted = next(c for c in after if c["column"] == PROMO_COLUMN)
    assert (promoted["method"], promoted["status"], promoted["field_key"]) == ("standard", "auto", "note")


def test_dashboard_counts_and_savings(accounts) -> None:
    u1, u2, owner, admin = accounts["user1"], accounts["user2"], accounts["owner"], accounts["admin1"]
    mine = u1.get("/dashboard").json()
    assert mine["scope"] == "mine"
    assert mine["month_runs"] == len(u1.get("/runs").json()) >= 1
    assert mine["validated_rows"] >= 2 and mine["validated_files"] >= 1
    assert mine["baseline_minutes"] is None and mine["saved_seconds"] == 0 and mine["runs_without_baseline"] >= 1
    assert u2.get("/dashboard").json()["month_runs"] == 0

    # 소유자가 수동 기준 소요 시간을 정하면 절약 시간 = 기준 - 실제 처리 시간
    assert admin.put("/settings", json={"manual_baseline_minutes": 30}).status_code == 403
    assert owner.put("/settings", json={"manual_baseline_minutes": 30}).json()["manual_baseline_minutes"] == 30
    runs = u1.get("/runs").json()
    done = [r for r in runs if r["status"] == "done"]
    expected = sum(max(0, 1800 - r["duration_ms"] / 1000) for r in done)
    mine = u1.get("/dashboard").json()
    assert mine["saved_runs"] == len(done) and mine["runs_without_baseline"] == 0
    assert abs(mine["saved_seconds"] - expected) < 1
    everyone = admin.get("/dashboard").json()
    assert everyone["scope"] == "all" and everyone["month_runs"] >= mine["month_runs"]

    # 통합 전 발견한 오류: 합계 불일치(차단)가 있는 자료 → 다시 검사해도 같은 오류는 한 번만
    rows = [["기관명", "집행액"], ["행정안전부", 100], ["기상청", 200], ["합계", 999]]
    fid = _upload(u2, rows, "합계틀림.xlsx")
    review = u2.post("/reviews", json={"file_ids": [fid]}).json()
    blocks = review["counts"]["block"]
    assert blocks >= 1
    u2.post(f"/reviews/{review['id']}/recheck", json={})
    stats = u2.get("/dashboard").json()
    assert stats["blocks_found"] == blocks
    assert stats["errors_found"] == stats["blocks_found"] + stats["warnings_found"]
    owner.put("/settings", json={"manual_baseline_minutes": None})


def test_glossary_name_learning_needs_manager(accounts) -> None:
    """'이 형식 기억하기'로 기관명을 공용 용어사전에 학습하는 것은 관리자·소유자만.

    일반 사용자는 열 이름만 개인 별칭으로 학습한다.
    """
    u3, owner = accounts["user3"], accounts["owner"]
    with sqlite3.connect(DATA_DIR / "moajwo.db") as con:
        had_entry = con.execute("select count(*) from glossary_entries where code = '1550000'").fetchone()[0]
    csv = "\n".join(["코드,정식명칭,유형", "1550000,국가유산청,중앙행정기관", ""]).encode()
    assert owner.post("/glossary/import", files=[("file", ("g.csv", csv, "text/csv"))]).json()["applied"]
    rows = [["기관명", "집행액"], ["국가유산", 100]]
    fid = _upload(u3, rows, "이름.xlsx")
    review = u3.post("/reviews", json={"file_ids": [fid]}).json()
    u3.patch(f"/reviews/{review['id']}", json={"remember_format": True})
    named = [i for i in review["items"] if i["kind"] == "entity" and i["category"] == "pending"]
    assert named and named[0]["payload"]["candidates"], "용어사전 후보가 있는 처음 보는 이름이 확인 필요로 나와야 함"
    for item in [i for i in review["items"] if i["category"] == "pending"]:
        candidates = item["payload"].get("candidates") or []
        choice = (
            item["payload"].get("field_key")
            if item["kind"] == "mapping"
            else (candidates[0]["code"] if candidates else None)
        )
        u3.post(
            f"/reviews/{review['id']}/items/{item['id']}/decision",
            json={"decision": "approved" if choice else "rejected", "choice": choice},
        )
    done = u3.post(f"/reviews/{review['id']}/complete").json()
    with sqlite3.connect(DATA_DIR / "moajwo.db") as con:
        learned = con.execute("select count(*) from glossary_aliases where alias = '국가유산'").fetchone()[0]
    assert learned == 0
    assert done["learned"]["names"] == 0 and done["learned"]["names_skipped"] == 1
    if not had_entry:  # 같은 테스트 DB를 쓰는 용어사전 테스트에 영향이 없도록
        with sqlite3.connect(DATA_DIR / "moajwo.db") as con:
            con.execute("delete from glossary_entries where code = '1550000'")


def test_dashboard_counts_same_content_once(accounts) -> None:
    """같은 파일을 다시 올려 검사해도(내용 sha256이 같으면) '검증한 행 수'에 한 번만 더한다."""
    u = accounts["user4"]
    rows = [["기관명", "집행액"], ["행정안전부", 1], ["기상청", 2], ["인사혁신처", 3]]
    before = u.get("/dashboard").json()["validated_rows"]
    data = _xlsx(
        rows
    )  # 한 번 만든 바이트를 두 번 올린다 (xlsx는 저장 시각이 들어가 다시 만들면 sha256이 달라질 수 있음)
    first, second = (
        u.post("/files", files=[("files", (name, data, XLSX))]).json()[0]["id"]
        for name in ("같은내용.xlsx", "같은내용_다시.xlsx")
    )
    assert u.post("/validation", json={"file_ids": [first]}).status_code == 200
    mid = u.get("/dashboard").json()["validated_rows"]
    assert u.post("/validation", json={"file_ids": [second]}).status_code == 200
    after = u.get("/dashboard").json()["validated_rows"]
    assert mid - before == 3 and after == mid


def _decision_rows(fid: int) -> list[tuple]:
    with sqlite3.connect(DATA_DIR / "moajwo.db") as con:
        return con.execute(
            "select column_name, field_key from mapping_decisions where source_file_id = ?", (fid,)
        ).fetchall()


def _pii_ack(fid: int) -> object:
    with sqlite3.connect(DATA_DIR / "moajwo.db") as con:
        return con.execute("select pii_acknowledged_at from source_files where id = ?", (fid,)).fetchone()[0]


def test_review_cannot_change_other_users_file(accounts) -> None:
    """교차 검증 ③: 관리자·소유자가 남의 파일로 자기 검토를 만들어 승인해도 원본의 매핑·개인정보 확인은 그대로."""
    u3, admin, owner = accounts["user3"], accounts["admin1"], accounts["owner"]
    column = "교차검증 전용 메모"  # 사전·승격 이름에 없는 열 → 확인 필요 항목
    rows = [
        ["기관명", column, "담당 연락처"],
        ["행정안전부", "정상", "010-1234-5678"],
        ["기상청", "검토", "010-2345-6789"],
    ]
    fid = _upload(u3, rows, "u3개인정보.xlsx")
    # 직접 변경은 막혀 있다 (기존 동작)
    direct = admin.put(
        f"/files/{fid}/mapping/decisions",
        json={"decisions": [{"sheet": "현황", "column": column, "field_key": "note"}]},
    )
    assert direct.status_code in (403, 404)
    for client in (admin, owner):
        review = client.post("/reviews", json={"file_ids": [fid]})
        assert review.status_code == 201, review.text  # 조회용 검토는 만들 수 있다
        review = review.json()
        pending = {i["kind"]: i for i in review["items"] if i["category"] == "pending"}
        assert {"mapping", "pii"} <= set(pending), pending
        for kind in ("mapping", "pii"):
            res = client.post(
                f"/reviews/{review['id']}/items/{pending[kind]['id']}/decision",
                json={"decision": "approved", "choice": "note"} if kind == "mapping" else {"decision": "approved"},
            )
            assert res.status_code == 403, (kind, res.text)
        assert _decision_rows(fid) == [] and _pii_ack(fid) is None  # 부분 변경 없음
    # 파일 주인은 자기 검토에서 그대로 처리할 수 있다
    review = u3.post("/reviews", json={"file_ids": [fid]}).json()
    item = next(i for i in review["items"] if i["category"] == "pending" and i["kind"] == "mapping")
    res = u3.post(
        f"/reviews/{review['id']}/items/{item['id']}/decision", json={"decision": "approved", "choice": "note"}
    )
    assert res.status_code == 200, res.text
    assert (column, "note") in _decision_rows(fid)


def test_recheck_and_automation_cannot_write_other_users_file(accounts) -> None:
    """교차 검증 ③: 재검사의 파일 교체와 자동화 실행으로도 남의 파일 매핑을 바꾸지 못한다."""
    from app.db import SessionLocal
    from app.models import Automation, User
    from app.review import column_rule_key

    u4, admin = accounts["user4"], accounts["admin1"]
    victim = _upload(u4, name="u4대상.xlsx")
    mine = _upload(admin, name="admin본인.xlsx")
    own = admin.put(  # 관리자 자기 파일의 확정 → 교체 시 남의 파일로 복사되면 안 된다
        f"/files/{mine}/mapping/decisions",
        json={"decisions": [{"sheet": "현황", "column": "집행액", "field_key": "budget"}]},
    )
    assert own.status_code == 200, own.text
    review = admin.post("/reviews", json={"file_ids": [mine]}).json()
    res = admin.post(f"/reviews/{review['id']}/recheck", json={"replace": {str(mine): victim}})
    assert res.status_code == 403, res.text
    assert _decision_rows(victim) == []

    with SessionLocal() as db:
        admin_id = db.query(User.id).filter(User.username == "admin1").scalar()
        automation = Automation(
            name="우회 확인",
            owner_id=admin_id,
            column_rules=json.dumps([{"key": column_rule_key(PROMO_COLUMN), "field_key": "note"}]),
            entity_rules="{}",
            validation="{}",
            output_format="{}",
        )
        db.add(automation)
        db.commit()
        automation_id = automation.id
    res = admin.post(f"/automations/{automation_id}/run", json={"file_ids": [victim]})
    assert res.status_code in (201, 403, 422), res.text
    assert _decision_rows(victim) == []
