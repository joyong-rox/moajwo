"""실제 앱 요청 경로(TestClient — 세션 DB가 SQLite든 PostgreSQL이든 그 DB)에서의 동시 요청 경합 (R1·R4).

- 동기화: 해시 함수에 둔 barrier로 두 요청이 모두 검사(현재 비밀번호 확인·거절 조건)를 지난 뒤 **조건부 UPDATE에서
  겹치게** 한다. sleep·실행 순서의 우연에 기대지 않는다. 각 요청은 별도 스레드에서 돌고 요청마다 풀에서 꺼낸 서로 다른
  DB 연결을 쓴다 (barrier 안에서 풀의 checked-out 연결 수를 기록 — 둘째 도착 때 2 이상).
- 확인 수준: 함수 직접 호출이 아니라 HTTP 요청(쿠키 인증·본문 검증·라우터)을 지난 경로. 단, 동기화 지점은 시험용
  monkeypatch다.
- 기록: 환경 변수 MOAJWO_RACE_RECORD가 가리키는 파일에 JSON 한 줄씩 — 실제 dialect·연결 수·요청 수·응답·최종 인증 버전·
  세션 수·이력 수. 접속 문자열·비밀번호·해시는 쓰지 않는다.
"""

import json
import os
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import auth, auth_api
from tests.conftest import db_rows
from tests.test_accounts import NEW_PASSWORD, _first_change, _listed, _provision, owner  # noqa: F401
from tests.test_auth import _client, app_real_login  # noqa: F401


def _dialect() -> str:
    from app.db import engine

    return engine.dialect.name


def _checked_out() -> int | None:
    from app.db import engine

    fn = getattr(engine.pool, "checkedout", None)
    return fn() if callable(fn) else None


def _record(name: str, data: dict[str, Any]) -> None:
    path = os.environ.get("MOAJWO_RACE_RECORD")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"test": name, "dialect": _dialect(), **data}, ensure_ascii=False) + "\n")


class _Barrier:
    """처음 n번의 해시 호출을 한 지점에 모은다 (그 뒤 호출은 그냥 지나감).
    도착 때마다 풀의 checked-out 연결 수를 적는다."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, module: Any, n: int) -> None:
        self.barrier = threading.Barrier(n)
        self.seen: list[int | None] = []
        self.lock = threading.Lock()
        real = module.hash_password

        def gated(password: str) -> str:
            with self.lock:
                first = len(self.seen) < n
                if first:
                    self.seen.append(_checked_out())
            if first:
                self.barrier.wait(timeout=20)
            return real(password)

        monkeypatch.setattr(module, "hash_password", gated)

    def summary(self) -> dict[str, Any]:
        return {"gathered": len(self.seen), "pool_checked_out_at_arrival": self.seen}


def _events(owner_client: TestClient, uid: int) -> list[tuple[str, str]]:
    rows = owner_client.get("/admin/audit", params={"target_id": uid}).json()
    return [(e["action"], e["result"]) for e in reversed(rows)]


def _sessions(uid: int) -> int:
    return db_rows("select count(*) from auth_sessions where user_id = ?", uid)[0][0]


def _run(threads: list[threading.Thread]) -> None:
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not any(t.is_alive() for t in threads), "경합 요청이 끝나지 않음"


def test_api_concurrent_reissue_same_version_applies_once(app_real_login, owner, monkeypatch) -> None:  # noqa: F811
    """같은 인증 버전으로 동시에 두 번 재발급 → 성공 하나·409 하나.
    버전 +1, 기존 세션 종료, 성공한 임시 비밀번호만 유효, 성공 이력 1건·거절 이력 1건."""
    user, temp = _provision(owner, "race-reissue")
    uid = user["id"]
    worker = _first_change(app_real_login, "race-reissue", temp)  # 본인 비밀번호 사용자 — 세션 1개
    version = _listed(owner, "race-reissue")["auth_version"]
    gate = _Barrier(monkeypatch, auth, 2)
    results: list[tuple[int, dict]] = []

    def attempt() -> None:
        res = owner.post(f"/users/{uid}/password", json={"expected_auth_version": version})
        results.append((res.status_code, res.json()))

    _run([threading.Thread(target=attempt) for _ in range(2)])
    assert sorted(r[0] for r in results) == [200, 409], results
    assert (
        gate.seen and max(x for x in gate.seen if x is not None) >= 2 if any(x is not None for x in gate.seen) else True
    )
    temp2 = next(body["temp_password"] for code, body in results if code == 200)
    after = _listed(owner, "race-reissue")
    assert after["auth_version"] == version + 1 and after["must_change_password"] is True
    assert worker.get("/files").status_code == 401  # 기존 세션 종료
    assert _sessions(uid) == 0
    login = {"username": "race-reissue", "password": NEW_PASSWORD}
    assert _client(app_real_login).post("/auth/login", json=login).status_code == 401  # 이전 본인 비밀번호 무효
    restricted = _client(app_real_login)
    assert restricted.post("/auth/login", json={"username": "race-reissue", "password": temp2}).status_code == 200
    assert restricted.get("/files").status_code == 403
    events = _events(owner, uid)
    assert events.count(("password_reset", "ok")) == 1 and events.count(("password_reset", "denied")) == 1, events
    _record("api_concurrent_reissue_same_version", {
        "requests": 2, "responses": sorted(r[0] for r in results), "final_auth_version": after["auth_version"],
        "version_before": version, "sessions_after": _sessions(uid), "events": events, **gate.summary(),
    })  # fmt: skip


def test_api_concurrent_first_change_applies_once(app_real_login, owner, monkeypatch) -> None:  # noqa: F811
    """임시 비밀번호로 두 기기에서 로그인한 사용자가 동시에 최초 변경 → 성공(204) 하나, 나머지는 충돌(409)이나
    인증 거절(401). 500·두 번 성공 없음. 새 비밀번호 하나만 유효, must_change 해제, 버전 +1, 세션 0,
    최초 변경 이력 1건."""
    user, temp = _provision(owner, "race-first")
    uid = user["id"]
    version = _listed(owner, "race-first")["auth_version"]
    clients = []
    for _ in range(2):
        c = _client(app_real_login)
        assert c.post("/auth/login", json={"username": "race-first", "password": temp}).status_code == 200
        clients.append(c)
    assert _sessions(uid) == 2
    gate = _Barrier(monkeypatch, auth_api, 2)
    results: list[int] = []

    def attempt(i: int) -> None:
        body = {
            "current_password": temp,
            "new_password": f"chosen-pass-{i}",
            "new_password_confirm": f"chosen-pass-{i}",
        }
        results.append(clients[i].post("/auth/password", json=body).status_code)

    _run([threading.Thread(target=attempt, args=(i,)) for i in range(2)])
    assert sorted(results) in ([204, 401], [204, 409]), results
    after = _listed(owner, "race-first")
    assert after["auth_version"] == version + 1 and after["must_change_password"] is False
    assert _sessions(uid) == 0
    valid = [
        _client(app_real_login)
        .post("/auth/login", json={"username": "race-first", "password": f"chosen-pass-{i}"})
        .status_code
        for i in range(2)
    ]
    assert sorted(valid) == [200, 401], valid  # 새 비밀번호 하나만 유효
    assert (
        _client(app_real_login).post("/auth/login", json={"username": "race-first", "password": temp}).status_code
        == 401
    )
    events = _events(owner, uid)
    assert events.count(("first_password_change", "ok")) == 1, events
    _record("api_concurrent_first_change", {
        "requests": 2, "responses": sorted(results), "final_auth_version": after["auth_version"],
        "version_before": version, "sessions_after": 0, "valid_new_passwords": valid.count(200), "events": events,
        **gate.summary(),
    })  # fmt: skip


def test_api_reissue_vs_suspend_keeps_suspended_state(app_real_login, owner, monkeypatch) -> None:  # noqa: F811
    """재발급이 검사를 지나 저장 직전에 멈춘 사이 이용 중지가 먼저 반영되면, 재발급은 409(버전이 바뀜)이고 계정은
    중지 그대로다 — 활성으로 돌아가지 않고, 중지 전 세션이 되살아나지 않으며, 새 임시 비밀번호는 생기지 않는다
    (재개 뒤 기존 비밀번호로 로그인)."""
    user, temp = _provision(owner, "race-stop")
    uid = user["id"]
    worker = _first_change(app_real_login, "race-stop", temp)
    version = _listed(owner, "race-stop")["auth_version"]
    verified, proceed = threading.Event(), threading.Event()
    real = auth.hash_password

    def gated(password: str) -> str:  # 재발급의 새 임시 비밀번호 해시 — 거절 조건·버전 읽기를 지난 뒤의 지점
        if not verified.is_set():
            verified.set()
            assert proceed.wait(20), "재개 신호 없음"
        return real(password)

    monkeypatch.setattr(auth, "hash_password", gated)
    outcome: dict[str, Any] = {}

    def reissue() -> None:
        res = owner.post(f"/users/{uid}/password", json={"expected_auth_version": version})
        outcome["status"], outcome["body"] = res.status_code, res.json()

    t = threading.Thread(target=reissue)
    t.start()
    assert verified.wait(20)
    stopped = owner.post(f"/users/{uid}/suspend", json={"reason": "경합 시험"})  # 재발급이 멈춘 사이 중지가 먼저 커밋
    assert stopped.status_code == 200 and stopped.json()["status"] == "suspended"
    proceed.set()
    t.join(60)
    assert outcome["status"] == 409, outcome
    after = _listed(owner, "race-stop")
    assert after["status"] == "suspended" and after["must_change_password"] is False
    assert after["auth_version"] == version + 1  # 중지 한 번만 반영됨
    assert worker.get("/files").status_code == 401  # 중지 전 세션은 끝남
    blocked = _client(app_real_login).post("/auth/login", json={"username": "race-stop", "password": NEW_PASSWORD})
    assert blocked.status_code == 403 and "이용이 중지된" in blocked.json()["detail"]
    events = _events(owner, uid)
    assert events[-2:] == [("suspend", "ok"), ("password_reset", "denied")] or events[-2:] == [
        ("password_reset", "denied"), ("suspend", "ok"),
    ], events  # fmt: skip
    assert owner.post(f"/users/{uid}/resume", json={}).json()["status"] == "active"
    assert worker.get("/files").status_code == 401  # 재개해도 옛 세션은 되살아나지 않는다
    assert _client(app_real_login, "race-stop", NEW_PASSWORD).get("/files").status_code == 200  # 기존 비밀번호 그대로
    _record("api_reissue_vs_suspend", {
        "requests": 2, "reissue_status": outcome["status"], "final_status": after["status"],
        "final_auth_version": after["auth_version"], "version_before": version, "events": events,
    })  # fmt: skip
