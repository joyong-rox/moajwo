"""마이그레이션 0024(저장한 작업 번호를 다시 쓰지 않음) — 빈 SQLite 파일에 alembic을 실제로 실행해 확인한다.

- 0023 → 0024: keyjoin_jobs를 AUTOINCREMENT로 다시 만들고 행(번호·소유자·이름·버전·설정·시각)·고유 제약·외래 키·색인은
  그대로. 순번은 남은 최대 번호에서 시작하고, 그 뒤 지운 번호는 다시 쓰지 않는다. 0024 전에 지워진 번호 중 남은 최대보다
  큰 번호는 한 번 다시 쓰일 수 있다(남는 경계 — 기록이 없음).
- downgrade 0023: 행·번호 그대로, AUTOINCREMENT만 빠진다.
- 0021 이하로 가는 연쇄 downgrade는 0024(맨 위)에서 먼저 거절돼 아무것도 바뀌지 않는다.
  0022까지는 허용(0023이 표를 지움).
PostgreSQL은 test_migration_0024_postgres.py (시퀀스라 바꾸는 것 없음)."""

import sqlite3
import subprocess
from pathlib import Path

import pytest

from tests.test_migrations import _alembic

pytestmark = pytest.mark.sqlite_only(
    "SQLite 파일에 마이그레이션을 실행해 읽음 (PostgreSQL은 test_migration_0024_postgres)"
)

ROWS = "SELECT id, owner_id, name, version, spec, created_at, updated_at FROM keyjoin_jobs ORDER BY id"


def _user(db: sqlite3.Connection, name: str, temp: int = 0) -> int:
    db.execute(
        "INSERT INTO users (username, display_name, password_hash, role, is_active, status, failed_logins, "
        f"auth_version, must_change_password) VALUES (?, ?, 'scrypt$synthetic', 'user', 1, 'active', 0, 0, {temp})",
        (name, name.upper()),
    )
    return db.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def _shape(db: sqlite3.Connection) -> tuple:
    """열·고유 색인·외래 키·이름 붙은 색인 (AUTOINCREMENT 말고 바뀌면 안 되는 것)"""
    cols = [tuple(r[1:]) for r in db.execute("PRAGMA table_info(keyjoin_jobs)")]
    idx = sorted((r[1], r[2]) for r in db.execute("PRAGMA index_list(keyjoin_jobs)"))
    uq = [tuple(r[2] for r in db.execute(f"PRAGMA index_info('{name}')")) for name, unique in idx if unique]
    fks = [tuple(r[2:]) for r in db.execute("PRAGMA foreign_key_list(keyjoin_jobs)")]
    return cols, idx, uq, fks


def _sql(db: sqlite3.Connection) -> str:
    return db.execute("SELECT sql FROM sqlite_master WHERE name = 'keyjoin_jobs'").fetchone()[0]


def _seq(db: sqlite3.Connection) -> int | None:
    row = db.execute("SELECT seq FROM sqlite_sequence WHERE name = 'keyjoin_jobs'").fetchone()
    return row[0] if row else None


def _add(db: sqlite3.Connection, owner: int, name: str) -> int:
    cur = db.execute("INSERT INTO keyjoin_jobs (owner_id, name, spec) VALUES (?, ?, '{}')", (owner, name))
    db.commit()
    return cur.lastrowid


def test_0024_keeps_rows_and_never_reuses_a_deleted_number(tmp_path: Path) -> None:
    _alembic(tmp_path, "upgrade", "0023_keyjoin_jobs")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    uid = _user(db, "u1")
    for name in ("가", "나", "다"):
        db.execute(
            "INSERT INTO keyjoin_jobs (owner_id, name, spec, updated_at) "
            "VALUES (?, ?, '{\"k\": 1}', '2026-11-04 01:02:03')",
            (uid, name),
        )
    db.execute("UPDATE keyjoin_jobs SET version = 4 WHERE name = '나'")
    db.execute("DELETE FROM keyjoin_jobs WHERE id = 3")
    db.commit()
    # 0023: 가장 큰 번호를 지우면 SQLite가 그 번호를 다시 준다 (고치려는 조건)
    assert _add(db, uid, "재사용 확인") == 3
    db.execute("DELETE FROM keyjoin_jobs WHERE id = 3")
    db.commit()
    rows, shape = db.execute(ROWS).fetchall(), _shape(db)
    assert "AUTOINCREMENT" not in _sql(db).upper()
    db.close()

    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0024_keyjoin_job_ids"
    assert db.execute(ROWS).fetchall() == rows  # 번호·소유자·이름·버전·설정·시각 그대로
    assert _shape(db) == shape  # 열·고유 제약·외래 키(ON DELETE CASCADE)·색인 그대로
    assert "AUTOINCREMENT" in _sql(db).upper()
    assert _seq(db) == 2  # 남은 최대 번호에서 시작
    assert _add(db, uid, "라") == 3  # 0024 전에 지워진 3은 한 번 다시 쓰일 수 있다 (남는 경계, 모듈 설명)
    db.execute("DELETE FROM keyjoin_jobs WHERE id = 3")
    db.commit()
    assert _add(db, uid, "라") == 4  # 0024 뒤에 지운 번호는 다시 쓰지 않는다 — 같은 이름으로 다시 저장해도
    with pytest.raises(sqlite3.IntegrityError):  # 같은 소유자·같은 이름은 여전히 하나
        _add(db, uid, "라")
    db.rollback()
    db.close()


def test_0024_on_an_empty_table_starts_at_one_and_still_does_not_reuse(tmp_path: Path) -> None:
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert _seq(db) == 0
    uid = _user(db, "u1")
    first = _add(db, uid, "가")
    assert first == 1
    db.execute("DELETE FROM keyjoin_jobs WHERE id = ?", (first,))
    db.commit()
    assert _add(db, uid, "가") == 2
    db.close()


def test_0024_downgrade_keeps_rows_and_numbers(tmp_path: Path) -> None:
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    uid = _user(db, "u1")
    for name in ("가", "나", "다"):
        _add(db, uid, name)
    db.execute("DELETE FROM keyjoin_jobs WHERE name = '가'")
    db.commit()
    rows, shape = db.execute(ROWS).fetchall(), _shape(db)
    db.close()
    _alembic(tmp_path, "downgrade", "0023_keyjoin_jobs")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0023_keyjoin_jobs"
    assert db.execute(ROWS).fetchall() == rows and _shape(db) == shape
    assert "AUTOINCREMENT" not in _sql(db).upper() and _seq(db) is None
    db.close()
    _alembic(tmp_path, "upgrade", "head")  # 다시 올려도 행 그대로
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute(ROWS).fetchall() == rows and _seq(db) == 3
    db.close()


def test_0024_chain_downgrade_below_0022_is_refused_at_the_top(tmp_path: Path) -> None:
    """임시 비밀번호 상태 계정이 있을 때 'downgrade 0021'은 0024에서 먼저 거절돼 리비전·표·번호 방식이 그대로다
    (0023으로 내려간 뒤 거절돼 중간에서 멈추지 않음). 'downgrade 0022'(0023이 이 표를 지움)는 지금처럼 허용된다."""
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    uid = _user(db, "t1", temp=1)
    _add(db, uid, "월간")
    db.close()
    with pytest.raises(subprocess.CalledProcessError) as info:
        _alembic(tmp_path, "downgrade", "0021_run_request_key")
    err = info.value.stderr.decode("utf-8", "replace")
    for needle in ("임시 비밀번호 상태인 계정이 1개", "0021로 내리지 않습니다", "아무것도 바꾸지 않았습니다"):
        assert needle in err, (needle, err[-400:])
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0024_keyjoin_job_ids"
    assert db.execute("SELECT count(*) FROM keyjoin_jobs").fetchone()[0] == 1 and "AUTOINCREMENT" in _sql(db).upper()
    db.close()
    _alembic(tmp_path, "downgrade", "0022_temp_passwords")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0022_temp_passwords"
    assert "keyjoin_jobs" not in {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert db.execute("SELECT must_change_password FROM users").fetchall() == [(1,)]
    db.close()
