"""같은 DB 확인 도구(app/dbcheck.py) — 앱 주소(풀러·앱 계정)와 마이그레이션 주소(직접 연결·소유 계정)가 같은 DB인지.

- 비교 규칙·주소 없음·닿지 않는 주소는 DB 없이 돈다 (거절되는 로컬 포트 — 외부로 요청하지 않음).
- PostgreSQL 시험은 MOAJWO_TEST_PG_ADMIN_URL(일회용 시험 서버의 관리자 주소 — 운영·시연 DB 아님)이 있을 때만 돈다:
  같은 DB를 서로 다른 두 계정으로 → 같음(0), 다른 DB → 다름(1). 한쪽은 서비스 주소(서비스 파일·비밀번호 파일)로 붙는다.
- 어느 경우에도 출력에 주소·호스트·비밀번호가 없다.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import dbcheck
from tests.conftest import PG_ADMIN, pg_database, pg_service_files

UNREACHABLE = "postgresql://u:pw-secret-123@127.0.0.1:9/x"  # 거절되는 로컬 포트
APP_ENV, MIG_ENV = "MOAJWO_DBCHECK_TEST_APP", "MOAJWO_DBCHECK_TEST_MIGRATION"
pg = pytest.mark.skipif(not PG_ADMIN, reason="MOAJWO_TEST_PG_ADMIN_URL 없음 — PostgreSQL 시험 서버가 있을 때만")


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any], str]:
    code = dbcheck.main(list(args))
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.out + captured.err


def _side(**over: Any) -> dict[str, Any]:
    base = {"user": "u", "database": "neondb", "database_oid": 16384,
            "postmaster_start_time": "2026-10-25T00:00:00+00:00", "server_version_num": 170011,
            "alembic_version": "0019_analysis_runs", "neon_tenant_id": None, "neon_timeline_id": None,
            "unreadable": []}  # fmt: skip
    return {**base, **over}


def test_compare_needs_every_field_to_match() -> None:
    same = dbcheck.compare(_side(user="moajwo_app"), _side(user="neondb_owner"))  # 계정은 달라도 된다
    assert same["result"] == "same" and same["differences"] == [] and same["not_compared"] == []
    # 같은 DB 이름·OID라도 다른 서버(다른 Neon 프로젝트·분기)면 다르다
    other = dbcheck.compare(_side(neon_timeline_id="a"), _side(neon_timeline_id="b"))
    assert other["result"] == "different" and other["differences"] == ["neon_timeline_id"]
    restarted = dbcheck.compare(_side(), _side(postmaster_start_time="2026-10-25T01:00:00+00:00"))
    assert restarted["result"] == "different" and "한 번 더 실행" in restarted["message"]
    # 한쪽이 읽지 못한 항목은 비교에서 빼고 알린다 (같다고 치지 않고, 다르다고도 하지 않음)
    partial = dbcheck.compare(_side(alembic_version=None, unreadable=["alembic_version"]), _side())
    assert partial["result"] == "same" and partial["not_compared"] == ["alembic_version"]
    assert "alembic_version" not in partial["compared"]
    empty = dbcheck.compare(_side(alembic_version=None), _side())  # 마이그레이션 안 한 DB와 한 DB
    assert empty["result"] == "different" and empty["differences"] == ["alembic_version"]


def test_missing_or_non_postgres_address_cannot_be_checked(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.delenv(APP_ENV, raising=False)
    monkeypatch.setenv(MIG_ENV, UNREACHABLE)
    code, out, raw = _run(capsys, "--app-env", APP_ENV, "--migration-env", MIG_ENV)
    assert code == dbcheck.EXIT_UNKNOWN and out["result"] == "unknown" and out["failed"] == "app"
    assert f"환경 변수 {APP_ENV}에 주소가 없습니다" in out["error"]
    monkeypatch.setenv(APP_ENV, "sqlite:///x.db")
    code, out, raw = _run(capsys, "--app-env", APP_ENV, "--migration-env", MIG_ENV)
    assert code == dbcheck.EXIT_UNKNOWN and "PostgreSQL이 아닙니다" in out["error"]


def test_unreachable_address_is_exit_2_and_never_printed(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(dbcheck, "CONNECT_TIMEOUT_S", 2)  # 닿지 않는 포트에서 오래 기다리지 않게 (시험 시간)
    monkeypatch.setenv(APP_ENV, UNREACHABLE)
    monkeypatch.setenv(MIG_ENV, UNREACHABLE)
    code, out, raw = _run(capsys, "--app-env", APP_ENV, "--migration-env", MIG_ENV)
    assert code == dbcheck.EXIT_UNKNOWN and out["result"] == "unknown" and "OperationalError" in out["error"]
    assert "pw-secret-123" not in raw and "127.0.0.1" not in raw


# ---------------------------------------------------------------- PostgreSQL (일회용 시험 서버)


def _admin(sql: str, db: str | None = None) -> None:
    url = make_url(PG_ADMIN).set(drivername="postgresql+psycopg")
    e = create_engine(url.set(database=db) if db else url, isolation_level="AUTOCOMMIT")
    with e.connect() as c:
        c.execute(text(sql))
    e.dispose()


@pytest.fixture
def two_databases() -> Any:
    """같은 시험 서버의 빈 DB 둘(한쪽만 alembic_version이 있음)과, 관리자가 아닌 읽기 계정 하나."""
    names = ("dbcheck_one", "dbcheck_two")
    urls = [pg_database(n) for n in names]
    role, password = "dbcheck_" + secrets.token_hex(4), "Ck-" + secrets.token_urlsafe(12) + ":@/"
    _admin("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)", names[0])
    _admin("INSERT INTO alembic_version VALUES ('0019_analysis_runs')", names[0])
    _admin(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
    _admin(f"GRANT SELECT ON alembic_version TO {role}", names[0])
    reader = make_url(urls[0]).set(username=role, password=password).render_as_string(hide_password=False)
    yield {"one": urls[0], "two": urls[1], "reader_one": reader, "role": role, "password": password}
    for n in names:
        pg_database(n, drop=True)
    _admin(f"DROP ROLE IF EXISTS {role}")


@pg
def test_same_database_through_two_accounts_is_the_same(two_databases, tmp_path: Path, monkeypatch, capsys) -> None:
    """앱 쪽은 환경 변수(비밀번호가 든 주소), 마이그레이션 쪽은 서비스 주소(비밀번호는 비밀번호 파일) — 계정이 달라도
    같은 DB면 0. 출력에 주소·비밀번호가 없다."""
    for name in ("PGSERVICE", "PGPASSWORD"):
        monkeypatch.delenv(name, raising=False)
    for name, value in pg_service_files(tmp_path, "dbcheck-migrate", two_databases["one"]).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(APP_ENV, two_databases["reader_one"])
    code, out, raw = _run(capsys, "--app-env", APP_ENV, "--migration-service", "dbcheck-migrate")
    assert code == dbcheck.EXIT_SAME and out["result"] == "same", out
    assert out["app"]["user"] == two_databases["role"] and out["migration"]["user"] != two_databases["role"]
    assert out["app"]["alembic_version"] == out["migration"]["alembic_version"] == "0019_analysis_runs"
    assert out["app"]["neon_timeline_id"] is None and out["not_compared"] == []  # Neon이 아니면 식별자 없음
    assert out["app_input"] == f"env:{APP_ENV}" and out["migration_input"] == "service:dbcheck-migrate"
    admin_password = make_url(PG_ADMIN).password
    assert two_databases["password"] not in raw and admin_password not in raw and "127.0.0.1" not in raw


@pg
def test_different_database_on_the_same_server_is_different(two_databases, monkeypatch, capsys) -> None:
    monkeypatch.setenv(APP_ENV, two_databases["one"])
    monkeypatch.setenv(MIG_ENV, two_databases["two"])
    code, out, raw = _run(capsys, "--app-env", APP_ENV, "--migration-env", MIG_ENV)
    assert code == dbcheck.EXIT_DIFFERENT and out["result"] == "different"
    assert {"database", "database_oid", "alembic_version"} <= set(out["differences"])
    assert "postmaster_start_time" not in out["differences"]  # 같은 서버
    assert make_url(PG_ADMIN).password not in raw
