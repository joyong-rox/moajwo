"""마이그레이션 0023(저장한 작업 keyjoin_jobs) — 빈 SQLite 파일에 alembic을 실제로 실행해 확인한다.

- 0022 → head: keyjoin_jobs 표가 생기고(열·고유 제약·색인), 기존 표의 행은 그대로.
- downgrade 0022: 이 표만 지우고 계정·실행 기록·자동화 표는 그대로 (저장한 설정만 사라짐).
PostgreSQL은 test_migration_0023_postgres.py."""

import sqlite3
import subprocess
from pathlib import Path

import pytest

from tests.test_migrations import _alembic

pytestmark = pytest.mark.sqlite_only(
    "SQLite 파일에 마이그레이션을 실행해 읽음 (PostgreSQL은 test_migration_0023_postgres)"
)

COLUMNS = {"id", "owner_id", "name", "version", "spec", "created_at", "updated_at"}


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def test_0023_creates_keyjoin_jobs_and_downgrade_removes_only_that_table(tmp_path: Path) -> None:
    _alembic(tmp_path, "upgrade", "0022_temp_passwords")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.execute(
        "INSERT INTO users (username, display_name, password_hash, role, is_active, status, failed_logins, "
        "auth_version, must_change_password) VALUES ('u1', 'U1', 'scrypt$synthetic', 'user', 1, 'active', 0, 0, 0)"
    )
    db.commit()
    assert "keyjoin_jobs" not in {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    db.close()

    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0023_keyjoin_jobs"
    assert _columns(db, "keyjoin_jobs") == COLUMNS
    assert db.execute("SELECT count(*) FROM keyjoin_jobs").fetchone()[0] == 0  # 빈 표 — 기존 행은 바꾸지 않는다
    assert db.execute("SELECT username, status, must_change_password FROM users").fetchall() == [("u1", "active", 0)]
    uid = db.execute("SELECT id FROM users WHERE username = 'u1'").fetchone()[0]
    db.execute("INSERT INTO keyjoin_jobs (owner_id, name, spec) VALUES (?, '월간', '{}')", (uid,))
    db.commit()
    assert db.execute("SELECT version FROM keyjoin_jobs").fetchone()[0] == 1  # 서버 기본값
    with pytest.raises(sqlite3.IntegrityError):  # 같은 소유자·같은 이름은 하나
        db.execute("INSERT INTO keyjoin_jobs (owner_id, name, spec) VALUES (?, '월간', '{}')", (uid,))
    db.rollback()
    indexes = {r[1] for r in db.execute("PRAGMA index_list(keyjoin_jobs)")}
    assert "ix_keyjoin_jobs_owner_id" in indexes
    db.close()

    _alembic(tmp_path, "downgrade", "0022_temp_passwords")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "keyjoin_jobs" not in tables and {"users", "runs", "automations", "source_files"} <= tables
    assert db.execute("SELECT username FROM users").fetchall() == [("u1",)]
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0022_temp_passwords"
    db.close()


def test_0023_chain_downgrade_below_0022_is_refused_at_the_top_but_one_step_is_allowed(tmp_path: Path) -> None:
    """임시 비밀번호 상태 계정(또는 담당 있는 실행 중 행)이 있을 때: 'downgrade 0021'은 0023에서 먼저 거절돼 리비전·표가
    그대로(중간 0022에서 멈춰 표만 사라지는 상태 방지), 'downgrade 0022'(이 표만)는 허용된다."""
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.execute(
        "INSERT INTO users (username, display_name, password_hash, role, is_active, status, failed_logins, "
        "auth_version, must_change_password) VALUES ('t1', 'T1', 'scrypt$synthetic', 'user', 1, 'active', 0, 0, 1)"
    )
    uid = db.execute("SELECT id FROM users WHERE username = 't1'").fetchone()[0]
    db.execute("INSERT INTO keyjoin_jobs (owner_id, name, spec) VALUES (?, '월간', '{}')", (uid,))
    db.commit()
    db.close()
    with pytest.raises(subprocess.CalledProcessError) as info:
        _alembic(tmp_path, "downgrade", "0021_run_request_key")
    err = info.value.stderr.decode("utf-8", "replace")
    for needle in ("임시 비밀번호 상태인 계정이 1개", "0021로 내리지 않습니다", "아무것도 바꾸지 않았습니다"):
        assert needle in err, (needle, err[-400:])
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0023_keyjoin_jobs"
    assert db.execute("SELECT count(*) FROM keyjoin_jobs").fetchone()[0] == 1  # 표·행 그대로
    db.close()
    _alembic(tmp_path, "downgrade", "0022_temp_passwords")  # 이 표만 내리는 한 단계는 임시 계정이 있어도 된다
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0022_temp_passwords"
    assert "keyjoin_jobs" not in {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert db.execute("SELECT must_change_password FROM users").fetchall() == [(1,)]  # 계정은 그대로
    db.close()
