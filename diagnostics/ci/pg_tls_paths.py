"""실제 진입 경로의 TLS 옵션 전달 확인 (2026-10-27 2차 지시 3-3) — pg_tls_check.sh가 띄운 일회용 PostgreSQL(TLS·hostssl만).

psycopg 매트릭스는 드라이버 직접 연결이다. 앱·app.dbcheck·Alembic·app.dbcopy가 실제로 쓰는 연결 경로가 주소의 TLS 옵션을
그대로 전달하는지 정상 1건 + 대표 거절(호스트 이름 불일치: verify-full만 막는 경우) 1건으로 본다. 공통 경로의 소스 근거:
app/config.py `_database`(앱 엔진·Alembic env.py·app.dbcopy가 공유 — 드라이버 이름만 psycopg로 바꾸고 질의 문자열은 그대로),
app/dbcheck.py(같은 방식 `make_url().set(drivername=…)`).
확인: 정상 TLS에서는 조회·마이그레이션·dry-run이 되고, 잘못된 인증서에서는 성공 응답·빈 SQLite 대체·스키마 변경·업무 자료 변경이
없으며 출력에 주소·비밀번호가 없다. 실제 Neon이 아니다.
환경: PGTLS_PORT, PGTLS_CA, PGTLS_PASSWORD, PGTLS_BACKEND(backend 폴더), PGTLS_PYTHON(선택). 출력: <out>/pg-tls-paths.json·.md"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg

OUT = Path(sys.argv[1])
PORT = int(os.environ["PGTLS_PORT"])
CA, PW = os.environ["PGTLS_CA"], os.environ["PGTLS_PASSWORD"]
BACKEND = Path(os.environ["PGTLS_BACKEND"]).resolve()
PY = os.environ.get("PGTLS_PYTHON", sys.executable)
APP_PORT = int(os.environ.get("PGTLS_APP_PORT", "18555"))
SECRETS = (PW, CA)
WORK = Path(tempfile.mkdtemp(prefix="pgtls-paths-"))
ROWS: list[dict] = []


def url(db: str, *, host: str = "localhost", mode: str = "verify-full") -> str:
    return f"postgresql://postgres:{PW}@{host}:{PORT}/{db}?sslmode={mode}&sslrootcert={CA}"


GOOD = {"host": "localhost", "mode": "verify-full"}
BAD = {"host": "127.0.0.1", "mode": "verify-full"}  # 인증서 이름(localhost)과 다른 접속 이름 → verify-full이 거절


def scrub(text: str) -> str:
    for s in SECRETS:
        text = text.replace(s, "<지움>")
    return text


def admin(sql: str) -> list:
    with psycopg.connect(url("postgres"), autocommit=True, connect_timeout=10) as con:
        cur = con.execute(sql)
        return cur.fetchall() if cur.description else []


def fresh_db(name: str) -> str:
    admin(f'DROP DATABASE IF EXISTS "{name}"')
    admin(f'CREATE DATABASE "{name}"')
    return name


def tables(db: str) -> list[str]:
    with psycopg.connect(url(db), connect_timeout=10) as con:
        return [r[0] for r in con.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")]


def revision(db: str) -> str | None:
    if "alembic_version" not in tables(db):
        return None
    with psycopg.connect(url(db), connect_timeout=10) as con:
        return con.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def env(data_dir: Path, **urls: str) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MOAJWO_", "PGTLS_"))}
    e.update({"MOAJWO_DB_MODE": "external", "MOAJWO_DATA_DIR": str(data_dir), "PYTHONIOENCODING": "utf-8",
              "MOAJWO_DB_CONNECT_TIMEOUT_S": "8", "MOAJWO_MIGRATION_LOCK_TIMEOUT_S": "10"})  # fmt: skip
    e.update(urls)
    return e


def run(cmd: list[str], e: dict, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=BACKEND, env=e, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def rec(path: str, case: str, ok: bool, detail: dict) -> None:
    ROWS.append({"path": path, "case": case, "expected_met": ok, "detail": detail})
    print(f"{'기대 충족' if ok else '기대 어긋남'} | {path} | {case} | {json.dumps(detail, ensure_ascii=False)[:400]}")


def leak(*texts: str) -> bool:
    return any(PW in t or CA in t for t in texts if t)


# ---------------------------------------------------------------- Alembic (마이그레이션 주소 = DATABASE_URL 폴백)
def check_alembic() -> None:
    db = fresh_db("tls_alembic_ok")
    data = WORK / "alembic-ok"
    data.mkdir()
    r = run([PY, "-m", "alembic", "upgrade", "head"], env(data, DATABASE_URL=url(db, **GOOD)), timeout=300)
    rev = revision(db)
    rec("alembic", "정상 TLS(verify-full+우리 CA): 격리 DB 마이그레이션 → head 리비전", r.returncode == 0 and rev is not None and rev.startswith("00"),
        {"exit": r.returncode, "revision": rev, "leak": leak(r.stdout, r.stderr)})  # fmt: skip
    db2 = fresh_db("tls_alembic_bad")
    data2 = WORK / "alembic-bad"
    data2.mkdir()
    t0 = time.monotonic()
    r = run([PY, "-m", "alembic", "upgrade", "head"], env(data2, DATABASE_URL=url(db2, **BAD)), timeout=300)
    took = round(time.monotonic() - t0, 1)
    left = tables(db2)
    sqlite_made = list(data2.glob("*.db"))
    rec("alembic", "잘못된 TLS(호스트 이름 불일치): 실패하고 스키마·리비전 변경 없음, SQLite 대체 없음, 출력에 비밀값 없음",
        r.returncode != 0 and left == [] and not sqlite_made and not leak(r.stdout, r.stderr) and "does not match host name" in (r.stderr + r.stdout),
        {"exit": r.returncode, "seconds": took, "tables_left": left, "sqlite_files": [p.name for p in sqlite_made],
         "stderr_tail": scrub(r.stderr[-200:]).replace("\n", " ")})  # fmt: skip


# ---------------------------------------------------------------- 앱 (uvicorn app.main:app — /health · 가입 상태)
def _wait_health(port: int, limit: float = 40) -> tuple[int | None, str]:
    deadline = time.monotonic() + limit
    last: tuple[int | None, str] = (None, "")
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as res:
                return res.status, res.read().decode("utf-8", "replace")[:200]
        except urllib.error.HTTPError as exc:
            last = (exc.code, exc.read().decode("utf-8", "replace")[:200])
            if exc.code >= 500:
                return last
        except (urllib.error.URLError, ConnectionError, OSError):
            last = (None, "connect failed")
        time.sleep(0.5)
    return last


def _app(data: Path, dburl: str, port: int) -> tuple[subprocess.Popen, Path]:
    log = data / "app.log"
    p = subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=BACKEND,
                         env=env(data, DATABASE_URL=dburl), stdout=open(log, "w", encoding="utf-8"), stderr=subprocess.STDOUT)  # fmt: skip
    return p, log


def _stop(p: subprocess.Popen) -> None:
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(15)
        except subprocess.TimeoutExpired:
            p.kill()


def check_app() -> None:
    db = fresh_db("tls_app_ok")
    data = WORK / "app-ok"
    data.mkdir()
    r = run([PY, "-m", "alembic", "upgrade", "head"], env(data, DATABASE_URL=url(db, **GOOD)), timeout=300)
    assert r.returncode == 0, scrub(r.stderr[-300:])
    p, log = _app(data, url(db, **GOOD), APP_PORT)
    try:
        status, body = _wait_health(APP_PORT)
        with urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT}/auth/status", timeout=5) as res:
            auth = res.status, res.read().decode("utf-8", "replace")[:120]
    finally:
        _stop(p)
    rec("app", "정상 TLS: /health 200 database ok · /auth/status 200 (조회 가능)", status == 200 and '"database":"ok"' in body.replace(" ", "") and auth[0] == 200,
        {"health": status, "body": body, "auth_status": auth[0], "leak": leak(log.read_text(encoding="utf-8", errors="replace"))})  # fmt: skip
    db2 = fresh_db("tls_app_bad")
    data2 = WORK / "app-bad"
    data2.mkdir()
    p, log = _app(data2, url(db2, **BAD), APP_PORT + 1)
    try:
        status, body = _wait_health(APP_PORT + 1, limit=60)
        exited = p.poll()
    finally:
        _stop(p)
    text = log.read_text(encoding="utf-8", errors="replace")
    sqlite_made = list(data2.glob("*.db"))
    ok = (status != 200) and not sqlite_made and not leak(text) and (exited is not None or status == 503)
    rec("app", "잘못된 TLS: 성공 응답 없음(/health 503 또는 기동 실패) · 빈 SQLite 대체 없음 · 로그에 비밀값 없음", ok,
        {"health": status, "body": scrub(body), "process_exit": exited, "sqlite_files": [q.name for q in sqlite_made],
         "log_tail": scrub(text[-240:]).replace("\n", " ")})  # fmt: skip


# ---------------------------------------------------------------- app.dbcheck (앱 주소·마이그레이션 주소 같은 DB 확인)
def check_dbcheck() -> None:
    db = fresh_db("tls_dbcheck")
    data = WORK / "dbcheck"
    data.mkdir()
    good = env(data, DATABASE_URL=url(db, **GOOD), MOAJWO_MIGRATION_DATABASE_URL=url(db, **GOOD))
    r = run([PY, "-m", "app.dbcheck"], good, timeout=120)
    out = r.stdout
    rec("dbcheck", "정상 TLS 두 주소: 종료 0 · result same", r.returncode == 0 and '"same"' in out and not leak(out, r.stderr),
        {"exit": r.returncode, "stdout": scrub(out[:160]).replace("\n", " ")})  # fmt: skip
    bad = env(data, DATABASE_URL=url(db, **BAD), MOAJWO_MIGRATION_DATABASE_URL=url(db, **GOOD))
    t0 = time.monotonic()
    r = run([PY, "-m", "app.dbcheck"], bad, timeout=120)
    took = round(time.monotonic() - t0, 1)
    rec("dbcheck", "앱 주소가 잘못된 TLS: 제한 시간 안에 비정상 종료(0 아님) · 주소·비밀번호 노출 없음",
        r.returncode != 0 and took < 60 and not leak(r.stdout, r.stderr),
        {"exit": r.returncode, "seconds": took, "stdout": scrub(r.stdout[:200]).replace("\n", " ")})  # fmt: skip


# ---------------------------------------------------------------- app.dbcopy (dry-run — 직접 연결 주소, --target-env)
def check_dbcopy() -> None:
    src_dir = WORK / "dbcopy-src"
    src_dir.mkdir()
    r = run([PY, "-m", "alembic", "upgrade", "head"], {**env(src_dir), "MOAJWO_DB_MODE": "sqlite"}, timeout=300)
    assert r.returncode == 0, scrub(r.stderr[-300:])
    src = src_dir / "moajwo.db"
    con = sqlite3.connect(src)
    con.execute("INSERT INTO users (username, display_name, password_hash, role, is_active, status, failed_logins, auth_version)"
                " VALUES ('tls-u1', '합성', 'scrypt$synthetic', 'user', 1, 'active', 0, 0)")  # fmt: skip
    con.commit()
    con.close()
    sha_before = hashlib.sha256(src.read_bytes()).hexdigest()
    db = fresh_db("tls_dbcopy")
    data = WORK / "dbcopy-data"
    data.mkdir()
    r = run([PY, "-m", "alembic", "upgrade", "head"], env(data, DATABASE_URL=url(db, **GOOD)), timeout=300)
    assert r.returncode == 0, scrub(r.stderr[-300:])
    before = {t: admin_count(db, t) for t in ("users", "runs")}
    # app.config가 MOAJWO_DB_MODE=external이면 DATABASE_URL을 요구한다(없으면 빈 SQLite로 열지 않고 멈춤) — 대상은 --target-env로 따로 준다
    e = env(data, DATABASE_URL=url(db, **GOOD), PGTLS_T=url(db, **GOOD))
    r = run([PY, "-m", "app.dbcopy", "--source", f"sqlite:///{src.as_posix()}", "--target-env", "PGTLS_T"], e, timeout=300)
    rep = _json(r.stdout)
    rec("dbcopy", "정상 TLS dry-run: 종료 0 · 확인 단계 통과(표별 건수 읽음) · 대상 그대로",
        r.returncode == 0 and str(rep.get("result", "")).startswith("dry-run") and {t: admin_count(db, t) for t in before} == before and not leak(r.stdout, r.stderr),
        {"exit": r.returncode, "stage": rep.get("stage"), "result": rep.get("result"), "stderr_tail": scrub(r.stderr[-160:]).replace(chr(10), " ")})  # fmt: skip
    e = env(data, DATABASE_URL=url(db, **GOOD), PGTLS_T=url(db, **BAD))
    r = run([PY, "-m", "app.dbcopy", "--source", f"sqlite:///{src.as_posix()}", "--target-env", "PGTLS_T", "--apply"], e, timeout=300)
    rep = _json(r.stdout)
    rec("dbcopy", "잘못된 TLS --apply: '대상 접속' 단계에서 멈춤 · db_applied no · 원천 해시 불변 · 대상 건수 불변 · 출력에 비밀값 없음",
        r.returncode == 1 and rep.get("stage") == "대상 접속" and rep.get("db_applied") == "no"
        and hashlib.sha256(src.read_bytes()).hexdigest() == sha_before and {t: admin_count(db, t) for t in before} == before
        and not leak(r.stdout, r.stderr),
        {"exit": r.returncode, "stage": rep.get("stage"), "db_applied": rep.get("db_applied"), "error": scrub(str(rep.get("error", "")))[:160],
         "stderr_tail": scrub(r.stderr[-160:]).replace(chr(10), " ")})  # fmt: skip


def admin_count(db: str, table: str) -> int:
    with psycopg.connect(url(db), connect_timeout=10) as con:
        return con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]


def _json(text: str) -> dict:
    try:
        return json.loads(text)
    except ValueError:
        return {}


def main() -> int:
    for name, fn in (("alembic", check_alembic), ("app", check_app), ("dbcheck", check_dbcheck), ("dbcopy", check_dbcopy)):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — 한 경로의 도구 오류가 나머지를 막지 않게 (측정 불완전으로 기록)
            rec(name, "경로 확인 실행 오류 (측정 불완전)", False, {"error": scrub(f"{type(exc).__name__}: {exc}")[:300]})
    met = sum(r["expected_met"] for r in ROWS)
    (OUT / "pg-tls-paths.json").write_text(json.dumps({"rows": ROWS, "met": met, "total": len(ROWS)}, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"### 실제 진입 경로의 TLS 옵션 전달 — 충족 {met}/{len(ROWS)}", "", "| 경로 | 경우 | 판정 | 상세 |", "|---|---|---|---|"]
    md += [f"| {r['path']} | {r['case']} | {'충족' if r['expected_met'] else '❌'} | {json.dumps(r['detail'], ensure_ascii=False)[:200]} |" for r in ROWS]
    (OUT / "pg-tls-paths.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(md) + "\n")
    shutil.rmtree(WORK, ignore_errors=True)
    print(f"요약 [경로] | 충족 {met}/{len(ROWS)}")
    return 0 if met == len(ROWS) else 1


if __name__ == "__main__":
    sys.exit(main())
