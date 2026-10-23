"""결과 파일 메모리 예산과 중단된 실행 정리 (docs/MEMORY.md '결과 파일 만들기').

- 묶기·통합의 결과 칸이 한도(MOAJWO_MAX_OUTPUT_CELLS)를 넘으면 실행 기록·결과·임시 파일을 만들기 전에 거절한다.
  (512MiB 격리 컨테이너에서 한도 안의 묶기 20만 행이 메모리 부족으로 죽고 '실행 중' 기록·임시 파일이 남았다)
- 기동할 때, 그 전에 '실행 중'으로 남은 실행은 '실패'와 이유로, 쓰다 만 임시 결과는 지운다. 진행 중일 수 있는 것은 두고,
  완성된 결과·다른 기록은 건드리지 않는다.
"""

import os
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import bundle, integration
from app import main as app_main
from app.db import SessionLocal
from app.integration import KST, OUTPUT_DIR
from app.models import IntegrationRun
from app.recovery import INTERRUPTED, recover_interrupted
from tests.test_accounts import owner  # noqa: F401
from tests.test_auth import _client, app_real_login  # noqa: F401
from tests.test_bundle import HEAD, MAP, _confirm, _plan, _upload, _xlsx

ROWS = {"ob-a": [["서울특별시", "종로구", 10], ["서울특별시", "중구", 20]], "ob-b": [["부산광역시", "중구", 30]]}


@pytest.fixture(scope="module")
def user(app_real_login, owner) -> TestClient:  # noqa: F811
    body = {"username": "ob-user", "display_name": "OB", "status": "active", "password": "pass-ob-user"}
    assert owner.post("/users", json=body).status_code == 201
    return _client(app_real_login, "ob-user", "pass-ob-user")


def _state() -> dict[str, Any]:
    """실행 기록 수·'실행 중' 수와 결과 폴더의 파일 (완성·임시)."""
    with SessionLocal() as db:
        runs = db.scalar(select(func.count(IntegrationRun.id)))
        running = db.scalar(select(func.count(IntegrationRun.id)).where(IntegrationRun.status == "running"))
    files = sorted(p.name for p in OUTPUT_DIR.glob("*")) if OUTPUT_DIR.is_dir() else []
    return {"runs": runs, "running": running, "files": files}


def _files(c: TestClient) -> list[int]:
    fids = []
    for name, rows in ROWS.items():
        fid = _upload(c, f"{name}.xlsx", _xlsx({name: [HEAD, *rows]}))
        _confirm(c, fid, MAP)
        fids.append(fid)
    return fids


def test_bundle_over_the_output_budget_is_refused_before_any_record(user, monkeypatch) -> None:
    fids = _files(user)
    plan = _plan(user, {"file_ids": fids})
    assert plan["ready"] and plan["limits"]["cells"] == bundle.MAX_OUTPUT_CELLS
    cells = plan["total_rows"] * (len(HEAD) + 2)  # 결과 시트마다 '원본 행'·'행 종류' 두 열이 더 붙음
    monkeypatch.setattr(bundle, "MAX_OUTPUT_CELLS", cells - 1)
    before = _state()
    over = _plan(user, {"file_ids": fids})
    assert not over["ready"] and any(f"결과에 넣을 칸이 {cells:,}개" in p for p in over["problems"]), over["problems"]
    res = user.post("/bundle/run", json={"file_ids": fids, "expected_basis": over["basis"]})
    assert res.status_code == 409 and "칸을 넘습니다" in str(res.json()["detail"]["problems"])
    assert _state() == before  # 실행 기록·결과·임시 파일 모두 없음
    monkeypatch.setattr(bundle, "MAX_OUTPUT_CELLS", cells)  # 경계: 한도와 같으면 만든다
    ok = _plan(user, {"file_ids": fids})
    assert ok["ready"], ok["problems"]
    assert user.post("/bundle/run", json={"file_ids": fids, "expected_basis": ok["basis"]}).status_code == 201


def test_integration_over_the_output_budget_is_refused_before_a_running_record(user, monkeypatch) -> None:
    fid = _files(user)[0]
    review = user.post("/reviews", json={"file_ids": [fid]}).json()
    for item in review["items"]:  # 확인 필요 항목: 후보가 있으면 첫 후보로 승인, 없으면 거부 (합성 자료)
        if item["category"] == "pending":
            options = (item.get("payload") or {}).get("candidates") or []
            choice = (options[0].get("code") or options[0].get("key")) if options else None
            decision = {"decision": "approved", "choice": choice} if choice else {"decision": "rejected"}
            user.post(f"/reviews/{review['id']}/items/{item['id']}/decision", json=decision)
    done = user.post(f"/reviews/{review['id']}/complete")
    assert done.status_code == 200, done.text
    monkeypatch.setattr(integration, "MAX_OUTPUT_CELLS", 1)
    before = _state()
    res = user.post(f"/reviews/{review['id']}/integrate", json={})
    assert res.status_code == 409 and "칸을 넘습니다" in res.json()["detail"]
    assert _state() == before
    monkeypatch.undo()
    assert user.post(f"/reviews/{review['id']}/integrate", json={}).status_code == 201


def test_startup_recovery_marks_interrupted_runs_failed_and_removes_partial_files() -> None:
    process_start = datetime.now(KST) - timedelta(minutes=1)
    with SessionLocal() as db:
        stuck = IntegrationRun(status="running", file_ids="[]", started_at=process_start - timedelta(minutes=5))
        live = IntegrationRun(status="running", file_ids="[]", started_at=process_start + timedelta(seconds=30))
        done = IntegrationRun(status="done", file_ids="[]", started_at=process_start - timedelta(minutes=9))
        db.add_all([stuck, live, done])
        db.commit()
        ids = (stuck.id, live.id, done.id)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stale, fresh, kept = (
        OUTPUT_DIR / "tmpstale.xlsx.tmp",
        OUTPUT_DIR / "tmpfresh.xlsx.tmp",
        OUTPUT_DIR / "완성_9999.xlsx",
    )
    for p in (stale, fresh, kept):
        p.write_bytes(b"partial")
    old = (process_start - timedelta(minutes=5)).timestamp()
    os.utime(stale, (old, old))
    os.utime(kept, (old, old))
    try:
        with SessionLocal() as db:
            assert recover_interrupted(db, process_start) == {"runs": 1, "temp_files": 1}
        with SessionLocal() as db:
            s, lv, d = (db.get(IntegrationRun, i) for i in ids)
            assert (s.status, s.error) == ("failed", INTERRUPTED) and s.finished_at is not None
            assert lv.status == "running" and d.status == "done"  # 이 프로세스 뒤에 시작한 실행·완료된 실행은 그대로
        assert not stale.exists() and fresh.exists() and kept.exists()  # 완성된 결과는 이름이 .tmp가 아니다
    finally:
        with SessionLocal() as db:
            for i in ids:
                db.delete(db.get(IntegrationRun, i))
            db.commit()
        for p in (stale, fresh, kept):
            p.unlink(missing_ok=True)


def test_recovery_runs_at_startup_and_a_failure_does_not_stop_the_server(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(app_main, "recover_interrupted", lambda db: calls.append("recovered"))
    with TestClient(app_main.app):
        pass
    assert calls == ["recovered"]

    def broken(db: Any) -> None:
        raise app_main.SQLAlchemyError("DB에 닿지 못함")

    monkeypatch.setattr(app_main, "recover_interrupted", broken)
    with TestClient(app_main.app) as c:  # 기동은 계속된다
        assert c.get("/health").status_code in (200, 503)
