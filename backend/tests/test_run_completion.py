"""결과 파일 완료 경계 — 묶기·직접 통합·자동화 통합·공통 key 연결 네 흐름에 오류를 주입한다 (함수 대역: os.replace·
os.chmod·Session.commit·Session.execute를 감싼다. 실제 프로세스 종료는 test_run_recovery.py).

- 이름을 바꾼 직후(권한 바꾸기 실패) · 완료 commit 직전(commit 실패, 반영 안 됨) · commit 응답 유실(반영됨) →
  완성된 결과 파일을 지우지 않고 결과가 한 번 done이 된다 (201).
- 응답 유실 + DB에 닿지 못함 → 실패가 아니라 '확인하지 못했습니다'(503, run_uncertain)로 답하고, 파일은 그대로, DB의
  done도 그대로다 (뒤에 내려받기 됨).
- 이름을 바꾸기 전 실패(같은 이름 있음·이름 바꾸기 실패) → 실패로 기록하고, 요약은 시작 때 값·변경 수 0·변경 기록 없음,
  임시 파일 없음, 원래 있던 파일은 그대로.
같은 시험을 고치기 전 코드에서도 돌릴 수 있게 새 모듈(app.runlease·app.recovery)은 그것만 보는 시험 안에서 가져온다.
합성 자료만 쓴다.
"""

import hashlib
import io
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import DATA_DIR

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
OUTPUTS = DATA_DIR / "outputs"
HEADER = ["기관명", "담당 업무 구분", "집행액", "추진 실적 메모", "세부 사업 번호", "시도"]
SEPT = [
    HEADER,
    ["행안부", "재정", "1,000", "정상", "S-1", "서울"],
    ["기상청", "총무", 2500, "검토중", "S-2", "부산광역시"],
]
BUNDLE_HEAD = ["시도", "시군구", "인구수"]
BUNDLE_MAP = {"시도": "sido", "시군구": "sigungu", "인구수": "population"}
FLOWS = ("bundle", "integrate", "automation", "keyjoin")
MODES = ("chmod_after_rename", "before_commit", "lost_response", "lost_then_down")


def _xlsx(rows: list[list], title: str | None = None, sheet: str = "현황") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    if title:
        ws.append([title])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def flows():
    """세 흐름을 돌리는 클라이언트 (서버 오류도 응답으로 받는다 — 고치기 전 코드의 DB 오류를 그대로 보려고)."""
    from app import automation, files, integration, review, validation_api
    from app.db import Base, engine
    from app.main import app
    from app.mapping import MappingEngine, load_dictionary

    def no_llm(system: str, user: str, schema: dict) -> dict[str, Any]:
        return {"key": "none", "confidence": 0, "reason": "해당 없음"}

    mapping_engine = MappingEngine(load_dictionary(), None, no_llm)
    patch = pytest.MonkeyPatch()
    for module in (files, validation_api, review, integration, automation):
        patch.setattr(module, "get_engine", lambda: mapping_engine)
    Base.metadata.create_all(engine)
    before_codes = {r[0] for r in _sql("select code from glossary_entries")}
    before_aliases = {r[0] for r in _sql("select id from glossary_aliases")}
    with TestClient(app, raise_server_exceptions=False) as c:
        glossary_csv = (
            "코드,정식명칭,유형,시도코드,시군구코드,별칭\n"
            "1741000,행정안전부,중앙행정기관,,,행안부\n"
            "1360000,기상청,중앙행정기관,,,\n"
            "6110000,서울특별시,광역자치단체,11,,\n"
            "6260000,부산광역시,광역자치단체,26,,\n"
        )
        res = c.post("/glossary/import", files=[("file", ("g.csv", glossary_csv.encode("utf-8"), "text/csv"))])
        assert res.status_code == 200, res.text
        yield c
    patch.undo()
    # 같은 시험 DB를 쓰는 다른 모듈에 영향이 없게
    for (alias_id,) in _sql("select id from glossary_aliases"):
        if alias_id not in before_aliases:
            _sql("delete from glossary_aliases where id = :i", i=alias_id)
    for (code,) in _sql("select code from glossary_entries"):
        if code not in before_codes:
            _sql("delete from glossary_entries where code = :c", c=code)


def _sql(sql: str, **params: Any) -> list[Any]:
    """앱과 같은 DB에 바로 (SQLite·PostgreSQL 모두)."""
    from sqlalchemy import text

    from app.db import engine

    with engine.begin() as con:
        res = con.execute(text(sql), params)
        return list(res) if res.returns_rows else []


def _upload(c: TestClient, name: str, data: bytes) -> int:
    res = c.post("/files", files=[("files", (name, data, XLSX))])
    assert res.status_code == 200 and res.json()[0]["id"], res.text
    return res.json()[0]["id"]


def _approve_all(c: TestClient, review: dict) -> None:
    for item in [i for i in review["items"] if i["category"] == "pending" and i["decision"] is None]:
        candidates = item["payload"].get("candidates") or []
        if item["kind"] == "mapping":
            choice = item["payload"].get("field_key")
        else:
            choice = candidates[0].get("code") if candidates else None
        decision = "approved" if (choice or item["kind"] == "pii") else "rejected"
        body = {"decision": decision, "choice": choice}
        res = c.post(f"/reviews/{review['id']}/items/{item['id']}/decision", json=body)
        assert res.status_code == 200, res.text


def _ready_review(c: TestClient, tag: str) -> int:
    review = c.post("/reviews", json={"file_ids": [_upload(c, f"{tag}.xlsx", _xlsx(SEPT))]}).json()
    _approve_all(c, review)
    done = c.post(f"/reviews/{review['id']}/complete")
    assert done.status_code == 200, done.text
    return review["id"]


def _prepare(c: TestClient, flow: str, tag: str) -> Callable[[], Any]:
    """그 흐름의 '결과 만들기' 요청을 준비하고(결과는 아직 만들지 않음) 보낼 함수를 돌려준다."""
    if flow == "bundle":
        rows = [BUNDLE_HEAD, ["서울특별시", "종로구", 10], ["서울특별시", "중구", 20]]
        fid = _upload(c, f"{tag}.xlsx", _xlsx(rows, "주민등록 인구 현황 2026년 9월 (단위: 명)", sheet=tag))
        cols = {col["name"]: col["column_id"] for col in c.get(f"/files/{fid}/preview?sheet=s0").json()["columns"]}
        items = [{"sheet_id": "s0", "column_id": cols[n], "field_key": k} for n, k in BUNDLE_MAP.items()]
        assert c.put(f"/files/{fid}/mapping/decisions", json={"decisions": items}).status_code == 200
        plan = c.post("/bundle/plan", json={"file_ids": [fid]}).json()
        assert plan["ready"], plan["problems"]
        return lambda: c.post("/bundle/run", json={"file_ids": [fid], "expected_basis": plan["basis"]})
    if flow == "integrate":
        rid = _ready_review(c, tag)
        return lambda: c.post(f"/reviews/{rid}/integrate")
    if flow == "keyjoin":  # 공통 key 연결: 두 파일을 사번으로 (요청마다 새 request_id)
        a = _upload(c, f"{tag}-a.xlsx", _xlsx([["사번", "부서"], ["001", "총무"], ["002", "기획"]], sheet=f"{tag}a"))
        b = _upload(c, f"{tag}-b.xlsx", _xlsx([["사번", "건수"], ["001", 10], ["003", 30]], sheet=f"{tag}b"))
        inputs = [
            {"file_id": f, "sheet_id": "s0", "key_columns": ["c0"], "columns": [{"column_id": "c1"}]} for f in (a, b)
        ]
        body = {"inputs": inputs, "scope": "base", "base": {"file_id": a, "sheet_id": "s0"}}
        plan = c.post("/keyjoin/plan", json=body).json()
        assert plan["ready"], plan
        return lambda: c.post("/keyjoin/run", json={**body, "expected_basis": plan["basis"],
                                                     "request_id": uuid.uuid4().hex})  # fmt: skip
    first = _ready_review(c, f"{tag}-first")  # 자동화를 만들 완료 실행
    done = c.post(f"/reviews/{first}/integrate")
    assert done.status_code == 201, done.text
    automation = c.post(f"/runs/{done.json()['id']}/automation", json={"name": f"자동화 {tag}"}).json()
    fid = _upload(c, f"{tag}-next.xlsx", _xlsx(SEPT))
    return lambda: c.post(f"/automations/{automation['id']}/run", json={"file_ids": [fid]})


def _run_of(res: Any) -> int:
    body = res.json()
    if res.status_code == 503:
        return body["detail"]["run_id"]
    return body["run"]["id"] if "run" in body else body["id"]


def _row(run_id: int) -> dict[str, Any]:
    from sqlalchemy import text

    from app.db import engine

    with engine.connect() as con:
        row = con.execute(text("select * from runs where id = :i"), {"i": run_id}).mappings().one()
        changes = con.execute(text("select count(*) from run_changes where run_id = :i"), {"i": run_id}).scalar()
    return {**dict(row), "changes": changes}


def _latest_run_id() -> int:
    return _sql("select max(id) from runs")[0][0] or 0


def _files_of(run_id: int) -> tuple[list[Path], list[Path]]:
    """그 실행 번호의 최종 결과 파일과 임시 파일 (이름에 실행 번호가 들어 있다)."""
    finals = [p for p in OUTPUTS.glob(f"*_{run_id:04d}_*.xlsx")]
    temps = [p for p in OUTPUTS.glob(f"*_{run_id:04d}_*.tmp")]
    return finals, temps


class Inject:
    """완료 경계 오류 주입. 결과 폴더의 최종 .xlsx로 이름을 바꾼 뒤에만 한 번 일어난다."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
        self.mode, self.renamed, self.fired, self.down = mode, None, False, False
        real_replace, real_chmod, real_commit, real_execute = os.replace, os.chmod, Session.commit, Session.execute

        def replace(src: Any, dst: Any, *a: Any, **k: Any) -> None:
            real_replace(src, dst, *a, **k)
            if self.renamed is None and str(dst).endswith(".xlsx") and "outputs" in str(dst):
                self.renamed = Path(dst)

        def chmod(path: Any, *a: Any, **k: Any) -> None:
            if mode == "chmod_after_rename" and not self.fired and self.renamed and Path(path) == self.renamed:
                self.fired = True
                raise PermissionError("injected: chmod after rename")
            real_chmod(path, *a, **k)

        def commit(session: Session) -> None:
            if mode in ("before_commit", "lost_response", "lost_then_down") and self.renamed and not self.fired:
                self.fired = True
                if mode != "before_commit":
                    real_commit(session)  # 반영됐지만 응답을 잃음
                    self.down = mode == "lost_then_down"
                raise OperationalError("COMMIT", {}, ConnectionResetError("injected: connection lost"))
            real_commit(session)

        def execute(session: Session, *a: Any, **k: Any) -> Any:
            if self.down:
                raise OperationalError("SELECT", {}, ConnectionRefusedError("injected: database unreachable"))
            return real_execute(session, *a, **k)

        monkeypatch.setattr(os, "replace", replace)
        monkeypatch.setattr(os, "chmod", chmod)
        monkeypatch.setattr(Session, "commit", commit)
        monkeypatch.setattr(Session, "execute", execute)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("flow", FLOWS)
def test_completion_boundary_keeps_the_finished_file_and_completes_once(
    flows: TestClient, monkeypatch: pytest.MonkeyPatch, flow: str, mode: str
) -> None:
    send = _prepare(flows, flow, f"cb-{flow}-{mode}")
    before = _latest_run_id()
    inject = Inject(monkeypatch, mode)
    res = send()
    inject.down = False  # DB가 돌아옴
    monkeypatch.undo()
    assert inject.fired, "주입 지점에 닿지 않았다"
    run_id = _run_of(res) if res.status_code in (201, 503) else _latest_run_id()
    assert run_id > before
    row = _row(run_id)
    finals, temps = _files_of(run_id)
    # 완성된 결과 파일은 지우지 않고, 기록은 한 번 done (실패 없음)
    assert [p.name for p in finals] == [inject.renamed.name], (res.status_code, res.text, row["status"], row["error"])
    assert row["status"] == "done" and row["error"] is None, (row["status"], row["error"])
    assert row["output_sha256"] == hashlib.sha256(finals[0].read_bytes()).hexdigest()
    assert not temps
    if mode == "lost_then_down":  # 판정할 수 없을 때는 실패가 아니라 '확인하지 못했습니다'
        assert res.status_code == 503, res.text
        detail = res.json()["detail"]
        assert detail["code"] == "run_uncertain" and "실패" not in detail["message"]
        assert "다시 실행" not in detail["message"]
    else:
        assert res.status_code == 201, res.text
    assert flows.get(f"/runs/{run_id}/download").status_code == 200


@pytest.mark.parametrize("flow", ("bundle", "integrate", "keyjoin"))
def test_same_name_result_is_never_overwritten_and_the_run_fails_cleanly(
    flows: TestClient, monkeypatch: pytest.MonkeyPatch, flow: str
) -> None:
    from app import bundle, integration, keyjoin

    send = _prepare(flows, flow, f"same-{flow}")
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    taken = OUTPUTS / f"이미있음_{flow}_9999_20260101_000000.xlsx"
    taken.write_bytes(b"existing result")
    module = {"bundle": bundle, "integrate": integration, "keyjoin": keyjoin}[flow]
    monkeypatch.setattr(module, "_output_path", lambda run_id, now: taken)
    try:
        res = send()
        assert res.status_code == 409 and "같은 이름의 결과 파일이 이미 있습니다" in res.text, res.text
        row = _row(_latest_run_id())
        assert row["status"] == "failed" and row["output_path"] is None and row["output_sha256"] is None
        assert row["change_count"] == 0 and row["changes"] == 0
        assert "summary_items" not in row["summary"] and "columns" not in json.loads(row["summary"])
        if flow == "keyjoin":  # 시작 요약(방식·요청 번호·범위)만 남고 결과 내용(header·inputs 목록)은 없다
            assert json.loads(row["summary"])["mode"] == "keyjoin" and "header" not in json.loads(row["summary"])
        assert taken.read_bytes() == b"existing result"  # 원래 있던 파일은 그대로
        assert not list(OUTPUTS.glob(f"{taken.name}.*.tmp")) and not list(OUTPUTS.glob("tmp*.xlsx.tmp"))
    finally:
        os.chmod(taken, 0o600)
        taken.unlink()


@pytest.mark.parametrize("flow", ("bundle", "integrate", "keyjoin"))
def test_rename_failure_before_completion_records_failed_without_files(
    flows: TestClient, monkeypatch: pytest.MonkeyPatch, flow: str
) -> None:
    send = _prepare(flows, flow, f"mv-{flow}")
    real_replace = os.replace

    def broken(src: Any, dst: Any, *a: Any, **k: Any) -> None:
        if str(dst).endswith(".xlsx") and "outputs" in str(dst):
            raise OSError(28, "injected: no space left on device")
        real_replace(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", broken)
    res = send()
    monkeypatch.undo()
    assert res.status_code == 409, res.text
    run_id = _latest_run_id()
    row = _row(run_id)
    assert row["status"] == "failed" and row["change_count"] == 0 and row["changes"] == 0
    assert _files_of(run_id) == ([], [])


def test_prepare_commit_lost_response_is_judged_by_the_recorded_hash(
    flows: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(새 판만) 준비 commit의 응답을 잃어도 내 sha256이 기록됐으면 이어서 완료한다."""
    from app import runlease

    send = _prepare(flows, "integrate", "prep-lost")
    real_prepare, real_commit = runlease.prepare, Session.commit
    state = {"armed": False, "fired": False}

    def prepare(*a: Any, **k: Any) -> str:
        state["armed"] = True
        try:
            return real_prepare(*a, **k)
        finally:
            state["armed"] = False

    def commit(session: Session) -> None:
        real_commit(session)
        if state["armed"] and not state["fired"]:
            state["fired"] = True
            raise OperationalError("COMMIT", {}, ConnectionResetError("injected: prepare response lost"))

    monkeypatch.setattr(runlease, "prepare", prepare)
    monkeypatch.setattr(Session, "commit", commit)
    res = send()
    monkeypatch.undo()
    assert state["fired"] and res.status_code == 201, res.text
    row = _row(res.json()["id"])
    assert row["status"] == "done" and row["changes"] == row["change_count"] > 0


def test_completion_update_and_failure_record_both_unreachable_leave_the_file_for_recovery(
    flows: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(새 판만) 완료 UPDATE가 DB 오류이고 실패 기록도 못 남기면, 임시 파일을 지우지 않고 503으로 답한다 — 이 프로세스의
    정리가 담당이 놓은 실행으로 보고 해시로 완료를 확정한다 (실패로 보였다가 완료가 되지 않음)."""
    from app import recovery, runlease

    send = _prepare(flows, "integrate", "both-down")
    real_execute = Session.execute
    state = {"down": False}

    def mark_done(*a: Any, **k: Any) -> int:
        state["down"] = True
        raise OperationalError("UPDATE", {}, ConnectionResetError("injected: connection lost"))

    def execute(session: Session, *a: Any, **k: Any) -> Any:
        if state["down"]:
            raise OperationalError("UPDATE", {}, ConnectionRefusedError("injected: database unreachable"))
        return real_execute(session, *a, **k)

    monkeypatch.setattr(runlease, "_mark_done", mark_done)
    monkeypatch.setattr(Session, "execute", execute)
    res = send()
    monkeypatch.undo()
    assert res.status_code == 503 and res.json()["detail"]["code"] == "run_uncertain", res.text
    run_id = res.json()["detail"]["run_id"]
    row = _row(run_id)
    finals, temps = _files_of(run_id)
    assert row["status"] == "running" and row["output_sha256"] and not finals and len(temps) == 1
    report = recovery.recover()
    assert report["complete"], report
    row = _row(run_id)
    finals, temps = _files_of(run_id)
    assert row["status"] == "done" and len(finals) == 1 and not temps
    assert row["output_sha256"] == hashlib.sha256(finals[0].read_bytes()).hexdigest()
    assert json.loads(row["summary"])["recovered"]
