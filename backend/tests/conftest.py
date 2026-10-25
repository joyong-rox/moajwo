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
# 시험마다 빈 DB를 새로 만드는 시험(이관·마이그레이션·여러 프로세스)용 관리자 주소 — 일회용 시험 서버만.
# 없으면 그 시험은 건너뛴다.
PG_ADMIN = os.environ.get("MOAJWO_TEST_PG_ADMIN_URL", "").strip()

import pytest  # noqa: E402


def pytest_configure(config):  # noqa: ANN001
    config.addinivalue_line(
        "markers",
        "sqlite_only(reason): SQLite 자체 동작(파일·SQLite 마이그레이션 등)만 확인 — PostgreSQL 실행에서 건너뜀",
    )


def pytest_collection_modifyitems(config, items):  # noqa: ANN001
    """PostgreSQL로 돌릴 때 sqlite_only 표시가 붙은 시험만 그 이유로 건너뛴다 (파일 단위로 빼지 않는다)."""
    if not POSTGRES:
        return
    for item in items:
        mark = item.get_closest_marker("sqlite_only")
        if mark is not None:
            item.add_marker(pytest.mark.skip(reason="SQLite 전용 — " + (mark.args[0] if mark.args else "")))


TEST_PASSWORD = "test-password-1"


def _engine():  # noqa: ANN202
    from app.db import engine

    return engine


def _execute(cur, sql: str, args: tuple):  # noqa: ANN001, ANN202
    postgres = _engine().dialect.name == "postgresql"
    if args:  # PostgreSQL 자리표시자는 %s — 글자 그대로의 %(LIKE '%…%')는 %%로 적어야 한다
        cur.execute(sql.replace("%", "%%").replace("?", "%s") if postgres else sql, args)
    else:  # 매개변수가 없으면 그대로 (PostgreSQL에서 LIKE '%…'의 %를 자리표시자로 읽지 않게)
        cur.execute(sql)
    return cur


def db_rows(sql: str, *args):  # noqa: ANN201
    """시험용 직접 조회 — 앱과 같은 DB(SQLite·PostgreSQL)에 붙는다. 자리표시자는 ?로 쓴다."""
    raw = _engine().raw_connection()
    try:
        cur = _execute(raw.cursor(), sql, args)
        rows = [tuple(r) for r in cur.fetchall()] if cur.description else []
        raw.commit()
        return rows
    finally:
        raw.close()


class DbConnection:
    """sqlite3 연결처럼 쓰는 시험용 연결 (con.execute(sql, args).fetchone() · with 블록) — 앱과 같은 DB에 붙는다.
    with 블록이 끝나면 커밋하고(예외면 되돌리고) 닫는다. 자리표시자는 ?로 쓴다."""

    def __init__(self) -> None:
        self.raw = _engine().raw_connection()

    def execute(self, sql: str, args: tuple = ()):  # noqa: ANN201
        return _execute(self.raw.cursor(), sql, tuple(args))

    def __enter__(self) -> "DbConnection":
        return self

    def __exit__(self, kind, *_) -> None:  # noqa: ANN001
        try:
            (self.raw.commit if kind is None else self.raw.rollback)()
        finally:
            self.raw.close()


def db_connect() -> DbConnection:
    return DbConnection()


def pg_database(name: str, drop: bool = False) -> str:
    """일회용 시험 서버(PG_ADMIN)에 빈 DB를 새로 만들고(같은 이름이 있으면 지우고) 주소를 돌려준다.
    drop이면 지우기만 한다."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    admin = make_url(PG_ADMIN)
    engine = create_engine(admin.set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT")
    with engine.connect() as con:
        con.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        if not drop:
            con.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()
    return admin.set(database=name).render_as_string(hide_password=False)


def pg_service_files(directory, name: str, url: str) -> dict[str, str]:  # noqa: ANN001
    """시험용 libpq 서비스 파일·비밀번호 파일을 만들고 그 경로 환경 변수(PGSERVICEFILE·PGPASSFILE)를 돌려준다.
    서비스 파일에는 비밀번호를 넣지 않는다 (비밀번호 파일에만 — docs/EXTERNAL_DB.md 3-1). 비밀번호 파일의 DB 칸은 *라
    서비스 주소에서 DB 이름을 바꿔도(postgresql:///다른DB?service=이름) 맞는다. 접속 주소는 postgresql:///?service=이름."""
    from pathlib import Path

    from sqlalchemy.engine import make_url

    url_ = make_url(url)
    if not url_.password or not url_.host:
        pytest.skip("시험 서버 주소에 호스트·비밀번호가 없어 합성 비밀번호 파일을 만들 수 없음")
    port = str(url_.port or 5432)
    lines = [f"[{name}]", f"host={url_.host}", f"port={port}", f"dbname={url_.database}", f"user={url_.username}"]
    lines += [f"{key}={value}" for key, value in url_.query.items() if isinstance(value, str)]  # sslmode 등

    def esc(value: str) -> str:  # 비밀번호 파일 형식: 칸 안의 : 와 \ 는 \ 로 이스케이프
        return value.replace("\\", "\\\\").replace(":", "\\:")

    folder = Path(directory)
    service, passfile = folder / "pg_service.conf", folder / "pgpass.conf"
    service.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    passfile.write_text(f"{esc(url_.host)}:{port}:*:{esc(url_.username)}:{esc(url_.password)}\n", encoding="utf-8",
                        newline="\n")  # fmt: skip
    if os.name != "nt":
        passfile.chmod(0o600)  # 유닉스 libpq는 그룹·다른 사용자 권한이 있는 비밀번호 파일을 무시한다
    return {"PGSERVICEFILE": str(service), "PGPASSFILE": str(passfile)}


def db_dump() -> str:
    """앱 DB의 모든 표·모든 행을 글자로 (SQLite iterdump 대신 — 두 DB에서 같게). '원문이 어디에도 없음' 확인용."""
    from sqlalchemy import inspect, text

    engine = _engine()
    parts = []
    with engine.connect() as con:
        for name in inspect(con).get_table_names():
            for row in con.execute(text(f'SELECT * FROM "{name}"')):
                parts.append(f"{name}: {tuple(row)!r}")
    return "\n".join(parts)


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
