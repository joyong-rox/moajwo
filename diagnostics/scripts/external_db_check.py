"""O-03 계정 보존 확인 (실제 프로세스·실제 PostgreSQL) — 격리 시험 DB에서만 돌린다. 운영·시연 DB 주소를 넣지 않는다.

순서 (단계마다 결과를 results.tsv에 남기고, 실패해도 다음 단계로 간다):
 1. SQLite 앱(uvicorn)으로 최초 소유자·일반 계정 A·B·승인 대기 계정과 업무 기록(업로드·확정·검토·통합·묶기·분석)을 만든다.
 2. 빈 PostgreSQL에 마이그레이션(스키마 소유 계정, 서비스 주소) → 같은 DB 확인(app.dbcheck: 앱 계정 주소와 소유 계정
    주소 → 같음, 다른 DB → 다름) → 이관 dry-run(--target-env) → 적용(--target-env) → 다시 적용(서비스 주소, 거절)
    → 다른 빈 DB에서 실패 지점별(표 복사 뒤·모든 시퀀스 뒤) 되돌림: 표 건수와 시퀀스 상태가 그대로인지.
 3. '재배포': 빈 데이터 폴더 + 외부 DB(앱 계정, 데이터만 읽고 쓰는 권한)로 새 프로세스 → 같은 계정 로그인·역할·상태,
    소유 관계(남의 자료 404·관리자 조회), 지워진 원본·결과 파일의 안내(410), 새 가입·새 업로드(번호 충돌 없음).
 4. '재시작': 같은 데이터 폴더로 다시 띄워 로그인·방금 올린 파일 유지.
 5. '휴면 뒤 재접속': 관리자 계정으로 앱 연결을 모두 끊은 뒤(pg_terminate_backend) 요청 — 성공과 걸린 시간.
 6. 장애: 앱 계정 로그인 금지(인증 실패) 동안 상태·로그인·최초 설정이 503이고 '설정 필요'가 아님 → 되돌리면 회복.
    DB 주소 없이 외부 모드로 띄우면 기동하지 않고 SQLite 파일도 만들지 않음.
    이관 잠금 대기: 다른 연결이 이관처럼 표를 잠근 동안 앱 요청은 앱 계정의 잠금 대기 제한(lock_timeout) 뒤 503(바쁨)
    — 빈 표를 읽어 '최초 설정 필요'로 답하지 않음 → 잠금이 풀리면 정상.
 7. 빈 PostgreSQL에서 최초 소유자 설정을 동시에 8번 — 소유자는 하나.
 8. 논리 백업(pg_dump -Fc, PGSERVICE) → 다른 빈 DB로 복원(pg_restore --dbname=service=…) → psql(서비스)로 건수,
    그 DB로 띄워 로그인·건수 확인, 원래 DB는 그대로.
 9. 비밀값 점검: 하위 프로세스 명령 인자 전부와 이 스크립트 자신의 인자에 실행 때 만든 비밀번호(원문·URL 인코딩·비밀번호
    파일 이스케이프)가 없는지(모든 하위 프로세스를 감싸 기록), 임시 서비스·비밀번호 파일이 본인만 읽게 제한됐고 끝에
    지워졌는지, 결과 폴더의 파일에 비밀번호가 없는지.

비밀번호를 명령 인자에 두지 않는다 (셸 기록·프로세스 목록에 남음):
- 관리자(슈퍼유저) 접속: --admin-service 이름(PGSERVICEFILE·PGPASSFILE이 가리키는 서비스·비밀번호 파일) 또는
  --admin-env 환경 변수 이름(그 변수에 주소).
- 이 스크립트가 만드는 계정의 비밀번호는 실행 때 만들고 출력·저장하지 않는다. 하위 프로세스에는 환경 변수(앱의
  DATABASE_URL 등)나 임시 서비스·비밀번호 파일(본인만 접근, 끝나면 지움 — 서비스 파일에는 비밀번호 없음)로만 넘긴다:
  이관 도구는 --target-env 또는 서비스 주소, pg_dump·pg_restore·psql은 PGSERVICE·PGSERVICEFILE·PGPASSFILE.
- --role-prefix(기본 moajwo → moajwo_owner·moajwo_app)를 바꾸면 같은 시험 서버의 다른 계정 비밀번호를 건드리지 않는다.
합성 자료만 쓴다.
사용법: python external_db_check.py (--admin-service 이름 | --admin-env 변수) --backend <backend 폴더> --out <폴더>
        [--python <앱 파이썬>] [--pg-bin <pg_dump·pg_restore·psql 폴더>] [--port 8190] [--role-prefix moajwo]
"""

from __future__ import annotations

import argparse
import csv
import http.cookiejar
import io
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

RESULTS: list[tuple[str, str, str]] = []
ARGV: list[list[str]] = []  # 이 스크립트가 띄운 모든 하위 프로세스의 명령 인자 (9단계 비밀값 점검)
# 하위 프로세스용 서비스 파일에 옮기는 접속 매개변수 (관리자 연결에서 읽음 — 비밀번호 없음)
CONN_KEYS = ("host", "hostaddr", "port", "sslmode", "sslrootcert", "sslcert", "sslkey", "channel_binding")


class _RecordingPopen(subprocess.Popen):
    """명령 인자를 ARGV에 남기는 Popen — subprocess.run도 이것을 쓴다 (main에서 subprocess.Popen을 바꿔 끼움)."""

    def __init__(self, args, *rest, **kwargs) -> None:  # noqa: ANN001
        ARGV.append([str(x) for x in args] if isinstance(args, list | tuple) else [str(args)])
        super().__init__(args, *rest, **kwargs)


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


def child_env(admin_env: str | None, **extra: str) -> dict[str, str]:
    """하위 프로세스 환경: 이 셸의 DB 주소·앱 설정·libpq 변수(PG*)·관리자 주소 변수는 넘기지 않고, 필요한 것만 더한다."""
    drop = ("DATABASE_URL", "MOAJWO_", "PG")
    env = {k: v for k, v in os.environ.items() if not k.startswith(drop) and k != admin_env}
    env.update({"PYTHONIOENCODING": "utf-8", **extra})
    return env


def _private_file(path: Path, body: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # POSIX: 본인만 읽고 쓰기
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)


def pgpass_escape(value: str) -> str:
    """비밀번호 파일 형식: 칸 안의 : 와 \\ 는 \\ 로 이스케이프한다 (PostgreSQL 문서)."""
    return value.replace("\\", "\\\\").replace(":", "\\:")


class SecretFiles:
    """하위 프로세스용 임시 서비스 파일·비밀번호 파일 (사용자 임시 폴더 안, 끝에 지운다).
    서비스 파일에는 접속 정보만, 비밀번호는 비밀번호 파일에만 둔다 (유닉스 libpq는 비밀번호 파일만 권한을 검사한다).
    두 파일 모두 내용을 쓰기 전에 본인만 접근하게 줄인다: POSIX chmod 0600, Windows는 libpq가 권한을 검사하지 않으므로
    docs/EXTERNAL_DB.md 3-1과 같은 icacls 명령(상속을 끊고 본인에게만 전체 권한)."""

    def __init__(self, conn: dict[str, str]) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="extdb-secret-"))  # POSIX 0700
        self.conn = conn
        self.service, self.passfile = self.dir / "pg_service.conf", self.dir / "pgpass.conf"
        self.sections: dict[str, dict[str, str]] = {}
        self.passwords: dict[str, str] = {}
        self._write()  # 빈 파일을 먼저 만들고 권한을 줄인 뒤에 내용을 쓴다 (O_TRUNC로 다시 써도 권한은 그대로)
        self.restricted = all(self._restrict(p) for p in (self.service, self.passfile))

    @staticmethod
    def _restrict(path: Path) -> bool:
        if os.name != "nt":
            os.chmod(path, 0o600)
            return True
        user = os.environ.get("USERNAME", "")
        p = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"], capture_output=True)
        return p.returncode == 0

    def add(self, name: str, user: str, password: str, db: str) -> str:
        """서비스 하나(접속 정보 + DB·계정)를 더하고 비밀번호 없는 서비스 주소를 돌려준다."""
        self.sections[name] = {**self.conn, "dbname": db, "user": user}
        self.passwords[user] = password
        self._write()
        return f"postgresql:///?service={name}"

    def _write(self) -> None:
        services = "".join(f"[{n}]\n" + "".join(f"{k}={v}\n" for k, v in s.items()) for n, s in self.sections.items())
        host, port = pgpass_escape(self.conn.get("host", "*")), self.conn.get("port", "*")
        lines = "".join(f"{host}:{port}:*:{pgpass_escape(u)}:{pgpass_escape(p)}\n" for u, p in self.passwords.items())
        _private_file(self.service, services)
        _private_file(self.passfile, lines)

    def private(self) -> bool:
        """두 파일이 본인만 접근 가능한지 (POSIX: 0600 / Windows: ACL 항목이 본인 하나)."""
        if not self.restricted:
            return False
        if os.name != "nt":
            return all(stat.S_IMODE(os.stat(p).st_mode) == 0o600 for p in (self.service, self.passfile))
        user = os.environ.get("USERNAME", "").lower()
        for path in (self.service, self.passfile):
            out = subprocess.run(["icacls", str(path)], capture_output=True).stdout.decode("utf-8", errors="replace")
            aces = [ln for ln in out.splitlines() if ":(" in ln]  # 권한 항목 줄만 (끝의 처리 결과 요약 줄 제외)
            if len(aces) != 1 or f"\\{user}:" not in aces[0].lower():
                return False
        return True

    def env(self, service: str | None = None) -> dict[str, str]:
        extra = {"PGSERVICEFILE": str(self.service), "PGPASSFILE": str(self.passfile)}
        return {**extra, "PGSERVICE": service} if service else extra

    def remove(self) -> bool:
        return remove_tree(self.dir)


class Server:
    """uvicorn 한 프로세스 (모델 호출 차단, 127.0.0.1만)."""

    def __init__(self, a: argparse.Namespace, data: Path, port: int, env: dict[str, str], log: Path) -> None:
        self.a, self.data, self.port, self.env, self.log = a, data, port, env, log
        self.proc: subprocess.Popen | None = None

    def start(self, migrate: bool = True) -> bool:
        env = child_env(self.a.admin_env, MOAJWO_DATA_DIR=str(self.data), OLLAMA_BASE_URL="http://127.0.0.1:9", **self.env)
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


ADMIN: URL | None = None  # 관리자 접속 (서비스 주소 또는 환경 변수의 주소) — 이 프로세스 안에서만 쓴다


def admin_exec(*sql: str, db: str | None = None) -> None:
    e = create_engine(ADMIN.set(database=db) if db else ADMIN, isolation_level="AUTOCOMMIT")
    with e.connect() as c:
        for s in sql:
            c.execute(text(s))
    e.dispose()


def count(url: str, table: str) -> int:
    e = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    with e.connect() as c:
        n = c.execute(text(f'SELECT count(*) FROM "{table}"')).scalar()
    e.dispose()
    return n


def sequence_state(url: str, table: str) -> tuple[int, bool]:
    e = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    with e.connect() as c:
        seq = c.execute(text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table}).scalar()
        last, called = c.execute(text(f"SELECT last_value, is_called FROM {seq}")).one()
    e.dispose()
    return int(last), bool(called)


def csv_bytes(rows: list[list]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("utf-8")


def login(server: Server, user: str, pw: str) -> tuple[Client, int, dict]:
    c = server.client
    s, body = c.call("POST", "/auth/login", {"username": user, "password": pw})
    return c, s, body if isinstance(body, dict) else {}


def remove_tree(path: Path) -> bool:
    """작업 폴더를 지운다. 앱은 업로드 원본·결과 파일을 읽기 전용으로 두므로(원본 보존) Windows에서는 쓰기 권한을
    되돌린 뒤 다시 지운다 (그냥 rmtree하면 그 파일들이 남는다)."""

    def again(func, target, _exc) -> None:  # noqa: ANN001
        os.chmod(target, stat.S_IWRITE)
        func(target)

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=again)
        else:
            shutil.rmtree(path, onerror=again)
    except OSError:
        pass
    return not path.exists()


def secret_forms(value: str) -> set[str]:
    """비밀번호가 명령 인자·파일에 나타날 수 있는 모양: 원문, URL 인코딩(둘), 비밀번호 파일 이스케이프."""
    return {value, quote(value, safe=""), quote(value, safe=" +"), pgpass_escape(value)}


def main() -> None:
    global ADMIN
    ap = argparse.ArgumentParser()
    admin = ap.add_mutually_exclusive_group(required=True)
    admin.add_argument("--admin-service", metavar="NAME", help="관리자 접속 libpq 서비스 이름 (PGSERVICEFILE·PGPASSFILE)")
    admin.add_argument("--admin-env", metavar="VAR", help="관리자 주소를 읽을 환경 변수 이름")
    ap.add_argument("--backend", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--pg-bin", type=Path)
    ap.add_argument("--port", type=int, default=8190)
    ap.add_argument("--role-prefix", default="moajwo", help="시험 계정 이름 앞부분 (…_owner·…_app)")
    a = ap.parse_args()
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", a.role_prefix):
        ap.error("--role-prefix는 영문 소문자·숫자·밑줄만")
    subprocess.Popen = _RecordingPopen  # 모든 하위 프로세스의 명령 인자를 남긴다 (9단계)
    a.out, a.backend = a.out.resolve(), a.backend.resolve()  # 하위 프로세스는 backend 폴더에서 돈다
    a.out.mkdir(parents=True, exist_ok=True)
    log = a.out / "servers.log"
    work = Path(tempfile.mkdtemp(prefix="extdb-"))
    if a.admin_service:
        ADMIN = make_url(f"postgresql+psycopg:///?service={quote(a.admin_service, safe='')}")
    else:
        raw = os.environ.get(a.admin_env, "").strip()
        if not raw:
            ap.error(f"환경 변수 {a.admin_env}에 관리자 주소가 없습니다")
        ADMIN = make_url(raw).set(drivername="postgresql+psycopg")
    owner_role, app_role = f"{a.role_prefix}_owner", f"{a.role_prefix}_app"
    pw = {k: "Ex-" + secrets.token_urlsafe(12) + "!%" for k in ("owner", "a", "b", "pending", "new", "role_o", "role_a")}
    # 스키마 소유 계정(마이그레이션·이관)과 앱 계정(데이터만) — 비밀번호에 URL·비밀번호 파일 특수문자를 넣어
    # URL 인코딩(앱 환경 변수)과 비밀번호 파일 이스케이프(서비스 접속)를 함께 확인
    pw["role_o"] += "@:/"
    pw["role_a"] += "?&#:"
    hidden = set().union(*(secret_forms(v) for v in pw.values()))
    if ADMIN.password:
        hidden |= secret_forms(ADMIN.password)
    e = create_engine(ADMIN, isolation_level="AUTOCOMMIT")
    with e.connect() as c:  # 관리자 연결의 접속 정보(호스트·포트·TLS)를 시험 계정 접속에 그대로 쓴다 (비밀번호 제외)
        params = c.connection.dbapi_connection.info.get_parameters()
    e.dispose()
    conn = {k: params[k] for k in CONN_KEYS if params.get(k)}
    vault = SecretFiles(conn)
    try:
        run_checks(a, log, work, pw, conn, vault, owner_role, app_role)
    except Exception:  # 중간에 멈춰도 지금까지의 결과와 아래 점검은 남긴다 (오류 종류와 위치만)
        res("aborted", type(sys.exc_info()[1]).__name__, traceback.format_exc().strip().splitlines()[-1][:160])
    finally:
        removed = vault.remove()
        work_removed = remove_tree(work)

    # ---------------------------------------------------------------- 9. 비밀값 점검
    res("secret_files_removed", removed, "임시 서비스·비밀번호 파일 폴더를 지움")
    res("work_files_removed", work_removed, "합성 원천 SQLite·업로드·결과·백업 파일 작업 폴더를 지움 (읽기 전용 파일 포함)")
    leaks = [i for i, argv in enumerate(ARGV) if any(h in " ".join(argv) for h in hidden)]
    own = any(h in " ".join(sys.argv) for h in hidden)
    programs = sorted({Path(argv[0]).stem + (f" {argv[2]}" if argv[1:2] == ["-m"] and len(argv) > 2 else "") for argv in ARGV})
    res("argv_no_secrets", not leaks and not own,
        f"하위 프로세스 {len(ARGV)}개({', '.join(programs)})와 자기 인자에 생성 비밀번호 {len(pw)}개"
        f"(원문·URL 인코딩·비밀번호 파일 이스케이프){' — 관리자 비밀번호 포함' if ADMIN.password else ''} 없음"
        if not leaks and not own else f"발견: 하위 프로세스 {len(leaks)}개, 자기 인자 {own} (값은 적지 않음)")  # fmt: skip
    files = [p for p in a.out.iterdir() if p.is_file() and p.name != "results.tsv"]
    found = [p.name for p in files if any(h in p.read_text(encoding="utf-8", errors="replace") for h in hidden)]
    body = "\n".join("\t".join(r) for r in RESULTS)
    res("outputs_no_secrets", not found and not any(h in body for h in hidden),
        f"결과 행과 {', '.join(sorted(p.name for p in files))}에 생성 비밀번호 없음" if not found else f"발견: {found}")  # fmt: skip
    with (a.out / "results.tsv").open("w", encoding="utf-8", newline="\n") as f:
        for row in RESULTS:
            f.write("\t".join(row) + "\n")


def run_checks(a: argparse.Namespace, log: Path, work: Path, pw: dict[str, str], conn: dict[str, str],
               vault: SecretFiles, owner_role: str, app_role: str) -> None:  # fmt: skip
    for role, key in ((owner_role, "role_o"), (app_role, "role_a")):
        admin_exec(f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN "
                   f"CREATE ROLE {role} LOGIN; END IF; END $$",
                   f"ALTER ROLE {role} LOGIN PASSWORD '{pw[key]}'")  # fmt: skip
    # 앱 계정의 서버 쪽 제한 시간 (docs/EXTERNAL_DB.md 5장과 같은 값) — 잠금·긴 문장에서 무한히 기다리지 않게
    admin_exec(f"ALTER ROLE {app_role} SET lock_timeout = '5s'", f"ALTER ROLE {app_role} SET statement_timeout = '60s'",
               f"ALTER ROLE {app_role} SET idle_in_transaction_session_timeout = '60s'")  # fmt: skip
    res("secret_files_private", vault.private(),
        "임시 서비스·비밀번호 파일: " + ("chmod 0600" if os.name != "nt" else "icacls /inheritance:r /grant:r 본인:F → 권한 항목이 본인 하나"))  # fmt: skip

    def pg_url(user: str, password: str, db: str) -> str:
        """앱 환경 변수용 주소 (비밀번호는 퍼센트 인코딩 — 환경 변수로만 넘기고 명령 인자에 넣지 않는다)."""
        query = {k: v for k, v in conn.items() if k not in ("host", "port")}
        port = int(conn["port"]) if conn.get("port") else None
        return URL.create("postgresql", username=user, password=password, host=conn.get("host"), port=port,
                          database=db, query=query).render_as_string(hide_password=False)  # fmt: skip

    owner_url = lambda db: pg_url(owner_role, pw["role_o"], db)  # noqa: E731
    app_url = lambda db: pg_url(app_role, pw["role_a"], db)  # noqa: E731
    owner_service = lambda db: vault.add(f"extdb-owner-{db}", owner_role, pw["role_o"], db)  # noqa: E731

    def fresh_db(name: str) -> None:
        admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', f'CREATE DATABASE "{name}" OWNER {owner_role}')
        admin_exec(f"GRANT USAGE ON SCHEMA public TO {app_role}",
                   f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA public "
                   f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_role}",
                   f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_role} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {app_role}",
                   db=name)  # fmt: skip

    def run(cmd: list[str], **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, cwd=a.backend, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=child_env(a.admin_env, **env))  # fmt: skip

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
    s_run, run_ = ca.call("POST", f"/reviews/{rid}/integrate", {"include_unmapped": True})
    run_id = run_.get("id") if isinstance(run_, dict) else None
    plan = ca.call("POST", "/bundle/plan", {"file_ids": [fid]})[1]
    s_b, bundle = ca.call("POST", "/bundle/run", {"file_ids": [fid], "expected_basis": plan["basis"]})
    res("sqlite_records", f"file={fid} review={rid} complete={s_done} integrate={s_run} run={run_id} bundle={s_b}")
    cb, _, _ = login(sq, "ex-b", pw["b"])
    cb.call("POST", "/files", files=[("B.csv", csv_bytes(rows))])
    sq.stop()
    src_db = src_data / "moajwo.db"
    source = f"sqlite:///{src_db.as_posix()}"

    # ---------------------------------------------------------------- 2. 마이그레이션 · 같은 DB 확인 · 이관
    fresh_db("extdb_main")
    # 직접 연결·소유 계정을 서비스 주소로 (접속 정보는 서비스 파일, 비밀번호는 비밀번호 파일)
    m = run([a.python, "-m", "alembic", "upgrade", "head"], DATABASE_URL=owner_service("extdb_main"),
            MOAJWO_DB_MODE="external", MOAJWO_DATA_DIR=str(work / "mig"), **vault.env())  # fmt: skip
    res("pg_migrate_head", m.returncode == 0, (m.stderr or m.stdout)[-200:] if m.returncode else "빈 DB → head (서비스 주소·비밀번호 파일)")
    fresh_db("extdb_other")
    check_env = {"EXTDB_APP": app_url("extdb_main"), "EXTDB_MIGRATION": owner_url("extdb_main"),
                 "EXTDB_OTHER": owner_url("extdb_other"), **vault.env()}  # fmt: skip
    for name, args, want in (("same_db_check", ["--app-env", "EXTDB_APP", "--migration-service", "extdb-owner-extdb_main"], 0),
                             ("same_db_check_env", ["--app-env", "EXTDB_APP", "--migration-env", "EXTDB_MIGRATION"], 0),
                             ("different_db_check", ["--app-env", "EXTDB_APP", "--migration-env", "EXTDB_OTHER"], 1)):  # fmt: skip
        p = run([a.python, "-m", "app.dbcheck", *args], **check_env)
        try:
            out = json.loads(p.stdout)
        except ValueError:
            out = {"result": p.stderr[-160:]}
        users = f"{out.get('app', {}).get('user')} / {out.get('migration', {}).get('user')}"
        res(name, p.returncode == want, f"exit={p.returncode} {out.get('result')} differences={out.get('differences')} 계정 {users}")

    def dbcopy(*args: str, env: dict[str, str]) -> tuple[int, dict]:
        p = run([a.python, "-m", "app.dbcopy", "--source", source, *args], MOAJWO_DATA_DIR=str(work / "copy"), **env)
        try:
            return p.returncode, json.loads(p.stdout)
        except ValueError:
            return p.returncode, {"error": p.stderr[-300:]}

    target_env = {"EXTDB_TARGET": owner_url("extdb_main")}
    code, dry = dbcopy("--target-env", "EXTDB_TARGET", env=target_env)
    res("copy_dry_run", f"exit={code} {dry.get('result') or dry.get('error')}",
        f"users {dry.get('counts', {}).get('users', {}).get('source')}, 대상 쓰기 없음={count(owner_url('extdb_main'), 'users') == 0}, "
        f"--target-env, target_secret_in_args={dry.get('target_secret_in_args')}, tz_unverified_runs={dry.get('tz_unverified_runs')}")  # fmt: skip
    code, applied = dbcopy("--target-env", "EXTDB_TARGET", "--apply", "--report", str(a.out / "dbcopy-report.json"),
                           env=target_env)  # fmt: skip
    res("copy_apply", f"exit={code} {applied.get('result') or applied.get('error')}",
        f"{applied.get('seconds')}s, target_secret_in_args={applied.get('target_secret_in_args')}")  # fmt: skip
    digests = applied.get("digests", {})
    same = bool(digests) and all(v["source"] == v["target"] for v in digests.values())
    res("copy_digest", same, f"표 {len(digests)}개 내용 해시 원천 = 대상")
    # 서비스 주소로 다시 적용 — 명령 인자에 비밀번호 없이 접속해 이관 표시를 보고 거절
    code, again = dbcopy("--target", owner_service("extdb_main"), "--apply", env=vault.env())
    res("copy_again_refused", code == 1 and "이관 표시" in again.get("error", ""),
        f"서비스 주소 --target, target_secret_in_args={again.get('target_secret_in_args')} · {again.get('error', '')[:70]}")  # fmt: skip
    res("copy_source_unchanged", applied.get("source", {}).get("unchanged"), "원천 파일 해시 앞뒤 같음 (스냅숏에서 읽음)")
    for point in ("table:source_files", "sequence:all"):
        fresh_db("extdb_fail")
        run([a.python, "-m", "alembic", "upgrade", "head"], DATABASE_URL=owner_url("extdb_fail"), MOAJWO_DB_MODE="external",
            MOAJWO_DATA_DIR=str(work / "m2"))  # fmt: skip
        seq_before = sequence_state(owner_url("extdb_fail"), "users")
        fail = run([a.python, "-c", "import os, sys; from app.dbcopy import copy; "
                    "copy(sys.argv[1], os.environ['EXTDB_TARGET'], apply=True, fail_at=sys.argv[2])", source, point],
                   EXTDB_TARGET=owner_url("extdb_fail"), MOAJWO_DATA_DIR=str(work / "copy"))  # fmt: skip
        users, seq_after = count(owner_url("extdb_fail"), "users"), sequence_state(owner_url("extdb_fail"), "users")
        res(f"copy_failure_rolled_back[{point}]", fail.returncode != 0 and users == 0 and seq_after == seq_before,
            f"실패 뒤 users {users}행 · users 시퀀스 {seq_before} → {seq_after}")  # fmt: skip

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
    admin_exec(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = '{app_role}'")
    t = time.monotonic()
    s, _ = ca.call("GET", "/auth/me")
    res("reconnect_after_drop", s, f"{round((time.monotonic() - t) * 1000)}ms (끊긴 연결은 꺼낼 때 확인 후 새로 맺음)")

    # ---------------------------------------------------------------- 6. 장애
    admin_exec(f"ALTER ROLE {app_role} NOLOGIN")
    admin_exec(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = '{app_role}'")
    probe = pgs.client
    outage = {p: probe.call(m, p, b)[0] for m, p, b in (("GET", "/auth/status", None),
              ("POST", "/auth/login", {"username": "ex-a", "password": pw["a"]}),
              ("POST", "/auth/setup", {"username": "ex-x", "display_name": "x", "password": pw["new"]}),
              ("GET", "/health", None))}  # fmt: skip
    res("outage_503", outage, "인증 실패(앱 계정 로그인 금지) 동안")
    admin_exec(f"ALTER ROLE {app_role} LOGIN PASSWORD '{pw['role_a']}'")
    res("outage_recovered", login(pgs, "ex-a", pw["a"])[1], "되돌린 뒤 로그인")
    pgs.stop()
    nourl = Server(a, work / "nourl", a.port + 2, {"MOAJWO_DB_MODE": "external"}, log)
    started = nourl.start()
    nourl.stop()
    res("missing_url_refused", not started and not (work / "nourl" / "moajwo.db").exists(),
        "외부 모드 + DATABASE_URL 없음 → 기동 안 함, SQLite 파일 없음")  # fmt: skip
    fresh_db("extdb_lockwait")
    lw = Server(a, work / "lockwait", a.port + 5, {"DATABASE_URL": app_url("extdb_lockwait"),
                "MOAJWO_MIGRATION_DATABASE_URL": owner_url("extdb_lockwait"), "MOAJWO_DB_MODE": "external"}, log)  # fmt: skip
    res("lockwait_start", lw.start())
    holder = create_engine(owner_url("extdb_lockwait").replace("postgresql://", "postgresql+psycopg://", 1))
    with holder.connect() as h:  # 이관 도구처럼: 이관 잠금 + 표 ACCESS EXCLUSIVE 잠금을 쥔 채 커밋하지 않음
        h.execute(text("SELECT pg_advisory_xact_lock(727274)"))
        h.execute(text("LOCK TABLE users, app_settings, auth_sessions IN ACCESS EXCLUSIVE MODE"))
        t = time.monotonic()
        s, body = lw.client.call("GET", "/auth/status")
        waited = round(time.monotonic() - t, 1)
        h.rollback()
    holder.dispose()
    detail = body.get("detail", "") if isinstance(body, dict) else ""
    res("lockwait_503_busy", f"{s} {waited}s", f"{detail[:40]} · 최초 설정 응답 아님={'setup_required' not in str(body)}")
    s, body = lw.client.call("GET", "/auth/status")
    res("lockwait_released", s, f"잠금이 풀린 뒤 상태 확인 (빈 DB라 setup_required={body.get('setup_required') if isinstance(body, dict) else body})")
    lw.stop()

    # ---------------------------------------------------------------- 7. 최초 소유자 동시 설정
    fresh_db("extdb_race")
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

    # ---------------------------------------------------------------- 8. 논리 백업·격리 복원 (서비스·비밀번호 파일)
    dump = work / "moajwo.dump"
    if a.pg_bin:
        def pg_tool(tool: str, *args: str, service: str | None = None) -> subprocess.CompletedProcess:
            # --no-password: 비밀번호 파일이 맞지 않으면 묻지 않고 바로 실패한다 (묻는 창에서 멈추지 않게)
            return run([str(a.pg_bin / tool), "--no-password", *args], **vault.env(service))

        owner_service("extdb_main")
        b = pg_tool("pg_dump", "--format=custom", "--no-owner", "--no-privileges", f"--file={dump}",
                    service="extdb-owner-extdb_main")  # fmt: skip
        res("backup_dump", b.returncode == 0, f"{dump.stat().st_size if dump.exists() else 0}B, PGSERVICE" + (b.stderr[-120:] if b.returncode else ""))
        fresh_db("extdb_restore")
        owner_service("extdb_restore")
        r = pg_tool("pg_restore", "--no-owner", "--no-privileges", "--exit-on-error", "--dbname=service=extdb-owner-extdb_restore", str(dump))
        res("backup_restore", r.returncode == 0, "--dbname=service=… " + r.stderr[-160:])
        q = pg_tool("psql", "--no-psqlrc", "--tuples-only", "--no-align", "--command=SELECT count(*) FROM users",
                    service="extdb-owner-extdb_restore")  # fmt: skip
        res("restore_psql_count", q.returncode == 0 and q.stdout.strip() == str(count(owner_url("extdb_main"), "users")),
            f"psql(PGSERVICE) users={q.stdout.strip()} " + q.stderr[-120:])  # fmt: skip
        admin_exec(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_role}",
                   f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role}", db="extdb_restore")  # fmt: skip
        rest = Server(a, work / "restored", a.port + 4, {"DATABASE_URL": app_url("extdb_restore"),
                      "MOAJWO_MIGRATION_DATABASE_URL": owner_url("extdb_restore"), "MOAJWO_DB_MODE": "external"}, log)  # fmt: skip
        res("restore_start", rest.start())
        _, s, _ = login(rest, "ex-a", pw["a"])
        rest.stop()
        res("restore_login_and_counts", f"login={s} users={count(owner_url('extdb_restore'), 'users')} "
            f"원본 users={count(owner_url('extdb_main'), 'users')}", "복원본 = 원본, 원본 DB는 그대로")  # fmt: skip
    else:
        res("backup", "건너뜀", "pg_dump 위치(--pg-bin) 없음")


if __name__ == "__main__":
    main()
