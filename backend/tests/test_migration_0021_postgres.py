"""0021(결과 실행의 요청 번호·내용 해시·고유 색인) 마이그레이션을 PostgreSQL에서 — docs/DEPLOY.md 10장의 경계 그대로.

- 0020 → 0021: 이 판 전의 key 연결 실행은 요약의 요청 번호로 request_kind·request_id만 채우고 해시는 비워 둔다.
  다른 실행은 비워 둔다. 고유 색인 uq_runs_request(user_id, request_kind, request_id)가 같은 사용자의 중복을 막고
  NULL끼리는 겹치지 않는다.
- 채울 수 없는 실행 기록(같은 사용자·같은 번호 둘 이상 / 읽을 수 없는 요약 / 번호 없음)이 있으면 열을 만들기 전에
  멈춘다 —
  트랜잭션 DDL이라 리비전·열·행 모두 그대로.
- 0021 → 0020: 색인·열만 빠지고 행은 그대로. 다시 올려도 같은 채움. head는 모델과 같다.
- 표 잠금을 오래 기다리지 않는다 (lock_timeout 5초 — 0020과 같음).
빈 DB를 만드는 시험이라 MOAJWO_TEST_PG_ADMIN_URL(일회용 시험 서버의 관리자 주소)이 있을 때만 돈다.
"""

import json
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import pg_database
from tests.test_migration_0020_postgres import INSERT_RUN, _diff, _engine, _ok, _revision, _sql
from tests.test_migrations_postgres import _alembic, needs_admin

PREVIOUS, HEAD = "0020_run_worker_lease", "0021_run_request_key"
RID1, RID2 = "a" * 32, "b" * 32
REQUEST_COLUMNS = {"request_kind", "request_id", "request_hash"}


def _request_columns(url: str) -> set[str]:
    rows = _sql(url, "SELECT column_name FROM information_schema.columns WHERE table_name = 'runs' AND "
                     "column_name IN ('request_kind', 'request_id', 'request_hash')")  # fmt: skip
    return {r[0] for r in rows}


def _has_index(url: str) -> bool:
    return bool(_sql(url, "SELECT 1 FROM pg_indexes WHERE tablename = 'runs' AND indexname = 'uq_runs_request'"))


def _keyjoin_summary(rid: str | None) -> str:
    data = {"mode": "keyjoin", "mode_label": "공통 key 연결", "basis": "x" * 64}
    if rid is not None:
        data["request_id"] = rid
    return json.dumps(data, ensure_ascii=False)


def _user(url: str, name: str) -> int:
    return _sql(url, "INSERT INTO users (username, display_name, password_hash, role, is_active, status, "
                     "failed_logins, "
                     "auth_version) VALUES (:n, :n, 'scrypt$synthetic', 'user', true, 'active', 0, 0) RETURNING id",
                n=name)[0][0]  # fmt: skip


def _run(url: str, user_id: int | None, status: str, summary: str) -> int:
    sql = ("INSERT INTO runs (user_id, status, trigger, input_file_count, file_ids, row_count, change_count,"
           " warning_count,"
           " summary) VALUES (:u, :s, 'manual', 0, '[]', 0, 0, 0, :j) RETURNING id")  # fmt: skip
    return _sql(url, sql, u=user_id, s=status, j=summary)[0][0]


def _rows(url: str) -> list[tuple]:
    return _sql(url, "SELECT id, user_id, status, summary FROM runs ORDER BY id")


@needs_admin
def test_0020_0021_0020_0021_backfills_keyjoin_runs_and_keeps_the_rows(tmp_path: Path) -> None:
    url = pg_database("mig_0021_round")
    try:
        assert _ok(_alembic(url, tmp_path, "upgrade", PREVIOUS))
        u1, u2 = _user(url, "mig-u1"), _user(url, "mig-u2")
        a = _run(url, u1, "done", _keyjoin_summary(RID1))
        b = _run(url, u2, "done", _keyjoin_summary(RID1))  # 다른 사용자 — 같은 번호여도 된다
        c = _run(url, u1, "failed", _keyjoin_summary(RID2))
        d = _run(url, u1, "done", json.dumps({"mode": "bundle", "files": ["keyjoin 결과.xlsx"]}))
        e = _run(url, u1, "done", "{}")
        f = _run(url, None, "done", _keyjoin_summary(RID2))  # 사용자가 지워진 행
        before = _rows(url)
        assert _ok(_alembic(url, tmp_path, "upgrade", "head"))
        assert _revision(url) == HEAD and _request_columns(url) == REQUEST_COLUMNS and _has_index(url)
        filled = _sql(url, "SELECT id, request_kind, request_id, request_hash FROM runs ORDER BY id")
        assert filled == [
            (a, "keyjoin", RID1, None),
            (b, "keyjoin", RID1, None),
            (c, "keyjoin", RID2, None),
            (d, None, None, None),
            (e, None, None, None),
            (f, "keyjoin", RID2, None),
        ]
        assert _rows(url) == before and _diff(url) == []
        # 고유 색인: 같은 사용자·같은 종류·같은 번호의 둘째 행은 들어가지 않는다 (실행 행을 넣는 순간)
        eng = _engine(url)
        try:
            with eng.begin() as conn:
                dup = INSERT_RUN.format(
                    extra=", user_id, request_kind, request_id", values=f", {u1}, 'keyjoin', '{RID1}'"
                )
                conn.execute(text(dup), {"status": "running"})
            raise AssertionError("같은 사용자·같은 요청 번호의 둘째 행이 들어갔다")
        except IntegrityError as exc:
            assert "uq_runs_request" in str(exc.orig)
        eng.dispose()
        for _ in range(2):  # 번호가 없는 실행은 몇 개든
            _run(url, u1, "done", "{}")
        after = _rows(url)
        assert _ok(_alembic(url, tmp_path, "downgrade", PREVIOUS))
        assert _revision(url) == PREVIOUS and _request_columns(url) == set() and not _has_index(url)
        assert _rows(url) == after
        assert _ok(_alembic(url, tmp_path, "upgrade", "head"))
        assert _sql(
            url, "SELECT id, request_kind, request_id FROM runs WHERE id IN (:a, :b, :c) ORDER BY id", a=a, b=b, c=c
        ) == [
            (a, "keyjoin", RID1),
            (b, "keyjoin", RID1),
            (c, "keyjoin", RID2),
        ]
    finally:
        pg_database("mig_0021_round", drop=True)


@needs_admin
def test_0021_stops_before_changing_anything_when_a_user_has_the_same_request_id_twice(tmp_path: Path) -> None:
    url = pg_database("mig_0021_dup")
    try:
        assert _ok(_alembic(url, tmp_path, "upgrade", PREVIOUS))
        u1 = _user(url, "mig-dup")
        first = _run(url, u1, "done", _keyjoin_summary(RID1))
        second = _run(url, u1, "done", _keyjoin_summary(RID1))
        _run(url, u1, "done", '{"mode": "keyjoin", "request_id": ')  # 읽을 수 없는 요약도 함께 알린다
        before = _rows(url)
        refused = _alembic(url, tmp_path, "upgrade", "head")
        assert refused.returncode != 0, refused.stdout[-400:]
        err = refused.stderr
        assert "0021로 올리지 않습니다" in err and f"실행 [{first}, {second}]" in err and "읽을 수 없는 실행 1건" in err
        assert "basis" not in err  # 요약 원문은 알리지 않는다
        assert _revision(url) == PREVIOUS and _request_columns(url) == set() and not _has_index(url)
        assert _rows(url) == before
        _sql(url, "DELETE FROM runs WHERE id <> :k", k=first)
        assert _ok(_alembic(url, tmp_path, "upgrade", "head"))
        assert _revision(url) == HEAD
    finally:
        pg_database("mig_0021_dup", drop=True)


@needs_admin
def test_0021_up_and_down_give_up_behind_an_open_transaction_on_runs(tmp_path: Path) -> None:
    """앞 서버의 열린 트랜잭션이 runs를 읽고 있으면 0021도 5초만 기다리고 실패한다 — 리비전·열이 그대로."""
    url = pg_database("mig_0021_lock")
    try:
        assert _ok(_alembic(url, tmp_path, "upgrade", PREVIOUS))
        for step, target, then in (("upgrade", "head", set()), ("downgrade", PREVIOUS, REQUEST_COLUMNS)):
            if step == "downgrade":
                assert _ok(_alembic(url, tmp_path, "upgrade", "head"))
            revision = _revision(url)
            started = time.monotonic()
            assert _ok(_alembic(url, tmp_path, "current"))
            base = time.monotonic() - started
            e = _engine(url)
            with e.connect() as holder:
                holder.execute(text("SELECT count(*) FROM runs"))
                started = time.monotonic()
                blocked = _alembic(url, tmp_path, step, target, MOAJWO_MIGRATION_LOCK_TIMEOUT_S="120")
                took = time.monotonic() - started
                holder.rollback()
            e.dispose()
            assert blocked.returncode != 0 and "LockNotAvailable" in blocked.stderr, (step, blocked.stderr[-500:])
            assert 4 <= took - base < 15, (step, took, base)
            assert _revision(url) == revision and _request_columns(url) == then, step
        assert _ok(_alembic(url, tmp_path, "downgrade", PREVIOUS))
    finally:
        pg_database("mig_0021_lock", drop=True)
