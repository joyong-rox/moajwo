"""같은 DB 확인 도구(app/dbcheck.py) — 앱 주소(풀러·앱 계정)와 마이그레이션 주소(직접 연결·소유 계정)가 같은 DB인지.

- 비교 규칙·주소 없음·닿지 않는 주소는 DB 없이 돈다 (거절되는 로컬 포트 — 외부로 요청하지 않음).
- PostgreSQL 시험은 MOAJWO_TEST_PG_ADMIN_URL(일회용 시험 서버의 관리자 주소 — 운영·시연 DB 아님)이 있을 때만 돈다:
  같은 DB를 서로 다른 두 계정으로 → 같음(0), 다른 DB → 다름(1). 한쪽은 서비스 주소(서비스 파일·비밀번호 파일)로 붙는다.
- 어느 경우에도 출력에 주소·호스트·비밀번호가 없다.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import dbcheck
from tests.conftest import PG_ADMIN, pg_database, pg_role_with_password

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


@pytest.mark.parametrize("flag", ["--app-env", "--app-service"])
def test_an_address_in_a_name_flag_is_refused_without_repeating_it(
    flag: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """이름 자리(환경 변수 이름·서비스 이름)에 주소를 넣는 실수(예: --app-env "$DATABASE_URL") — 받은 값을 출력에
    되풀이하지 않고 확인 못 함(2)으로 끝낸다. DB에 닿지 않는다."""
    monkeypatch.setenv(MIG_ENV, UNREACHABLE)
    code, out, raw = _run(capsys, flag, UNREACHABLE, "--migration-env", MIG_ENV)
    assert code == dbcheck.EXIT_UNKNOWN and out["result"] == "unknown" and out["failed"] == "app"
    assert "이름" in out["error"] and "app_input" not in out
    assert "pw-secret-123" not in raw and "127.0.0.1" not in raw


@pytest.mark.parametrize(("env_name", "service"), [(UNREACHABLE, None), (None, UNREACHABLE)], ids=["env", "service"])
def test_resolve_refuses_a_value_that_is_not_a_name(env_name: str | None, service: str | None) -> None:
    """앱·마이그레이션 양쪽이 같은 resolve를 쓴다 — 이름이 아닌 값은 그 값을 문구에 넣지 않고 거절."""
    with pytest.raises(dbcheck.CheckError) as exc:
        dbcheck.resolve(env_name, service, "MOAJWO_MIGRATION_DATABASE_URL")
    assert "pw-secret-123" not in str(exc.value) and "127.0.0.1" not in str(exc.value)


def test_unreadable_connect_timeout_setting_is_exit_2() -> None:
    """시간 제한 환경 변수가 정수가 아니면 확인 못 함(2) — 파이썬 일반 실패(1)와 섞여 '다른 DB'로 읽히지 않게."""
    env = {**os.environ, "MOAJWO_DB_CONNECT_TIMEOUT_S": "15s", "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, "-m", "app.dbcheck", "--app-env", "MOAJWO_DBCHECK_TEST_NOT_SET"],
                       capture_output=True, text=True, encoding="utf-8", env=env,
                       cwd=Path(__file__).resolve().parents[1], timeout=120)  # fmt: skip
    assert p.returncode == dbcheck.EXIT_UNKNOWN, (p.returncode, p.stderr[-300:])
    assert json.loads(p.stdout)["result"] == "unknown"


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
    pg_role_with_password(role, password)
    _admin(f"GRANT SELECT ON alembic_version TO {role}", names[0])
    reader = make_url(urls[0]).set(username=role, password=password).render_as_string(hide_password=False)
    yield {"one": urls[0], "two": urls[1], "reader_one": reader, "role": role, "password": password}
    for n in names:
        pg_database(n, drop=True)
    _admin(f"DROP ROLE IF EXISTS {role}")


@pg
def test_same_database_through_two_accounts_is_the_same(two_databases, service_files, monkeypatch, capsys) -> None:
    """앱 쪽은 환경 변수(비밀번호가 든 주소), 마이그레이션 쪽은 서비스 주소(비밀번호는 비밀번호 파일) — 계정이 달라도
    같은 DB면 0. 출력에 주소·비밀번호가 없다."""
    for name in ("PGSERVICE", "PGPASSWORD"):
        monkeypatch.delenv(name, raising=False)
    for name, value in service_files("dbcheck-migrate", two_databases["one"]).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(APP_ENV, two_databases["reader_one"])
    code, out, raw = _run(capsys, "--app-env", APP_ENV, "--migration-service", "dbcheck-migrate")
    assert code == dbcheck.EXIT_SAME and out["result"] == "same", out
    assert out["app"]["user"] == two_databases["role"] and out["migration"]["user"] != two_databases["role"]
    assert out["app"]["alembic_version"] == out["migration"]["alembic_version"] == "0019_analysis_runs"
    assert out["app"]["neon_timeline_id"] is None and out["not_compared"] == []  # Neon이 아니면 식별자 없음
    assert out["app_input"] == f"env:{APP_ENV}" and out["migration_input"] == "service:dbcheck-migrate"
    for secret in (two_databases["password"], make_url(PG_ADMIN).password):
        assert not secret or secret not in raw
    assert "127.0.0.1" not in raw


@pg
def test_different_database_on_the_same_server_is_different(two_databases, monkeypatch, capsys) -> None:
    monkeypatch.setenv(APP_ENV, two_databases["one"])
    monkeypatch.setenv(MIG_ENV, two_databases["two"])
    code, out, raw = _run(capsys, "--app-env", APP_ENV, "--migration-env", MIG_ENV)
    assert code == dbcheck.EXIT_DIFFERENT and out["result"] == "different"
    assert {"database", "database_oid", "alembic_version"} <= set(out["differences"])
    assert "postmaster_start_time" not in out["differences"]  # 같은 서버
    secret = make_url(PG_ADMIN).password
    assert not secret or secret not in raw


@pg
def test_probe_does_not_wait_forever_behind_a_lock(two_databases, monkeypatch, capsys) -> None:
    """이관·마이그레이션이 alembic_version을 잠근 동안 확인은 잠금 대기 제한(5초) 뒤 확인 못 함(2)으로 끝난다 —
    끝없이 기다리거나, 잠금 오류를 '읽지 못한 항목'으로 삼켜 같음으로 답하지 않는다."""
    monkeypatch.setenv(APP_ENV, two_databases["one"])
    monkeypatch.setenv(MIG_ENV, two_databases["one"])
    holder = create_engine(make_url(two_databases["one"]).set(drivername="postgresql+psycopg"))
    result: dict[str, Any] = {}

    def check() -> None:
        result["code"] = dbcheck.main(["--app-env", APP_ENV, "--migration-env", MIG_ENV])

    with holder.connect() as h:
        h.execute(text("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE"))  # 이관 도구와 같은 잠금, 커밋하지 않음
        started = time.monotonic()
        th = threading.Thread(target=check, daemon=True)
        th.start()
        th.join(20)
        waited, still_waiting = time.monotonic() - started, th.is_alive()
        h.rollback()  # 잠금을 푼다 (아래 join이 끝나게)
    th.join(30)
    holder.dispose()
    out = json.loads(capsys.readouterr().out)
    assert not still_waiting, "잠금을 20초 넘게 기다림"
    assert result["code"] == dbcheck.EXIT_UNKNOWN and "55P03" in out["error"] and waited < 15, (waited, out)
