"""PostgreSQL 마이그레이션 (외부 DB 모드) — test_migrations.py(SQLite 파일)의 PostgreSQL 쪽 대응.

- 빈 PostgreSQL에서 head까지 실제로 올리고, 결과가 앱 모델과 차이 없음(열 타입 포함), 기본 자료가 SQLite head와 같음.
- 기동 때 마이그레이션이 잠금(다른 인스턴스·이관 도구)이나 응답 없는 서버 앞에서 무한히 기다리지 않고 실패한다
  — 서버는 마이그레이션 뒤에 뜨므로, 멈춘 채 정상 기동처럼 보이지 않게.
빈 DB를 새로 만드는 시험은 MOAJWO_TEST_PG_ADMIN_URL(일회용 시험 서버의 관리자 주소)이 있을 때만 돈다.
"""

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text

from app.config import MIGRATION_LOCK_KEY
from app.db import Base
from tests.conftest import PG_ADMIN, pg_database

BACKEND = Path(__file__).resolve().parent.parent
needs_admin = pytest.mark.skipif(not PG_ADMIN, reason="MOAJWO_TEST_PG_ADMIN_URL 없음 — PostgreSQL 시험 서버 필요")
SEED = "SELECT code, name, CAST(valid_to AS TEXT), source FROM glossary_entries ORDER BY code, name"


def _alembic(url: str, data: Path, *args: str, **extra: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MOAJWO_"))}
    mode = "external" if url.startswith("postgresql") else ""
    env.update({"DATABASE_URL": url, "MOAJWO_DB_MODE": mode, "MOAJWO_DATA_DIR": str(data), "PYTHONIOENCODING": "utf-8"})
    env.update(extra)
    cmd = [sys.executable, "-m", "alembic", *args]
    return subprocess.run(cmd, cwd=BACKEND, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=180)  # fmt: skip


def _engine(url: str):  # noqa: ANN202
    return create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))


@needs_admin
def test_empty_postgres_reaches_head_matching_the_models_and_the_sqlite_seed(tmp_path: Path) -> None:
    url = pg_database("mig_pg_head")
    try:
        done = _alembic(url, tmp_path, "upgrade", "head")
        assert done.returncode == 0, done.stderr[-800:]
        e = _engine(url)
        with e.connect() as c:
            diff = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
            pg_seed = [tuple(r) for r in c.execute(text(SEED))]
            pg_aliases = c.execute(text("SELECT count(*) FROM glossary_aliases")).scalar()
        e.dispose()
        assert diff == []  # 마이그레이션 결과 = 앱 모델 (표·열·타입·인덱스)
        sqlite = tmp_path / "sqlite"
        sqlite.mkdir()
        db = sqlite / "moajwo.db"
        assert _alembic(f"sqlite:///{db.as_posix()}", sqlite, "upgrade", "head").returncode == 0
        s = create_engine(f"sqlite:///{db.as_posix()}")
        with s.connect() as c:
            sq_seed = [tuple(r) for r in c.execute(text(SEED))]
            sq_aliases = c.execute(text("SELECT count(*) FROM glossary_aliases")).scalar()
        s.dispose()
        assert pg_seed == sq_seed and len(pg_seed) > 0 and pg_aliases == sq_aliases  # 기본 용어사전 자료가 같음
    finally:
        pg_database("mig_pg_head", drop=True)


@needs_admin
def test_startup_migration_gives_up_on_a_held_lock_instead_of_waiting(tmp_path: Path) -> None:
    """다른 인스턴스·이관 도구가 잠금을 쥐고 있으면 제한 시간 뒤 실패(종료 코드 ≠ 0) — 풀리면 같은 명령이 성공."""
    url = pg_database("mig_pg_lock")
    try:
        e = _engine(url)
        with e.connect() as holder:
            holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
            holder.commit()
            started = time.monotonic()
            blocked = _alembic(url, tmp_path, "upgrade", "head", MOAJWO_MIGRATION_LOCK_TIMEOUT_S="2")
            took = time.monotonic() - started
            holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
            holder.commit()
        e.dispose()
        assert blocked.returncode != 0 and "LockNotAvailable" in blocked.stderr, blocked.stderr[-500:]
        assert took < 60
        assert _alembic(url, tmp_path, "upgrade", "head").returncode == 0
    finally:
        pg_database("mig_pg_lock", drop=True)


def test_startup_migration_gives_up_on_a_server_that_never_answers(tmp_path: Path) -> None:
    """연결은 받지만 답하지 않는 서버(로컬 시험 소켓) — 접속 제한 시간 뒤 실패한다 (외부로 요청하지 않음)."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    held: list[socket.socket] = []

    def accept() -> None:
        try:
            while True:
                held.append(server.accept()[0])
        except OSError:
            pass

    threading.Thread(target=accept, daemon=True).start()
    url = f"postgresql://u:p@127.0.0.1:{server.getsockname()[1]}/x?sslmode=disable"
    started = time.monotonic()
    try:
        silent = _alembic(url, tmp_path, "upgrade", "head", MOAJWO_DB_CONNECT_TIMEOUT_S="2")
    finally:
        server.close()
        for conn in held:
            conn.close()
    assert silent.returncode != 0 and time.monotonic() - started < 60
    assert not (tmp_path / "moajwo.db").exists()  # SQLite로 대신 열지 않음
