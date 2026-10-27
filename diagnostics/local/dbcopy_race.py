"""이관 도구(app.dbcopy)의 동시 적용·잠금·대상 변경을 별도 프로세스로 확인한다 (2026-10-27 지시 6장).

합성 SQLite 원천과 일회용 PostgreSQL 대상(빈 DB — 시나리오마다 관리자 주소로 새로 만든다)에 실제
`python -m app.dbcopy --apply` 프로세스를 여러 개 띄운다. 순서는 sleep 운이 아니라 단계 게이트(MOAJWO_DBCOPY_GATE —
dbcopy._gate, 진단용 환경 변수)와 서버의 잠금 대기 관찰(pg_stat_activity.wait_event_type = 'Lock')로 정한다.
게이트가 없는 판(수정 전)에서는 게이트 시나리오를 '제어 불가'로 적고 자연 경합 반복(N2·N3)·앱 쓰기(W)·실패 지점(F)만 돈다.

시나리오
- G1 교착 간섭: B가 확인 단계(alembic_version을 읽은 뒤)에서 멈춘 동안 A가 잠금 단계(모든 표를 잠근 뒤)까지 간다.
  B를 풀면 표별 건수 조회가 A의 잠금에 막혀 기다리고(서버가 'Lock' 대기로 보인다), A를 풀면 A가 적용하고 B는 '이미 이관
  표시'로 거절된다. 앞 판은 이 순서에서 B의 확인 트랜잭션이 alembic_version의 읽기 잠금을 들고 있어 A의 마지막 잠금과
  서로 기다리는 교착(40P01)이 났다.
- G2 잠금 대기 한도: A가 잠금 단계에 멈춘 동안 B(--lock-timeout-s 2)를 시작 — B는 확인 단계에서 2초 안에 LockNotAvailable로
  멈추고(무한 대기 없음) A는 그 뒤 적용한다.
- G3 늦게 시작: A가 잠금 단계에 멈춘 동안 B 시작 → B는 확인 단계의 첫 읽기부터 기다림(서버 'Lock' 대기 관찰) → A 적용 →
  B '이미 이관 표시' 거절.
- N2·N3 자연 경합: 2개·3개를 같은 순간에 시작 (반복) — 적용 1·나머지 명확한 거절·교착 없음.
- W 적용 중 앱 쓰기: 다른 연결이 users에 쓰는 트랜잭션을 연 채로 두면 이관의 LOCK TABLE이 한도 안에 멈춘다(55P03).
- C 이관 직전 대상 변경: A가 확인 단계 뒤 게이트에서 멈춘 동안 users에 행을 넣음 → 잠금 안 재확인이 거절(덮어쓰지 않음).
- F 실패 지점(table:·sequence:first·sequence:all·compare·commit_lost_before·commit_lost_after): 각각 대상 건수·이관 표시·
  시퀀스와 원천 해시를 대조 (fail_at은 명령줄에 없어 copy()를 직접 부르는 래퍼로).
각 경우 종료 코드·stage·오류 종류(원문 없이)·대상 상태(표별 건수·이관 표시·시퀀스)·원천 sha256을 기록하고 PostgreSQL 서버
로그의 교착·잠금 대기 줄(log_lock_waits)을 함께 모은다. 비밀번호·주소는 환경 변수로만 받고 기록하지 않는다.

사용법: python dbcopy_race.py --backend <backend 폴더> --out <증거 폴더> --pgbin <PostgreSQL bin> --pglog <서버 로그 파일>
        [--only G1,N2,...] [--repeat 3]
        (환경 변수 RACE_ADMIN_URL: 관리자 주소 — DB 생성·삭제용, RACE_DB_PREFIX: 만들 DB 이름 접두)
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ap = argparse.ArgumentParser()
ap.add_argument("--backend", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--pgbin", required=True)
ap.add_argument("--pglog", required=True)
ap.add_argument("--only", default="")
ap.add_argument("--repeat", type=int, default=3)
ARGS = ap.parse_args()
BACKEND = Path(ARGS.backend).resolve()
OUT = Path(ARGS.out)
OUT.mkdir(parents=True, exist_ok=True)
PGBIN = Path(ARGS.pgbin)
PGLOG = Path(ARGS.pglog)
ONLY = {s for s in ARGS.only.split(",") if s}
ADMIN = os.environ["RACE_ADMIN_URL"]
PREFIX = os.environ.get("RACE_DB_PREFIX", "race")
LOG = OUT / "race.log"
RESULTS: list[dict] = []
WORK = Path(tempfile.mkdtemp(prefix="dbcopy-race-"))
MARKER_KEY = "dbcopy_applied"  # dbcopy.MARKER
REFUSALS = ("이관 표시", "이미 자료", "자료가 생겼습니다", "LockNotAvailable", "55P03")
USER_COLS = "(username, display_name, password_hash, role, is_active, status, failed_logins, auth_version)"


def say(text: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def record(sid: str, what: str, ok: bool, detail: dict) -> None:
    RESULTS.append({"scenario": sid, "check": what, "expected_met": ok, "detail": detail})
    say(f"{'기대 충족' if ok else '기대 어긋남'} | {sid} | {what} | {json.dumps(detail, ensure_ascii=False)[:900]}")


# ---------------------------------------------------------------- PostgreSQL (주소는 변수로만)


def _psql(url: str, sql: str) -> str:
    r = subprocess.run([str(PGBIN / "psql.exe"), url, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)  # fmt: skip
    if r.returncode != 0:
        raise RuntimeError(f"psql 실패 ({r.returncode}): {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def _db_url(name: str) -> str:
    parts = urlsplit(ADMIN)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, ""))


def fresh_db(tag: str) -> str:
    name = f"{PREFIX}_{tag}_{uuid.uuid4().hex[:6]}"
    _psql(ADMIN, f'CREATE DATABASE "{name}"')
    url = _db_url(name)
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=base_env(url),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")  # fmt: skip
    assert r.returncode == 0, r.stderr[-400:]
    return url


def drop_db(url: str) -> None:
    name = urlsplit(url).path.lstrip("/")
    _psql(ADMIN, f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{name}' AND pid <> pg_backend_pid()")
    _psql(ADMIN, f'DROP DATABASE IF EXISTS "{name}"')


def target_state(url: str) -> dict:
    tables = [t for t in _psql(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1").split("\n") if t]
    counts = {t: int(_psql(url, f'SELECT count(*) FROM "{t}"')) for t in tables}
    marker = _psql(url, f"SELECT count(*) FROM app_settings WHERE key = '{MARKER_KEY}'")
    seq = _psql(url, "SELECT last_value || ',' || is_called FROM users_id_seq")
    return {"counts": counts, "marker": int(marker or 0), "users_seq": seq}


def lock_waiters(url: str) -> int:
    return int(_psql(url, "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'") or 0)


def wait_lock_waiter(url: str, limit: float = 30) -> float | None:
    """서버가 잠금 대기 중인 백엔드를 보일 때까지 기다린다 — 걸린 초(없으면 None)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        if lock_waiters(url) >= 1:
            return round(time.monotonic() - t0, 2)
        time.sleep(0.1)
    return None


def base_env(target_url: str, app_name: str = "race", gate: Path | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MOAJWO_", "RACE_"))}
    env.update({"DATABASE_URL": target_url, "MOAJWO_DB_MODE": "external", "PYTHONIOENCODING": "utf-8",
                "PGAPPNAME": app_name, "RACE_T": target_url, "MOAJWO_DATA_DIR": str(WORK / "data")})  # fmt: skip
    if gate is not None:
        env["MOAJWO_DBCOPY_GATE"] = str(gate)
    return env


def pglog_pos() -> int:
    return len(PGLOG.read_bytes()) if PGLOG.exists() else 0


def pglog_since(pos: int) -> list[str]:
    data = PGLOG.read_bytes() if PGLOG.exists() else b""
    text = data[pos:].decode("utf-8", "replace")
    keys = ("deadlock", "still waiting", "acquired", "canceling", "terminating")
    return [ln[:240] for ln in text.splitlines() if any(k in ln for k in keys)][:40]


def gates_supported() -> bool:
    return "MOAJWO_DBCOPY_GATE" in (BACKEND / "app" / "dbcopy.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 원천 (합성 SQLite)


def make_source(data_dir: Path, rows: int = 3000) -> tuple[Path, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MOAJWO_", "RACE_"))}
    env.update({"MOAJWO_DATA_DIR": str(data_dir), "PYTHONIOENCODING": "utf-8"})
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")  # fmt: skip
    assert r.returncode == 0, r.stderr[-400:]
    path = data_dir / "moajwo.db"
    con = sqlite3.connect(path)
    for i in range(1, rows + 1):  # 합성 계정 — 이름·값은 합성 표식뿐
        con.execute(
            f"INSERT INTO users {USER_COLS} VALUES (?, ?, 'scrypt$synthetic', 'user', 1, 'active', 0, 0)",
            (f"race-u{i:05d}", f"합성 사용자 {i}"),
        )
    con.commit()
    con.close()
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- 실행


def start(target_url: str, source: Path, tag: str, gate: Path | None = None, lock_s: int = 8) -> subprocess.Popen:
    report = WORK / f"report-{tag}-{uuid.uuid4().hex[:6]}.json"
    cmd = [sys.executable, "-X", "utf8", "-m", "app.dbcopy", "--source", f"sqlite:///{source.as_posix()}", "--target-env", "RACE_T",
           "--report", str(report), "--lock-timeout-s", str(lock_s), "--apply"]  # fmt: skip
    p = subprocess.Popen(cmd, cwd=BACKEND, env=base_env(target_url, f"dbcopy-{tag}", gate), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")  # fmt: skip
    p.report = report  # type: ignore[attr-defined]
    p.tag = tag  # type: ignore[attr-defined]
    return p


def finish(p: subprocess.Popen, timeout: float = 300) -> dict:
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, err = p.communicate()
    rep: dict = {}
    try:
        rep = json.loads(p.report.read_text(encoding="utf-8")) if p.report.exists() else json.loads(out)  # type: ignore[attr-defined]
    except (ValueError, OSError):
        rep = {}
    blob = (rep.get("error") or "") + " " + (rep.get("result") or "") + " " + (err or "")
    kind = next((m for m in ("DeadlockDetected", "40P01", *REFUSALS, "적용함") if m in blob), "")
    return {"tag": p.tag, "exit": p.returncode, "stage": rep.get("stage"), "db_applied": rep.get("db_applied"),  # type: ignore[attr-defined]
            "kind": kind, "error": (rep.get("error") or "")[:160], "result": (rep.get("result") or "")[:80],
            "after_commit": rep.get("after_commit"),
            "stderr_tail": (err or "")[-200:].replace("\n", " ") if p.returncode not in (0, 1) else ""}  # fmt: skip


def wait_gate(folder: Path, stage: str, limit: float = 120) -> bool:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if any(folder.glob(f"arrived-{stage}-*")):
            return True
        time.sleep(0.02)
    return False


def go(folder: Path, *stages: str) -> None:
    for s in stages:
        (folder / f"go-{s}").write_text("x", encoding="utf-8")


def gate_dir(name: str) -> Path:
    d = WORK / f"gate-{name}-{uuid.uuid4().hex[:4]}"
    d.mkdir()
    return d


def is_applied(r: dict) -> bool:
    return r["exit"] == 0 and r["db_applied"] == "yes" and bool(r["after_commit"]) and r["after_commit"].get("verified") is True


def is_refused(r: dict) -> bool:
    return r["exit"] == 1 and r["db_applied"] == "no" and r["kind"] in REFUSALS


def has_deadlock(runs: list[dict], lines: list[str]) -> bool:
    return any(r["kind"] in ("DeadlockDetected", "40P01") for r in runs) or any("deadlock detected" in ln for ln in lines)


# ---------------------------------------------------------------- 게이트 시나리오 (수정 뒤 판에서만 제어 가능)


def uncontrollable(sid: str, what: str) -> None:
    record(sid, f"{what} — 이 판에는 단계 게이트(MOAJWO_DBCOPY_GATE)가 없어 순서를 제어할 수 없음 (자연 경합 N2·N3만)", False,
           {"gates": False})  # fmt: skip


def g1(source: Path, source_sha: str) -> None:
    if not gates_supported():
        return uncontrollable("G1", "교착 간섭(B 확인 단계 정지 → A 잠금 단계 → B 재개)")
    url = fresh_db("g1")
    try:
        pos = pglog_pos()
        ga, gb = gate_dir("g1a"), gate_dir("g1b")
        b = start(url, source, "g1B", gb)
        assert wait_gate(gb, "revision"), "B가 확인 단계(리비전 읽은 뒤)에 이르지 않음"
        a = start(url, source, "g1A", ga)
        go(ga, "revision", "checked")
        assert wait_gate(ga, "locked"), "A가 잠금 단계(모든 표 잠근 뒤)에 이르지 않음"  # B가 아무 잠금도 들고 있지 않아야 가능
        go(gb, "revision", "checked", "locked")  # B 재개 — 표별 건수 조회가 A의 잠금을 기다린다
        waited = wait_lock_waiter(url)
        go(ga, "locked")  # A 적용
        ra, rb = finish(a), finish(b)
        lines = pglog_since(pos)
        state = target_state(url)
        ok = (is_applied(ra) and is_refused(rb) and rb["kind"] == "이관 표시" and rb["stage"] == "확인" and waited is not None
              and not has_deadlock([ra, rb], lines) and state["marker"] == 1 and state["counts"].get("users") == 3000)  # fmt: skip
        record("G1", "교착 간섭 순서: A 적용(verified)·B는 서버 'Lock' 대기 뒤 확인 단계에서 '이미 이관 표시'로 거절 · 교착(40P01) 없음 · 이관 표시 1 · users 3000",
               ok and hashlib.sha256(source.read_bytes()).hexdigest() == source_sha,
               {"A": ra, "B": rb, "B_lock_wait_seen_after_s": waited, "pglog": lines[:12], "state": {k: state[k] for k in ("marker", "users_seq")},
                "users": state["counts"].get("users")})  # fmt: skip
    finally:
        drop_db(url)


def g2(source: Path, source_sha: str) -> None:
    if not gates_supported():
        return uncontrollable("G2", "잠금 대기 한도(A 잠금 단계 정지 → B --lock-timeout-s 2)")
    url = fresh_db("g2")
    try:
        pos = pglog_pos()
        ga = gate_dir("g2a")
        a = start(url, source, "g2A", ga)
        go(ga, "revision", "checked")
        assert wait_gate(ga, "locked"), "A가 잠금 단계에 이르지 않음"
        t0 = time.monotonic()
        b = start(url, source, "g2B", lock_s=2)
        rb = finish(b, timeout=120)
        took = round(time.monotonic() - t0, 1)
        go(ga, "locked")
        ra = finish(a)
        lines = pglog_since(pos)
        state = target_state(url)
        ok = (is_refused(rb) and rb["kind"] in ("LockNotAvailable", "55P03") and rb["stage"] == "확인" and took < 20
              and is_applied(ra) and not has_deadlock([ra, rb], lines) and state["marker"] == 1)  # fmt: skip
        record("G2", "A가 잠금을 든 동안 시작한 B(한도 2초): 확인 단계에서 LockNotAvailable로 멈춤(무한 대기 없음) · A는 뒤에 적용 · 교착 없음",
               ok, {"A": ra, "B": rb, "B_seconds": took, "pglog": lines[:10], "marker": state["marker"]})  # fmt: skip
    finally:
        drop_db(url)


def g3(source: Path, source_sha: str) -> None:
    if not gates_supported():
        return uncontrollable("G3", "늦게 시작(A 잠금 단계 정지 → B 시작·대기 → A 적용 → B 거절)")
    url = fresh_db("g3")
    try:
        pos = pglog_pos()
        ga, gb = gate_dir("g3a"), gate_dir("g3b")
        a = start(url, source, "g3A", ga)
        go(ga, "revision", "checked")
        assert wait_gate(ga, "locked"), "A가 잠금 단계에 이르지 않음"
        b = start(url, source, "g3B", gb, lock_s=20)
        go(gb, "revision", "checked", "locked")
        waited = wait_lock_waiter(url)  # B의 첫 읽기(alembic_version)가 A의 잠금을 기다린다
        go(ga, "locked")
        ra, rb = finish(a), finish(b)
        lines = pglog_since(pos)
        state = target_state(url)
        ok = (is_applied(ra) and is_refused(rb) and rb["kind"] == "이관 표시" and waited is not None
              and not has_deadlock([ra, rb], lines) and state["marker"] == 1)  # fmt: skip
        record("G3", "A 잠금 중 시작한 B: 서버 'Lock' 대기 뒤 A가 적용하면 '이미 이관 표시'로 거절 · 교착 없음 · 이관 표시 1", ok,
               {"A": ra, "B": rb, "B_lock_wait_seen_after_s": waited, "pglog": lines[:10], "marker": state["marker"]})  # fmt: skip
    finally:
        drop_db(url)


def natural(source: Path, source_sha: str, n: int, repeat: int) -> None:
    for k in range(1, repeat + 1):
        url = fresh_db(f"n{n}")
        try:
            pos = pglog_pos()
            procs = [start(url, source, f"n{n}-{k}-{i}") for i in range(n)]
            rs = [finish(p) for p in procs]
            lines = pglog_since(pos)
            state = target_state(url)
            applied = [r for r in rs if is_applied(r)]
            others = [r for r in rs if r not in applied]
            deadlock = has_deadlock(rs, lines)
            ok = len(applied) == 1 and all(is_refused(r) for r in others) and not deadlock and state["marker"] == 1
            record(f"N{n}", f"자연 경합 {n}개 동시 ({k}/{repeat}): 적용 1(verified)·나머지 명확한 거절·교착 없음·이관 표시 1", ok,
                   {"runs": rs, "deadlock": deadlock, "pglog": lines[:10], "marker": state["marker"], "users": state["counts"].get("users")})  # fmt: skip
        finally:
            drop_db(url)


def writer(source: Path, source_sha: str) -> None:
    url = fresh_db("w")
    try:
        pos = pglog_pos()
        hold = subprocess.Popen([str(PGBIN / "psql.exe"), url, "-At", "-c",
                                 f"BEGIN; INSERT INTO users {USER_COLS} VALUES ('race-writer', 'w', 'scrypt$synthetic', 'user', true, 'active', 0, 0);"
                                 " SELECT pg_sleep(40); ROLLBACK;"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)  # fmt: skip
        time.sleep(1.0)
        t0 = time.monotonic()
        r = finish(start(url, source, "w", lock_s=3))
        took = round(time.monotonic() - t0, 1)
        hold.kill()
        lines = pglog_since(pos)
        state = target_state(url)
        ok = (is_refused(r) and r["kind"] in ("LockNotAvailable", "55P03") and r["stage"] == "잠금" and took < 25
              and state["marker"] == 0 and state["counts"].get("users") == 0)  # fmt: skip
        record("W", "앱 쓰기 트랜잭션이 열린 동안 적용: 잠금 단계에서 한도(3초) 안에 LockNotAvailable로 멈춤 · 대상 비어 있음 · 이관 표시 0",
               ok, {"run": r, "seconds": took, "pglog": lines[:6], "marker": state["marker"], "users": state["counts"].get("users")})  # fmt: skip
    finally:
        drop_db(url)


def changed(source: Path, source_sha: str) -> None:
    if not gates_supported():
        return uncontrollable("C", "이관 직전 대상 변경(확인 뒤 정지 → 행 삽입 → 잠금 안 재확인)")
    url = fresh_db("c")
    try:
        g = gate_dir("c")
        a = start(url, source, "c", g)
        go(g, "revision")
        assert wait_gate(g, "checked"), "확인 단계 뒤 게이트에 이르지 않음"
        _psql(url, f"INSERT INTO users {USER_COLS} VALUES ('race-intruder', 'i', 'scrypt$synthetic', 'user', true, 'active', 0, 0)")
        go(g, "checked", "locked")
        r = finish(a)
        state = target_state(url)
        ok = (is_refused(r) and r["stage"] == "잠금" and r["kind"] in ("이미 자료", "자료가 생겼습니다")
              and state["marker"] == 0 and state["counts"].get("users") == 1)  # fmt: skip
        record("C", "확인 뒤·잠금 전에 대상에 행이 생김: 잠금 안 재확인이 거절(덮어쓰지 않음) · 이관 표시 0 · 침입 행 1만 남음", ok,
               {"run": r, "marker": state["marker"], "users": state["counts"].get("users")})  # fmt: skip
    finally:
        drop_db(url)


def failures(source: Path, source_sha: str) -> None:
    wrapper = WORK / "fail_at.py"
    wrapper.write_text(
        "import os, sys\n"
        "sys.path.insert(0, os.environ['RACE_BACKEND'])\n"
        "from app.dbcopy import CopyError, _dump, copy, safe_error\n"
        "report = {}\n"
        "try:\n"
        "    copy(sys.argv[1], os.environ['RACE_T'], apply=True, fail_at=sys.argv[2], report=report)\n"
        "    code = 0\n"
        "except CopyError as exc:\n"
        "    report['error'] = str(exc)\n"
        "    code = 1\n"
        "except Exception as exc:\n"
        "    report['error'] = f\"[{report.get('stage')}] {safe_error(exc)}\"\n"
        "    code = 5\n"
        "print(_dump(report))\n"
        "sys.exit(code)\n",
        encoding="utf-8",
    )
    for point, expect_applied in (("table:source_files", "no"), ("sequence:first", "no"), ("sequence:all", "no"), ("compare", "no"),
                                  ("commit_lost_before", "no"), ("commit_lost_after", "yes")):  # fmt: skip
        url = fresh_db("f")
        try:
            before = target_state(url)
            env = base_env(url, f"dbcopy-f-{point}")
            env["RACE_BACKEND"] = str(BACKEND)
            r = subprocess.run([sys.executable, "-X", "utf8", str(wrapper), f"sqlite:///{source.as_posix()}", point], cwd=BACKEND, env=env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)  # fmt: skip
            try:
                rep = json.loads(r.stdout)
            except ValueError:
                rep = {"error": r.stdout[-200:]}
            after = target_state(url)
            if expect_applied == "no":
                ok = r.returncode == 1 and rep.get("db_applied") == "no" and after == before
                what = f"실패 지점 {point}: 되돌림 — 대상 건수·이관 표시·시퀀스가 실패 전과 같음, db_applied no"
            else:
                ok = rep.get("db_applied") == "yes" and after["marker"] == 1 and (rep.get("after_commit") or {}).get("verified") is True
                what = f"실패 지점 {point}: 커밋은 됐고 응답만 잃음 — 새 연결 확인으로 적용함(verified), 이관 표시 1"
            record("F", what, ok, {"exit": r.returncode, "stage": rep.get("stage"), "db_applied": rep.get("db_applied"),
                                   "error": (rep.get("error") or "")[:120], "result": (rep.get("result") or "")[:80],
                                   "users_before": before["counts"].get("users"), "users_after": after["counts"].get("users"),
                                   "marker": after["marker"], "seq": after["users_seq"]})  # fmt: skip
        finally:
            drop_db(url)
    record("F", "원천 SQLite 해시 불변", hashlib.sha256(source.read_bytes()).hexdigest() == source_sha, {})


def main() -> int:
    head = subprocess.run(["git", "-C", str(BACKEND), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(BACKEND), "status", "--short", "app/dbcopy.py"], capture_output=True, text=True).stdout.strip()
    say(f"코드 {BACKEND} HEAD {head[:12]} (app/dbcopy.py 작업 중 변경: {'있음' if dirty else '없음'}) · 게이트 지원 {gates_supported()} · 작업 폴더 {WORK}")
    src_dir = WORK / "src"
    src_dir.mkdir(parents=True)
    src, sha = make_source(src_dir)
    say(f"원천 합성 SQLite 준비 users 3000 (sha256 {sha[:16]})")
    scenarios = {"G1": lambda: g1(src, sha), "G2": lambda: g2(src, sha), "G3": lambda: g3(src, sha),
                 "N2": lambda: natural(src, sha, 2, ARGS.repeat), "N3": lambda: natural(src, sha, 3, ARGS.repeat),
                 "W": lambda: writer(src, sha), "C": lambda: changed(src, sha), "F": lambda: failures(src, sha)}  # fmt: skip
    for sid, fn in scenarios.items():
        if ONLY and sid not in ONLY:
            continue
        say(f"--- {sid}")
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — 한 시나리오의 도구 오류가 나머지를 막지 않게
            record(sid, "시나리오 실행 오류 (도구 또는 제품 — 기록 확인)", False, {"error": f"{type(e).__name__}: {str(e)[:300]}"})
    (OUT / "race.json").write_text(
        json.dumps({"head": head, "dbcopy_dirty": bool(dirty), "gates": gates_supported(), "results": RESULTS}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    met = sum(r["expected_met"] for r in RESULTS)
    say(f"요약 | {met}/{len(RESULTS)} 기대 충족 | 어긋남 {[r['scenario'] + ' ' + r['check'][:30] for r in RESULTS if not r['expected_met']]}")
    shutil.rmtree(WORK, ignore_errors=True)
    return 0 if met == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
