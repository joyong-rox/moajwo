"""임시 비밀번호 폐기(D3) — 대상 조건·계약(expected_auth_version 필수)·효과·재개 뒤 로그인 불가·재발급으로 복구, 그리고
실제 세션 DB(SQLite 또는 PostgreSQL — 전체 시험을 PostgreSQL로 돌리면 PostgreSQL)에서의 동시 요청 경합: 폐기↔재개·
폐기↔재발급·같은 버전 폐기 2건은 각각 하나만 반영되고 최종 상태가 일관돼야 한다.

- 동기화: 대상 계정을 읽는 지점(auth_api._target) 바로 뒤에 둔 barrier·Event — 두 요청이 모두 같은 인증 버전을 읽은 뒤
  조건부 UPDATE에서 겹치게 한다(sleep·실행 순서의 우연에 기대지 않음). 재발급이 나중에 커밋되는 순서는 4차와 같이 해시
  함수(auth.hash_password)에 둔 Event로 만든다. 각 요청은 별도 스레드·풀의 서로 다른 연결.
- 기록: 환경 변수 MOAJWO_RACE_RECORD 파일에 JSON 한 줄씩(dialect·요청 수·응답·최종 버전·세션 수·이력·어느 쪽이
  반영됐는지). 접속 문자열·비밀번호·해시는 쓰지 않는다.
"""

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import auth, auth_api
from tests.conftest import db_rows
from tests.test_accounts import (  # noqa: F401
    NEW_PASSWORD,
    _first_change,
    _legacy_pending,
    _listed,
    _provision,
    _uid,
    owner,
)
from tests.test_auth import _client, app_real_login  # noqa: F401
from tests.test_auth_races_api import _checked_out, _events, _record, _run, _sessions

WRONG_LOGIN = "아이디 또는 비밀번호가 올바르지 않습니다."


def _row(uid: int) -> tuple:
    """계정 행의 요약 — 상태·활성·임시 상태·버전·만료 없음·비밀번호 사용 가능 여부(해시 값은 읽지 않는다)."""
    r = db_rows(
        "select status, is_active, must_change_password, auth_version, temp_password_expires_at is null, "
        "password_hash like 'scrypt$%' from users where id = ?",
        uid,
    )[0]
    return (r[0], bool(r[1]), bool(r[2]), r[3], bool(r[4]), bool(r[5]))


def _expire(uid: int) -> None:
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        db.get(User, uid).temp_password_expires_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()


def _revoke(admin: TestClient, uid: int, version: int | None = None, username: str | None = None):  # noqa: ANN202
    if version is None:
        version = _listed(admin, username or "")["auth_version"]
    return admin.post(f"/users/{uid}/password/revoke", json={"expected_auth_version": version})


def _suspend(admin: TestClient, uid: int) -> dict:
    res = admin.post(f"/users/{uid}/suspend", json={"reason": "폐기 시험"})
    assert res.status_code == 200 and res.json()["status"] == "suspended", res.text
    return res.json()


# ---------------------------------------------------------------- 대상 조건


def test_revoke_only_for_suspended_temp_accounts(app_real_login, owner) -> None:  # noqa: F811
    """폐기 대상은 '이용 중지'이면서 임시 비밀번호 상태인 계정뿐 — 이용 중(임시 포함)·임시 상태가 아닌 중지 계정·
    승인 대기·
    반려·최고 관리자는 409이고 계정은 그대로(거절 이력만)."""
    cases: list[tuple[str, int, str]] = []
    user, _ = _provision(owner, "rv-active-temp")  # 이용 중 + 임시
    cases.append(("rv-active-temp", user["id"], "이용 중"))
    user, temp = _provision(owner, "rv-changed")  # 변경 완료 → 중지: 임시 상태 아님
    _first_change(app_real_login, "rv-changed", temp)
    _suspend(owner, user["id"])
    cases.append(("rv-changed", user["id"], "임시 비밀번호 상태가 아닙니다"))
    user, _ = _provision(
        owner, "rv-active-expired"
    )  # 이용 중 + 만료된 임시 (만료는 임시 상태를 풀지 않지만 이용 중이라 대상 아님)
    _expire(user["id"])
    cases.append(("rv-active-expired", user["id"], "이용 중"))
    pending = _legacy_pending("rv-pending")  # 예전 가입 신청 (승인 대기)
    cases.append(("rv-pending", pending, "승인 대기"))
    rejected = _legacy_pending("rv-rejected")
    assert owner.post(f"/users/{rejected}/reject", json={"reason": "x"}).status_code == 200
    cases.append(("rv-rejected", rejected, "가입 반려"))
    cases.append(("test-owner", _uid(owner, "test-owner"), "최고 관리자"))

    for username, uid, needle in cases:
        before = _row(uid)
        events_before = len(_events(owner, uid))
        res = _revoke(owner, uid, username=username)
        assert res.status_code == 409, (username, res.text)
        assert needle in res.json()["detail"], (username, res.json()["detail"])
        assert "폐기 대상 아님" in res.json()["detail"] or username == "test-owner", res.json()["detail"]
        assert _row(uid) == before, username  # 비밀번호·임시 상태·버전·상태 그대로
        assert (
            _events(owner, uid)[-1] == ("password_revoke", "denied") and len(_events(owner, uid)) == events_before + 1
        )
    assert _listed(owner, "rv-active-temp")["password_revoked"] is False


# ---------------------------------------------------------------- 계약·효과·복구


def test_revoke_contract_effects_and_recovery(app_real_login, owner) -> None:  # noqa: F811
    """expected_auth_version 필수(422/409) → 폐기 한 번 = 사용할 수 없는 비밀번호·임시 상태 해제·버전 +1·세션 0·
    이력 1건,
    상태는 이용 중지 그대로 → 다시 폐기 409(임시 상태 아님) → 재개해도 그 비밀번호로 로그인 불가(일반 401, 세션 없음) →
    재발급 → 임시 로그인·변경 → 업무 가능."""
    user, temp = _provision(owner, "rv-main")
    uid = user["id"]
    restricted = _client(app_real_login)
    assert restricted.post("/auth/login", json={"username": "rv-main", "password": temp}).status_code == 200
    assert _sessions(uid) == 1
    _suspend(owner, uid)  # 중지: 세션 종료·버전 +1
    assert _sessions(uid) == 0
    listed = _listed(owner, "rv-main")
    assert listed["status"] == "suspended" and listed["must_change_password"] is True
    assert listed["password_revoked"] is False
    version = listed["auth_version"]
    held = _row(uid)
    events_before = len(_events(owner, uid))
    # 버전 없음·잘못된 자료형은 422 — 계정·이력 그대로 (불리언을 0/1로 읽지 않는다)
    for payload in (
        {},
        {"expected_auth_version": None},
        {"expected_auth_version": True},
        {"expected_auth_version": "1"},
        {"expected_auth_version": -1},
        {"expected_auth_version": 1.5},
        {"expected_auth_version": version, "x": 1},
    ):
        res = owner.post(f"/users/{uid}/password/revoke", json=payload)
        assert res.status_code == 422, (payload, res.text)
    assert owner.post(f"/users/{uid}/password/revoke").status_code == 422  # 본문 없음
    raw_null = owner.post(
        f"/users/{uid}/password/revoke", content=b"null", headers={"Content-Type": "application/json"}
    )
    assert raw_null.status_code == 422
    assert _row(uid) == held and len(_events(owner, uid)) == events_before
    # 오래된 버전은 409 — 거절 이력만
    stale = _revoke(owner, uid, version - 1)
    assert stale.status_code == 409 and "그 사이 바뀌" in stale.json()["detail"]
    assert _row(uid) == held and _events(owner, uid)[-1] == ("password_revoke", "denied")
    # 폐기 — 한 번
    done = _revoke(owner, uid, version)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "suspended" and body["is_active"] is False
    assert body["must_change_password"] is False and body["temp_password_expires_at"] is None
    assert body["password_revoked"] is True and body["auth_version"] == version + 1
    assert _row(uid) == ("suspended", False, False, version + 1, True, False)  # 비밀번호 사용 불가
    assert _sessions(uid) == 0
    events = owner.get("/admin/audit", params={"target_id": uid}).json()
    ok = [e for e in events if e["action"] == "password_revoke" and e["result"] == "ok"]
    assert len(ok) == 1 and ok[0]["before_status"] == "suspended" and ok[0]["after_status"] == "suspended"
    assert ok[0]["action_label"] == "임시 비밀번호 폐기" and "재발급" in ok[0]["reason"]
    # downgrade 보호가 세는 집합에서 빠진다 (플래그만 지운 것이 아니라 비밀번호를 못 쓰게 바꾼 결과)
    assert db_rows("select count(*) from users where must_change_password and id = ?", uid) == [(0,)]
    # 다시 폐기 — 임시 상태가 아니라 409
    again = _revoke(owner, uid, version + 1)
    assert again.status_code == 409 and "임시 비밀번호 상태가 아닙니다" in again.json()["detail"]
    assert _row(uid) == ("suspended", False, False, version + 1, True, False)
    # 재개해도 로그인 불가 — 폐기 전 임시 비밀번호·아무 비밀번호나 모두 일반 401(계정 상태·폐기 여부를 알리지 않음),
    # 세션 없음
    resumed = owner.post(f"/users/{uid}/resume", json={})
    assert resumed.status_code == 200 and resumed.json()["status"] == "active"
    assert resumed.json()["password_revoked"] is True
    for password in (temp, "wrong-pass-9", auth.UNUSABLE_PASSWORD_HASH):
        res = _client(app_real_login).post("/auth/login", json={"username": "rv-main", "password": password})
        assert res.status_code == 401 and res.json()["detail"] == WRONG_LOGIN, res.text
    assert _sessions(uid) == 0
    # 로그인하려면 재발급 — 그 뒤 임시 로그인·본인 비밀번호 변경·업무 가능
    reissued = owner.post(
        f"/users/{uid}/password", json={"expected_auth_version": _listed(owner, "rv-main")["auth_version"]}
    )
    assert reissued.status_code == 200, reissued.text
    assert reissued.json()["user"]["password_revoked"] is False
    worker = _first_change(app_real_login, "rv-main", reissued.json()["temp_password"], "after-revoke-1")
    assert worker.get("/files").status_code == 200
    assert _listed(owner, "rv-main")["password_revoked"] is False
    # 중지 + 만료된 임시 계정도 폐기 대상 (만료는 임시 상태를 풀지 않는다)
    user2, _ = _provision(owner, "rv-expired-susp")
    _expire(user2["id"])
    _suspend(owner, user2["id"])
    assert _listed(owner, "rv-expired-susp")["temp_password_expired"] is True
    res = _revoke(owner, user2["id"], username="rv-expired-susp")
    assert res.status_code == 200 and res.json()["password_revoked"] is True
    # 이력(API·표)에는 비밀번호·해시가 없다 — 폐기 이력도 마찬가지
    raw = owner.get("/admin/audit").text + repr(db_rows("select * from account_events"))
    for secret in (temp, reissued.json()["temp_password"], "after-revoke-1", "scrypt$", auth.UNUSABLE_PASSWORD_HASH):
        assert secret not in raw


# ---------------------------------------------------------------- 경합 (실제 세션 DB)


class _ReadGate:
    """처음 n번의 대상 계정 읽기(auth_api._target) 직후를 한 지점에 모은다 — 모두 같은 인증 버전·상태를 읽은 뒤 조건부
    UPDATE에서 겹친다. 도착 때마다 풀의 checked-out 연결 수를 적는다."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, n: int) -> None:
        self.barrier = threading.Barrier(n)
        self.seen: list[int | None] = []
        self.lock = threading.Lock()
        real = auth_api._target

        def gated(db: Any, user_id: int) -> Any:
            row = real(db, user_id)
            with self.lock:
                first = len(self.seen) < n
                if first:
                    self.seen.append(_checked_out())
            if first:
                self.barrier.wait(timeout=20)
            return row

        monkeypatch.setattr(auth_api, "_target", gated)

    def summary(self) -> dict[str, Any]:
        return {"gathered": len(self.seen), "pool_checked_out_at_arrival": self.seen}


class _Pause:
    """첫 번째 호출(대상 계정 읽기 또는 해시) 직후에 멈추고(reached) 신호(proceed)를 받으면 이어 간다 — 다른 요청이 먼저
    커밋되게 순서를 정한다. 멈출 요청의 스레드를 먼저 시작한 뒤 reached를 기다리고, 다른 요청을 보내고, proceed를
    준다."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, module: Any, attr: str) -> None:
        self.reached, self.proceed = threading.Event(), threading.Event()
        real = getattr(module, attr)
        lock = threading.Lock()
        armed = [True]

        def gated(*args: Any, **kwargs: Any) -> Any:
            out = real(*args, **kwargs)
            with lock:
                mine = armed[0]
                armed[0] = False
            if mine:
                self.reached.set()
                assert self.proceed.wait(20), "재개 신호 없음"
            return out

        monkeypatch.setattr(module, attr, gated)


def _in_thread(fn: Any) -> tuple[threading.Thread, dict[str, Any]]:
    out: dict[str, Any] = {}

    def run() -> None:
        res = fn()
        out["status"], out["body"] = res.status_code, res.json()

    return threading.Thread(target=run), out


def test_api_concurrent_revoke_same_version_applies_once(app_real_login, owner, monkeypatch) -> None:  # noqa: F811
    """같은 인증 버전으로 동시에 두 번 폐기 → 200 하나·409 하나. 버전 +1, 비밀번호 사용 불가, 성공 이력 1건·거절
    이력 1건."""
    user, _ = _provision(owner, "rv-race-twice")
    uid = user["id"]
    _suspend(owner, uid)
    version = _listed(owner, "rv-race-twice")["auth_version"]
    gate = _ReadGate(monkeypatch, 2)
    results: list[int] = []

    def attempt() -> None:
        results.append(_revoke(owner, uid, version).status_code)

    _run([threading.Thread(target=attempt) for _ in range(2)])
    assert sorted(results) == [200, 409], results
    after = _row(uid)
    assert after == ("suspended", False, False, version + 1, True, False)
    events = _events(owner, uid)
    assert events.count(("password_revoke", "ok")) == 1 and events.count(("password_revoke", "denied")) == 1
    _record("api_concurrent_revoke_same_version", {
        "requests": 2, "responses": sorted(results), "version_before": version, "final_auth_version": after[3],
        "final_status": after[0], "password_usable": after[5], "sessions_after": _sessions(uid), "events": events,
        **gate.summary(),
    })  # fmt: skip


def test_api_revoke_vs_resume_only_one_applies(app_real_login, owner, monkeypatch) -> None:  # noqa: F811
    """폐기와 재개가 동시에: (A) 재개가 먼저 커밋 → 폐기 409(상태가 이용 중) — 임시 비밀번호 그대로 유효.
    (B) 폐기가 먼저 커밋 → 재개 409(인증 버전이 바뀜) — 중지·폐기 그대로. (C) 둘 다 읽은 뒤 동시에 → 둘 중 하나만 200.
    그 뒤 순서대로 재개하면 로그인 불가 → 재발급으로 복구."""
    user, temp = _provision(owner, "rv-race-resume")
    uid = user["id"]
    _suspend(owner, uid)
    v0 = _listed(owner, "rv-race-resume")["auth_version"]
    # (A) 폐기가 계정을 읽은 직후 멈춘 사이 재개가 먼저 반영
    pause = _Pause(monkeypatch, auth_api, "_target")
    t, revoke_out = _in_thread(lambda: _revoke(owner, uid, v0))
    t.start()
    assert pause.reached.wait(20)
    resumed = owner.post(f"/users/{uid}/resume", json={})
    assert resumed.status_code == 200 and resumed.json()["status"] == "active"
    pause.proceed.set()
    t.join(60)
    # 폐기 요청은 멈추기 전에 읽어 둔 '이용 중지' 상태로 검사를 지났지만 조건부 UPDATE(상태 = 이용 중지)가 0행 → 409
    assert revoke_out["status"] == 409 and "그 사이 바뀌" in revoke_out["body"]["detail"], revoke_out
    a = _row(uid)
    # 재개만 반영 — 임시 상태·만료·비밀번호 그대로 (재개는 버전을 올리지 않음)
    assert a == ("active", True, True, v0, False, True)
    limited = _client(app_real_login)
    assert limited.post("/auth/login", json={"username": "rv-race-resume", "password": temp}).status_code == 200
    assert limited.get("/files").status_code == 403  # 임시 비밀번호 상태 그대로
    events_a = _events(owner, uid)[-2:]
    assert sorted(events_a) == sorted([("resume", "ok"), ("password_revoke", "denied")]), events_a
    _record("api_revoke_vs_resume_A_resume_first", {
        "requests": 2, "revoke_status": 409, "resume_status": 200, "version_before": v0, "final_auth_version": a[3],
        "final_status": a[0], "must_change_after": a[2], "events": events_a,
    })  # fmt: skip
    # (B) 다시 중지한 뒤: 재개가 계정을 읽은 직후 멈춘 사이 폐기가 먼저 반영
    _suspend(owner, uid)
    assert limited.get("/auth/me").status_code == 401
    v1 = _listed(owner, "rv-race-resume")["auth_version"]
    pause = _Pause(monkeypatch, auth_api, "_target")
    t, resume_out = _in_thread(lambda: owner.post(f"/users/{uid}/resume", json={}))
    t.start()
    assert pause.reached.wait(20)
    revoked = _revoke(owner, uid, v1)
    assert revoked.status_code == 200 and revoked.json()["password_revoked"] is True
    pause.proceed.set()
    t.join(60)
    assert resume_out["status"] == 409, resume_out
    assert "그 사이 계정 정보가 바뀌었습니다" in resume_out["body"]["detail"], resume_out
    b = _row(uid)
    assert b == ("suspended", False, False, v1 + 1, True, False)  # 폐기만 반영 — 중지 그대로
    events_b = _events(owner, uid)[-2:]
    assert sorted(events_b) == sorted([("password_revoke", "ok"), ("resume", "denied")]), events_b
    _record("api_revoke_vs_resume_B_revoke_first", {
        "requests": 2, "revoke_status": 200, "resume_status": 409, "version_before": v1, "final_auth_version": b[3],
        "final_status": b[0], "password_usable": b[5], "events": events_b,
    })  # fmt: skip
    # 순서대로 재개 → 로그인 불가 → 재발급 → 로그인
    assert owner.post(f"/users/{uid}/resume", json={}).json()["status"] == "active"
    login = {"username": "rv-race-resume", "password": temp}
    assert _client(app_real_login).post("/auth/login", json=login).status_code == 401
    assert _sessions(uid) == 0
    reissued = owner.post(
        f"/users/{uid}/password", json={"expected_auth_version": _listed(owner, "rv-race-resume")["auth_version"]}
    )
    assert reissued.status_code == 200
    worker = _first_change(app_real_login, "rv-race-resume", reissued.json()["temp_password"])
    assert worker.get("/files").status_code == 200
    # (C) 둘 다 읽은 뒤 동시에 — 어느 쪽이 이기든 하나만 200, 최종 상태는 두 가지 일관된 상태 중 하나
    user2, temp2 = _provision(owner, "rv-race-resume2")
    uid2 = user2["id"]
    _suspend(owner, uid2)
    v2 = _listed(owner, "rv-race-resume2")["auth_version"]
    gate = _ReadGate(monkeypatch, 2)
    results: dict[str, int] = {}

    def do_revoke() -> None:
        results["revoke"] = _revoke(owner, uid2, v2).status_code

    def do_resume() -> None:
        results["resume"] = owner.post(f"/users/{uid2}/resume", json={}).status_code

    _run([threading.Thread(target=do_revoke), threading.Thread(target=do_resume)])
    assert sorted(results.values()) == [200, 409], results
    c = _row(uid2)
    if results["revoke"] == 200:
        assert c == ("suspended", False, False, v2 + 1, True, False)
        winner = "revoke"
    else:
        assert c == ("active", True, True, v2, False, True)
        winner = "resume"
    _record("api_revoke_vs_resume_C_both_read_then_race", {
        "requests": 2, "responses": results, "winner": winner, "version_before": v2, "final_auth_version": c[3],
        "final_status": c[0], "must_change_after": c[2], "password_usable": c[5], **gate.summary(),
    })  # fmt: skip


def test_api_revoke_vs_reissue_only_one_applies(app_real_login, owner, monkeypatch) -> None:  # noqa: F811
    """폐기와 재발급이 동시에: (A) 재발급이 먼저 커밋 → 폐기 409(버전) — 새 임시 비밀번호 유효. (B) 폐기가 먼저 커밋 →
    재발급 409(버전) — 새 임시 비밀번호는 저장되지 않고 폐기 그대로. (C) 둘 다 읽은 뒤 동시에 → 하나만 200.
    어느 경우에도 유효한 임시 비밀번호가 둘이 되거나 폐기된 계정에 임시 상태가 남지 않는다."""
    user, _ = _provision(owner, "rv-race-reissue")
    uid = user["id"]
    _suspend(owner, uid)
    v0 = _listed(owner, "rv-race-reissue")["auth_version"]
    # (A) 폐기가 계정을 읽은 직후 멈춘 사이 재발급이 먼저 반영
    pause = _Pause(monkeypatch, auth_api, "_target")
    t, revoke_out = _in_thread(lambda: _revoke(owner, uid, v0))
    t.start()
    assert pause.reached.wait(20)
    reissued = owner.post(f"/users/{uid}/password", json={"expected_auth_version": v0})
    assert reissued.status_code == 200 and reissued.json()["user"]["status"] == "suspended"
    new_temp = reissued.json()["temp_password"]
    pause.proceed.set()
    t.join(60)
    assert revoke_out["status"] == 409 and "그 사이 바뀌" in revoke_out["body"]["detail"], revoke_out
    a = _row(uid)
    assert a == ("suspended", False, True, v0 + 1, False, True)  # 재발급만 반영 — 임시 상태·새 비밀번호
    events_a = _events(owner, uid)[-2:]
    assert sorted(events_a) == sorted([("password_reset", "ok"), ("password_revoke", "denied")]), events_a
    _record("api_revoke_vs_reissue_A_reissue_first", {
        "requests": 2, "revoke_status": 409, "reissue_status": 200, "version_before": v0, "final_auth_version": a[3],
        "final_status": a[0], "must_change_after": a[2], "events": events_a,
    })  # fmt: skip
    # (B) 재발급이 새 해시를 만든 직후(버전을 읽은 뒤·UPDATE 전) 멈춘 사이 폐기가 먼저 반영
    v1 = a[3]
    pause = _Pause(monkeypatch, auth, "hash_password")
    t, reissue_out = _in_thread(lambda: owner.post(f"/users/{uid}/password", json={"expected_auth_version": v1}))
    t.start()
    assert pause.reached.wait(20)
    revoked = _revoke(owner, uid, v1)
    assert revoked.status_code == 200 and revoked.json()["password_revoked"] is True
    pause.proceed.set()
    t.join(60)
    assert reissue_out["status"] == 409 and "그 사이 바뀌" in reissue_out["body"]["detail"], reissue_out
    b = _row(uid)
    assert b == ("suspended", False, False, v1 + 1, True, False)  # 폐기만 반영 — 새 임시 비밀번호 없음
    assert owner.post(f"/users/{uid}/resume", json={}).json()["status"] == "active"
    for password in (new_temp, "wrong-pass-9"):  # 멈췄던 재발급의 비밀번호도, 그 전 것도 모두 쓸 수 없다
        res = _client(app_real_login).post("/auth/login", json={"username": "rv-race-reissue", "password": password})
        assert res.status_code == 401 and res.json()["detail"] == WRONG_LOGIN
    events_b = _events(owner, uid)[-3:]
    assert ("password_revoke", "ok") in events_b and ("password_reset", "denied") in events_b, events_b
    _record("api_revoke_vs_reissue_B_revoke_first", {
        "requests": 2, "revoke_status": 200, "reissue_status": 409, "version_before": v1, "final_auth_version": b[3],
        "final_status": b[0], "password_usable": b[5], "events": events_b,
    })  # fmt: skip
    # (C) 둘 다 읽은 뒤 동시에 — 하나만 200
    user2, _ = _provision(owner, "rv-race-reissue2")
    uid2 = user2["id"]
    _suspend(owner, uid2)
    v2 = _listed(owner, "rv-race-reissue2")["auth_version"]
    gate = _ReadGate(monkeypatch, 2)
    results: dict[str, int] = {}
    temps: list[str] = []

    def do_revoke() -> None:
        results["revoke"] = _revoke(owner, uid2, v2).status_code

    def do_reissue() -> None:
        res = owner.post(f"/users/{uid2}/password", json={"expected_auth_version": v2})
        results["reissue"] = res.status_code
        if res.status_code == 200:
            temps.append(res.json()["temp_password"])

    _run([threading.Thread(target=do_revoke), threading.Thread(target=do_reissue)])
    assert sorted(results.values()) == [200, 409], results
    c = _row(uid2)
    if results["revoke"] == 200:
        assert c == ("suspended", False, False, v2 + 1, True, False) and temps == []
        winner = "revoke"
    else:
        assert c == ("suspended", False, True, v2 + 1, False, True) and len(temps) == 1
        winner = "reissue"
    _record("api_revoke_vs_reissue_C_both_read_then_race", {
        "requests": 2, "responses": results, "winner": winner, "version_before": v2, "final_auth_version": c[3],
        "final_status": c[0], "must_change_after": c[2], "password_usable": c[5], "valid_temps": len(temps),
        **gate.summary(),
    })  # fmt: skip
