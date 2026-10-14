"""회원가입 승인·계정 상태·최고 관리자 관리 화면 API (0016).

- 실제 쿠키 로그인으로 검사한다 (test_auth의 app_real_login 재사용). 사용자마다 별도 TestClient.
- 최초 설정 전 가입 차단·동시 가입·동시 승인은 빈 임시 DB에서 함수를 직접 불러 검사한다.
"""

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.config import DATA_DIR
from tests.test_auth import _client, _upload, app_real_login  # noqa: F401

PENDING_MSG = "최고 관리자의 승인 후 로그인할 수 있습니다"
DONE_MSG = "가입 신청이 완료되었습니다. 최고 관리자의 승인 후 로그인할 수 있습니다."
WORK_PATHS = ("/files", "/runs", "/dashboard", "/automations", "/auth/me")


def _signup(c: TestClient, username: str, password: str = "signup-pass-1", **extra: Any):
    body = {"username": username, "display_name": username.upper(), "password": password,
            "password_confirm": password, **extra}  # fmt: skip
    return c.post("/auth/signup", json=body)


def _uid(owner: TestClient, username: str) -> int:
    return next(u["id"] for u in owner.get("/users").json() if u["username"] == username)


def _db_rows(sql: str, *args: Any) -> list[tuple]:
    with sqlite3.connect(DATA_DIR / "moajwo.db") as con:
        return con.execute(sql, args).fetchall()


@pytest.fixture(scope="module")
def owner(app_real_login) -> TestClient:  # noqa: F811
    """실제 설치처럼 최초 설정 완료 표시를 둔다 (conftest는 소유자를 DB에 직접 넣어 표시가 없다)."""
    from app.auth_api import SETUP_KEY
    from app.db import SessionLocal
    from app.models import AppSetting

    with SessionLocal() as db:
        if db.get(AppSetting, SETUP_KEY) is None:
            db.add(AppSetting(key=SETUP_KEY, value='{"source": "test"}'))
            db.commit()
    return _client(app_real_login, "test-owner")


# ---------------------------------------------------------------- 가입 신청


def test_signup_creates_pending_user_without_session(app_real_login, owner) -> None:  # noqa: F811
    anon = _client(app_real_login)
    res = _signup(anon, "signer1")
    assert res.status_code == 201, res.text
    assert res.json() == {"username": "signer1", "status": "pending", "status_label": "승인 대기", "message": DONE_MSG}
    assert "set-cookie" not in res.headers and not anon.cookies.get("moajwo_session")  # 업무 세션 없음
    assert anon.get("/auth/me").status_code == 401
    row = _db_rows("select role, status, is_active from users where username = 'signer1'")
    assert row == [("user", "pending", 0)]
    assert not _db_rows("select 1 from auth_sessions s join users u on u.id = s.user_id where u.username = 'signer1'")
    listed = next(u for u in owner.get("/users", params={"status": "pending"}).json() if u["username"] == "signer1")
    assert (listed["status_label"], listed["role_label"], listed["last_login_at"]) == ("승인 대기", "일반 사용자", None)


def test_signup_cannot_choose_role_status_or_activation(app_real_login, owner) -> None:  # noqa: F811
    anon = _client(app_real_login)
    for extra in ({"role": "owner"}, {"role": "admin"}, {"status": "active"}, {"is_active": True}, {"approved": True}):
        res = _signup(anon, "sneaky", **extra)
        assert res.status_code == 422, (extra, res.text)
    assert not _db_rows("select 1 from users where username = 'sneaky'")
    # 비밀번호 확인 불일치·규칙 위반
    body = {
        "username": "mismatch",
        "display_name": "M",
        "password": "signup-pass-1",
        "password_confirm": "other-pass-1",
    }
    assert anon.post("/auth/signup", json=body).status_code == 422
    assert _signup(anon, "short", password="short").status_code == 422
    assert _signup(anon, "Bad Name!").status_code == 422
    assert not _db_rows("select 1 from users where username in ('mismatch', 'short')")


def test_duplicate_username_rejected(app_real_login, owner) -> None:  # noqa: F811
    anon = _client(app_real_login)
    assert _signup(anon, "dupe1").status_code == 201
    again = _signup(anon, "DUPE1")  # 아이디는 소문자로 맞춰 비교
    assert again.status_code == 409 and "이미 사용 중" in again.json()["detail"]
    assert _signup(anon, "test-owner").status_code == 409  # 기존 계정(최고 관리자)과도 겹치지 않는다
    assert len(_db_rows("select 1 from users where username = 'dupe1'")) == 1


# ---------------------------------------------------------------- 로그인 안내·업무 API 차단


def test_status_message_only_with_correct_password(app_real_login, owner) -> None:  # noqa: F811
    anon = _client(app_real_login)
    assert _signup(anon, "waiting1").status_code == 201
    wrong = anon.post("/auth/login", json={"username": "waiting1", "password": "wrong-pass-9"})
    unknown = anon.post("/auth/login", json={"username": "nobody-here", "password": "wrong-pass-9"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]  # 상태도, 아이디 존재 여부도 알려 주지 않는다
    right = anon.post("/auth/login", json={"username": "waiting1", "password": "signup-pass-1"})
    assert right.status_code == 403 and PENDING_MSG in right.json()["detail"]
    assert not anon.cookies.get("moajwo_session") and anon.get("/files").status_code == 401


def test_all_business_apis_require_active_status(app_real_login, owner) -> None:  # noqa: F811
    """메뉴 숨김이 아니라 서버가 막는다: 승인 대기·반려·중지 계정에 세션이 남아 있어도(직접 넣어 흉내) 401."""
    from app.auth import _token_hash
    from app.db import SessionLocal
    from app.models import AuthSession, User

    anon = _client(app_real_login)
    for name in ("blocked-p", "blocked-r", "blocked-s"):
        assert _signup(anon, name).status_code == 201
    assert owner.post(f"/users/{_uid(owner, 'blocked-r')}/reject", json={"reason": "확인 불가"}).status_code == 200
    assert owner.post(f"/users/{_uid(owner, 'blocked-s')}/approve", json={}).status_code == 200
    assert owner.post(f"/users/{_uid(owner, 'blocked-s')}/suspend", json={}).status_code == 200
    now = datetime.now(UTC)
    for name, status in (("blocked-p", "pending"), ("blocked-r", "rejected"), ("blocked-s", "suspended")):
        token = f"forged-{name}-token-0123456789"
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.username == name))
            assert user.status == status and user.is_active is False
            db.add(AuthSession(token_hash=_token_hash(token), user_id=user.id, created_at=now,
                               expires_at=now + timedelta(hours=1)))  # fmt: skip
            db.commit()
        c = TestClient(app_real_login, cookies={"moajwo_session": token})
        for path in WORK_PATHS:
            assert c.get(path).status_code == 401, (name, path)
        assert c.post("/files", files=[("files", ("x.xlsx", b"x", "application/octet-stream"))]).status_code == 401
        assert c.get("/auth/status").json()["user"] is None
    # is_active만 켜져 있고 상태가 이용 중이 아니어도 막는다 (이중 확인)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "blocked-p"))
        user.is_active = True
        db.commit()
    c = TestClient(app_real_login, cookies={"moajwo_session": "forged-blocked-p-token-0123456789"})
    assert c.get("/files").status_code == 401
    with SessionLocal() as db:
        db.scalar(select(User).where(User.username == "blocked-p")).is_active = False
        db.commit()


# ---------------------------------------------------------------- 승인 → 이용 → 중지·재개·비밀번호 초기화


def test_approve_then_use_then_suspend_resume_and_reset(app_real_login, owner) -> None:  # noqa: F811
    anon = _client(app_real_login)
    assert _signup(anon, "worker1").status_code == 201
    uid = _uid(owner, "worker1")
    approved = owner.post(f"/users/{uid}/approve", json={"reason": "소속 확인"})
    assert approved.status_code == 200 and approved.json()["status"] == "active"
    worker = _client(app_real_login, "worker1", "signup-pass-1")
    fid = _upload(worker, name="가입자료.xlsx")
    assert fid in [f["id"] for f in worker.get("/files").json()]
    assert owner.get("/admin/overview").status_code == 200
    assert worker.get("/admin/overview").status_code == 403  # 일반 사용자는 관리 API 불가
    listed = next(u for u in owner.get("/users", params={"q": "worker1"}).json() if u["username"] == "worker1")
    assert listed["last_login_at"] is not None and listed["last_activity_at"] is not None  # 로그인과 업무 활동을 따로

    # 이용 중지: 남은 쿠키로 업무 API 불가, 로그인하면 중지 안내
    res = owner.post(f"/users/{uid}/suspend", json={"reason": "퇴사 예정"})
    assert res.status_code == 200 and (res.json()["status"], res.json()["status_reason"]) == ("suspended", "퇴사 예정")
    for path in WORK_PATHS:
        assert worker.get(path).status_code == 401, path
    login = _client(app_real_login).post("/auth/login", json={"username": "worker1", "password": "signup-pass-1"})
    assert login.status_code == 403 and "이용이 중지된" in login.json()["detail"]
    # 재개 → 다시 로그인해서 이용, 기존 파일 그대로
    assert owner.post(f"/users/{uid}/resume", json={}).json()["status"] == "active"
    worker = _client(app_real_login, "worker1", "signup-pass-1")
    assert fid in [f["id"] for f in worker.get("/files").json()]

    # 비밀번호 초기화: 기존 세션 종료, 옛 비밀번호 불가, 새 비밀번호 가능
    assert owner.post(f"/users/{uid}/password", json={"password": "reset-pass-77"}).status_code == 204
    assert worker.get("/files").status_code == 401
    old = _client(app_real_login).post("/auth/login", json={"username": "worker1", "password": "signup-pass-1"})
    assert old.status_code == 401
    assert _client(app_real_login, "worker1", "reset-pass-77").get("/files").status_code == 200

    events = owner.get("/admin/audit", params={"target_id": uid}).json()
    assert [(e["action"], e["before_status"], e["after_status"], e["result"]) for e in reversed(events)] == [
        ("signup", None, "pending", "ok"),
        ("approve", "pending", "active", "ok"),
        ("suspend", "active", "suspended", "ok"),
        ("resume", "suspended", "active", "ok"),
        ("password_reset", "active", "active", "ok"),
    ]
    assert events[2]["reason"] == "퇴사 예정" and events[3]["reason"] == "소속 확인"
    assert all(e["actor_name"] == "test-owner" for e in events[:-1]) and events[-1]["actor_name"] == "worker1"
    raw = owner.get("/admin/audit").text + repr(_db_rows("select * from account_events"))
    for secret in ("signup-pass-1", "reset-pass-77", "scrypt$", "moajwo_session"):
        assert secret not in raw  # 비밀번호·해시·쿠키를 이력에 남기지 않는다


def test_transitions_do_not_overwrite_and_duplicates_are_logged(app_real_login, owner) -> None:  # noqa: F811
    anon = _client(app_real_login)
    for name in ("trans-a", "trans-b"):
        assert _signup(anon, name).status_code == 201
    a, b = _uid(owner, "trans-a"), _uid(owner, "trans-b")
    assert owner.post(f"/users/{a}/approve", json={}).status_code == 200
    dup = owner.post(f"/users/{a}/approve", json={})  # 중복 클릭
    assert dup.status_code == 409 and "이용 중" in dup.json()["detail"]
    assert owner.post(f"/users/{a}/reject", json={}).status_code == 409  # 승인된 계정을 반려로 덮어쓰지 않음
    assert owner.post(f"/users/{a}/resume", json={}).status_code == 409
    assert owner.post(f"/users/{b}/reject", json={"reason": "중복 신청"}).json()["status"] == "rejected"
    for action in ("approve", "resume", "suspend", "reject"):  # 반려는 다른 상태로 덮어쓰지 않는다
        assert owner.post(f"/users/{b}/{action}", json={}).status_code == 409, action
    assert _db_rows("select status, status_reason from users where id = ?", b) == [("rejected", "중복 신청")]
    rejected = _client(app_real_login).post("/auth/login", json={"username": "trans-b", "password": "signup-pass-1"})
    assert rejected.status_code == 403 and "반려" in rejected.json()["detail"]
    events = owner.get("/admin/audit", params={"target_id": a}).json()
    assert [(e["action"], e["result"]) for e in reversed(events)] == [
        ("signup", "ok"), ("approve", "ok"), ("approve", "denied"), ("reject", "denied"), ("resume", "denied")
    ]  # fmt: skip
    assert all(e["before_status"] == e["after_status"] == "active" for e in events[:3])  # 반영 안 됨 → 변화 없음
    assert owner.post("/users/999999/approve", json={}).status_code == 404


def test_no_self_approval_or_owner_escalation(app_real_login, owner) -> None:  # noqa: F811
    anon = _client(app_real_login)
    assert _signup(anon, "climber").status_code == 201
    uid = _uid(owner, "climber")
    assert anon.post(f"/users/{uid}/approve", json={}).status_code == 401  # 승인 대기 = 세션 없음
    assert owner.post(f"/users/{uid}/approve", json={}).status_code == 200
    me = _client(app_real_login, "climber", "signup-pass-1")
    other = _signup(_client(app_real_login), "climber-friend")
    assert other.status_code == 201
    friend = _uid(owner, "climber-friend")
    for path in (f"/users/{uid}/approve", f"/users/{friend}/approve", f"/users/{uid}/resume"):
        assert me.post(path, json={}).status_code == 403, path
    boss = {"username": "climber2", "display_name": "C", "role": "owner", "status": "active", "password": "pass-c2-xx"}
    assert me.post("/users", json=boss).status_code == 403
    assert me.patch(f"/users/{uid}", json={"display_name": "X"}).status_code == 403
    assert (
        me.post("/auth/setup", json={"username": "c3", "display_name": "c3", "password": "password-123"}).status_code
        == 409
    )
    assert me.get("/auth/me").json()["role"] == "user"
    owner_id = _uid(owner, "test-owner")
    for action in ("suspend", "reject", "approve", "resume"):  # 최고 관리자 계정 자체는 바꿀 수 없다
        assert owner.post(f"/users/{owner_id}/{action}", json={}).status_code == 409, action
    assert owner.post(f"/users/{owner_id}/password", json={"password": "whatever-123"}).status_code == 409
    assert _db_rows("select role, status, is_active from users where id = ?", owner_id) == [("owner", "active", 1)]
    assert _db_rows("select status from users where id = ?", friend) == [("pending",)]


def test_owner_created_accounts_are_users_with_explicit_approval(app_real_login, owner) -> None:  # noqa: F811
    base = {"display_name": "직접 추가", "password": "direct-pass-1"}
    assert owner.post("/users", json={**base, "username": "direct0"}).status_code == 422  # 승인 여부를 정해야 함
    assert owner.post("/users", json={**base, "username": "direct1", "status": "active"}).json()["status"] == "active"
    pending = owner.post("/users", json={**base, "username": "direct2", "status": "pending"}).json()
    assert (pending["status"], pending["role"]) == ("pending", "user")
    assert owner.post("/users", json={**base, "username": "direct3", "status": "suspended"}).status_code == 422
    assert _client(app_real_login, "direct1", "direct-pass-1").get("/files").status_code == 200
    blocked = _client(app_real_login).post("/auth/login", json={"username": "direct2", "password": "direct-pass-1"})
    assert blocked.status_code == 403
    events = owner.get("/admin/audit", params={"target_id": pending["id"]}).json()
    assert [(e["action"], e["after_status"], e["actor_name"]) for e in events] == [("create", "pending", "test-owner")]


def test_user_list_search_and_filter(owner) -> None:
    assert {u["username"] for u in owner.get("/users", params={"q": "TRANS-"}).json()} == {"trans-a", "trans-b"}
    assert {u["username"] for u in owner.get("/users", params={"q": "직접 추가"}).json()} >= {"direct1", "direct2"}
    by_status = owner.get("/users", params={"status": "rejected"}).json()
    assert by_status and all(u["status"] == "rejected" for u in by_status)
    everyone = owner.get("/users").json()
    firsts = [u["status"] for u in everyone]
    assert firsts[: firsts.count("pending")] == ["pending"] * firsts.count("pending")  # 승인 대기가 먼저


# ---------------------------------------------------------------- 관리 현황 통계


def test_overview_matches_actual_data(app_real_login, owner) -> None:  # noqa: F811
    from app.timefmt import KST, utc_to_kst

    today = datetime.now(KST).date()
    stats = owner.get("/admin/overview", params={"start": today.isoformat(), "end": today.isoformat()}).json()
    assert stats["timezone"].startswith("한국 시간")
    by_status = dict(_db_rows("select status, count(*) from users group by status"))
    assert {s["status"]: s["count"] for s in stats["users"]} == {
        s: by_status.get(s, 0) for s in ("pending", "active", "rejected", "suspended")
    }
    inactive = {r[0] for r in _db_rows("select id from users where status != 'active'")}
    uploads = [
        owner_id
        for at, owner_id in _db_rows("select uploaded_at, uploaded_by from source_files")
        if utc_to_kst(datetime.fromisoformat(at)).date() == today
    ]
    assert stats["uploads"]["total"] == len(uploads) > 0
    assert stats["uploads"]["inactive_accounts"] == sum(1 for o in uploads if o in inactive)
    runs = owner.get("/runs").json()
    todays = [r for r in runs if datetime.fromisoformat(r["started_at"]).astimezone(KST).date() == today]
    counts = stats["runs"]
    assert counts["total"] == len(todays) and counts["done"] == sum(r["status"] == "done" for r in todays)
    assert counts["review"] == sum(r["status"] == "review" for r in todays)
    assert counts["failed"] == sum(r["status"] == "failed" for r in todays)
    # 이용 중지 계정의 기록도 센다 (비활성이라는 이유로 빼지 않음)
    suspended_upload = stats["uploads"]["inactive_accounts"]
    assert suspended_upload == sum(1 for o in uploads if o in inactive)
    past = owner.get("/admin/overview", params={"start": "2000-01-01", "end": "2000-01-31"}).json()
    assert past["uploads"]["total"] == 0 and past["runs"]["total"] == 0
    assert owner.get("/admin/overview", params={"start": "2026-02-01", "end": "2026-01-01"}).status_code == 422
    assert owner.get("/admin/overview", params={"start": "2020-01-01", "end": "2026-01-01"}).status_code == 422


# ---------------------------------------------------------------- 빈 DB: 최초 설정 전 가입 차단·동시 요청


def _fresh(tmp_path: Path):
    from app.db import Base

    engine = create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    return sessionmaker(engine)


def _signup_in(name: str):
    from app import auth_api

    return auth_api.SignupIn(
        username=name, display_name=name, password="signup-pass-1", password_confirm="signup-pass-1"
    )


def test_signup_blocked_until_owner_setup_and_setup_stays_closed(tmp_path: Path) -> None:
    from app import auth_api
    from app.models import AccountEvent, User

    Session = _fresh(tmp_path)
    with Session() as db:
        with pytest.raises(HTTPException) as info:
            auth_api.signup(_signup_in("early"), db)
        assert info.value.status_code == 409 and "최초 설정" in info.value.detail
        assert db.scalar(select(func.count(User.id))) == 0
        assert auth_api.auth_status(_Req(), db).setup_required is True  # 최초 설정은 그대로 열려 있다
        auth_api.setup(auth_api.SetupIn(username="first", display_name="F", password="password-123"), Response(), db)
        first = db.scalar(select(User).where(User.username == "first"))
        assert (first.role, first.status, first.is_active) == ("owner", "active", True)  # 승인 대기 없이 바로
        assert auth_api.signup(_signup_in("later"), db).status == "pending"
        # 소유자가 이용 중지되거나 계정 수가 바뀌어도 최초 설정은 다시 열리지 않는다
        first.status, first.is_active = "suspended", False
        db.query(User).filter(User.username == "later").delete()
        db.commit()
        assert auth_api.auth_status(_Req(), db).setup_required is False
        with pytest.raises(HTTPException) as info:
            auth_api.setup(
                auth_api.SetupIn(username="again", display_name="A", password="password-123"), Response(), db
            )
        assert info.value.status_code == 409
        assert [e.action for e in db.scalars(select(AccountEvent).order_by(AccountEvent.id))] == ["setup", "signup"]


class _Req:
    cookies: dict[str, str] = {}


def test_concurrent_signups_same_username(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import auth_api
    from app.models import User

    Session = _fresh(tmp_path)
    with Session() as db:
        auth_api.setup(auth_api.SetupIn(username="boss", display_name="B", password="password-123"), Response(), db)
    barrier = threading.Barrier(3)
    real_hash = auth_api.hash_password

    def slow_hash(password: str) -> str:  # 세 요청 모두 '아이디 없음' 검사를 통과한 뒤 저장하게 만든다
        barrier.wait(timeout=10)
        return real_hash(password)

    monkeypatch.setattr(auth_api, "hash_password", slow_hash)
    outcomes: list[Any] = []

    def attempt() -> None:
        with Session() as db:
            try:
                outcomes.append(auth_api.signup(_signup_in("same-name"), db).status)
            except HTTPException as exc:
                outcomes.append(exc.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(map(str, outcomes)) == ["409", "409", "pending"]
    with Session() as db:
        assert db.scalar(select(func.count(User.id)).where(User.username == "same-name")) == 1


def test_concurrent_approvals_apply_once(tmp_path: Path) -> None:
    from app import auth_api
    from app.auth import CurrentUser
    from app.models import AccountEvent, User

    Session = _fresh(tmp_path)
    with Session() as db:
        boss = auth_api.setup(
            auth_api.SetupIn(username="boss", display_name="B", password="password-123"), Response(), db
        )
        auth_api.signup(_signup_in("applicant"), db)
        target = db.scalar(select(User.id).where(User.username == "applicant"))
    actor = CurrentUser(boss.id, "boss", "B", "owner")
    barrier = threading.Barrier(4)
    outcomes: list[Any] = []

    def attempt() -> None:
        with Session() as db:
            barrier.wait(timeout=10)
            try:
                outcomes.append(auth_api._change_status(db, actor, target, "approve", None).status)
            except HTTPException as exc:
                outcomes.append(exc.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(map(str, outcomes)) == ["409", "409", "409", "active"]
    with Session() as db:
        events = [
            (e.action, e.result) for e in db.scalars(select(AccountEvent).where(AccountEvent.target_id == target))
        ]
        assert sorted(events) == sorted([("signup", "ok"), ("approve", "ok")] + [("approve", "denied")] * 3)
        assert db.get(User, target).status == "active"
