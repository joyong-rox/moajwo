"""0022(임시 비밀번호 상태)의 PostgreSQL 경계 — 실제 PostgreSQL 일회용 DB(MOAJWO_TEST_PG_ADMIN_URL)에서 alembic을
프로세스로 돌린다.

- 빈 DB → 0021 → head: 기존 계정은 False·NULL(비밀번호·상태 그대로).
- downgrade: 임시 비밀번호 상태인 계정이 **이용 중·이용 중지·만료와 관계없이** 하나라도 있으면 거절 — 리비전·새
  열·계정·업무 행 그대로. 이용 중지는 임시 상태를 풀지 않는다(중지된 임시 계정 하나만 남아도 거절). 실행 중인 결과
  실행(담당 있음)도 거절.
- 모든 계정이 본인 비밀번호 변경을 끝낸 뒤에는 두 열만 지운다.
"""

from datetime import UTC, datetime
from pathlib import Path

from tests.conftest import pg_database
from tests.test_migration_0020_postgres import INSERT_RUN, _ok, _revision, _sql
from tests.test_migrations_postgres import _alembic, needs_admin

HEAD, PREVIOUS = "0022_temp_passwords", "0021_run_request_key"
TEMP_COLUMNS = {"must_change_password", "temp_password_expires_at"}
FUTURE, PAST = datetime(2099, 1, 1, tzinfo=UTC), datetime(2020, 1, 1, tzinfo=UTC)


def _temp_columns(url: str) -> set[str]:
    rows = _sql(url, "SELECT column_name FROM information_schema.columns WHERE table_name = 'users' AND "
                     "column_name IN ('must_change_password', 'temp_password_expires_at')")  # fmt: skip
    return {r[0] for r in rows}


def _user(url: str, name: str, *, role: str = "user", status: str = "active", active: bool = True,
          must: bool = False, expires: datetime | None = None) -> int:  # fmt: skip
    return _sql(url, "INSERT INTO users (username, display_name, password_hash, role, is_active, status, "
                     "failed_logins, auth_version, must_change_password, temp_password_expires_at) "
                     "VALUES (:n, :n, 'scrypt$synthetic', :r, :a, :s, 0, 0, :m, :e) RETURNING id",
                n=name, r=role, a=active, s=status, m=must, e=expires)[0][0]  # fmt: skip


def _snapshot(url: str) -> tuple:
    return (
        _revision(url),
        _temp_columns(url),
        _sql(
            url,
            "SELECT id, username, role, status, is_active, password_hash, auth_version, must_change_password, "
            "temp_password_expires_at FROM users ORDER BY id",
        ),  # fmt: skip
        _sql(url, "SELECT id, status, worker FROM runs ORDER BY id"),
    )


@needs_admin
def test_0022_refuses_downgrade_while_any_temp_password_account_exists_on_postgres(tmp_path: Path) -> None:
    url = pg_database("mig_0022_guard")
    try:
        assert _ok(_alembic(url, tmp_path, "upgrade", PREVIOUS))
        rox = _sql(url, "INSERT INTO users (username, display_name, password_hash, role, is_active, status, "
                        "failed_logins, auth_version) "
                        "VALUES ('rox', 'R', 'scrypt$x', 'owner', true, 'active', 0, 0) RETURNING id",
                        )[0][0]  # fmt: skip
        assert _ok(_alembic(url, tmp_path, "upgrade", "head"))
        assert _revision(url) == HEAD and _temp_columns(url) == TEMP_COLUMNS
        assert _sql(url, "SELECT must_change_password, temp_password_expires_at, password_hash FROM users") == [
            (False, None, "scrypt$x")
        ]  # 기존 계정은 임시 상태가 아니고 비밀번호 그대로
        _user(url, "t-active", must=True, expires=FUTURE)
        _user(url, "t-susp", status="suspended", active=False, must=True, expires=FUTURE)
        _user(url, "t-expired", must=True, expires=PAST)
        _user(url, "done")
        run_id = _sql(url, INSERT_RUN.format(extra=", user_id", values=f", {rox}"), status="done")[0][0]

        def refused(*needles: str) -> None:
            held = _snapshot(url)
            done = _alembic(url, tmp_path, "downgrade", PREVIOUS)
            assert done.returncode != 0, done.stdout[-400:]
            for needle in needles:
                assert needle in done.stderr, (needle, done.stderr[-400:])
            assert _snapshot(url) == held  # 리비전·열·계정·업무 행 그대로

        refused("임시 비밀번호 상태인 계정이 3개", "상태와 관계없이", "이용 중지", "아무것도 바꾸지 않았습니다")
        _sql(url, "UPDATE users SET must_change_password = false, temp_password_expires_at = NULL "
                  "WHERE username IN ('t-active', 't-expired')")  # fmt: skip
        refused("임시 비밀번호 상태인 계정이 1개")  # 중지된 임시 계정 하나만 남아도 거절 — 중지로는 풀리지 않는다
        _sql(url, "UPDATE users SET must_change_password = false, temp_password_expires_at = NULL")
        _sql(url, "UPDATE runs SET status = 'running', worker = 'abcdef012345-live', heartbeat_at = now() "
                  "WHERE id = :i", i=run_id)  # fmt: skip
        refused(
            "0021로 내리지 않습니다", "app.recovery recover"
        )  # 임시 계정이 없어도 실행 중 결과 실행(담당 있음)은 거절
        _sql(url, "UPDATE runs SET status = 'done', worker = NULL, finished_at = now() WHERE id = :i", i=run_id)
        assert _ok(_alembic(url, tmp_path, "downgrade", PREVIOUS))
        assert _revision(url) == PREVIOUS and _temp_columns(url) == set()
        assert _sql(url, "SELECT username, password_hash, status FROM users ORDER BY id") == [
            ("rox", "scrypt$x", "active"), ("t-active", "scrypt$synthetic", "active"),
            ("t-susp", "scrypt$synthetic", "suspended"), ("t-expired", "scrypt$synthetic", "active"),
            ("done", "scrypt$synthetic", "active"),
        ]  # fmt: skip
        assert _ok(_alembic(url, tmp_path, "upgrade", "head"))  # 다시 올리면 모두 False·NULL
        assert _sql(
            url, "SELECT count(*) FROM users WHERE must_change_password OR temp_password_expires_at IS NOT NULL"
        ) == [(0,)]
    finally:
        pg_database("mig_0022_guard", drop=True)
