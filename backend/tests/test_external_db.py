"""O-03 외부 DB 모드: 설정 해석, DB에 닿지 못할 때의 응답, 시간대 보존 (SQLite·PostgreSQL 실행 모두).

- DB 주소가 없거나 틀리면 빈 SQLite로 대신 열지 않는다 (최초 관리자 설정이 다시 열리지 않게).
- DB에 닿지 못한 요청은 503 — 비밀번호 오류·계정 없음·최초 설정 필요로 보이지 않는다.
- KST로 기록하는 시각과 UTC 기본값이 저장 뒤에도 같은 순간이다 (PostgreSQL은 시간대를 붙여 돌려준다).
프로세스 재시작·재배포·휴면·이관·백업 복원은 diagnostics/scripts/external_db_check.py가 실제 프로세스로 확인한다.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app import db as app_db
from app.config import _database
from app.main import DB_UNAVAILABLE, app
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


@pytest.fixture
def unreachable(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """앱의 DB 세션이 닿지 않는 PostgreSQL을 가리키게 한다 (거절되는 로컬 포트 — 외부로 요청하지 않음)."""
    bad = create_engine("postgresql+psycopg://nobody:wrong@127.0.0.1:9/none", connect_args={"connect_timeout": 3})
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=bad))
    return TestClient(app)


def test_unreachable_database_is_503_not_a_login_or_setup_answer(unreachable: TestClient) -> None:
    c = unreachable
    status = c.get("/auth/status")
    assert status.status_code == 503 and status.json() == {"detail": DB_UNAVAILABLE}  # 'setup_required'가 아니다
    login = c.post("/auth/login", json={"username": "someone", "password": "pw-123456"})
    assert login.status_code == 503 and "비밀번호" not in login.text
    setup = c.post("/auth/setup", json={"username": "owner2", "display_name": "새 소유자", "password": "Pw-123456!"})
    assert setup.status_code == 503 and "이미 계정" not in setup.text
    signup = {"username": "new1", "display_name": "신규", "password": "Pw-123456!", "password_confirm": "Pw-123456!"}
    res = c.post("/auth/signup", json=signup)
    assert res.status_code == 503 and "이미" not in res.text
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
