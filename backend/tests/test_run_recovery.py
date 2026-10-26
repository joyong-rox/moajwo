"""멈춘 결과 실행 정리 — 실제 서버 프로세스(uvicorn) 여럿이 같은 DB·결과 폴더를 쓴다 (app/recovery.py, app/runlease.py).

실제 프로세스 시험 (자식 서버는 시험 전용 시작 스크립트로 띄운다 — 결과 저장·이름 바꾸기 지점에서 신호 파일로 멈춘다.
운영 코드에 시험 갈고리는 없다. 시간 대기 대신 '멈춤' 파일과 '진행' 파일로 순서를 정한다):
1. A가 결과를 만드는 중(임시 파일 저장 뒤)에 B가 같은 DB·폴더로 기동 → A의 기록·임시 파일 그대로 → A가 끝나면
   done 한 번.
2. 같은 폴더 두 프로세스: B가 A의 임시 파일과 이전 판 이름의 임시 파일을 지우지 않는다.
3. A1을 실제로 강제 종료 → B 기동 정리가 A1의 실행과 A1의 임시 파일만 정리 (살아 있는 A2의 실행·다른 사용자 기록·
   완료된 결과는 그대로).
4. 이름을 바꾼 뒤·완료 기록 전에 강제 종료 → 결과 파일 해시로 done. 준비 뒤·이름 바꾸기 전 종료 → 임시 파일로 done.
5. 다른 디스크(다른 결과 폴더)의 담당은 죽음을 확정할 수 없다 → 실패로 정하지 않고, 생존 신호가 오래되면 '결과 확인
   필요'만. 살아 있던 담당은 그 뒤에도 완료할 수 있다 (실패→완료 뒤집힘 없음).
같은 프로세스 안 시험: 판정표, 조회 뒤 완료(경합), 기동 정리 중 DB 오류(완료로 보고하지 않음), 생존 신호, CLI.
PostgreSQL 실행(MOAJWO_TEST_DATABASE_URL)에서는 실제 프로세스 시험마다 새 DB를 만든다 (MOAJWO_TEST_PG_ADMIN_URL 필요).
같은 시험을 고치기 전 코드에서도 돌릴 수 있게 새 열·새 모듈은 그것만 보는 단계·시험 안에서만 쓴다. 합성 자료만 쓴다.
"""

import hashlib
import io
import json
import os
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

try:  # 시험 의존성은 httpx2 (Starlette TestClient와 같은 방식으로 가져온다)
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

BACKEND = Path(__file__).resolve().parent.parent
TEST_PG = os.environ.get("MOAJWO_TEST_DATABASE_URL", "").strip()
TEST_PG_ADMIN = os.environ.get("MOAJWO_TEST_PG_ADMIN_URL", "").strip()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEAD = ["시도", "시군구", "인구수"]
MAP = {"시도": "sido", "시군구": "sigungu", "인구수": "population"}
PASSWORD = "Rec-pass-2026!"

CHILD = r"""
import os, pathlib, sys, time
ctl = pathlib.Path(os.environ["T_CTL"])

def point():
    try:
        return (ctl / "point").read_text().strip()
    except OSError:
        return ""

def pause(where):
    (ctl / "paused").write_text(where)
    while not (ctl / "go").exists():
        time.sleep(0.02)

import openpyxl.workbook.workbook as wbmod
real_save = wbmod.Workbook.save

def save(self, filename):
    real_save(self, filename)
    if point() == "saved" and str(filename).endswith(".tmp"):
        pause("saved")

wbmod.Workbook.save = save
real_replace = os.replace

def replace(src, dst, *a, **k):
    final = str(dst).endswith(".xlsx") and "outputs" in str(dst)
    if final and point() == "before_replace":
        pause("before_replace")
    real_replace(src, dst, *a, **k)
    if final and point() == "after_replace":
        pause("after_replace")

os.replace = replace
if os.environ.get("T_SLOW_BEAT"):
    try:
        import app.runlease as rl
        rl.HEARTBEAT_S = 3600
    except ImportError:
        pass
import uvicorn
uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stack:
    """시험 하나의 DB(새 SQLite 파일 또는 새 PostgreSQL DB)와 마이그레이션까지."""

    def __init__(self, tmp_path: Path, name: str) -> None:
        self.tmp = tmp_path
        self.data = tmp_path / "data"
        self.data.mkdir()
        self.base = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MOAJWO_"))}
        self.base.update(
            {"PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(BACKEND), "OLLAMA_BASE_URL": "http://127.0.0.1:9"}
        )
        self.url = f"sqlite:///{(self.data / 'moajwo.db').as_posix()}"
        self.pg_name = None
        if TEST_PG:
            if not TEST_PG_ADMIN:
                pytest.skip("PostgreSQL 실행: 새 DB를 만들 관리자 주소(MOAJWO_TEST_PG_ADMIN_URL)가 없음")
            self.pg_name = f"rec_{name}"
            self.url = _pg_database(self.pg_name)
            self.base.update({"MOAJWO_DB_MODE": "external"})
        self.base["DATABASE_URL"] = self.url
        env = {**self.base, "MOAJWO_DATA_DIR": str(self.data)}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True,
                       capture_output=True)  # fmt: skip
        (tmp_path / "child.py").write_text(CHILD, encoding="utf-8")
        self.engine = create_engine(self.url.replace("postgresql://", "postgresql+psycopg://", 1))
        self.servers: list[Server] = []

    def server(self, name: str, data: Path | None = None, **env: str) -> "Server":
        srv = Server(self, name, data or self.data, env)
        self.servers.append(srv)
        return srv

    def row(self, run_id: int) -> dict[str, Any]:
        with self.engine.connect() as con:
            r = con.execute(text("SELECT * FROM runs WHERE id = :i"), {"i": run_id}).mappings().one()
            n = con.execute(text("SELECT count(*) FROM run_changes WHERE run_id = :i"), {"i": run_id}).scalar()
        return {**dict(r), "changes": n}

    def running(self) -> list[int]:
        with self.engine.connect() as con:
            return [r[0] for r in con.execute(text("SELECT id FROM runs WHERE status = 'running' ORDER BY id"))]

    def execute(self, sql: str, **params: Any) -> None:
        with self.engine.begin() as con:
            con.execute(text(sql), params)

    def close(self) -> None:
        for srv in self.servers:
            srv.kill()
        self.engine.dispose()
        if self.pg_name:
            _pg_database(self.pg_name, drop=True)


def _pg_database(name: str, drop: bool = False) -> str:
    admin = make_url(TEST_PG_ADMIN)
    engine = create_engine(admin.set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT")
    with engine.connect() as con:
        con.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        if not drop:
            con.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()
    return admin.set(database=name).render_as_string(hide_password=False)


class Server:
    def __init__(self, stack: Stack, name: str, data: Path, extra: dict[str, str]) -> None:
        self.port, self.name, self.data = _free_port(), name, data
        self.ctl = stack.tmp / f"ctl-{name}"
        self.ctl.mkdir()
        env = {**stack.base, "MOAJWO_DATA_DIR": str(data), "T_CTL": str(self.ctl), **extra}
        self.log = open(stack.tmp / f"{name}.log", "w", encoding="utf-8")  # noqa: SIM115 — 서버가 끝날 때 닫는다
        cmd = [sys.executable, str(stack.tmp / "child.py"), str(self.port)]
        self.proc = subprocess.Popen(cmd, cwd=BACKEND, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:  # 기동(lifespan의 정리 포함)이 끝나야 /health가 답한다
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=2).status_code in (200, 503):
                    return
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.1)
        log = (stack.tmp / f"{name}.log").read_text(encoding="utf-8")
        raise AssertionError(f"서버 {name}가 뜨지 않음: {log[-2000:]}")

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=120)

    def pause_at(self, where: str) -> None:
        (self.ctl / "go").unlink(missing_ok=True)
        (self.ctl / "paused").unlink(missing_ok=True)
        (self.ctl / "point").write_text(where)

    def wait_paused(self, timeout: float = 60) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (self.ctl / "paused").exists():
                return (self.ctl / "paused").read_text()
            time.sleep(0.02)
        raise AssertionError(f"서버 {self.name}가 멈춤 지점에 닿지 않음")

    def release(self) -> None:
        (self.ctl / "point").write_text("")
        (self.ctl / "go").write_text("1")

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()  # Windows: TerminateProcess, POSIX: SIGKILL — 정리 코드 없이 바로 끝난다
            self.proc.wait(30)
        self.log.close()


@pytest.fixture
def stack(tmp_path: Path, request: pytest.FixtureRequest):
    s = Stack(tmp_path, request.node.name.split("[")[0][-40:].lower())
    try:
        yield s
    finally:
        s.close()


def _xlsx(rows: list[list], sheet: str) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(["주민등록 인구 현황 2026년 9월 (단위: 명)"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _owner(c: httpx.Client) -> None:
    res = c.post("/auth/setup", json={"username": "rec-owner", "display_name": "정리 시험", "password": PASSWORD})
    assert res.status_code == 201, res.text


def _login(c: httpx.Client, username: str) -> None:
    assert c.post("/auth/login", json={"username": username, "password": PASSWORD}).status_code == 200


def _bundle_ready(c: httpx.Client, tag: str) -> dict[str, Any]:
    rows = [HEAD, ["서울특별시", "종로구", 10], ["서울특별시", "중구", 20]]
    up = c.post("/files", files=[("files", (f"{tag}.xlsx", _xlsx(rows, tag), XLSX))])
    assert up.status_code == 200, up.text
    fid = up.json()[0]["id"]
    cols = {col["name"]: col["column_id"] for col in c.get(f"/files/{fid}/preview?sheet=s0").json()["columns"]}
    items = [{"sheet_id": "s0", "column_id": cols[n], "field_key": k} for n, k in MAP.items()]
    assert c.put(f"/files/{fid}/mapping/decisions", json={"decisions": items}).status_code == 200
    plan = c.post("/bundle/plan", json={"file_ids": [fid]}).json()
    assert plan["ready"], plan
    return {"file_ids": [fid], "expected_basis": plan["basis"]}


def _start(c: httpx.Client, body: dict[str, Any]) -> tuple[threading.Thread, dict[str, Any]]:
    out: dict[str, Any] = {}

    def go() -> None:
        try:
            out["res"] = c.post("/bundle/run", json=body)
        except httpx.HTTPError as exc:  # 그 서버를 강제 종료한 경우
            out["error"] = exc

    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th, out


def _outputs(data: Path) -> Path:
    return data / "outputs"


def _temps(data: Path) -> set[str]:
    return {p.name for p in _outputs(data).glob("*.tmp")} if _outputs(data).is_dir() else set()


def _legacy_temp(data: Path) -> Path:
    """이전 판 이름의 쓰다 만 임시 파일 (실행과 연결 없음, 오래됨) — 자동으로 지우면 안 된다."""
    _outputs(data).mkdir(parents=True, exist_ok=True)
    p = _outputs(data) / "tmpLEGACYcase.xlsx.tmp"
    p.write_bytes(b"partial")
    old = time.time() - 3600
    os.utime(p, (old, old))
    return p


def _final(data: Path, row: dict[str, Any]) -> Path | None:
    return data / row["output_path"] if row["output_path"] else None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _age_heartbeat(stack: Stack, run_id: int, seconds: int) -> None:
    """시간이 흐른 것을 대신해 생존 신호를 과거로 (UTC 저장 — SQLite는 시간대 없는 UTC 문자열). 고치기 전 코드에는 이
    열이 없어 건너뛴다 (그 코드는 생존 신호 없이 시작 시각만으로 판단한다)."""
    try:
        if TEST_PG:
            sql = f"UPDATE runs SET heartbeat_at = now() - interval '{seconds} seconds' WHERE id = :i"
        else:
            sql = f"UPDATE runs SET heartbeat_at = datetime('now', '-{seconds} seconds') WHERE id = :i"
        stack.execute(sql, i=run_id)
    except Exception:  # noqa: BLE001 — 열 없음 (고치기 전 코드)
        pass


# ---------------------------------------------------------------- 1·2. 살아 있는 실행은 다른 서버 기동이 건드리지 않음


def test_live_run_is_kept_while_another_server_starts_and_completes_once(stack: Stack) -> None:
    a = stack.server("a")
    ca = a.client()
    _owner(ca)
    body = _bundle_ready(ca, "live")
    legacy = _legacy_temp(stack.data)
    a.pause_at("saved")
    th, out = _start(ca, body)
    a.wait_paused()
    (run_id,) = stack.running()
    a_temps = _temps(stack.data) - {legacy.name}
    assert a_temps, "A의 임시 파일이 있어야 한다"
    stack.server("b")  # 같은 DB·같은 결과 폴더로 기동 — 기동 정리가 돈 뒤에 /health가 답한다
    mid = stack.row(run_id)
    assert (mid["status"], mid["error"]) == ("running", None), (mid["status"], mid["error"])
    assert a_temps <= _temps(stack.data), "B가 A의 진행 중 임시 파일을 지웠다"
    assert legacy.exists(), "B가 실행과 연결 없는 이전 판 임시 파일을 지웠다"
    a.release()
    th.join(120)
    assert out["res"].status_code == 201, out["res"].text
    row = stack.row(run_id)
    final = _final(stack.data, row)
    assert (row["status"], row["error"]) == ("done", None)
    assert final is not None and final.is_file() and _sha(final) == row["output_sha256"]
    assert not (_temps(stack.data) - {legacy.name}), "끝난 실행의 임시 파일이 남았다"


def test_two_processes_on_one_folder_keep_each_others_temp_files(stack: Stack) -> None:
    a = stack.server("a")
    ca = a.client()
    _owner(ca)
    body = _bundle_ready(ca, "folder")
    legacy = _legacy_temp(stack.data)
    a.pause_at("saved")
    th, out = _start(ca, body)
    a.wait_paused()
    a_temps = _temps(stack.data) - {legacy.name}
    b = stack.server("b")
    cb = b.client()
    _login(cb, "rec-owner")
    listed = cb.get("/runs")  # B의 목록 요청도 아무것도 바꾸지 않는다 (정리는 예약만)
    assert listed.status_code == 200
    assert a_temps <= _temps(stack.data) and legacy.exists()
    a.release()
    th.join(120)
    assert out["res"].status_code == 201, out["res"].text


# ---------------------------------------------------------------- 3·4. 실제로 죽은 담당만, 근거대로


def test_killed_server_only_its_run_and_temp_are_cleaned(stack: Stack) -> None:
    a1, a2 = stack.server("a1"), stack.server("a2")
    c1, c2 = a1.client(), a2.client()
    _owner(c1)
    done = c1.post("/bundle/run", json=_bundle_ready(c1, "kept"))  # 완료된 결과 — 그대로여야 한다
    assert done.status_code == 201, done.text
    done_row = stack.row(done.json()["id"])
    done_file = _final(stack.data, done_row)
    user = {"username": "rec-user2", "display_name": "다른 사용자", "status": "active", "password": PASSWORD}
    assert c1.post("/users", json=user).status_code == 201
    _login(c2, "rec-user2")
    b1, b2 = _bundle_ready(c1, "dead"), _bundle_ready(c2, "alive")
    legacy = _legacy_temp(stack.data)
    a1.pause_at("saved")
    t1, _ = _start(c1, b1)
    a1.wait_paused()
    (dead_id,) = stack.running()
    dead_temps = _temps(stack.data) - {legacy.name}
    a2.pause_at("saved")
    t2, out2 = _start(c2, b2)
    a2.wait_paused()
    alive_id = next(i for i in stack.running() if i != dead_id)
    alive_temps = _temps(stack.data) - dead_temps - {legacy.name}
    a1.kill()  # 실제 강제 종료 — 정리 코드가 돌 틈이 없다
    stack.server("b")
    dead, alive = stack.row(dead_id), stack.row(alive_id)
    assert dead["status"] == "failed" and "결과 파일은 저장되지 않았습니다" in (dead["error"] or ""), dead
    assert dead["output_path"] is None and dead["changes"] == 0
    assert not (dead_temps & _temps(stack.data)), "죽은 A1의 임시 파일이 남았다"
    assert (alive["status"], alive["error"]) == ("running", None), "살아 있는 A2의 실행을 건드렸다"
    assert alive_temps <= _temps(stack.data), "살아 있는 A2의 임시 파일을 지웠다"
    assert legacy.exists()
    kept = stack.row(done_row["id"])
    assert (kept["status"], kept["output_sha256"]) == ("done", done_row["output_sha256"]) and done_file.is_file()
    a2.release()
    t2.join(120)
    assert out2["res"].status_code == 201, out2["res"].text
    assert stack.row(alive_id)["status"] == "done"
    t1.join(1)


def test_killed_after_rename_is_completed_from_the_saved_file(stack: Stack) -> None:
    a = stack.server("a")
    ca = a.client()
    _owner(ca)
    body = _bundle_ready(ca, "renamed")
    a.pause_at("after_replace")
    _start(ca, body)
    a.wait_paused()
    (run_id,) = stack.running()
    a.kill()
    stack.server("b")
    row = stack.row(run_id)
    final = _final(stack.data, row)
    assert final is not None and final.is_file()
    assert "만들어지지 않았습니다" not in (row["error"] or "") and "저장되지 않았습니다" not in (row["error"] or "")
    assert (row["status"], row["error"]) == ("done", None), (row["status"], row["error"])
    assert _sha(final) == row["output_sha256"]


def test_killed_after_prepare_before_rename_is_completed_from_the_temp_file(stack: Stack) -> None:
    a = stack.server("a")
    ca = a.client()
    _owner(ca)
    body = _bundle_ready(ca, "prepared")
    a.pause_at("before_replace")
    _start(ca, body)
    a.wait_paused()
    (run_id,) = stack.running()
    a.kill()  # 완료 UPDATE의 잠금을 쥔 채 죽는다 — DB가 그 트랜잭션을 되돌린다
    stack.server("b")
    row = stack.row(run_id)
    final = _final(stack.data, row)
    assert (row["status"], row["error"]) == ("done", None), (row["status"], row["error"])
    assert final is not None and final.is_file() and _sha(final) == row["output_sha256"]
    assert not _temps(stack.data)


# ---------------------------------------------------------------- 5. 다른 디스크 — 근거가 없으면 실패로 정하지 않는다


def test_other_disk_run_is_never_failed_without_evidence(stack: Stack) -> None:
    disk_a = stack.tmp / "data-a"
    disk_a.mkdir()
    a = stack.server("a", data=disk_a)  # 같은 DB, 다른 결과 폴더 (Render 무중단 배포의 이전·새 인스턴스와 같은 꼴)
    ca = a.client()
    _owner(ca)
    body = _bundle_ready(ca, "otherdisk")
    a.pause_at("saved")
    _start(ca, body)
    a.wait_paused()
    (run_id,) = stack.running()
    a_temps = _temps(disk_a)
    a.kill()
    b = stack.server("b")
    first = stack.row(run_id)
    assert first["status"] == "running", "생존 신호가 아직 새로운데 판정했다"
    b.kill()
    _age_heartbeat(stack, run_id, 3600)
    stack.server("b2")
    row = stack.row(run_id)
    assert row["status"] == "check", (row["status"], row["error"])
    assert "확인할 수 없어" in row["error"] and "다시 실행" not in row["error"]
    assert a_temps <= _temps(disk_a), "다른 디스크의 파일을 건드렸다"


def test_alive_other_disk_run_marked_check_can_still_complete(stack: Stack) -> None:
    disk_a = stack.tmp / "data-a"
    disk_a.mkdir()
    a = stack.server("a", data=disk_a, T_SLOW_BEAT="1")
    ca = a.client()
    _owner(ca)
    body = _bundle_ready(ca, "slowbeat")
    a.pause_at("saved")
    th, out = _start(ca, body)
    a.wait_paused()
    (run_id,) = stack.running()
    _age_heartbeat(stack, run_id, 3600)  # 살아 있지만 생존 신호가 끊긴 것처럼
    stack.server("b")
    mid = stack.row(run_id)
    assert mid["status"] == "check", "근거 없이 실패로 정했거나 판정하지 않았다"
    a.release()
    th.join(120)
    assert out["res"].status_code == 201, out["res"].text
    row = stack.row(run_id)
    assert (row["status"], row["error"]) == ("done", None), (row["status"], row["error"])


# ---------------------------------------------------------------- 같은 프로세스 안 — 판정표·경합·DB 오류·생존 신호·CLI
# (새 판의 함수만 본다)


@pytest.fixture
def head_revision():
    """시험 DB는 create_all로 만들어 리비전 기록이 없다 — 운영 DB(기동 때 'alembic upgrade head')처럼 0020 리비전을
    적어 두고(복구 명령은 리비전과 열이 맞을 때만 쓴다), 끝나면 그 전 상태로 되돌린다."""
    from app.db import engine

    with engine.begin() as c:
        existed = inspect(c).has_table("alembic_version")
        before = [r for (r,) in c.execute(text("SELECT version_num FROM alembic_version"))] if existed else None
        if not existed:
            c.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        c.execute(text("DELETE FROM alembic_version"))
        c.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0020_run_worker_lease')"))
    yield
    with engine.begin() as c:
        if before is None:
            c.execute(text("DROP TABLE alembic_version"))
        else:
            c.execute(text("DELETE FROM alembic_version"))
            for r in before:
                c.execute(text("INSERT INTO alembic_version (version_num) VALUES (:r)"), {"r": r})


@pytest.fixture
def dead_worker():
    """같은 결과 폴더의 죽은 담당 (잠금 파일은 있고 아무도 쥐지 않음)."""
    from app import runlease

    proc = "dead" + os.urandom(4).hex()
    runlease.WORKERS_DIR.mkdir(parents=True, exist_ok=True)
    lock = runlease.WORKERS_DIR / f"{proc}.lock"
    lock.write_bytes(b"")
    worker = f"{runlease.identity()['disk']}-{proc}"
    yield worker, proc
    lock.unlink(missing_ok=True)


def _insert_run(**values: Any) -> int:
    from app.db import SessionLocal
    from app.models import IntegrationRun

    base = {"status": "running", "trigger": "manual", "file_ids": "[]", "summary": json.dumps({"mode": "sheets"})}
    with SessionLocal() as db:
        run = IntegrationRun(**{**base, **values})
        db.add(run)
        db.commit()
        return run.id


def _sql(sql: str, **params: Any) -> list[Any]:
    """앱과 같은 DB에 바로 (SQLite·PostgreSQL 모두)."""
    from app.db import engine

    with engine.begin() as con:
        res = con.execute(text(sql), params)
        return list(res) if res.returns_rows else []


def _get_run(run_id: int) -> dict[str, Any]:
    cols = "status, error, output_path, output_sha256, change_count, summary, finished_at"
    (row,) = _sql(f"SELECT {cols} FROM runs WHERE id = :i", i=run_id)
    return dict(zip(cols.split(", "), row, strict=True))


def _case(worker: str, proc: str, name: str, prepared: bytes | None, final: bytes | None, temp: bytes | None) -> dict:
    """판정표의 한 줄: 결과 폴더에 최종·임시 파일을 두고 실행 기록을 만든다."""
    from app.runlease import OUTPUT_DIR

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"판정_{name}.xlsx"
    tmp = target.with_name(f"{target.name}.{proc}.tmp")
    for path, data in ((target, final), (tmp, temp)):
        path.unlink(missing_ok=True)
        if data is not None:
            path.write_bytes(data)
    sha = hashlib.sha256(prepared).hexdigest() if prepared is not None else None
    run_id = _insert_run(worker=worker, output_path=f"outputs/{target.name}", output_sha256=sha)
    return {"id": run_id, "target": target, "tmp": tmp}


def test_decision_table_for_a_dead_worker_on_the_same_disk(dead_worker: tuple[str, str]) -> None:
    from app import recovery

    worker, proc = dead_worker
    good, bad = b"complete result", b"something else"
    cases = {
        "unprepared_tmp": (_case(worker, proc, "a", None, None, good), "failed", recovery.FAILED_UNSAVED),
        "unprepared_final": (_case(worker, proc, "b", None, good, None), "check", recovery.CHECK_UNEXPECTED),
        "final_same": (_case(worker, proc, "c", good, good, None), "done", None),
        "tmp_same": (_case(worker, proc, "d", good, None, good), "done", None),
        "final_different": (_case(worker, proc, "e", good, bad, None), "check", recovery.CHECK_MISMATCH),
        "tmp_different": (_case(worker, proc, "f", good, None, bad), "failed", recovery.FAILED_BROKEN),
        "nothing": (_case(worker, proc, "g", good, None, None), "failed", recovery.FAILED_MISSING),
    }
    report = recovery.recover()
    assert report["complete"], report
    for name, (case, status, message) in cases.items():
        row = _get_run(case["id"])
        assert (row["status"], row["error"]) == (status, message), name
        if status == "failed":
            assert row["output_path"] is None and row["output_sha256"] is None and row["change_count"] == 0, name
            assert not case["tmp"].exists(), f"{name}: 그 실행의 임시 파일을 지우지 않았다"
            assert not case["target"].exists(), name
        if status == "done":
            assert case["target"].read_bytes() == good and not case["tmp"].exists(), name
            assert json.loads(row["summary"])["recovered"] == recovery.RECOVERED, name
            if name == "tmp_same" and os.name != "nt":
                assert not os.stat(case["target"]).st_mode & stat.S_IWUSR, "옮긴 결과를 읽기 전용으로 두지 않았다"
        if status == "check":
            assert case["target"].exists(), f"{name}: 확인 필요인 실행의 파일을 지웠다"
            assert "다시 실행" not in message
        if case["target"].exists() and status != "done":
            assert "저장되지 않았습니다" not in (row["error"] or ""), name
    for case, _, _ in cases.values():
        for path in (case["target"], case["tmp"]):
            if path.exists():
                os.chmod(path, 0o600)
                path.unlink()


def test_alive_and_unknown_owners_are_not_settled(
    dead_worker: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import recovery, runlease

    alive_proc = "a1fe" + os.urandom(4).hex()  # 프로세스 식별자는 12자리 hex
    lock = runlease.WORKERS_DIR / f"{alive_proc}.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    assert runlease._lock(fd)  # 다른 핸들이 쥐고 있으면 살아 있음
    try:
        alive = _insert_run(worker=f"{runlease.identity()['disk']}-{alive_proc}", output_path="outputs/x_alive.xlsx")
        other_disk = _insert_run(worker="0123456789ab-0123456789ab", output_path="outputs/x_other.xlsx")
        legacy = _insert_run(worker=None, output_path=None)
        report = recovery.recover()
        assert _get_run(alive)["status"] == "running"
        # 다른 디스크: 생존 신호가 없으면(이전 기록) 오래된 것으로 본다 → 결과 확인 필요 (실패 아님)
        assert (_get_run(other_disk)["status"], _get_run(other_disk)["error"]) == ("check", recovery.CHECK_UNKNOWN)
        assert _get_run(legacy)["status"] == "running" and report["waiting"] >= 1  # 이전 판은 기동 뒤 한동안 기다림
        monkeypatch.setattr(recovery, "LEGACY_GRACE_S", 0)
        recovery.recover()
        assert (_get_run(legacy)["status"], _get_run(legacy)["error"]) == ("check", recovery.CHECK_LEGACY)
    finally:
        runlease._unlock(fd)
        os.close(fd)
        lock.unlink(missing_ok=True)
        _sql("UPDATE runs SET status = 'failed' WHERE id IN (:a, :b)", a=alive, b=legacy)


def test_run_completed_after_the_query_is_not_touched(
    dead_worker: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import recovery

    worker, proc = dead_worker
    case = _case(worker, proc, "race", None, None, b"partial")
    real = recovery._file_state
    calls = {"n": 0}

    def owner_finishes_meanwhile(path: Path | None, sha: str | None) -> str:
        calls["n"] += 1
        if calls["n"] == 1:  # 정리가 후보를 고른 뒤, 가로채기 전에 담당이 완료를 기록한다
            _sql("UPDATE runs SET status = 'done', error = NULL WHERE id = :i", i=case["id"])
        return real(path, sha)

    monkeypatch.setattr(recovery, "_file_state", owner_finishes_meanwhile)
    recovery.recover()
    row = _get_run(case["id"])
    assert (row["status"], row["error"]) == ("done", None), "조회 뒤 완료된 실행을 덮었다"
    assert case["tmp"].exists(), "가로채지 못했는데 파일을 건드렸다"
    case["tmp"].unlink()


def test_database_failure_during_startup_recovery_is_not_reported_complete(
    dead_worker: tuple[str, str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from sqlalchemy.exc import OperationalError

    from app import recovery

    worker, proc = dead_worker
    case = _case(worker, proc, "dbdown", None, None, b"partial")

    class Down:
        def __enter__(self) -> "Down":
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def execute(self, *a: Any, **k: Any) -> Any:
            raise OperationalError("SELECT", {}, ConnectionRefusedError("injected: database unreachable"))

    monkeypatch.setattr(recovery.app_db, "SessionLocal", lambda: Down())
    scheduled: list[float] = []
    monkeypatch.setattr(recovery, "schedule", scheduled.append)
    caplog.set_level("INFO", logger="app.recovery")
    assert recovery.startup() is None
    messages = [r.getMessage() for r in caplog.records if r.name == "app.recovery"]
    assert any("startup recovery incomplete" in m for m in messages), messages
    assert not any("recovery complete" in m for m in messages), messages
    assert scheduled == [recovery.RETRY_S[0]]  # 다시 볼 예약 (계속 두드리지 않고 정해 둔 간격으로)
    monkeypatch.undo()
    assert _get_run(case["id"])["status"] == "running" and case["tmp"].exists()  # 정리 못 했으니 그대로
    report = recovery.recover()
    assert report["complete"] and _get_run(case["id"])["status"] == "failed"


def test_one_run_failing_to_settle_makes_the_report_incomplete(
    dead_worker: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import recovery, runlease

    worker, proc = dead_worker
    lock = runlease.WORKERS_DIR / f"{proc}.lock"
    old = time.time() - 600  # 그 서버가 뜬 지 오래 — 잠금 파일도 오래됐다 (정리는 60초보다 오래된 것만 지운다)
    os.utime(lock, (old, old))
    first = _case(worker, proc, "err1", None, None, None)
    second = _case(worker, proc, "err2", None, None, None)
    real = recovery._settle_dead

    def flaky(run_id: int, worker_id: str | None) -> str:
        if run_id == first["id"]:
            raise PermissionError("injected: antivirus lock")
        return real(run_id, worker_id)

    monkeypatch.setattr(recovery, "_settle_dead", flaky)
    report = recovery.recover()
    assert report["errors"] == 1 and not report["complete"] and report["next_s"] is not None
    assert _get_run(first["id"])["status"] == "running"  # 판정하지 않고 다음 차례로 (확인 필요로 굳히지 않음)
    assert _get_run(second["id"])["status"] == "failed"
    assert lock.exists(), "아직 실행 중인 담당의 잠금 파일을 지웠다 — 다음 차례에 끝났다는 근거가 없다"
    monkeypatch.undo()
    recovery.recover()
    assert _get_run(first["id"])["status"] == "failed"  # 근거(잠금)로 정함 — '결과 확인 필요'로 굳지 않음
    recovery.recover()
    assert not lock.exists()  # 그 담당의 실행이 모두 끝난 뒤에야 지운다


def _cli(data: Path, *args: str) -> subprocess.CompletedProcess:
    """격리 데이터 폴더(SQLite)로 backend 명령을 별도 프로세스로 — 서버를 멈춘 뒤 운영자가 치는 것과 같게."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MOAJWO_", "DATABASE_URL"))}
    env.update({"MOAJWO_DATA_DIR": str(data), "PYTHONIOENCODING": "utf-8", "OLLAMA_BASE_URL": "http://127.0.0.1:9"})
    return subprocess.run([sys.executable, *args], cwd=BACKEND, env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=180)  # fmt: skip


def test_cli_recover_in_its_own_process_reports_what_still_blocks_the_downgrade(tmp_path: Path) -> None:
    """서버를 멈춘 롤백 전 정리: 같은 결과 폴더의 끝난 담당 → 판정, 근거 없는 실행은 신호가 10분 넘었을 때만 '결과
    확인 필요', 신호가 새로운 다른 디스크 실행·이전 판 실행은 그대로. 담당이 있는 실행 중 행이 남으면 종료 2와 그 수를
    알리고, 없어지면 종료 0 — 그 뒤 downgrade가 된다."""
    data = tmp_path / "data"
    assert _cli(data, "-m", "alembic", "upgrade", "head").returncode == 0
    disk = _cli(data, "-c", "from app import runlease; print(runlease.identity()['disk'])").stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{12}", disk), disk
    proc = "dead" + os.urandom(4).hex()
    lock = data / "outputs" / ".workers" / f"{proc}.lock"
    lock.write_bytes(b"")
    old = time.time() - 600
    os.utime(lock, (old, old))
    now = datetime.now(UTC)
    beat = {"stale": now - timedelta(seconds=3600), "fresh": now - timedelta(seconds=30)}
    rows = {  # 이름: (담당, 마지막 생존 신호)
        "dead": (f"{disk}-{proc}", beat["stale"]),
        "other_stale": ("bbbbbbbbbbbb-cccccccccccc", beat["stale"]),
        "other_fresh": ("bbbbbbbbbbbb-dddddddddddd", beat["fresh"]),
        "legacy": (None, None),
    }
    con = sqlite3.connect(data / "moajwo.db")
    ids = {}
    for name, (worker, at) in rows.items():
        cur = con.execute(
            "INSERT INTO runs (status, trigger, input_file_count, file_ids, row_count, change_count, warning_count,"
            " summary, worker, heartbeat_at)"
            " VALUES ('running', 'manual', 0, '[]', 0, 0, 0, '{\"mode\": \"sheets\"}', ?, ?)",
            (worker, at.strftime("%Y-%m-%d %H:%M:%S.%f") if at else None),
        )
        ids[name] = cur.lastrowid
    con.commit()

    def states() -> dict[str, str]:
        return {n: con.execute("SELECT status FROM runs WHERE id = ?", (i,)).fetchone()[0] for n, i in ids.items()}

    first = _cli(data, "-m", "app.recovery", "recover")
    out = json.loads(first.stdout)
    assert first.returncode == 2, (first.returncode, first.stdout, first.stderr[-400:])
    assert out["blocking"] == 1 and out["legacy"] == 1 and out["complete"] is True, out
    assert states() == {"dead": "failed", "other_stale": "check", "other_fresh": "running", "legacy": "running"}
    refused = _cli(data, "-m", "alembic", "downgrade", "0019_analysis_runs")
    assert refused.returncode != 0 and "실행 중인 결과 실행이 1건" in refused.stderr
    con.execute("UPDATE runs SET heartbeat_at = ? WHERE id = ?",
                (beat["stale"].strftime("%Y-%m-%d %H:%M:%S.%f"), ids["other_fresh"]))  # fmt: skip
    con.commit()
    second = _cli(data, "-m", "app.recovery", "recover")
    assert second.returncode == 0 and json.loads(second.stdout)["blocking"] == 0, second.stdout
    assert states()["other_fresh"] == "check" and states()["legacy"] == "running"
    con.close()
    down = _cli(data, "-m", "alembic", "downgrade", "0019_analysis_runs")
    assert down.returncode == 0, down.stderr[-400:]


def test_cli_on_a_database_before_0020_only_reads_counts_and_changes_nothing(tmp_path: Path) -> None:
    """반영 전 운영 DB(0019)에 새 명령을 잘못 써도: status는 실행 기록의 상태별 건수만 읽고(종료 0), recover·resolve는
    아무것도 바꾸지 않고 이유를 알린다(종료 1) — 담당·생존 신호 열이 없다는 오류 추적으로 끝나지 않는다."""
    data = tmp_path / "data"
    assert _cli(data, "-m", "alembic", "upgrade", "0019_analysis_runs").returncode == 0
    con = sqlite3.connect(data / "moajwo.db")
    for status in ("running", "done"):
        con.execute(
            "INSERT INTO runs (status, trigger, input_file_count, file_ids, row_count, change_count, warning_count,"
            " summary) VALUES (?, 'manual', 0, '[]', 0, 0, 0, '{\"mode\": \"sheets\"}')",
            (status,),
        )
    con.commit()
    query = "SELECT id, status, error, summary, finished_at FROM runs ORDER BY id"
    before = con.execute(query).fetchall()
    con.close()
    files = lambda: sorted(p.relative_to(data).as_posix() for p in data.rglob("*"))  # noqa: E731
    files_before = files()
    shown = _cli(data, "-m", "app.recovery", "status")
    assert shown.returncode == 0, shown.stderr[-400:]
    out = json.loads(shown.stdout)
    assert (out["revision"], out["lease_columns"]) == ("0019_analysis_runs", False), out
    assert out["runs_by_status"] == {"done": 1, "running": 1}, out
    for args in (("recover",), ("resolve", "1", "--failed")):
        refused = _cli(data, "-m", "app.recovery", *args)
        assert refused.returncode == 1, (args, refused.stdout, refused.stderr[-400:])
        assert "이 DB는 0019_analysis_runs입니다" in json.loads(refused.stdout)["error"]
        assert "Traceback" not in refused.stderr
    con = sqlite3.connect(data / "moajwo.db")
    assert con.execute(query).fetchall() == before  # 행을 바꾸지 않음
    con.close()
    assert files() == files_before  # 잠금·디스크 식별 파일도 만들지 않음


def _odd_database(data: Path, case: str) -> None:
    """지원하는 판(0019·0020)이 아니거나 리비전과 실제 열이 맞지 않는 DB를 만든다 (실행 중 1행과 결과 확인 필요 1행 —
    판을 잘못 짐작하면 resolve가 바꿀 수 있는 행)."""
    target = "0018_mapping_analyses" if case == "unsupported" else (
        "0019_analysis_runs" if case == "0020_without_columns" else "head"
    )  # fmt: skip
    assert _cli(data, "-m", "alembic", "upgrade", target).returncode == 0
    con = sqlite3.connect(data / "moajwo.db")
    for status in ("running", "check"):
        con.execute(
            "INSERT INTO runs (status, trigger, input_file_count, file_ids, row_count, change_count, warning_count,"
            " summary) VALUES (?, 'manual', 0, '[]', 0, 0, 0, '{}')",
            (status,),
        )
    if case == "missing":
        con.execute("DELETE FROM alembic_version")
    elif case == "no_version_table":  # 리비전 표 자체가 없음 (0020 표 구조)
        con.execute("DROP TABLE alembic_version")
    elif case == "null_revision":  # 손으로 다시 만든 리비전 표 (NOT NULL 없음)에 빈 값
        con.execute("DROP TABLE alembic_version")
        con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        con.execute("INSERT INTO alembic_version (version_num) VALUES (NULL), ('0020_run_worker_lease')")
    elif case == "two":
        con.execute("INSERT INTO alembic_version (version_num) VALUES ('0019_analysis_runs')")
    elif case == "0020_without_columns":  # 0019 표 구조에 리비전만 0020 (열 없음)
        con.execute("UPDATE alembic_version SET version_num = '0020_run_worker_lease'")
    elif case == "0020_partial":  # 0020에서 생존 신호 열만 빠짐
        con.execute("ALTER TABLE runs DROP COLUMN heartbeat_at")
    elif case == "0019_with_columns":  # 0020 표 구조에 리비전만 0019 (열 남음)
        con.execute("UPDATE alembic_version SET version_num = '0019_analysis_runs'")
    elif case == "0019_with_worker":  # 0019 리비전에 담당 열만 남음
        con.execute("ALTER TABLE runs DROP COLUMN heartbeat_at")
        con.execute("UPDATE alembic_version SET version_num = '0019_analysis_runs'")
    con.commit()
    con.close()


def _no_raw(res: subprocess.CompletedProcess, data: Path) -> None:
    """출력(stdout·stderr)에 경로(그대로·JSON 이스케이프·슬래시 형태)·SQL·오류 추적이 없다."""
    shown = res.stdout + res.stderr
    for path in (str(data), data.as_posix(), json.dumps(str(data))[1:-1]):
        assert path not in shown, path
    for raw in ("Traceback", "[SQL", "[parameters", "SELECT", "UPDATE", "PRAGMA", "sqlite3."):
        assert raw not in shown, raw


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("missing", "리비전 기록(alembic_version)이 없습니다"),
        ("no_version_table", "리비전 기록(alembic_version)이 없습니다"),
        ("null_revision", "리비전 기록(alembic_version)에 빈 값이 있습니다 (2행)"),
        ("two", "리비전이 2개입니다"),
        ("unsupported", "이 판의 복구 명령이 다루지 않는 리비전(0018_mapping_analyses)입니다"),
        (
            "0020_without_columns",
            "리비전은 0020_run_worker_lease인데 담당·생존 신호 열(runs.worker·heartbeat_at)이 없습니다",
        ),
        ("0020_partial", "리비전은 0020_run_worker_lease인데 담당·생존 신호 열(runs.worker·heartbeat_at)이 일부만"),
        (
            "0019_with_columns",
            "리비전은 0019_analysis_runs인데 0020의 담당·생존 신호 열(heartbeat_at, worker)이 있습니다",
        ),
        ("0019_with_worker", "리비전은 0019_analysis_runs인데 0020의 담당·생존 신호 열(worker)이 있습니다"),
    ],
)
def test_cli_refuses_a_database_whose_revision_it_does_not_support_or_that_does_not_match(
    tmp_path: Path, case: str, expected: str
) -> None:
    """지원하는 리비전과 필요한 열이 맞을 때만 쓴다: 리비전 없음(행·표)·빈 값·여러 개·다루지 않는 리비전·리비전과 열
    불일치(전부·일부)면 status를 포함해 진단만 알리고 종료 1 — 어느 판이라고 짐작하지 않고('0020 이전 판' 아님)
    결과 확인 필요 행도 바꾸지 않으며 행·리비전·파일 그대로. 출력에 경로·SQL이 없다."""
    data = tmp_path / "data"
    _odd_database(data, case)
    db = data / "moajwo.db"
    query = "SELECT id, status, error, summary, finished_at FROM runs ORDER BY id"

    def state() -> tuple:
        con = sqlite3.connect(db)
        try:
            versions = (
                con.execute("SELECT version_num FROM alembic_version").fetchall()
                if con.execute("SELECT count(*) FROM sqlite_master WHERE name = 'alembic_version'").fetchone()[0]
                else None
            )
            return con.execute(query).fetchall(), versions
        finally:
            con.close()

    before = state()
    files_before = sorted(p.relative_to(data).as_posix() for p in data.rglob("*"))
    for args in (("status",), ("recover",), ("resolve", "2", "--failed")):
        res = _cli(data, "-m", "app.recovery", *args)
        assert res.returncode == 1, (case, args, res.stdout, res.stderr[-400:])
        out = json.loads(res.stdout)
        assert expected in out["error"] and "아무것도 바꾸지 않음" in out["error"], (case, out)
        assert "0020 이전 판" not in res.stdout
        _no_raw(res, data)
    assert state() == before  # 실행 기록(결과 확인 필요 행 포함)·리비전을 바꾸지 않음
    assert sorted(p.relative_to(data).as_posix() for p in data.rglob("*")) == files_before


def test_cli_without_a_runs_table_refuses_and_changes_nothing(tmp_path: Path) -> None:
    data = tmp_path / "data"
    assert _cli(data, "-m", "alembic", "upgrade", "head").returncode == 0
    con = sqlite3.connect(data / "moajwo.db")
    con.execute("DROP TABLE run_changes")
    con.execute("DROP TABLE runs")
    con.commit()
    con.close()
    before = _sha(data / "moajwo.db")
    for args in (("status",), ("recover",), ("resolve", "1", "--failed")):
        res = _cli(data, "-m", "app.recovery", *args)
        assert res.returncode == 1, (args, res.stdout, res.stderr[-400:])
        assert "리비전은 0020_run_worker_lease인데 실행 기록 표(runs)가 없습니다" in json.loads(res.stdout)["error"]
        _no_raw(res, data)
    assert _sha(data / "moajwo.db") == before


def test_cli_without_the_database_file_does_not_create_it(tmp_path: Path) -> None:
    """데이터 폴더·볼륨을 잘못 골라 DB 파일이 없으면: 열지 않고(SQLite는 열면 빈 파일을 만듦) 폴더·볼륨을 확인하라고
    알린다 — 종료 1, 파일을 만들지 않는다 (예전: 빈 moajwo.db를 만들고 '리비전 기록이 없습니다'로 안내)."""
    data = tmp_path / "data"
    data.mkdir()
    for args in (("status",), ("recover",), ("resolve", "1", "--failed")):
        res = _cli(data, "-m", "app.recovery", *args)
        assert res.returncode == 1, (args, res.stdout, res.stderr[-400:])
        error = json.loads(res.stdout)["error"]
        assert error.startswith("DB 파일이 없습니다 — 데이터 폴더(MOAJWO_DATA_DIR)나 연결한 볼륨"), error
        assert "DB 파일을 만들지 않았습니다" in error and "리비전" not in error, error
        _no_raw(res, data)
    assert sorted(p.name for p in data.rglob("*")) == []


def test_cli_status_on_a_0020_database_names_the_revision(tmp_path: Path) -> None:
    data = tmp_path / "data"
    assert _cli(data, "-m", "alembic", "upgrade", "head").returncode == 0
    res = _cli(data, "-m", "app.recovery", "status")
    assert res.returncode == 0, (res.stdout, res.stderr[-400:])
    out = json.loads(res.stdout)
    assert out["revision"] == "0020_run_worker_lease" and out["running"] == [] and out["check"] == [], out
    _no_raw(res, data)


def test_cli_reports_a_database_it_cannot_read_without_its_path(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "moajwo.db").write_bytes(b"not a database " * 64)
    before = (data / "moajwo.db").read_bytes()
    for args in (("status",), ("recover",), ("resolve", "1", "--failed")):
        res = _cli(data, "-m", "app.recovery", *args)
        assert res.returncode == 1, (args, res.stdout, res.stderr[-400:])
        error = json.loads(res.stdout)["error"]
        assert error == "DB를 확인하지 못했습니다 (DatabaseError SQLITE_NOTADB) — 아무것도 바꾸지 않았습니다.", error
        _no_raw(res, data)
    assert (data / "moajwo.db").read_bytes() == before


def test_cli_failure_after_the_check_reports_only_the_kind(tmp_path: Path) -> None:
    """확인은 통과했는데 그다음 쓰기가 막히면(다른 연결이 쓰기 잠금을 쥠 — 서버가 떠 있는 동안 resolve) 오류 추적
    (SQL·매개변수) 대신 종류만 알리고 종료 1. 그 실행은 그대로다."""
    data = tmp_path / "data"
    assert _cli(data, "-m", "alembic", "upgrade", "head").returncode == 0
    con = sqlite3.connect(data / "moajwo.db", isolation_level=None)
    rid = con.execute(
        "INSERT INTO runs (status, trigger, input_file_count, file_ids, row_count, change_count, warning_count,"
        " summary, error) VALUES ('check', 'manual', 0, '[]', 0, 0, 0, '{}', '확인 필요')"
    ).lastrowid
    con.execute("BEGIN IMMEDIATE")  # 쓰기 잠금 (읽기는 됨 — 확인 단계는 통과)
    try:
        res = _cli(data, "-m", "app.recovery", "resolve", str(rid), "--failed")
    finally:
        con.execute("ROLLBACK")
    assert res.returncode == 1, (res.stdout, res.stderr[-400:])
    out = json.loads(res.stdout)
    expected = "resolve를 끝내지 못했습니다 (OperationalError SQLITE_BUSY) — status로 실행 상태를 확인하세요."
    assert out["error"] == expected, out
    _no_raw(res, data)
    assert con.execute("SELECT status FROM runs WHERE id = ?", (rid,)).fetchone()[0] == "check"
    con.close()


def test_heartbeat_runs_only_while_a_run_is_tracked(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import event

    from app import runlease

    monkeypatch.setattr(runlease, "HEARTBEAT_S", 0.05)
    run_id = _insert_run(worker=runlease.worker())
    statements: list[str] = []
    engine = runlease._engine()
    listen = lambda *a, **k: statements.append(a[2])  # noqa: E731
    event.listen(engine, "before_cursor_execute", listen)
    try:
        runlease.track(run_id)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not _get_heartbeat(run_id):
            time.sleep(0.02)
        assert _get_heartbeat(run_id), "생존 신호를 보내지 않았다"
        thread = runlease._beat["thread"]
        runlease.untrack(run_id)
        thread.join(10)
        assert not thread.is_alive() and runlease._beat["thread"] is None, "실행이 없는데 신호 스레드가 남았다"
        sent = len(statements)
        time.sleep(0.3)
        assert len(statements) == sent, "실행이 없는데 DB에 보냈다"
    finally:
        event.remove(engine, "before_cursor_execute", listen)
        runlease.untrack(run_id)
        _sql("UPDATE runs SET status = 'failed' WHERE id = :i", i=run_id)


def _get_heartbeat(run_id: int) -> Any:
    return _sql("SELECT heartbeat_at FROM runs WHERE id = :i", i=run_id)[0][0]


def test_cli_status_and_resolve_only_change_a_check_run(dead_worker: tuple[str, str], head_revision: None) -> None:
    from app import recovery

    check = _insert_run(status="check", error=recovery.CHECK_UNKNOWN, worker="0123456789ab-0123456789ab")
    done = _insert_run(status="done", worker="0123456789ab-0123456789ab")
    out = recovery.status()
    assert check in [r["id"] for r in out["check"]] and done not in [r["id"] for r in out["check"]]
    assert recovery.main(["resolve", str(done), "--failed"]) == 1 and _get_run(done)["status"] == "done"
    assert recovery.main(["resolve", str(check), "--failed"]) == 0
    row = _get_run(check)
    assert row["status"] == "failed" and "관리자가 확인해 실패로 정리" in row["error"]
    assert recovery.main(["resolve", str(check), "--failed"]) == 1  # 한 번만


def test_cli_recover_settles_finished_runs_while_servers_are_stopped(
    dead_worker: tuple[str, str], capsys: pytest.CaptureFixture[str], head_revision: None
) -> None:
    """롤백 전(서버를 멈춘 뒤) 한 번 정리: 같은 결과 폴더의 끝난 담당은 근거대로 확정하고, 이전 판 실행(담당 없음)은
    그대로 둔다 — 서버를 다시 띄우지 않아도 0020 downgrade 전에 정리할 수 있다."""
    from app import recovery

    worker, proc = dead_worker
    dead = _case(worker, proc, "cli_dead", None, None, b"partial")
    legacy = _insert_run(worker=None)
    capsys.readouterr()
    assert recovery.main(["recover"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["failed"] >= 1 and out["complete"] is True and out["waiting"] >= 1
    row = _get_run(dead["id"])
    assert row["status"] == "failed" and row["error"] == recovery.FAILED_UNSAVED
    assert not dead["tmp"].exists()  # 그 실행에 연결된 임시 파일만 지움
    assert _get_run(legacy)["status"] == "running"  # 이전 판 실행은 시각만으로 바꾸지 않음
    assert [r["id"] for r in recovery.status()["running"]].count(legacy) == 1
