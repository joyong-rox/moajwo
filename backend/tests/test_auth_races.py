"""로그인 ↔ 비밀번호 초기화·본인 비밀번호 변경·이용 중지의 경합 (세션이 되살아나지 않아야 한다).

재현 방법: 로그인의 비밀번호 확인(verify_password)이 성공한 직후 이벤트로 멈춰 세우고, 그 사이 다른 DB 연결(세션)에서
계정을 바꾸고 커밋한 뒤 로그인을 재개한다. sleep이나 우연한 실행 순서에 기대지 않는다.
- 함수 수준: 빈 임시 DB, 함수를 서로 다른 SQLAlchemy 세션(연결)으로 호출
- API 수준: 실제 쿠키 로그인(TestClient) — 경합 중 받은 쿠키로 업무 API가 열리지 않는지
"""

import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import auth
from tests.test_accounts import owner  # noqa: F401
from tests.test_auth import _client, app_real_login  # noqa: F401

OLD, NEW = "old-pass-race-1", "new-pass-race-2"


class Gate:
    """OLD 비밀번호 확인이 성공한 첫 로그인을 멈춰 세운다 (verified → 다른 연결에서 변경·커밋 → proceed)."""

    def __init__(self) -> None:
        self.armed = threading.Event()
        self.verified = threading.Event()
        self.proceed = threading.Event()

    def arm(self) -> None:
        self.armed.set()


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Gate:
    g = Gate()
    real = auth.verify_password

    def gated(password: str, stored: str) -> bool:
        ok = real(password, stored)
        if ok and password == OLD and g.armed.is_set() and not g.verified.is_set():
            g.verified.set()
            assert g.proceed.wait(15), "동기화 지점에서 재개 신호가 오지 않음"
        return ok

    monkeypatch.setattr(auth, "verify_password", gated)
    return g


def _in_thread(fn) -> tuple[threading.Thread, dict[str, Any]]:
    out: dict[str, Any] = {}

    def run() -> None:
        try:
            out["value"] = fn()
        except Exception as exc:  # noqa: BLE001 — 결과로 넘겨 본 스레드에서 판정
            out["error"] = exc

    t = threading.Thread(target=run)
    t.start()
    return t, out


# ---------------------------------------------------------------- 함수 수준 (빈 임시 DB, 서로 다른 연결)


class _Req:
    def __init__(self, token: str | None) -> None:
        self.cookies = {auth.COOKIE_NAME: token} if token else {}


def _fresh(tmp_path: Path):
    from app.db import Base
    from app.models import User

    engine = create_engine(f"sqlite:///{(tmp_path / 'race.db').as_posix()}", connect_args={"timeout": 30})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine)
    with Session() as db:
        for name, role in (("boss", "owner"), ("racer", "user")):
            user = User(username=name, display_name=name, password_hash=auth.hash_password(OLD), role=role,
                        failed_logins=0)  # fmt: skip
            auth.set_status(user, "active")
            db.add(user)
        db.commit()
        boss = db.scalar(select(User).where(User.username == "boss"))
        racer = db.scalar(select(User).where(User.username == "racer"))
        actor = auth.CurrentUser(boss.id, boss.username, boss.display_name, boss.role)
        return Session, actor, racer.id


def _valid(Session, token: str | None) -> bool:
    if not token:
        return False
    with Session() as db:
        try:
            auth.current_user(_Req(token), db)
            return True
        except HTTPException:
            return False


def _login(Session, password: str):
    def go():
        with Session() as db:
            return auth.login(db, "racer", password)[1]

    return go


def test_login_vs_admin_password_reset(tmp_path: Path, gate: Gate) -> None:
    from app import auth_api

    Session, actor, uid = _fresh(tmp_path)
    gate.arm()
    t, out = _in_thread(_login(Session, OLD))
    assert gate.verified.wait(15)
    with Session() as other:  # 다른 연결에서 초기화하고 커밋
        auth_api.reset_password(uid, auth_api.PasswordResetIn(password=NEW), actor, other)
    gate.proceed.set()
    t.join(30)
    assert not _valid(Session, out.get("value")), "이전 비밀번호로 시작한 로그인이 유효한 세션을 얻음"
    fresh = _login(Session, NEW)()
    assert _valid(Session, fresh)  # 새 비밀번호 로그인은 정상


def test_login_vs_own_password_change(tmp_path: Path, gate: Gate) -> None:
    from app import auth_api

    Session, _, uid = _fresh(tmp_path)
    first_device = _login(Session, OLD)()  # 이미 로그인한 기기
    gate.arm()
    t, out = _in_thread(_login(Session, OLD))  # 다른 기기의 로그인이 이전 비밀번호 확인 직후 대기
    assert gate.verified.wait(15)
    me = auth.CurrentUser(uid, "racer", "racer", "user")
    with Session() as other:
        res = auth_api.change_password(
            auth_api.PasswordChangeIn(current_password=OLD, new_password=NEW), me, Response(), other
        )
    renewed = res.headers["set-cookie"].split(f"{auth.COOKIE_NAME}=")[1].split(";")[0]
    gate.proceed.set()
    t.join(30)
    assert not _valid(Session, out.get("value")), "본인 비밀번호 변경 중 이전 비밀번호 로그인이 세션을 얻음"
    assert not _valid(Session, first_device)  # 다른 기기의 기존 세션은 종료
    assert _valid(Session, renewed)  # 변경한 기기는 새 세션으로 이어감 (기존 동작)


def test_login_vs_suspend_then_resume(tmp_path: Path, gate: Gate) -> None:
    from app import auth_api

    Session, actor, uid = _fresh(tmp_path)
    before = _login(Session, OLD)()  # 중지 전부터 있던 쿠키
    gate.arm()
    t, out = _in_thread(_login(Session, OLD))
    assert gate.verified.wait(15)
    with Session() as other:
        auth_api._change_status(other, actor, uid, "suspend", "경합 시험")
    gate.proceed.set()
    t.join(30)
    raced = out.get("value")
    assert not _valid(Session, raced)
    with Session() as other:
        auth_api._change_status(other, actor, uid, "resume", None)
    assert not _valid(Session, raced), "이용 중지 중 만든 쿠키가 재개 후 되살아남"
    assert not _valid(Session, before), "중지 전 쿠키가 재개 후 되살아남"
    assert _valid(Session, _login(Session, OLD)())  # 재개 후에는 새 로그인으로 이용


# ---------------------------------------------------------------- API 수준 (실제 쿠키)


def _make_user(owner_client: TestClient, name: str) -> int:  # noqa: F811
    body = {"username": name, "display_name": name, "status": "active", "password": OLD}
    res = owner_client.post("/users", json=body)
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _api_login(app: Any, name: str, password: str):
    def go():
        c = TestClient(app)
        res = c.post("/auth/login", json={"username": name, "password": password})
        return c, res.status_code

    return go


def test_api_reset_race_leaves_no_valid_cookie(app_real_login, owner, gate: Gate) -> None:  # noqa: F811
    uid = _make_user(owner, "race-api-reset")
    gate.arm()
    t, out = _in_thread(_api_login(app_real_login, "race-api-reset", OLD))
    assert gate.verified.wait(15)
    assert owner.post(f"/users/{uid}/password", json={"password": NEW}).status_code == 204
    gate.proceed.set()
    t.join(30)
    client, status = out["value"]
    assert client.get("/files").status_code == 401, f"경합 로그인({status})의 쿠키로 업무 API가 열림"
    assert client.get("/auth/status").json()["user"] is None
    assert _client(app_real_login, "race-api-reset", NEW).get("/files").status_code == 200


def test_api_own_change_race(app_real_login, owner, gate: Gate) -> None:  # noqa: F811
    _make_user(owner, "race-api-self")
    me = _client(app_real_login, "race-api-self", OLD)
    gate.arm()
    t, out = _in_thread(_api_login(app_real_login, "race-api-self", OLD))
    assert gate.verified.wait(15)
    res = me.post("/auth/password", json={"current_password": OLD, "new_password": NEW})
    assert res.status_code == 204, res.text
    gate.proceed.set()
    t.join(30)
    client, status = out["value"]
    assert client.get("/files").status_code == 401, f"경합 로그인({status})의 쿠키로 업무 API가 열림"
    assert me.get("/files").status_code == 200  # 변경한 기기는 새 세션으로 계속


def test_api_suspend_race_cookie_stays_dead_after_resume(app_real_login, owner, gate: Gate) -> None:  # noqa: F811
    uid = _make_user(owner, "race-api-stop")
    old_cookie = _client(app_real_login, "race-api-stop", OLD)
    gate.arm()
    t, out = _in_thread(_api_login(app_real_login, "race-api-stop", OLD))
    assert gate.verified.wait(15)
    assert owner.post(f"/users/{uid}/suspend", json={"reason": "경합 시험"}).status_code == 200
    gate.proceed.set()
    t.join(30)
    client, status = out["value"]
    assert client.get("/files").status_code == 401
    assert owner.post(f"/users/{uid}/resume", json={}).status_code == 200
    assert client.get("/files").status_code == 401, f"경합 로그인({status})의 쿠키가 재개 후 되살아남"
    assert old_cookie.get("/files").status_code == 401, "중지 전 쿠키가 재개 후 되살아남"
    assert _client(app_real_login, "race-api-stop", OLD).get("/files").status_code == 200  # 새 로그인 필요
    events = [(e["action"], e["result"]) for e in owner.get("/admin/audit", params={"target_id": uid}).json()]
    assert events[:2] == [("resume", "ok"), ("suspend", "ok")]  # 관리 이력 유지
