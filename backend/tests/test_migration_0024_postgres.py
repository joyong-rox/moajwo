"""0024(저장한 작업 번호를 다시 쓰지 않음)의 PostgreSQL 경계 — 실제 PostgreSQL 일회용 DB(MOAJWO_TEST_PG_ADMIN_URL)에서
alembic을 프로세스로 돌린다 (주소가 없으면 건너뜀).

- 0023 → 0024: 바꾸는 것 없음(id는 0023부터 시퀀스) — 행·열 정의 그대로, 리비전만 오른다.
- 가장 큰 번호를 지우고 같은 이름으로 다시 저장해도 같은 번호를 받지 않는다(시퀀스 — SQLite에서 고친 조건).
- downgrade 0023: 행 그대로. 시퀀스는 운영에서 조작하지 않는다(이 시험도 일회용 DB에서만).
"""

from pathlib import Path

from tests.conftest import pg_database
from tests.test_migration_0020_postgres import _ok, _revision, _sql
from tests.test_migrations_postgres import _alembic, needs_admin

HEAD, PREVIOUS = "0024_keyjoin_job_ids", "0023_keyjoin_jobs"
ROWS = "SELECT id, owner_id, name, version, spec, created_at, updated_at FROM keyjoin_jobs ORDER BY id"
COLUMNS = (
    "SELECT column_name, data_type, column_default, is_nullable FROM information_schema.columns "
    "WHERE table_name = 'keyjoin_jobs' ORDER BY ordinal_position"
)
INSERT = "INSERT INTO keyjoin_jobs (owner_id, name, spec) VALUES (:o, :n, '{}') RETURNING id"


@needs_admin
def test_0024_changes_nothing_on_postgres_and_numbers_are_not_reused(tmp_path: Path) -> None:
    url = pg_database("mig_0024_ids")
    try:
        assert _ok(_alembic(url, tmp_path, "upgrade", PREVIOUS))
        uid = _sql(url, "INSERT INTO users (username, display_name, password_hash, role, is_active, status, "
                        "failed_logins, auth_version, must_change_password) "
                        "VALUES ('u1', 'U1', 'scrypt$synthetic', 'user', true, 'active', 0, 0, false) RETURNING id",
                   )[0][0]  # fmt: skip
        first = _sql(url, INSERT, o=uid, n="가")[0][0]
        newest = _sql(url, INSERT, o=uid, n="나")[0][0]
        _sql(url, "DELETE FROM keyjoin_jobs WHERE id = :i", i=newest)
        rows, columns = _sql(url, ROWS), _sql(url, COLUMNS)
        assert [r[0] for r in rows] == [first]

        assert _ok(_alembic(url, tmp_path, "upgrade", "head"))
        assert _revision(url) == HEAD
        assert _sql(url, ROWS) == rows and _sql(url, COLUMNS) == columns  # 바꾸는 것 없음
        again = _sql(url, INSERT, o=uid, n="나")[0][0]  # 지운 가장 큰 번호와 같은 이름으로 다시 저장
        assert again > newest

        assert _ok(_alembic(url, tmp_path, "downgrade", PREVIOUS))
        assert _revision(url) == PREVIOUS
        assert [r[0] for r in _sql(url, ROWS)] == [first, again] and _sql(url, COLUMNS) == columns
    finally:
        pg_database("mig_0024_ids", drop=True)
