"""O-03 외부 DB 모드: 설정 해석, DB에 닿지 못할 때의 응답, 시간대 보존 (SQLite·PostgreSQL 실행 모두).

- DB 주소가 없거나 틀리면 빈 SQLite로 대신 열지 않는다 (최초 관리자 설정이 다시 열리지 않게).
- DB에 닿지 못하거나(연결·인증) 바빠서(연결 풀·잠금 대기 초과) 처리하지 못한 요청은 503 — 비밀번호 오류·계정 없음·
  최초 설정 필요로 보이지 않는다. 쓰기 요청은 '그대로입니다'라고 단정하지 않고 반영 여부를 확인하라고 한다.
- 데이터·코드·스키마 오류는 연결 장애(503)로 감추지 않는다 (500).
- KST로 기록하는 시각과 UTC 기본값이 저장 뒤에도 같은 순간이다 (PostgreSQL은 시간대를 붙여 돌려준다).
프로세스 재시작·재배포·휴면·이관·백업 복원·기동 시 잠금 대기는 diagnostics/scripts/external_db_check.py가
실제 프로세스로 확인한다.
"""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError, DBAPIError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app import db as app_db
from app.config import DATA_DIR, _database
from app.db import db_failure
from app.main import DB_BUSY, DB_UNREACHABLE, WRITE_UNCERTAIN, app, database_failure
from app.models import AuthSession, IntegrationRun, User
from app.timefmt import stored_kst, utc_to_kst

KST = timezone(timedelta(hours=9))


def test_settings_never_fall_back_to_sqlite_in_external_mode() -> None:
    assert _database("", "")[0] == "sqlite"
    assert _database("", "sqlite")[0] == "sqlite"
    with pytest.raises(ValueError, match="DATABASE_URL이 없습니다"):
        _database("", "external")
    with pytest.raises(ValueError, match="SQLite입니다"):
        _database("sqlite:///x.db", "external")
    with pytest.raises(ValueError, match="지원하지 않는 DB"):
        _database("mysql://u:secret-pw@h/db", "")
    with pytest.raises(ValueError, match="sqlite 또는 external"):
        _database("", "neon")
    with pytest.raises(ValueError) as exc:
        _database("mysql://u:secret-pw@h/db", "")
    assert "secret-pw" not in str(exc.value)  # 오류 문구에 주소·비밀번호를 넣지 않는다


def test_postgres_url_keeps_password_and_tls_options() -> None:
    raw = "postgres://app:p%40ss%3A%2F%25@ep-x-pooler.example.neon.tech/neondb?sslmode=require&channel_binding=require"
    mode, url = _database(raw, "")
    assert mode == "external"
    parsed = make_url(url)
    assert (parsed.drivername, parsed.username, parsed.password) == ("postgresql+psycopg", "app", "p@ss:/%")
    assert (parsed.host, parsed.database) == ("ep-x-pooler.example.neon.tech", "neondb")
    assert dict(parsed.query) == {"sslmode": "require", "channel_binding": "require"}  # 순서만 바뀔 수 있다
    assert _database(raw.replace("postgres://", "postgresql://"), "external")[1] == url


def test_postgres_url_keeps_verify_full_and_system_root_options() -> None:
    """권장 TLS 옵션(sslmode=verify-full·sslrootcert=system·channel_binding=require — docs/EXTERNAL_DB.md 3-2)도 그대로
    전달한다 (앱은 연결 옵션을 바꾸지 않는다)."""
    raw = "postgresql://app:pw@ep-x.example.neon.tech/neondb?sslmode=verify-full&sslrootcert=system"
    raw += "&channel_binding=require"
    mode, url = _database(raw, "external")
    assert mode == "external"
    assert dict(make_url(url).query) == {
        "sslmode": "verify-full",
        "sslrootcert": "system",
        "channel_binding": "require",
    }


@pytest.fixture
def unreachable(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """앱의 DB 세션이 닿지 않는 PostgreSQL을 가리키게 한다 (거절되는 로컬 포트 — 외부로 요청하지 않음)."""
    bad = create_engine("postgresql+psycopg://nobody:wrong@127.0.0.1:9/none", connect_args={"connect_timeout": 3})
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=bad))
    return TestClient(app)


def test_unreachable_database_is_503_not_a_login_or_setup_answer(unreachable: TestClient) -> None:
    c = unreachable
    status = c.get("/auth/status")
    assert status.status_code == 503 and status.json() == {"detail": DB_UNREACHABLE}  # 'setup_required'가 아니다
    login = c.post("/auth/login", json={"username": "someone", "password": "pw-123456"})
    assert login.status_code == 503 and login.json() == {"detail": DB_UNREACHABLE + WRITE_UNCERTAIN}
    assert "비밀번호" not in login.text and "그대로입니다" not in login.text  # 쓰기 결과를 단정하지 않는다
    setup = c.post("/auth/setup", json={"username": "owner2", "display_name": "새 소유자", "password": "Pw-123456!"})
    assert setup.status_code == 410 and "Pw-123456!" not in setup.text  # 공개 최초 설정은 종료 — DB와 무관하게 410
    res = c.post("/users", json={"username": "new1", "display_name": "신규"})  # 계정 발급: DB에 닿지 못하면 503
    assert res.status_code == 503 and "이미" not in res.text and "temp_password" not in res.text
    health = c.get("/health")
    assert health.status_code == 503 and health.json()["database"] == "error"
    for res in (status, login, setup, health):
        assert "nobody" not in res.text and "127.0.0.1:9" not in res.text  # 주소·계정 이름을 내보내지 않는다


def test_times_keep_the_same_instant_after_storage() -> None:
    """KST로 기록하는 시각(실행 시작·끝)과 UTC 시각(세션 만료)이 저장 뒤에도 같은 순간이다 — 9시간 밀림 없음."""
    started = datetime(2026, 10, 23, 9, 30, 15, tzinfo=KST)
    expires = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)
    with app_db.SessionLocal() as db:
        user = db.scalar(select(User).limit(1))
        run = IntegrationRun(status="done", file_ids="[]", started_at=started, finished_at=started, user_id=user.id)
        db.add(run)
        session = AuthSession(token_hash="t" * 64, user_id=user.id, created_at=expires, expires_at=expires)
        db.add(session)
        db.commit()
        run_id, session_id = run.id, session.id
    with app_db.SessionLocal() as db:
        run = db.get(IntegrationRun, run_id)
        session = db.get(AuthSession, session_id)
        assert stored_kst(run.started_at) == started and stored_kst(run.started_at).hour == 9
        assert utc_to_kst(session.expires_at) == expires
        assert utc_to_kst(session.created_at).hour == 21  # UTC 12시 = KST 21시
        server_default = db.get(User, user.id).created_at  # DB 기본값(UTC) — 지금과 하루 이상 차이 나지 않는다
        assert abs(utc_to_kst(server_default) - datetime.now(UTC)) < timedelta(days=1)
        db.delete(session)
        db.delete(run)
        db.commit()


def test_pool_exhaustion_is_503_busy_and_never_the_setup_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    """연결 풀이 바닥나면(작은 시험 풀, 하나뿐인 연결을 쥐고 있음) 대기 시간 뒤 503 —
    최초 설정·SQLite로 넘어가지 않는다."""
    url = app_db.engine.url
    extra = {"connect_args": {"check_same_thread": False}} if url.get_backend_name() == "sqlite" else {}
    small = create_engine(url, poolclass=QueuePool, pool_size=1, max_overflow=0, pool_timeout=1, **extra)
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=small))
    c = TestClient(app)
    held = small.connect()
    try:
        status = c.get("/auth/status")
        login = c.post("/auth/login", json={"username": "someone", "password": "pw-123456"})
    finally:
        held.close()
    assert status.status_code == 503 and status.json() == {"detail": DB_BUSY}
    assert login.status_code == 503 and login.json() == {"detail": DB_BUSY + WRITE_UNCERTAIN}
    again = c.get("/auth/status")  # 연결이 돌아오면 바로 정상 — 설정이 이미 끝난 DB라 최초 설정 필요가 아니다
    assert again.status_code == 200 and again.json()["setup_required"] is False
    small.dispose()
    if url.get_backend_name() == "postgresql":
        assert not (DATA_DIR / "moajwo.db").exists()  # 외부 DB 실행에서 SQLite 파일을 만들지 않음


def _dbapi(cls: type[DBAPIError], orig: Exception) -> DBAPIError:
    return cls("SELECT secret_column FROM users WHERE name = %(n)s", {"n": "secret-value"}, orig)


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (PoolTimeout("QueuePool limit"), "busy"),
        (_dbapi(OperationalError, psycopg.OperationalError("connection failed")), "unreachable"),  # 연결·인증 실패
        (_dbapi(OperationalError, psycopg.errors.AdminShutdown("terminating")), "unreachable"),
        (_dbapi(OperationalError, psycopg.errors.InvalidPassword("auth")), "unreachable"),
        (_dbapi(OperationalError, psycopg.errors.LockNotAvailable("lock timeout")), "busy"),  # 이관 잠금 대기 초과
        (_dbapi(OperationalError, psycopg.errors.QueryCanceled("statement timeout")), "busy"),
        (_dbapi(OperationalError, sqlite3.OperationalError("database is locked")), "busy"),
        (_dbapi(OperationalError, sqlite3.OperationalError("unable to open database file")), "unreachable"),
        (_dbapi(OperationalError, sqlite3.OperationalError("no such table: users")), None),  # 스키마 문제
        (_dbapi(ProgrammingError, psycopg.errors.UndefinedTable("relation does not exist")), None),
        (_dbapi(DataError, psycopg.errors.NumericValueOutOfRange("out of range")), None),  # 데이터 결함
    ],
)
def test_only_connection_and_capacity_failures_count_as_database_unavailable(exc: Exception, kind: str | None) -> None:
    assert db_failure(exc) == kind


def test_data_and_code_errors_stay_500_and_answers_carry_no_sql(caplog: pytest.LogCaptureFixture) -> None:
    """같은 처리기를 단 작은 앱: 연결 장애만 503, 데이터·코드 오류는 500 그대로. 응답·기록에 SQL·매개변수가 없다."""
    mini = FastAPI()
    mini.add_exception_handler(DBAPIError, database_failure)
    errors = {
        "lock": _dbapi(OperationalError, psycopg.errors.LockNotAvailable("lock timeout")),
        "data": _dbapi(DataError, psycopg.errors.NumericValueOutOfRange("out of range")),
        "schema": _dbapi(OperationalError, sqlite3.OperationalError("no such table: users")),
    }

    @mini.get("/r/{name}")
    def read(name: str) -> None:
        raise errors[name]

    @mini.post("/w/{name}")
    def write(name: str) -> None:
        raise errors[name]

    c = TestClient(mini, raise_server_exceptions=False)
    assert c.get("/r/lock").json() == {"detail": DB_BUSY}
    assert c.post("/w/lock").json() == {"detail": DB_BUSY + WRITE_UNCERTAIN}
    for name in ("data", "schema"):
        assert c.get(f"/r/{name}").status_code == 500 and c.post(f"/w/{name}").status_code == 500
    lock = c.get("/r/lock")
    assert "secret" not in lock.text and "SELECT" not in lock.text
    assert not any("secret" in r.getMessage() for r in caplog.records if r.name == "app.main")
