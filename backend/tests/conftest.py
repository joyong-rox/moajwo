import os
import tempfile

# app 모듈을 임포트하기 전에 테스트 전용 데이터 디렉터리를 지정한다 (실제 DB 보호).
os.environ["MOAJWO_DATA_DIR"] = tempfile.mkdtemp(prefix="moajwo-test-")
os.environ.pop("DATABASE_URL", None)
os.environ.pop("MOAJWO_DB_MODE", None)
os.environ.pop("MOAJWO_MIGRATION_DATABASE_URL", None)
# MOAJWO_TEST_DATABASE_URL이 있으면 그 PostgreSQL(시험마다 새로 만든 빈 DB)로 돌린다 — 외부 DB 모드 확인용.
# 운영·시연 DB 주소를 넣지 않는다. 비어 있으면 지금처럼 임시 SQLite.
POSTGRES = os.environ.get("MOAJWO_TEST_DATABASE_URL", "").strip()
if POSTGRES:
    os.environ["DATABASE_URL"] = POSTGRES

import pytest  # noqa: E402


def pytest_collection_modifyitems(config, items):  # noqa: ANN001
    """PostgreSQL로 돌릴 때 SQLite 파일을 직접 여는 시험(sqlite3.connect)은 건너뛴다 — 이유를 함께 남긴다."""
    if not POSTGRES:
        return
    skip = pytest.mark.skip(reason="SQLite 파일을 직접 읽는 시험 — PostgreSQL 실행에서 제외 (SQLite 실행에서 확인)")
    direct: dict[str, bool] = {}
    for item in items:
        path = str(item.fspath)
        if path not in direct:
            direct[path] = "sqlite3.connect(" in open(path, encoding="utf-8").read()
        if direct[path]:
            item.add_marker(skip)


TEST_PASSWORD = "test-password-1"


def db_rows(sql: str, *args):  # noqa: ANN201
    """시험용 직접 조회 — 앱과 같은 DB(SQLite·PostgreSQL)에 붙는다. 자리표시자는 ?로 쓴다."""
    from app.db import engine

    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute(sql.replace("?", "%s") if engine.dialect.name == "postgresql" else sql, args)
        rows = [tuple(r) for r in cur.fetchall()] if cur.description else []
        raw.commit()
        return rows
    finally:
        raw.close()


@pytest.fixture(scope="session", autouse=True)
def logged_in_owner():
    """기존 기능 테스트는 소유자 계정으로 로그인한 상태로 실행한다 (로그인 자체는 test_auth에서 검사)."""
    from app.auth import CurrentUser, current_user, hash_password
    from app.db import Base, SessionLocal, engine
    from app.main import app
    from app.models import User

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        user = User(
            username="test-owner",
            display_name="테스트 소유자",
            password_hash=hash_password(TEST_PASSWORD),
            role="owner",
            is_active=True,
            status="active",  # 0016: 계정 상태 기본값이 승인 대기라 명시 (최초 소유자는 이용 중)
            failed_logins=0,
        )
        db.add(user)
        db.commit()
        owner = CurrentUser(user.id, user.username, user.display_name, user.role)
    app.dependency_overrides[current_user] = lambda: owner
    yield owner
    app.dependency_overrides.pop(current_user, None)
