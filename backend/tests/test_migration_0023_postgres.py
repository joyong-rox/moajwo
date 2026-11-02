"""0023(저장한 작업 keyjoin_jobs)의 PostgreSQL 경계 — 실제 PostgreSQL 일회용 DB(MOAJWO_TEST_PG_ADMIN_URL)에서 alembic을
프로세스로 돌린다 (주소가 없으면 건너뜀).

- 빈 DB → 0022 → head: keyjoin_jobs 표(열·소유자별 이름 고유·색인)가 생기고 기존 계정·실행 행은 그대로.
- downgrade 0022: 이 표만 지우고 계정·실행 기록은 그대로 — 실행 중인 결과 실행이 있어도 막지 않는다(이 표와 무관).
"""

from pathlib import Path

from tests.conftest import pg_database
from tests.test_migration_0020_postgres import INSERT_RUN, _ok, _revision, _sql
from tests.test_migrations_postgres import _alembic, needs_admin

HEAD, PREVIOUS = "0023_keyjoin_jobs", "0022_temp_passwords"
COLUMNS = {"id", "owner_id", "name", "version", "spec", "created_at", "updated_at"}


def _columns(url: str) -> set[str]:
    rows = _sql(url, "SELECT column_name FROM information_schema.columns WHERE table_name = 'keyjoin_jobs'")
    return {r[0] for r in rows}


@needs_admin
def test_0023_creates_keyjoin_jobs_and_downgrade_removes_only_that_table_on_postgres(tmp_path: Path) -> None:
    url = pg_database("mig_0023_jobs")
    try:
        assert _ok(_alembic(url, tmp_path, "upgrade", PREVIOUS))
        uid = _sql(url, "INSERT INTO users (username, display_name, password_hash, role, is_active, status, "
                        "failed_logins, auth_version, must_change_password) "
                        "VALUES ('u1', 'U1', 'scrypt$synthetic', 'user', true, 'active', 0, 0, false) RETURNING id",
                   )[0][0]  # fmt: skip
        _sql(url, INSERT_RUN, status="running", worker="w-1")  # 실행 중인 결과 실행이 있어도 이 표와 무관
        assert _columns(url) == set()
        assert _ok(_alembic(url, tmp_path, "upgrade", "head"))
        assert _revision(url) == HEAD and _columns(url) == COLUMNS
        assert _sql(url, "SELECT count(*) FROM keyjoin_jobs")[0][0] == 0
        assert _sql(url, "SELECT username, status FROM users") == [("u1", "active")]
        _sql(url, "INSERT INTO keyjoin_jobs (owner_id, name, spec) VALUES (:o, '월간', '{}')", o=uid)
        assert _sql(url, "SELECT version FROM keyjoin_jobs")[0][0] == 1
        try:  # 같은 소유자·같은 이름은 고유 제약이 막는다 (SQL 오류)
            _sql(url, "INSERT INTO keyjoin_jobs (owner_id, name, spec) VALUES (:o, '월간', '{}')", o=uid)
            raise AssertionError("같은 이름이 들어갔다")
        except Exception as exc:  # noqa: BLE001 — 드라이버의 고유 제약 위반 예외 종류는 고정하지 않는다
            assert "uq_keyjoin_jobs_owner_name" in str(exc) or "duplicate" in str(exc).lower()
        indexes = {r[0] for r in _sql(url, "SELECT indexname FROM pg_indexes WHERE tablename = 'keyjoin_jobs'")}
        assert "ix_keyjoin_jobs_owner_id" in indexes and "uq_keyjoin_jobs_owner_name" in indexes
        assert _ok(_alembic(url, tmp_path, "downgrade", PREVIOUS))
        assert _revision(url) == PREVIOUS and _columns(url) == set()
        assert _sql(url, "SELECT username FROM users") == [("u1",)]
        assert _sql(url, "SELECT count(*) FROM runs WHERE status = 'running'")[0][0] == 1
    finally:
        pg_database("mig_0023_jobs", drop=True)
