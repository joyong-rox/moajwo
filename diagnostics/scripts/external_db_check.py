"""O-03 계정 보존 확인 (실제 프로세스·실제 PostgreSQL) — 격리 시험 DB에서만 돌린다. 운영·시연 DB 주소를 넣지 않는다.

순서 (단계마다 결과를 results.tsv에 남기고, 실패해도 다음 단계로 간다):
 1. SQLite 앱(uvicorn)으로 최초 소유자·일반 계정 A·B·승인 대기 계정과 업무 기록(업로드·확정·검토·통합·묶기·분석)을 만든다.
 2. 빈 PostgreSQL에 마이그레이션(스키마 소유 계정) → 이관 dry-run → 적용 → 다시 적용(거절) → 다른 빈 DB에서
    중간 실패(되돌림) 확인.
 3. '재배포': 빈 데이터 폴더 + 외부 DB(앱 계정, 데이터만 읽고 쓰는 권한)로 새 프로세스 → 같은 계정 로그인·역할·상태,
    소유 관계(남의 자료 404·관리자 조회), 지워진 원본·결과 파일의 안내(410), 새 가입·새 업로드(번호 충돌 없음).
 4. '재시작': 같은 데이터 폴더로 다시 띄워 로그인·방금 올린 파일 유지.
 5. '휴면 뒤 재접속': 관리자 계정으로 앱 연결을 모두 끊은 뒤(pg_terminate_backend) 요청 — 성공과 걸린 시간.
 6. 장애: 앱 계정 로그인 금지(인증 실패) 동안 상태·로그인·최초 설정이 503이고 '설정 필요'가 아님 → 되돌리면 회복.
    DB 주소 없이 외부 모드로 띄우면 기동하지 않고 SQLite 파일도 만들지 않음.
 7. 빈 PostgreSQL에서 최초 소유자 설정을 동시에 8번 — 소유자는 하나.
 8. 논리 백업(pg_dump -Fc) → 다른 빈 DB로 복원 → 그 DB로 띄워 로그인·건수 확인, 원래 DB는 그대로.
비밀번호는 실행 때 만들고 출력·저장하지 않는다. 합성 자료만 쓴다.
사용법: python external_db_check.py --admin-url <슈퍼유저 주소> --backend <backend 폴더> --out <폴더>
        [--python <앱 파이썬>] [--pg-bin <pg_dump·pg_restore 폴더> | --pg-docker <이미지>] [--port 8190]
"""

from __future__ import annotations

import argparse
import csv
import http.cookiejar
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from sqlalchemy import create_engine, text

RESULTS: list[tuple[str, str, str]] = []


def res(item: str, value: object, basis: str = "") -> None:
    RESULTS.append((item, str(value), basis))
    print(f"{item}\t{value}\t{basis}", flush=True)


class Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, body: object = None, files: list[tuple[str, bytes]] | None = None):
        headers, data = {}, None
        if files is not None:
            b = secrets.token_hex(8)
            parts = []
            for name, content in files:
                parts.append(f'--{b}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
                             "Content-Type: text/csv\r\n\r\n".encode() + content + b"\r\n")  # fmt: skip
            data = b"".join(parts) + f"--{b}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={b}"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.op.open(req, timeout=120) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw[:1] in b"[{" else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw


def with_password(url: str, user: str, password: str, db: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, f"{user}:{quote(password, safe='')}@{host}", f"/{db}", parts.query, ""))


class Server:
    """uvicorn 한 프로세스 (모델 호출 차단, 127.0.0.1만)."""

    def __init__(self, a: argparse.Namespace, data: Path, port: int, env: dict[str, str], log: Path) -> None:
        self.a, self.data, self.port, self.env, self.log = a, data, port, env, log
        self.proc: subprocess.Popen | None = None

    def start(self, migrate: bool = True) -> bool:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MOAJWO_"))}
        env.update({"MOAJWO_DATA_DIR": str(self.data), "OLLAMA_BASE_URL": "http://127.0.0.1:9",
                    "PYTHONIOENCODING": "utf-8", **self.env})  # fmt: skip
        self.data.mkdir(parents=True, exist_ok=True)
        out = self.log.open("a", encoding="utf-8")
        if migrate:
            m = subprocess.run([self.a.python, "-m", "alembic", "upgrade", "head"], cwd=self.a.backend, env=env,
                               stdout=out, stderr=subprocess.STDOUT)  # fmt: skip
            if m.returncode != 0:
                return False
        self.proc = subprocess.Popen([self.a.python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                      "--port", str(self.port)], cwd=self.a.backend, env=env, stdout=out,
                                     stderr=subprocess.STDOUT)  # fmt: skip
        for _ in range(120):
            if self.proc.poll() is not None:
                return False
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2)
                return True
            except urllib.error.HTTPError:
                return True  # 503이어도 프로세스는 떴다
            except OSError:
                time.sleep(0.5)
        return False

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    @property
    def client(self) -> Client:
        return Client(f"http://127.0.0.1:{self.port}")


def admin_exec(admin: str, *sql: str, db: str | None = None) -> None:
    parts = urlsplit(admin)
    url = urlunsplit((parts.scheme, parts.netloc, f"/{db}", parts.query, "")) if db else admin
    e = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1), isolation_level="AUTOCOMMIT")
    with e.connect() as c:
        for s in sql:
            c.execute(text(s))
    e.dispose()


def fresh_db(admin: str, name: str) -> None:
    admin_exec(admin, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', f'CREATE DATABASE "{name}" OWNER moajwo_owner')
    admin_exec(admin, "GRANT USAGE ON SCHEMA public TO moajwo_app",
               "ALTER DEFAULT PRIVILEGES FOR ROLE moajwo_owner IN SCHEMA public "
               "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO moajwo_app",
               "ALTER DEFAULT PRIVILEGES FOR ROLE moajwo_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO moajwo_app",
               db=name)  # fmt: skip


def count(url: str, table: str) -> int:
    e = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    with e.connect() as c:
        n = c.execute(text(f'SELECT count(*) FROM "{table}"')).scalar()
    e.dispose()
    return n


def csv_bytes(rows: list[list]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("utf-8")


def login(server: Server, user: str, pw: str) -> tuple[Client, int, dict]:
    c = server.client
    s, body = c.call("POST", "/auth/login", {"username": user, "password": pw})
    return c, s, body if isinstance(body, dict) else {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-url", required=True)
    ap.add_argument("--backend", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--pg-bin", type=Path)
    ap.add_argument("--pg-docker")
    ap.add_argument("--port", type=int, default=8190)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    log = a.out / "servers.log"
    work = Path(tempfile.mkdtemp(prefix="extdb-"))
    admin = a.admin_url
    pw = {k: "Ex-" + secrets.token_urlsafe(12) + "!%" for k in ("owner", "a", "b", "pending", "new", "role_o", "role_a")}
    # 스키마 소유 계정(마이그레이션·이관)과 앱 계정(데이터만) — 비밀번호에 URL 특수문자를 넣어 인코딩도 확인
    pw["role_o"] += "@:/"
    pw["role_a"] += "?&#"
    for role, key in (("moajwo_owner", "role_o"), ("moajwo_app", "role_a")):
        admin_exec(admin, f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
                          f"CREATE ROLE {role} LOGIN; END IF; END $$",
                   f"ALTER ROLE {role} LOGIN PASSWORD '{pw[key]}'")  # fmt: skip
    owner_url = lambda db: with_password(admin, "moajwo_owner", pw["role_o"], db)  # noqa: E731
    app_url = lambda db: with_password(admin, "moajwo_app", pw["role_a"], db)  # noqa: E731

    # ---------------------------------------------------------------- 1. SQLite 원천
    src_data = work / "src"
    sq = Server(a, src_data, a.port, {}, log)
    res("sqlite_start", sq.start())
    c = sq.client
    s, _ = c.call("POST", "/auth/setup", {"username": "ex-owner", "display_name": "소유자", "password": pw["owner"]})
    res("sqlite_setup", s)
    for u in ("a", "b", "pending"):
        c.call("POST", "/users", {"username": f"ex-{u}", "display_name": f"계정 {u}",
                                  "status": "pending" if u == "pending" else "active", "password": pw[u]})  # fmt: skip
    ca, s, me_a = login(sq, "ex-a", pw["a"])
    rows = [["시도", "시군구", "인구"], ["서울특별시", "종로구", 100], ["서울특별시", "중구", 200]]
    s, up = ca.call("POST", "/files", files=[("인구.csv", csv_bytes(rows))])
    fid = up[0]["id"]
    cols = ca.call("POST", f"/files/{fid}/mapping?use_llm=false")[1]["sheets"][0]["columns"]
    ca.call("PUT", f"/files/{fid}/mapping/decisions",
            {"decisions": [{"sheet_id": "s0", "column_id": x["column_id"], "field_key": x["field_key"]} for x in cols]})
    s, review = ca.call("POST", "/reviews", {"file_ids": [fid]})
    rid = review["id"]
    for item in review["items"]:  # 확인 필요 항목: 후보가 있으면 첫 후보로 승인, 없으면 거부 (합성 자료)
        if item["category"] == "pending":
            options = (item.get("payload") or {}).get("candidates") or []
            choice = options[0].get("code") or options[0].get("key") if options else None
            decision = {"decision": "approved", "choice": choice} if choice else {"decision": "rejected"}
            ca.call("POST", f"/reviews/{rid}/items/{item['id']}/decision", decision)
    s_done, _ = ca.call("POST", f"/reviews/{rid}/complete")
    s_run, run = ca.call("POST", f"/reviews/{rid}/integrate", {"include_unmapped": True})
    run_id = run.get("id") if isinstance(run, dict) else None
    plan = ca.call("POST", "/bundle/plan", {"file_ids": [fid]})[1]
    s_b, bundle = ca.call("POST", "/bundle/run", {"file_ids": [fid], "expected_basis": plan["basis"]})
    res("sqlite_records", f"file={fid} review={rid} complete={s_done} integrate={s_run} run={run_id} bundle={s_b}")
    cb, _, _ = login(sq, "ex-b", pw["b"])
    cb.call("POST", "/files", files=[("B.csv", csv_bytes(rows))])
    sq.stop()
    src_db = src_data / "moajwo.db"

    # ---------------------------------------------------------------- 2. 이관
    fresh_db(admin, "extdb_main")
    env_mig = {"DATABASE_URL": owner_url("extdb_main"), "MOAJWO_DB_MODE": "external"}
    m = subprocess.run([a.python, "-m", "alembic", "upgrade", "head"], cwd=a.backend, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, **env_mig, "MOAJWO_DATA_DIR": str(work / "mig"), "PYTHONIOENCODING": "utf-8"})  # fmt: skip
    res("pg_migrate_head", m.returncode == 0, m.stdout[-200:] if m.returncode else "빈 DB → head")

    def dbcopy(target: str, *extra: str) -> tuple[int, dict]:
        p = subprocess.run([a.python, "-m", "app.dbcopy", "--source", f"sqlite:///{src_db.as_posix()}", "--target",
                            target, *extra], cwd=a.backend, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, "MOAJWO_DATA_DIR": str(work / "copy"), "PYTHONIOENCODING": "utf-8"})  # fmt: skip
        try:
            return p.returncode, json.loads(p.stdout)
        except ValueError:
            return p.returncode, {"error": p.stderr[-300:]}

    code, dry = dbcopy(owner_url("extdb_main"))
    res("copy_dry_run", f"exit={code} {dry.get('result') or dry.get('error')}",
        f"users {dry.get('counts', {}).get('users', {}).get('source')}, 대상 쓰기 없음={count(owner_url('extdb_main'), 'users') == 0}")  # fmt: skip
    code, applied = dbcopy(owner_url("extdb_main"), "--apply", "--report", str(a.out / "dbcopy-report.json"))
    res("copy_apply", f"exit={code} {applied.get('result') or applied.get('error')}", f"{applied.get('seconds')}s")
    digests = applied.get("digests", {})
    same = bool(digests) and all(v["source"] == v["target"] for v in digests.values())
    res("copy_digest", same, f"표 {len(digests)}개 내용 해시 원천 = 대상")
    code, again = dbcopy(owner_url("extdb_main"), "--apply")
    res("copy_again_refused", code == 1 and "이미 자료" in again.get("error", ""), again.get("error", "")[:80])
    fresh_db(admin, "extdb_fail")
    subprocess.run([a.python, "-m", "alembic", "upgrade", "head"], cwd=a.backend, capture_output=True,
                   env={**os.environ, "DATABASE_URL": owner_url("extdb_fail"), "MOAJWO_DATA_DIR": str(work / "m2")})  # fmt: skip
    fail = subprocess.run([a.python, "-c", "import sys; from app.dbcopy import copy; copy(sys.argv[1], sys.argv[2], "
                           "apply=True, fail_after='source_files')", f"sqlite:///{src_db.as_posix()}",
                           owner_url("extdb_fail")], cwd=a.backend, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "MOAJWO_DATA_DIR": str(work / "copy")})  # fmt: skip
    res("copy_midway_failure_rolled_back", fail.returncode != 0 and count(owner_url("extdb_fail"), "users") == 0,
        f"source_files 뒤 실패 → users {count(owner_url('extdb_fail'), 'users')}행")  # fmt: skip

    # ---------------------------------------------------------------- 3. 재배포 (빈 데이터 폴더, 앱 계정)
    ext_env = {"DATABASE_URL": app_url("extdb_main"), "MOAJWO_MIGRATION_DATABASE_URL": owner_url("extdb_main"),
               "MOAJWO_DB_MODE": "external"}  # fmt: skip
    deploy_data = work / "deploy1"
    pgs = Server(a, deploy_data, a.port + 1, ext_env, log)
    res("redeploy_start", pgs.start(), "빈 데이터 폴더 + 외부 DB")
    ca, s, me = login(pgs, "ex-a", pw["a"])
    res("redeploy_login_a", f"{s} id={me.get('id')} role={me.get('role')}", f"이관 전 id={me_a.get('id')}")
    co, s, me_o = login(pgs, "ex-owner", pw["owner"])
    res("redeploy_login_owner", f"{s} role={me_o.get('role')}")
    _, s, _ = login(pgs, "ex-pending", pw["pending"])
    res("redeploy_pending_still_pending", s, "승인 대기 계정은 그대로 로그인 거절(403)")
    cb, s, _ = login(pgs, "ex-b", pw["b"])
    res("redeploy_ownership", f"A 검토={ca.call('GET', f'/reviews/{rid}')[0]} B가 A 검토={cb.call('GET', f'/reviews/{rid}')[0]} "
        f"소유자 조회={co.call('GET', f'/reviews/{rid}')[0]}", "200 / 404 / 200")  # fmt: skip
    s, body = ca.call("GET", f"/files/{fid}/preview")
    res("redeploy_original_gone", s, (body.get("detail") if isinstance(body, dict) else str(body))[:60])
    if run_id:
        s, body = ca.call("GET", f"/runs/{run_id}/download")
        res("redeploy_result_gone", s, (body.get("detail") if isinstance(body, dict) else "")[:60])
    s, runs = ca.call("GET", "/runs")
    res("redeploy_run_records", f"{s} {len(runs) if isinstance(runs, list) else runs}", "실행 기록은 DB에 그대로")
    s, new = co.call("POST", "/users", {"username": "ex-new", "display_name": "새 계정", "status": "active",
                                        "password": pw["new"]})  # fmt: skip
    res("redeploy_new_account", f"{s} id={new.get('id') if isinstance(new, dict) else None}", "번호가 이관한 최대값 다음")
    s, up2 = ca.call("POST", "/files", files=[("다시올림.csv", csv_bytes(rows))])
    fid2 = up2[0].get("id") if s == 200 else None
    res("redeploy_reupload", f"{s} id={fid2}", f"이관 전 마지막 id={fid}")
    pgs.stop()

    # ---------------------------------------------------------------- 4. 재시작 (같은 데이터 폴더)
    res("restart_start", pgs.start(migrate=False))
    res("restart_session_kept", ca.call("GET", "/auth/me")[0], "재시작 전 로그인 쿠키 그대로 (세션은 DB에)")
    ca, s, _ = login(pgs, "ex-a", pw["a"])
    res("restart_login_and_file", f"{s} 미리보기={ca.call('GET', f'/files/{fid2}/preview')[0]}", "방금 올린 파일 유지")

    # ---------------------------------------------------------------- 5. 휴면 뒤 재접속 (연결 강제 종료)
    admin_exec(admin, "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'moajwo_app'")
    t = time.monotonic()
    s, _ = ca.call("GET", "/auth/me")
    res("reconnect_after_drop", s, f"{round((time.monotonic() - t) * 1000)}ms (끊긴 연결은 꺼낼 때 확인 후 새로 맺음)")

    # ---------------------------------------------------------------- 6. 장애
    admin_exec(admin, "ALTER ROLE moajwo_app NOLOGIN")
    admin_exec(admin, "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = 'moajwo_app'")
    probe = pgs.client
    outage = {p: probe.call(m, p, b)[0] for m, p, b in (("GET", "/auth/status", None),
              ("POST", "/auth/login", {"username": "ex-a", "password": pw["a"]}),
              ("POST", "/auth/setup", {"username": "ex-x", "display_name": "x", "password": pw["new"]}),
              ("GET", "/health", None))}  # fmt: skip
    res("outage_503", outage, "인증 실패(앱 계정 로그인 금지) 동안")
    admin_exec(admin, f"ALTER ROLE moajwo_app LOGIN PASSWORD '{pw['role_a']}'")
    res("outage_recovered", login(pgs, "ex-a", pw["a"])[1], "되돌린 뒤 로그인")
    pgs.stop()
    nourl = Server(a, work / "nourl", a.port + 2, {"MOAJWO_DB_MODE": "external"}, log)
    started = nourl.start()
    nourl.stop()
    res("missing_url_refused", not started and not (work / "nourl" / "moajwo.db").exists(),
        "외부 모드 + DATABASE_URL 없음 → 기동 안 함, SQLite 파일 없음")  # fmt: skip

    # ---------------------------------------------------------------- 7. 최초 소유자 동시 설정
    fresh_db(admin, "extdb_race")
    race = Server(a, work / "race", a.port + 3, {"DATABASE_URL": app_url("extdb_race"),
                  "MOAJWO_MIGRATION_DATABASE_URL": owner_url("extdb_race"), "MOAJWO_DB_MODE": "external"}, log)  # fmt: skip
    res("race_start", race.start())
    codes: list[int] = []
    gate = threading.Barrier(8)

    def claim(i: int) -> None:
        gate.wait()
        codes.append(race.client.call("POST", "/auth/setup", {"username": f"race{i}", "display_name": f"r{i}",
                                                               "password": pw["new"]})[0])  # fmt: skip

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    race.stop()
    res("race_one_owner", f"응답 {sorted(codes)} · users {count(owner_url('extdb_race'), 'users')}",
        "201 하나, 나머지 409, 계정 1개")  # fmt: skip

    # ---------------------------------------------------------------- 8. 논리 백업·격리 복원
    dump = work / "moajwo.dump"
    if a.pg_bin or a.pg_docker:
        def pg_tool(tool: str, *args: str) -> subprocess.CompletedProcess:
            if a.pg_bin:
                return subprocess.run([str(a.pg_bin / tool), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
            return subprocess.run(["docker", "run", "--rm", "--network", "host", "-v", f"{work}:/work", a.pg_docker,
                                   tool, *[x.replace(str(work), "/work") for x in args]], capture_output=True, text=True, encoding="utf-8", errors="replace")  # fmt: skip

        b = pg_tool("pg_dump", "--format=custom", "--no-owner", "--no-privileges", f"--file={dump}",
                    owner_url("extdb_main"))  # fmt: skip
        res("backup_dump", b.returncode == 0, f"{dump.stat().st_size if dump.exists() else 0}B" + (b.stderr[-120:] if b.returncode else ""))
        fresh_db(admin, "extdb_restore")
        r = pg_tool("pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", f"--dbname={owner_url('extdb_restore')}", str(dump))
        res("backup_restore", r.returncode == 0, r.stderr[-160:])
        admin_exec(admin, "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO moajwo_app",
                   "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO moajwo_app", db="extdb_restore")  # fmt: skip
        rest = Server(a, work / "restored", a.port + 4, {"DATABASE_URL": app_url("extdb_restore"),
                      "MOAJWO_MIGRATION_DATABASE_URL": owner_url("extdb_restore"), "MOAJWO_DB_MODE": "external"}, log)  # fmt: skip
        res("restore_start", rest.start())
        _, s, _ = login(rest, "ex-a", pw["a"])
        rest.stop()
        res("restore_login_and_counts", f"login={s} users={count(owner_url('extdb_restore'), 'users')} "
            f"원본 users={count(owner_url('extdb_main'), 'users')}", "복원본 = 원본, 원본 DB는 그대로")  # fmt: skip
    else:
        res("backup", "건너뜀", "pg_dump 위치(--pg-bin 또는 --pg-docker) 없음")

    with (a.out / "results.tsv").open("w", encoding="utf-8") as f:
        for row in RESULTS:
            f.write("\t".join(row) + "\n")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
