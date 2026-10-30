"""계정 발급·임시 비밀번호·계정 상태·최고 관리자 관리 화면 API (0022).

- 실제 쿠키 로그인으로 검사한다 (test_auth의 app_real_login 재사용). 사용자마다 별도 TestClient.
- 공개 가입·최초 설정은 종료됐다(410). 공개 가입을 받던 때 남은 '승인 대기' 계정은 DB에 직접 넣어 흉내 낸다.
- 동시 초기화·동시 최초 변경·동시 승인은 빈 임시 DB에서 함수를 직접 불러 검사한다.
"""

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from tests.conftest import db_rows
from tests.test_auth import _client, _upload, app_real_login  # noqa: F401

PENDING_MSG = "최고 관리자의 승인 후 로그인할 수 있습니다"
RESTRICTED = "임시 비밀번호로 로그인한 상태"
WORK_PATHS = ("/files", "/runs", "/dashboard", "/automations")
NEW_PASSWORD = "my-own-pass-1"
LEGACY_PASSWORD = "signup-pass-1"


def _provision(owner: TestClient, username: str, display_name: str | None = None) -> tuple[dict, str]:
    """계정 발급 → (사용자, 임시 비밀번호). 임시 비밀번호가 든 응답은 저장되지 않는다(no-store)."""
    res = owner.post("/users", json={"username": username, "display_name": display_name or username.upper()})
    assert res.status_code == 201, res.text
    assert res.headers.get("cache-control") == "no-store"
    body = res.json()
    return body["user"], body["temp_password"]


def _first_change(app: Any, username: str, temp: str, new: str = NEW_PASSWORD) -> TestClient:
    """임시 비밀번호로 로그인(제한) → 본인 비밀번호로 변경 → 새 비밀번호로 다시 로그인한 클라이언트."""
    c = _client(app)
    res = c.post("/auth/login", json={"username": username, "password": temp})
    assert res.status_code == 200 and res.json()["must_change_password"] is True, res.text
    body = {"current_password": temp, "new_password": new, "new_password_confirm": new}
    res = c.post("/auth/password", json=body)
    assert res.status_code == 204, res.text
    return _client(app, username, new)


def _legacy_pending(username: str, password: str = LEGACY_PASSWORD) -> int:
    """공개 가입을 받던 때 남은 승인 대기 계정 — 지금은 API로 만들 수 없어 DB에 직접 넣는다 (이력도 당시처럼 signup)."""
    from app.auth import hash_password, record_event, set_status
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        user = User(username=username, display_name=username.upper(), password_hash=hash_password(password),
                    role="user", failed_logins=0)  # fmt: skip
        set_status(user, "pending")
        db.add(user)
        db.flush()
        record_event(db, None, user, "signup", None, "pending", after_role="user", actor_self=True)
        db.commit()
        return user.id


def _uid(owner: TestClient, username: str) -> int:
    return next(u["id"] for u in owner.get("/users").json() if u["username"] == username)


def _listed(owner: TestClient, username: str) -> dict:
    return next(u for u in owner.get("/users").json() if u["username"] == username)


def _db_rows(sql: str, *args: Any) -> list[tuple]:
    return db_rows(sql, *args)  # SQLite·PostgreSQL 모두


@pytest.fixture(scope="module")
def owner(app_real_login) -> TestClient:  # noqa: F811
    """실제 설치처럼 초기화 완료 표시를 둔다 (conftest는 소유자를 DB에 직접 넣어 표시가 없다)."""
    from app.auth_api import SETUP_KEY
    from app.db import SessionLocal
    from app.models import AppSetting

    with SessionLocal() as db:
        if db.get(AppSetting, SETUP_KEY) is None:
            db.add(AppSetting(key=SETUP_KEY, value='{"source": "test"}'))
            db.commit()
    return _client(app_real_login, "test-owner")


# ---------------------------------------------------------------- 공개 가입·최초 설정 종료


def test_public_setup_and_signup_are_closed(app_real_login, owner) -> None:  # noqa: F811
    anon = _client(app_real_login)
    body = {"username": "evil", "display_name": "x", "password": "password-123", "password_confirm": "password-123"}
    for path in ("/auth/setup", "/auth/signup"):
        res = anon.post(path, json=body)
        assert res.status_code == 410 and "종료" in res.json()["detail"], (path, res.text)
        assert "password-123" not in res.text and "evil" not in res.text  # 본문을 읽지도 되돌리지도 않는다
        assert owner.post(path, json=body).status_code == 410  # 로그인돼 있어도 같다
    assert not _db_rows("select 1 from users where username = 'evil'")
    assert anon.get("/auth/status").json() == {"setup_required": False, "user": None}


# ---------------------------------------------------------------- 발급 → 제한된 로그인 → 본인 비밀번호


def test_provision_then_restricted_login_then_first_change(app_real_login, owner) -> None:  # noqa: F811
    from app.auth import TEMP_PASSWORD_ALPHABET

    user, temp = _provision(owner, "signer1", "발급 사용자")
    assert (user["role"], user["status"], user["must_change_password"], user["temp_password_expired"]) == (
        "user", "active", True, False,
    )  # fmt: skip
    assert len(temp) == 14 and all(ch in TEMP_PASSWORD_ALPHABET for ch in temp)
    hours = (datetime.fromisoformat(user["temp_password_expires_at"]) - datetime.now(UTC)).total_seconds() / 3600
    assert 23 < hours <= 24
    assert _db_rows("select role, status, is_active, must_change_password from users where username = 'signer1'") == [
        ("user", "active", 1, 1)
    ]
    (stored,) = _db_rows("select password_hash from users where username = 'signer1'")[0]
    assert stored.startswith("scrypt$") and temp not in stored
    events = owner.get("/admin/audit", params={"target_id": user["id"]}).json()
    assert [(e["action"], e["before_status"], e["after_status"], e["actor_name"]) for e in events] == [
        ("provision", None, "active", "test-owner")
    ]
    assert temp not in owner.get("/admin/audit").text and temp not in repr(_db_rows("select * from account_events"))
    listed = _listed(owner, "signer1")
    assert listed["must_change_password"] is True and listed["temp_password_expired"] is False

    # 임시 비밀번호 로그인 = 제한된 상태: 본인 조회·비밀번호 변경·로그아웃만, 업무 API는 서버가 403
    restricted = _client(app_real_login)
    res = restricted.post("/auth/login", json={"username": "signer1", "password": temp})
    assert res.status_code == 200 and res.json()["must_change_password"] is True
    assert restricted.cookies.get("moajwo_session")
    assert restricted.get("/auth/me").json()["must_change_password"] is True
    assert restricted.get("/auth/status").json()["user"]["username"] == "signer1"
    for path in WORK_PATHS + ("/users", "/admin/overview"):
        res = restricted.get(path)
        assert res.status_code == 403 and RESTRICTED in res.json()["detail"], (path, res.text)
    assert restricted.post("/files", files=[("files", ("x.csv", b"a,b\n1,2\n", "text/csv"))]).status_code == 403
    assert restricted.post("/users", json={"username": "x1", "display_name": "x"}).status_code == 403
    assert not _db_rows("select 1 from source_files where uploaded_by = ?", user["id"])

    # 변경 규칙: 확인 불일치·임시 비밀번호와 같음·규칙 위반·현재 비밀번호 틀림
    change = "/auth/password"
    bad = restricted.post(
        change, json={"current_password": temp, "new_password": NEW_PASSWORD, "new_password_confirm": "other-pass-1"}
    )
    assert bad.status_code == 422 and "확인" in bad.json()["detail"]
    same = restricted.post(change, json={"current_password": temp, "new_password": temp, "new_password_confirm": temp})
    assert same.status_code == 422 and "달라야" in same.json()["detail"]
    assert restricted.post(change, json={"current_password": temp, "new_password": "short"}).status_code == 422
    assert (
        restricted.post(change, json={"current_password": "wrong-pass-1", "new_password": NEW_PASSWORD}).status_code
        == 409
    )
    assert _db_rows("select must_change_password from users where username = 'signer1'") == [(1,)]

    # 변경 성공: 임시 자격·제한 세션 종료 → 새 비밀번호로 다시 로그인
    ok = restricted.post(
        change, json={"current_password": temp, "new_password": NEW_PASSWORD, "new_password_confirm": NEW_PASSWORD}
    )
    assert (
        ok.status_code == 204
        and "moajwo_session=;" in ok.headers.get("set-cookie", "")
        or "Max-Age=0" in ok.headers.get("set-cookie", "")
    )
    assert restricted.get("/auth/me").status_code == 401  # 제한 세션은 끝났다
    assert (
        _client(app_real_login).post("/auth/login", json={"username": "signer1", "password": temp}).status_code == 401
    )
    full = _client(app_real_login, "signer1", NEW_PASSWORD)
    me = full.get("/auth/me").json()
    assert me["must_change_password"] is False and me["temp_password_expires_at"] is None
    assert full.get("/files").status_code == 200
    assert _db_rows("select must_change_password, temp_password_expires_at from users where username = 'signer1'") == [
        (0, None)
    ]
    listed = _listed(owner, "signer1")
    assert listed["must_change_password"] is False and listed["last_login_at"] is not None
    events = owner.get("/admin/audit", params={"target_id": user["id"]}).json()
    assert [(e["action"], e["actor_name"]) for e in reversed(events)] == [
        ("provision", "test-owner"), ("first_password_change", "signer1"),
    ]  # fmt: skip
    for secret in (temp, NEW_PASSWORD, "scrypt$", "moajwo_session"):
        assert secret not in owner.get("/admin/audit").text


def test_provision_rejects_reserved_duplicate_and_extra_fields(owner) -> None:
    for name in ("admin", "ADMIN", " Admin "):
        res = owner.post("/users", json={"username": name, "display_name": "A"})
        assert res.status_code == 422 and "관리자 전용" in res.json()["detail"], (name, res.text)
    assert not _db_rows("select 1 from users where username = 'admin'")
    _provision(owner, "dupe1")
    again = owner.post("/users", json={"username": "DUPE1", "display_name": "D"})  # 아이디는 소문자로 맞춰 비교
    assert again.status_code == 409 and "이미 있는" in again.json()["detail"]
    assert owner.post("/users", json={"username": "test-owner", "display_name": "T"}).status_code == 409
    assert len(_db_rows("select 1 from users where username = 'dupe1'")) == 1
    base = {"username": "sneaky", "display_name": "S"}
    for extra in ({"password": "chosen-pass-1"}, {"status": "pending"}, {"role": "owner"}, {"is_active": True}):
        res = owner.post("/users", json={**base, **extra})
        assert res.status_code == 422, (extra, res.text)
        assert "chosen-pass-1" not in res.text
    assert not _db_rows("select 1 from users where username = 'sneaky'")
    assert owner.post("/users", json={"username": "Bad Name!", "display_name": "B"}).status_code == 422
    assert owner.post("/users", json={"username": "blank1", "display_name": "  "}).status_code == 422


def test_provisioned_accounts_are_users_without_approval_step(app_real_login, owner) -> None:  # noqa: F811
    direct, temp = _provision(owner, "direct1", "직접 추가")
    assert (direct["status"], direct["role"]) == ("active", "user")
    _first_change(app_real_login, "direct1", temp)
    _provision(owner, "direct2", "직접 추가")  # 변경 전 — 임시 비밀번호 상태로 남아 있다
    events = owner.get("/admin/audit", params={"target_id": direct["id"]}).json()
    assert [(e["action"], e["after_status"], e["actor_name"]) for e in reversed(events)] == [
        ("provision", "active", "test-owner"), ("first_password_change", "active", "direct1"),
    ]  # fmt: skip


# ---------------------------------------------------------------- 만료·재발급


def test_temp_password_expiry_and_reissue(app_real_login, owner) -> None:  # noqa: F811
    from app.db import SessionLocal
    from app.models import User

    user, temp = _provision(owner, "expire1")
    uid = user["id"]
    with SessionLocal() as db:
        db.get(User, uid).temp_password_expires_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
    anon = _client(app_real_login)
    res = anon.post("/auth/login", json={"username": "expire1", "password": temp})
    assert res.status_code == 403 and "유효 기간" in res.json()["detail"]
    assert not anon.cookies.get("moajwo_session") and anon.get("/auth/me").status_code == 401
    wrong = anon.post("/auth/login", json={"username": "expire1", "password": "wrong-pass-9"})
    assert wrong.status_code == 401  # 비밀번호가 틀리면 만료 여부를 알려 주지 않는다
    assert _listed(owner, "expire1")["temp_password_expired"] is True

    # 재발급 — 목록에서 본 인증 버전과 함께; 이전 임시 비밀번호는 바로 무효
    listed = _listed(owner, "expire1")
    res = owner.post(f"/users/{uid}/password", json={"expected_auth_version": listed["auth_version"]})
    assert res.status_code == 200 and res.headers.get("cache-control") == "no-store", res.text
    reissued = res.json()
    assert reissued["temp_password"] != temp and reissued["user"]["temp_password_expired"] is False
    assert anon.post("/auth/login", json={"username": "expire1", "password": temp}).status_code == 401
    again = _client(app_real_login)
    assert (
        again.post("/auth/login", json={"username": "expire1", "password": reissued["temp_password"]}).status_code
        == 200
    )
    assert again.get("/files").status_code == 403
    # 오래된 버전(먼저 반영된 재발급 전의 화면)으로 다시 누르면 덮지 않는다
    stale = owner.post(f"/users/{uid}/password", json={"expected_auth_version": listed["auth_version"]})
    assert stale.status_code == 409 and "그 사이 바뀌" in stale.json()["detail"]
    assert again.get("/auth/me").status_code == 200  # 덮지 않았으므로 방금 로그인은 그대로
    # 버전 없이도 재발급할 수 있다 (확인 없음)
    assert owner.post(f"/users/{uid}/password").status_code == 200
    assert again.get("/auth/me").status_code == 401
    # 임시 비밀번호로 로그인한 뒤 만료되면 변경도 막힌다 (재발급 필요)
    latest = owner.post(f"/users/{uid}/password", json={}).json()["temp_password"]
    late = _client(app_real_login)
    assert late.post("/auth/login", json={"username": "expire1", "password": latest}).status_code == 200
    with SessionLocal() as db:
        db.get(User, uid).temp_password_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    res = late.post(
        "/auth/password",
        json={"current_password": latest, "new_password": NEW_PASSWORD, "new_password_confirm": NEW_PASSWORD},
    )
    assert res.status_code == 403 and "유효 기간" in res.json()["detail"]
    assert _db_rows("select must_change_password from users where id = ?", uid) == [(1,)]
    # 최고 관리자에게는 발급하지 않는다 (내 계정·설치 담당자 복구)
    owner_id = _uid(owner, "test-owner")
    res = owner.post(f"/users/{owner_id}/password", json={})
    assert res.status_code == 409 and "복구 명령" in res.json()["detail"]
    events = owner.get("/admin/audit", params={"target_id": uid}).json()
    assert [(e["action"], e["result"]) for e in reversed(events)] == [
        ("provision", "ok"), ("password_reset", "ok"), ("password_reset", "denied"),
        ("password_reset", "ok"), ("password_reset", "ok"),
    ]  # fmt: skip
    for secret in (temp, reissued["temp_password"], latest):
        assert secret not in owner.get("/admin/audit").text and secret not in repr(_db_rows("select * from users"))


def test_reissue_ends_sessions_and_keeps_suspended_state(app_real_login, owner) -> None:  # noqa: F811
    user, temp = _provision(owner, "work2")
    uid = user["id"]
    worker = _first_change(app_real_login, "work2", temp)
    fid = _upload(worker, name="발급자료.xlsx")
    assert fid in [f["id"] for f in worker.get("/files").json()]
    assert owner.get("/admin/overview").status_code == 200
    assert worker.get("/admin/overview").status_code == 403  # 일반 사용자는 관리 API 불가
    listed = _listed(owner, "work2")
    assert listed["last_login_at"] is not None and listed["last_activity_at"] is not None  # 로그인과 업무 활동을 따로

    issued = owner.post(f"/users/{uid}/password", json={"expected_auth_version": listed["auth_version"]})
    assert issued.status_code == 200
    temp2 = issued.json()["temp_password"]
    assert worker.get("/files").status_code == 401  # 기존 세션 종료
    assert (
        _client(app_real_login).post("/auth/login", json={"username": "work2", "password": NEW_PASSWORD}).status_code
        == 401
    )
    restricted = _client(app_real_login)
    assert restricted.post("/auth/login", json={"username": "work2", "password": temp2}).status_code == 200
    assert restricted.get("/files").status_code == 403

    # 이용 중지 → 재발급해도 중지 그대로 (활성화되지 않음) → 재개 → 임시 비밀번호로 로그인 → 변경 → 기존 파일 그대로
    res = owner.post(f"/users/{uid}/suspend", json={"reason": "퇴사 예정"})
    assert res.status_code == 200 and res.json()["status"] == "suspended"
    assert restricted.get("/auth/me").status_code == 401
    res = owner.post(f"/users/{uid}/password", json={})
    assert res.status_code == 200 and res.json()["user"]["status"] == "suspended"
    temp3 = res.json()["temp_password"]
    blocked = _client(app_real_login).post("/auth/login", json={"username": "work2", "password": temp3})
    assert blocked.status_code == 403 and "이용이 중지된" in blocked.json()["detail"]
    assert owner.post(f"/users/{uid}/resume", json={}).json()["status"] == "active"
    worker = _first_change(app_real_login, "work2", temp3, "after-resume-1")
    assert fid in [f["id"] for f in worker.get("/files").json()]
    events = owner.get("/admin/audit", params={"target_id": uid}).json()
    assert [(e["action"], e["before_status"], e["after_status"], e["result"]) for e in reversed(events)] == [
        ("provision", None, "active", "ok"),
        ("first_password_change", "active", "active", "ok"),
        ("password_reset", "active", "active", "ok"),
        ("suspend", "active", "suspended", "ok"),
        ("password_reset", "suspended", "suspended", "ok"),
        ("resume", "suspended", "active", "ok"),
        ("first_password_change", "active", "active", "ok"),
    ]
    raw = owner.get("/admin/audit").text + repr(_db_rows("select * from account_events"))
    for secret in (temp, temp2, temp3, NEW_PASSWORD, "after-resume-1", "scrypt$", "moajwo_session"):
        assert secret not in raw  # 비밀번호·해시·쿠키를 이력에 남기지 않는다


# ---------------------------------------------------------------- 예전 가입 신청(승인 대기)·업무 API 차단


def test_legacy_pending_accounts_and_business_api_block(app_real_login, owner) -> None:  # noqa: F811
    from app.auth import _token_hash
    from app.db import SessionLocal
    from app.models import AuthSession, User

    anon = _client(app_real_login)
    for name in ("blocked-p", "blocked-r", "blocked-s", "trans-a", "trans-b"):
        _legacy_pending(name)
    wrong = anon.post("/auth/login", json={"username": "blocked-p", "password": "wrong-pass-9"})
    unknown = anon.post("/auth/login", json={"username": "nobody-here", "password": "wrong-pass-9"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]  # 상태도, 아이디 존재 여부도 알려 주지 않는다
    right = anon.post("/auth/login", json={"username": "blocked-p", "password": LEGACY_PASSWORD})
    assert right.status_code == 403 and PENDING_MSG in right.json()["detail"]
    assert not anon.cookies.get("moajwo_session") and anon.get("/files").status_code == 401
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
        for path in WORK_PATHS + ("/auth/me",):
            assert c.get(path).status_code == 401, (name, path)
        assert c.post("/files", files=[("files", ("x.xlsx", b"x", "application/octet-stream"))]).status_code == 401
        assert c.get("/auth/status").json()["user"] is None
    # is_active만 켜져 있고 상태가 이용 중이 아니어도 막는다 (이중 확인)
    with SessionLocal() as db:
        db.scalar(select(User).where(User.username == "blocked-p")).is_active = True
        db.commit()
    c = TestClient(app_real_login, cookies={"moajwo_session": "forged-blocked-p-token-0123456789"})
    assert c.get("/files").status_code == 401
    with SessionLocal() as db:
        db.scalar(select(User).where(User.username == "blocked-p")).is_active = False
        db.commit()

    # 승인한 예전 신청은 당시 비밀번호로 바로 이용(임시 비밀번호 상태 아님); 상태 전이는 덮지 않고 중복은 이력에 남는다
    a, b = _uid(owner, "trans-a"), _uid(owner, "trans-b")
    approved = owner.post(f"/users/{a}/approve", json={"reason": "소속 확인"})
    assert approved.status_code == 200 and approved.json()["must_change_password"] is False
    assert _client(app_real_login, "trans-a", LEGACY_PASSWORD).get("/files").status_code == 200
    dup = owner.post(f"/users/{a}/approve", json={})  # 중복 클릭
    assert dup.status_code == 409 and "이용 중" in dup.json()["detail"]
    assert owner.post(f"/users/{a}/reject", json={}).status_code == 409  # 승인된 계정을 반려로 덮어쓰지 않음
    assert owner.post(f"/users/{a}/resume", json={}).status_code == 409
    assert owner.post(f"/users/{b}/reject", json={"reason": "중복 신청"}).json()["status"] == "rejected"
    for action in ("approve", "resume", "suspend", "reject"):  # 반려는 다른 상태로 덮어쓰지 않는다
        assert owner.post(f"/users/{b}/{action}", json={}).status_code == 409, action
    assert owner.post(f"/users/{b}/password", json={}).status_code == 409  # 반려 계정에는 발급하지 않는다
    assert _db_rows("select status, status_reason from users where id = ?", b) == [("rejected", "중복 신청")]
    rejected = _client(app_real_login).post("/auth/login", json={"username": "trans-b", "password": LEGACY_PASSWORD})
    assert rejected.status_code == 403 and "반려" in rejected.json()["detail"]
    events = owner.get("/admin/audit", params={"target_id": a}).json()
    assert [(e["action"], e["result"]) for e in reversed(events)] == [
        ("signup", "ok"), ("approve", "ok"), ("approve", "denied"), ("reject", "denied"), ("resume", "denied")
    ]  # fmt: skip
    assert all(e["before_status"] == e["after_status"] == "active" for e in events[:3])  # 반영 안 됨 → 변화 없음
    assert events[-2]["reason"] == "소속 확인"
    assert owner.post("/users/999999/approve", json={}).status_code == 404


def test_no_self_approval_or_owner_escalation(app_real_login, owner) -> None:  # noqa: F811
    user, temp = _provision(owner, "climber")
    uid = user["id"]
    me = _first_change(app_real_login, "climber", temp)
    friend = _legacy_pending("climber-friend")
    for path in (f"/users/{uid}/approve", f"/users/{friend}/approve", f"/users/{uid}/resume", f"/users/{uid}/password"):
        assert me.post(path, json={}).status_code == 403, path
    assert me.post("/users", json={"username": "climber2", "display_name": "C"}).status_code == 403
    assert me.patch(f"/users/{uid}", json={"display_name": "X"}).status_code == 403
    assert (
        me.post("/auth/setup", json={"username": "c3", "display_name": "c3", "password": "password-123"}).status_code
        == 410
    )
    assert me.get("/auth/me").json()["role"] == "user"
    owner_id = _uid(owner, "test-owner")
    for action in ("suspend", "reject", "approve", "resume"):  # 최고 관리자 계정 자체는 바꿀 수 없다
        assert owner.post(f"/users/{owner_id}/{action}", json={}).status_code == 409, action
    assert owner.post(f"/users/{owner_id}/password", json={}).status_code == 409
    assert _db_rows("select role, status, is_active from users where id = ?", owner_id) == [("owner", "active", 1)]
    assert _db_rows("select status from users where id = ?", friend) == [("pending",)]


def test_user_list_search_and_filter(owner) -> None:
    assert {u["username"] for u in owner.get("/users", params={"q": "TRANS-"}).json()} == {"trans-a", "trans-b"}
    assert {u["username"] for u in owner.get("/users", params={"q": "직접 추가"}).json()} >= {"direct1", "direct2"}
    by_status = owner.get("/users", params={"status": "rejected"}).json()
    assert by_status and all(u["status"] == "rejected" for u in by_status)
    everyone = owner.get("/users").json()
    firsts = [u["status"] for u in everyone]
    assert firsts[: firsts.count("pending")] == ["pending"] * firsts.count("pending")  # 승인 대기(예전 신청)가 먼저
    for u in everyone:  # 목록에 비밀번호·해시·만료 시각 외의 비밀값이 없다
        assert "password_hash" not in u and "scrypt" not in repr(u)


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
        # PostgreSQL은 datetime, SQLite는 글자로 돌려준다
        if utc_to_kst(at if isinstance(at, datetime) else datetime.fromisoformat(at)).date() == today
    ]
    assert stats["uploads"]["total"] == len(uploads) > 0
    assert stats["uploads"]["inactive_accounts"] == sum(1 for o in uploads if o in inactive)
    runs = owner.get("/runs").json()
    todays = [r for r in runs if datetime.fromisoformat(r["started_at"]).astimezone(KST).date() == today]
    counts = stats["runs"]
    assert counts["total"] == len(todays) and counts["done"] == sum(r["status"] == "done" for r in todays)
    assert counts["review"] == sum(r["status"] == "review" for r in todays)
    assert counts["failed"] == sum(r["status"] == "failed" for r in todays)
    past = owner.get("/admin/overview", params={"start": "2000-01-01", "end": "2000-01-31"}).json()
    assert past["uploads"]["total"] == 0 and past["runs"]["total"] == 0
    assert owner.get("/admin/overview", params={"start": "2026-02-01", "end": "2026-01-01"}).status_code == 422
    assert owner.get("/admin/overview", params={"start": "2020-01-01", "end": "2026-01-01"}).status_code == 422


# ---------------------------------------------------------------- 빈 DB: 초기화 한 번·동시 요청


def _fresh(tmp_path: Path):
    from app.db import Base

    engine = create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    return sessionmaker(engine)


class _Req:
    cookies: dict[str, str] = {}


def test_create_owner_once_and_setup_stays_closed(tmp_path: Path) -> None:
    from app import auth, auth_api
    from app.models import AccountEvent, AppSetting, User

    Session = _fresh(tmp_path)
    with Session() as db:
        assert auth_api.auth_status(_Req(), db).setup_required is True  # 초기화 전 — 화면은 안내만
        with pytest.raises(auth.AuthError):
            auth.create_owner(db, "admin", "관리자", "short", reason="t")  # 규칙 위반 — 아무것도 없음
        assert db.scalar(select(func.count(User.id))) == 0 and db.get(AppSetting, auth.SETUP_KEY) is None
        first = auth.create_owner(db, "Admin", "관리자", "password-123", reason="설치 초기화 시험")
        assert (first.username, first.role, first.status, first.is_active, first.must_change_password) == (
            "admin", "owner", "active", True, False,
        )  # fmt: skip
        assert auth_api.auth_status(_Req(), db).setup_required is False
        with pytest.raises(auth.SetupDone):
            auth.create_owner(db, "again", "A", "password-123", reason="t")
        # 소유자가 이용 중지돼도 초기화는 다시 열리지 않는다
        first.status, first.is_active = "suspended", False
        db.commit()
        assert auth_api.auth_status(_Req(), db).setup_required is False
        with pytest.raises(auth.SetupDone):
            auth.create_owner(db, "again", "A", "password-123", reason="t")
        assert db.scalar(select(func.count(User.id))) == 1
        assert [e.action for e in db.scalars(select(AccountEvent).order_by(AccountEvent.id))] == ["setup"]


def test_concurrent_init_makes_one_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import auth
    from app.models import AppSetting, User

    Session = _fresh(tmp_path)
    barrier = threading.Barrier(3)
    real_hash = auth.hash_password

    def slow_hash(password: str) -> str:  # 세 초기화 모두 '계정 없음' 확인을 통과한 뒤 저장하게 만든다
        barrier.wait(timeout=10)
        return real_hash(password)

    monkeypatch.setattr(auth, "hash_password", slow_hash)
    outcomes: list[str] = []

    def attempt() -> None:
        with Session() as db:
            try:
                outcomes.append(auth.create_owner(db, "admin", "A", "password-123", reason="t").username)
            except auth.SetupDone:
                outcomes.append("done")

    threads = [threading.Thread(target=attempt) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(outcomes) == ["admin", "done", "done"]
    with Session() as db:
        assert db.scalar(select(func.count(User.id))) == 1 and db.get(AppSetting, auth.SETUP_KEY) is not None


def test_concurrent_first_change_applies_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import auth, auth_api
    from app.auth import CurrentUser
    from app.models import AuthSession, User

    Session = _fresh(tmp_path)
    temp = "TempPassw0rdX"
    with Session() as db:
        auth.create_owner(db, "admin", "A", "password-123", reason="t")
        user = User(username="newbie", display_name="N", password_hash=auth.hash_password(temp), role="user",
                    failed_logins=0, must_change_password=True,
                    temp_password_expires_at=datetime.now(UTC) + timedelta(hours=1))  # fmt: skip
        auth.set_status(user, "active")
        db.add(user)
        db.commit()
        uid, version = user.id, user.auth_version
        for i in range(2):
            db.add(AuthSession(token_hash=f"h{i}" * 16, user_id=uid, auth_version=version, created_at=datetime.now(UTC),
                               expires_at=datetime.now(UTC) + timedelta(hours=1)))  # fmt: skip
        db.commit()
    actor = CurrentUser(uid, "newbie", "N", "user", must_change_password=True)
    barrier = threading.Barrier(3)
    real_hash = auth_api.hash_password

    def slow_hash(password: str) -> str:  # 세 변경 요청이 모두 현재 비밀번호 확인을 통과한 뒤 저장하게 만든다
        barrier.wait(timeout=10)
        return real_hash(password)

    monkeypatch.setattr(auth_api, "hash_password", slow_hash)
    outcomes: list[Any] = []

    def attempt(i: int) -> None:
        body = auth_api.PasswordChangeIn(
            current_password=temp, new_password=f"chosen-pass-{i}", new_password_confirm=f"chosen-pass-{i}"
        )
        with Session() as db:
            try:
                outcomes.append(auth_api.change_password(body, actor, db).status_code)
            except HTTPException as exc:
                outcomes.append(exc.status_code)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(outcomes) == [204, 409, 409]
    with Session() as db:
        row = db.get(User, uid)
        assert (row.must_change_password, row.temp_password_expires_at, row.auth_version) == (False, None, version + 1)
        assert db.scalar(select(func.count(AuthSession.id)).where(AuthSession.user_id == uid)) == 0
        assert sum(auth.verify_password(f"chosen-pass-{i}", row.password_hash) for i in range(3)) == 1


def test_concurrent_approvals_apply_once(tmp_path: Path) -> None:
    from app import auth, auth_api
    from app.auth import CurrentUser
    from app.models import AccountEvent, User

    Session = _fresh(tmp_path)
    with Session() as db:
        boss = auth.create_owner(db, "admin", "B", "password-123", reason="t")
        applicant = User(username="applicant", display_name="A", password_hash=auth.hash_password("password-123"),
                         role="user", failed_logins=0)  # fmt: skip
        auth.set_status(applicant, "pending")
        db.add(applicant)
        db.commit()
        target, boss_id = applicant.id, boss.id
    actor = CurrentUser(boss_id, "admin", "B", "owner")
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
        assert sorted(events) == sorted([("approve", "ok")] + [("approve", "denied")] * 3)
        assert db.get(User, target).status == "active"
    _ = Response  # 기존 import 호환
