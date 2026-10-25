"""결과 파일 메모리 예산과 중단된 실행 정리 (docs/MEMORY.md '결과 파일 만들기').

- 묶기·통합의 결과 칸이 한도(MOAJWO_MAX_OUTPUT_CELLS)를 넘으면 실행 기록·결과·임시 파일을 만들기 전에 거절한다.
  (512MiB 격리 컨테이너에서 한도 안의 묶기 20만 행이 메모리 부족으로 죽고 '실행 중' 기록·임시 파일이 남았다)
- 쌓는 중에도 칸 수를 본다 (넘으면 남은 파일을 읽지 않음).
- 결과 하나의 글자 수 합(MOAJWO_MAX_OUTPUT_TEXT_CHARS)도 같은 때 본다 — 칸 수는 글자 양을 보지 않는다 (512MiB 격리
  컨테이너에서 칸 몇천 개·약 6,390만 자인 긴 글자 파일 넷의 통합이 수정 전·후 모두 메모리 부족으로 죽었다).
- 결과 만들기 차례(동시 1개, app/output_slot.py): 다른 결과를 만드는 중이면 기다리지 않고 거절하고 아무것도
  남기지 않는다.
멈춘 실행 정리는 test_run_recovery.py, 완료 경계는 test_run_completion.py.
"""

import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import bundle, integration
from app.db import SessionLocal
from app.integration import OUTPUT_DIR
from app.models import IntegrationRun
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
    # 담당 식별 표시(.disk-id·.workers)는 결과가 아니다
    files = sorted(p.name for p in OUTPUT_DIR.glob("*") if not p.name.startswith(".")) if OUTPUT_DIR.is_dir() else []
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


def _review_of(c: TestClient, fids: list[int]) -> int:
    review = c.post("/reviews", json={"file_ids": fids}).json()
    for item in review["items"]:  # 확인 필요 항목: 후보가 있으면 첫 후보로 승인, 없으면 거부 (합성 자료)
        if item["category"] == "pending":
            options = (item.get("payload") or {}).get("candidates") or []
            choice = (options[0].get("code") or options[0].get("key")) if options else None
            decision = {"decision": "approved", "choice": choice} if choice else {"decision": "rejected"}
            c.post(f"/reviews/{review['id']}/items/{item['id']}/decision", json=decision)
    done = c.post(f"/reviews/{review['id']}/complete")
    assert done.status_code == 200, done.text
    return review["id"]


def test_integration_stops_while_building_once_the_budget_is_passed(user, monkeypatch) -> None:
    """칸 예산은 결과를 다 쌓은 뒤가 아니라 쌓는 중에도 본다 — 넘으면 남은 파일은 읽지 않는다 (실행 기록 전)."""
    from app import files as files_module

    fids = []
    for n in range(3):
        rows = [["서울특별시", f"구{n}{i}", i] for i in range(40)]
        fid = _upload(user, f"ob-build-{n}.xlsx", _xlsx({f"build{n}": [HEAD, *rows]}))
        _confirm(user, fid, MAP)
        fids.append(fid)
    rid = _review_of(user, fids)
    reads: list[int] = []
    real = files_module.load_parsed

    def counting(session: Any, file_id: int) -> Any:
        reads.append(file_id)
        return real(session, file_id)

    monkeypatch.setattr(integration, "load_parsed", counting)
    monkeypatch.setattr(integration, "MAX_OUTPUT_CELLS", 100)  # 첫 파일의 행만으로 넘는다
    before = _state()
    res = user.post(f"/reviews/{rid}/integrate", json={})
    assert res.status_code == 409 and "칸을 넘습니다" in res.json()["detail"], res.text
    assert len(set(reads)) == 1, f"한도를 넘은 뒤에도 파일을 더 읽었다: {reads}"
    assert _state() == before  # 실행 기록·결과·임시 파일 없음


def _long_text_files(c: TestClient, prefix: str, n: int) -> list[int]:
    """칸은 몇십 개지만 글자가 많은 파일 (긴 '비고' 열 — 매핑하지 않은 열)."""
    fids = []
    for k in range(n):
        rows = [["서울특별시", f"구{k}{i}", i, "가" * 200 + f"-{prefix}{k}{i}"] for i in range(10)]
        fid = _upload(c, f"{prefix}-{k}.xlsx", _xlsx({f"{prefix}{k}": [[*HEAD, "비고"], *rows]}))
        _confirm(c, fid, MAP)
        fids.append(fid)
    return fids


def test_bundle_with_more_text_than_the_budget_is_refused_before_any_record(user, monkeypatch) -> None:
    fids = _long_text_files(user, "ob-text", 2)
    plan = _plan(user, {"file_ids": fids})
    assert plan["ready"] and plan["limits"]["text_chars"] == bundle.MAX_OUTPUT_TEXT_CHARS
    cells = plan["total_rows"] * (len(HEAD) + 1 + 2)
    assert cells < bundle.MAX_OUTPUT_CELLS  # 칸 예산에는 걸리지 않는 크기
    monkeypatch.setattr(bundle, "MAX_OUTPUT_TEXT_CHARS", 2_000)  # 긴 글자 20칸만으로 4천 자가 넘는다
    before = _state()
    over = _plan(user, {"file_ids": fids})
    assert not over["ready"] and any("결과에 넣을 글자가" in p for p in over["problems"]), over["problems"]
    res = user.post("/bundle/run", json={"file_ids": fids, "expected_basis": over["basis"]})
    assert res.status_code == 409 and "자를 넘습니다" in str(res.json()["detail"]["problems"])
    assert _state() == before  # 실행 기록·결과·임시 파일 모두 없음
    monkeypatch.undo()
    ok = _plan(user, {"file_ids": fids})
    assert user.post("/bundle/run", json={"file_ids": fids, "expected_basis": ok["basis"]}).status_code == 201


def test_integration_with_more_text_than_the_budget_stops_before_a_running_record(user, monkeypatch) -> None:
    """통합은 쌓는 중에 결과 칸 글자와 변경 기록(앞뒤 값·이유)의 글자를 함께 센다 — 넘으면 실행 기록 전에 409."""
    rid = _review_of(user, _long_text_files(user, "ob-itext", 1))
    monkeypatch.setattr(integration, "MAX_OUTPUT_TEXT_CHARS", 1_000)
    before = _state()
    res = user.post(f"/reviews/{rid}/integrate", json={"include_unmapped": True})
    assert res.status_code == 409 and "결과에 넣을 글자가" in res.json()["detail"], res.text
    assert _state() == before
    monkeypatch.undo()
    assert user.post(f"/reviews/{rid}/integrate", json={"include_unmapped": True}).status_code == 201


def test_a_second_result_is_refused_while_one_is_being_made_and_leaves_nothing(user, monkeypatch) -> None:
    """결과 만들기 차례(동시 1개): 다른 결과를 만드는 동안 온 요청은 기다리지 않고 503 — 실행 기록·파일을 만들지 않는다.
    차례는 끝나면(예외 포함) 돌려준다."""
    from app.output_slot import output_slot

    fids = _files(user)
    plan = _plan(user, {"file_ids": fids})
    links = [{"file_id": f, "sheet_id": "s0", "rows_are_records": True} for f in fids]
    summary = {"field_key": "population", "aggregates": ["sum"], "links": links}
    held, release = threading.Event(), threading.Event()

    def busy() -> None:  # 다른 요청이 결과를 만드는 중인 것처럼 차례를 쥔다
        with output_slot():
            held.set()
            release.wait(30)

    th = threading.Thread(target=busy, daemon=True)
    th.start()
    assert held.wait(10)
    before = _state()
    try:
        for res in (
            user.post("/bundle/run", json={"file_ids": fids, "expected_basis": plan["basis"]}),
            user.post("/bundle/plan", json={"file_ids": fids, "summary": {"items": [summary]}}),
        ):
            assert res.status_code == 503, res.text
            detail = res.json()["detail"]
            assert detail["code"] == "output_busy" and "시작하지 않았습니다" in detail["message"]
            assert res.headers.get("retry-after") == "10"
        assert _state() == before  # 기록·결과·임시 파일 없음
        assert user.post("/bundle/plan", json={"file_ids": fids}).status_code == 200  # 요약 없는 계획은 차례 밖
    finally:
        release.set()
        th.join(10)
    monkeypatch.setattr(bundle, "MAX_OUTPUT_CELLS", 0)  # 한도에 걸려 거절돼도 차례는 돌려준다
    refused = _plan(user, {"file_ids": fids})
    assert user.post("/bundle/run", json={"file_ids": fids, "expected_basis": refused["basis"]}).status_code == 409
    monkeypatch.undo()
    ok = _plan(user, {"file_ids": fids})
    assert user.post("/bundle/run", json={"file_ids": fids, "expected_basis": ok["basis"]}).status_code == 201


def test_output_slot_is_reentrant_in_one_thread_and_shared_across_threads() -> None:
    from app.output_slot import OutputBusy, output_slot

    with output_slot():
        with output_slot():  # 같은 스레드가 다시 잡아도(묶기 계획 → 만들기) 기다리지 않는다
            pass
        seen: list[str] = []

        def other() -> None:
            try:
                with output_slot():
                    seen.append("got")
            except OutputBusy:
                seen.append("busy")

        th = threading.Thread(target=other)
        th.start()
        th.join(10)
        assert seen == ["busy"]
    with output_slot():  # 다 돌려준 뒤에는 다시 잡힌다
        pass


def test_summary_is_not_evaluated_when_the_result_cannot_be_made(user, monkeypatch) -> None:
    """통계 요약 평가는 항목마다 대상 행 전부를 읽어 메모리를 크게 쓴다 — 결과가 이미 크기 한도를 넘으면 평가하지 않고,
    형식이 틀린 항목은 다른 항목을 평가하기 전에 거절한다 (512MiB에서 한도 넘는 요약 계획이 메모리 부족으로 죽었다)."""
    from app import analysis

    fids = _files(user)
    links = [{"file_id": f, "sheet_id": "s0", "rows_are_records": True} for f in fids]
    item = {"field_key": "population", "aggregates": ["sum"], "links": links}
    calls: list[int] = []
    real = analysis.evaluate

    def counting(*a: Any, **k: Any) -> Any:
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(analysis, "evaluate", counting)
    monkeypatch.setattr(bundle, "MAX_OUTPUT_CELLS", 1)
    plan = _plan(user, {"file_ids": fids, "summary": {"items": [item]}})
    assert not plan["ready"] and any("통계 요약은 판정하지 않았습니다" in p for p in plan["problems"]), plan
    assert calls == [], "한도를 넘은 결과의 통계 요약을 평가했다"
    monkeypatch.setattr(bundle, "MAX_OUTPUT_CELLS", 1_000_000)
    bad = {"field_key": "execution_rate", "aggregates": ["sum"], "links": links}  # 합계를 낼 수 없는 항목
    res = user.post("/bundle/plan", json={"file_ids": fids, "summary": {"items": [item, bad]}})
    assert res.status_code == 422 and calls == [], (res.status_code, calls)
    assert _plan(user, {"file_ids": fids, "summary": {"items": [item]}})["ready"] and calls == [1]
