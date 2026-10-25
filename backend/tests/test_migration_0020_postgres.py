"""0020(결과 실행의 담당·생존 신호) 마이그레이션을 PostgreSQL에서 — 롤백 경계가 실제 서버에서 그대로인지
(docs/DEPLOY.md).

- 0019 → 0020 → 0019 → 0020: 계정·실행 기록·변경 기록이 그대로이고 열만 오르내린다. 다시 올린 head는 모델과 같다.
- downgrade 거절: 이 판에서 시작한 실행 중 행(담당 있음)이 있으면 내리지 않고 아무것도 바꾸지 않는다.
- 표 잠금을 오래 기다리지 않는다 (0020의 lock_timeout 5초 — 마이그레이션 잠금 대기를 길게 줘도) — 올리기·내리기 모두,
  실패하면 리비전·열이 그대로다.
- 한 단계가 실패하면 그 마이그레이션 전체가 되돌아간다 (PostgreSQL 트랜잭션 DDL).
- 소유 계정이 올리고 내려도 표 소유자·권한이 그대로이고, 앱 계정이 새 열을 읽고 쓴다.
- PostgreSQL에서 멈춘 실행 정리(recover)·확인(status) 뒤에 내린다.
빈 DB와 시험 계정을 만드는 시험이라 MOAJWO_TEST_PG_ADMIN_URL(일회용 시험 서버의 관리자 주소)이 있을 때만 돈다.
"""

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db import Base
from tests.conftest import PG_ADMIN, pg_database, pg_role_with_password
from tests.test_migrations_postgres import BACKEND, _alembic, _engine, needs_admin

HEAD, PREVIOUS = "0020_run_worker_lease", "0019_analysis_runs"
INSERT_RUN = (
    "INSERT INTO runs (status, trigger, input_file_count, file_ids, row_count, change_count, warning_count, summary"
    "{extra}) VALUES (:status, 'manual', 0, '[]', 0, 0, 0, '{{}}'{values}) RETURNING id"
)


def _ok(done: subprocess.CompletedProcess) -> bool:
    assert done.returncode == 0, done.stderr[-800:]
    return True


def _sql(url: str, sql: str, **params: object) -> list[tuple]:
    e = _engine(url)
    with e.begin() as c:
        result = c.execute(text(sql), params)
        rows = [tuple(r) for r in result] if result.returns_rows else []
    e.dispose()
    return rows


def _revision(url: str) -> str:
    return _sql(url, "SELECT version_num FROM alembic_version")[0][0]


def _lease_columns(url: str) -> set[str]:
    rows = _sql(url, "SELECT column_name FROM information_schema.columns WHERE table_name = 'runs' AND "
                     "column_name IN ('worker', 'heartbeat_at')")  # fmt: skip
    return {r[0] for r in rows}


def _snapshot(url: str) -> dict[str, list[tuple]]:
    """0019·0020에 모두 있는 열만 — 계정·실행 기록·변경 기록·기본 용어사전."""
    return {
        "users": _sql(url, "SELECT id, username, password_hash, role, status FROM users ORDER BY id"),
        "runs": _sql(url, "SELECT id, status, error, summary, CAST(finished_at AS TEXT) FROM runs ORDER BY id"),
        "run_changes": _sql(url, 'SELECT id, run_id, "column", kind, before, after FROM run_changes ORDER BY id'),
        "glossary": _sql(url, "SELECT count(*) FROM glossary_entries"),
    }


def _seed(url: str) -> dict[str, int]:
    """0019 DB에 쌓인 자료: 계정 1, 완료 실행 1(변경 기록 1), 이전 판에서 시작한 실행 중 1(담당 없음)."""
    _sql(url, "INSERT INTO users (username, display_name, password_hash, role, is_active, status, failed_logins, "
              "auth_version) VALUES ('mig-owner', '소유자', 'scrypt$synthetic', 'owner', true, 'active', 0, 0)"
         )  # fmt: skip
    done = _sql(url, INSERT_RUN.format(extra="", values=""), status="done")[0][0]
    _sql(url, 'INSERT INTO run_changes (run_id, file_name, sheet, output_row, "column", source_column, kind, before, '
              "after, reason, confidence) VALUES (:r, 'a.csv', 's', 2, '인구', '인구', 'number', '1,000', '1000', "
              "'합성', 1.0)", r=done)  # fmt: skip
    legacy = _sql(url, INSERT_RUN.format(extra="", values=""), status="running")[0][0]
    return {"done": done, "legacy": legacy}


def _diff(url: str) -> list:
    e = _engine(url)
    with e.connect() as c:
        diff = compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
    e.dispose()
    return diff


@needs_admin
def test_0019_0020_0019_0020_keeps_the_rows_and_refuses_while_a_run_of_0020_is_running(tmp_path: Path) -> None:
    url = pg_database("mig_0020_round")
    try:
        assert _ok(_alembic(url, tmp_path, "upgrade", PREVIOUS))
        _seed(url)
        before = _snapshot(url)
        assert _ok(_alembic(url, tmp_path, "upgrade", "head"))
        assert _revision(url) == HEAD and _lease_columns(url) == {"worker", "heartbeat_at"}
        assert _snapshot(url) == before  # 기존 행은 그대로 (새 열은 비어 있음)
        assert _sql(url, "SELECT count(*) FROM runs WHERE worker IS NOT NULL OR heartbeat_at IS NOT NULL") == [(0,)]
        # 이 판에서 시작해 실행 중인 행(담당 있음): 내리지 않고, 리비전·열·행 모두 그대로
        live = _sql(url, INSERT_RUN.format(extra=", worker, heartbeat_at", values=", 'abcdef012345-live', now()"),
                    status="running")[0][0]  # fmt: skip
        held = _snapshot(url)
        refused = _alembic(url, tmp_path, "downgrade", PREVIOUS)
        assert refused.returncode != 0, refused.stdout[-400:]
        assert "0019로 내리지 않습니다" in refused.stderr and "app.recovery recover" in refused.stderr
        assert _revision(url) == HEAD and _lease_columns(url) == {"worker", "heartbeat_at"} and _snapshot(url) == held
        # 그 실행을 정리(완료)한 뒤에는 내린다 — 열만 빠지고 행은 그대로
        _sql(url, "UPDATE runs SET status = 'done', finished_at = now() WHERE id = :i", i=live)
        settled = _snapshot(url)
        assert _ok(_alembic(url, tmp_path, "downgrade", PREVIOUS))
        assert _revision(url) == PREVIOUS and _lease_columns(url) == set() and _snapshot(url) == settled
        # 다시 올려도 행은 그대로, head는 모델과 같다
        assert _ok(_alembic(url, tmp_path, "upgrade", "head"))
        assert _revision(url) == HEAD and _snapshot(url) == settled and _diff(url) == []
    finally:
        pg_database("mig_0020_round", drop=True)


@needs_admin
def test_0020_up_and_down_give_up_behind_an_open_transaction_on_runs(tmp_path: Path) -> None:
    """앞 서버의 열린 트랜잭션이 runs를 읽고 있으면 0020은 표 잠금을 5초만 기다리고 실패한다 (마이그레이션 잠금 대기를
    120초로 줘도) — 무중단 배포에서 그 서버의 실행 기록 조회까지 줄 세우지 않게. 실패하면 리비전·열이 그대로다."""
    url = pg_database("mig_0020_lock")
    try:
        assert _ok(_alembic(url, tmp_path, "upgrade", PREVIOUS))
        for step, target, then in (("upgrade", "head", set()), ("downgrade", PREVIOUS, {"worker", "heartbeat_at"})):
            if step == "downgrade":
                assert _ok(_alembic(url, tmp_path, "upgrade", "head"))
            revision = _revision(url)
            e = _engine(url)
            with e.connect() as holder:
                holder.execute(text("SELECT count(*) FROM runs"))  # 열린 트랜잭션이 표를 읽는 중 (ACCESS SHARE)
                started = time.monotonic()
                blocked = _alembic(url, tmp_path, step, target, MOAJWO_MIGRATION_LOCK_TIMEOUT_S="120")
                took = time.monotonic() - started
                holder.rollback()
            e.dispose()
            assert blocked.returncode != 0 and "LockNotAvailable" in blocked.stderr, (step, blocked.stderr[-500:])
            assert 5 <= took < 60, (step, took)
            assert _revision(url) == revision and _lease_columns(url) == then, step
        assert _ok(_alembic(url, tmp_path, "downgrade", PREVIOUS))  # 트랜잭션이 끝나면 같은 명령이 된다
    finally:
        pg_database("mig_0020_lock", drop=True)


@needs_admin
def test_a_failing_step_rolls_back_the_whole_0020_change(tmp_path: Path) -> None:
    """올리기: 둘째 열이 이미 있는 DB(손으로 바뀜)면 실패하고 첫 열(worker)도 남지 않는다. 내리기: worker에 기대는
    보기가 있어 둘째 단계가 막히면 먼저 지운 heartbeat_at도 되돌아온다 — 반쯤 바뀐 DB를 남기지 않는다."""
    url = pg_database("mig_0020_atomic")
    try:
        assert _ok(_alembic(url, tmp_path, "upgrade", PREVIOUS))
        _sql(url, "ALTER TABLE runs ADD COLUMN heartbeat_at timestamptz")
        failed = _alembic(url, tmp_path, "upgrade", "head")
        assert failed.returncode != 0 and "DuplicateColumn" in failed.stderr, failed.stderr[-500:]
        assert _revision(url) == PREVIOUS and _lease_columns(url) == {"heartbeat_at"}  # worker는 되돌아감
        _sql(url, "ALTER TABLE runs DROP COLUMN heartbeat_at")
        assert _ok(_alembic(url, tmp_path, "upgrade", "head"))
        _sql(url, "CREATE VIEW run_workers AS SELECT id, worker FROM runs")
        failed = _alembic(url, tmp_path, "downgrade", PREVIOUS)
        assert failed.returncode != 0 and "DependentObjectsStillExist" in failed.stderr, failed.stderr[-500:]
        assert _revision(url) == HEAD and _lease_columns(url) == {"worker", "heartbeat_at"}
    finally:
        pg_database("mig_0020_atomic", drop=True)


@needs_admin
def test_0020_keeps_the_table_owner_and_the_app_role_grants(tmp_path: Path) -> None:
    """소유 계정이 마이그레이션하고 앱 계정은 읽기·쓰기만 (docs/EXTERNAL_DB.md 5장). 0020을 올리고 내려도 runs의
    소유자와 권한(relacl)이 그대로이고, 표 권한으로 앱 계정이 새 열도 읽고 쓴다."""
    name, tag = "mig_0020_roles", secrets.token_hex(4)
    owner, app = f"mig_owner_{tag}", f"mig_app_{tag}"
    passwords = {owner: "Ow-" + secrets.token_urlsafe(12), app: "Ap-" + secrets.token_urlsafe(12)}
    admin_url = pg_database(name)
    admin = make_url(PG_ADMIN).set(drivername="postgresql+psycopg")

    def as_role(role: str) -> str:
        return make_url(admin_url).set(username=role, password=passwords[role]).render_as_string(hide_password=False)

    def as_admin(sql: str, db: str | None = None) -> None:
        e = create_engine(admin.set(database=db) if db else admin, isolation_level="AUTOCOMMIT")
        with e.connect() as c:
            c.execute(text(sql))
        e.dispose()

    try:
        for role, password in passwords.items():
            pg_role_with_password(role, password)
        as_admin(f'ALTER DATABASE "{name}" OWNER TO {owner}')
        assert _ok(_alembic(as_role(owner), tmp_path, "upgrade", PREVIOUS))
        for grant in (f"GRANT USAGE ON SCHEMA public TO {app}",
                      f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app}",
                      f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app}"):  # fmt: skip
            as_admin(grant, name)
        acl = "SELECT pg_get_userbyid(relowner), CAST(relacl AS TEXT) FROM pg_class WHERE relname = 'runs'"
        before = _sql(admin_url, acl)
        assert before[0][0] == owner
        for step, target in (("upgrade", "head"), ("downgrade", PREVIOUS), ("upgrade", "head")):
            assert _ok(_alembic(as_role(owner), tmp_path, step, target))
            assert _sql(admin_url, acl) == before, step
        insert = INSERT_RUN.format(extra=", worker, heartbeat_at", values=", 'abcdef012345-app', now()")
        e = _engine(as_role(app))
        with e.begin() as c:
            rid = c.execute(text(insert), {"status": "running"}).scalar()
            c.execute(
                text("UPDATE runs SET worker = 'abcdef012345-app2', heartbeat_at = now() WHERE id = :i"), {"i": rid}
            )
            assert c.execute(text("SELECT worker FROM runs WHERE id = :i"), {"i": rid}).scalar() == "abcdef012345-app2"
            c.execute(text("DELETE FROM runs WHERE id = :i"), {"i": rid})
        e.dispose()
    finally:
        pg_database(name, drop=True)
        for role in (app, owner):
            as_admin(f"DROP ROLE IF EXISTS {role}")


def _recovery(url: str, data: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MOAJWO_"))}
    env.update({"DATABASE_URL": url, "MOAJWO_DB_MODE": "external", "MOAJWO_DATA_DIR": str(data),
                "PYTHONIOENCODING": "utf-8"})  # fmt: skip
    return subprocess.run([sys.executable, "-m", "app.recovery", *args], cwd=BACKEND, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180)  # fmt: skip


@needs_admin
def test_recover_and_status_on_postgresql_before_the_downgrade(tmp_path: Path) -> None:
    """서버를 멈춘 롤백 전 정리를 PostgreSQL에서: 다른 결과 폴더(다른 디스크)의 실행은 생존 신호가 오래됐을 때만 '결과
    확인 필요'(실패로 바꾸지 않음) — recover 종료 0과 status로 확인한 뒤 내리면 그 행은 'check'로 남는다 (DEPLOY.md)."""
    url = pg_database("mig_0020_recover")
    try:
        assert _ok(_alembic(url, tmp_path, "upgrade", "head"))
        values = ", 'f0f0f0f0f0f0-gone1234', now() - interval '1 day'"  # 이 결과 폴더가 아닌 담당, 하루 전 신호
        rid = _sql(url, INSERT_RUN.format(extra=", worker, heartbeat_at", values=values), status="running")[0][0]
        shown = _recovery(url, tmp_path, "status")
        assert shown.returncode == 0, shown.stderr[-500:]
        assert rid in [r["id"] for r in json.loads(shown.stdout)["running"]]
        settled = _recovery(url, tmp_path, "recover")
        assert settled.returncode == 0, (settled.stdout[-500:], settled.stderr[-500:])
        assert _sql(url, "SELECT status FROM runs WHERE id = :i", i=rid) == [("check",)]
        assert rid in [r["id"] for r in json.loads(_recovery(url, tmp_path, "status").stdout)["check"]]
        assert _ok(_alembic(url, tmp_path, "downgrade", PREVIOUS))  # 담당이 있는 실행 중 행이 없으므로
        assert _revision(url) == PREVIOUS and _sql(url, "SELECT status FROM runs WHERE id = :i", i=rid) == [("check",)]
        for out in (shown.stdout, settled.stdout):
            assert "postgresql://" not in out and (make_url(PG_ADMIN).password or "\0") not in out
    finally:
        pg_database("mig_0020_recover", drop=True)
