"""이관 도구(app/dbcopy.py) — 원천 보호, 보고 파일, 실패 시 대상 그대로, 커밋 불확실성, 동시 이관, 대상 주소 입력.

- 보고 파일·원천 보호 시험은 DB 없이 돈다 (경로 검사는 DB에 닿기 전). 원천은 시험마다 만드는 합성 SQLite 파일이다.
- 대상 주소: --target-env(환경 변수), 서비스 주소(서비스 파일·비밀번호 파일), 비밀번호가 든 --target(호환 — 경고).
- PostgreSQL 시험은 MOAJWO_TEST_PG_ADMIN_URL(일회용 시험 서버의 관리자 주소 — 운영·시연 DB 아님)이 있을 때만 돈다.
  시험마다 빈 DB를 템플릿에서 새로 만들고 지운다. 실패 지점마다 표 내용·시퀀스·다음 INSERT 번호를 실패 전후로 비교한다.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import dbcopy
from app.auth import hash_password
from app.models import AppSetting, AuthSession, Automation, IntegrationRun, LearnedAlias, RunChange, SourceFile, User
from app.timefmt import KST

BACKEND = Path(__file__).resolve().parent.parent
UNREACHABLE = "postgresql://u:pw-secret-123@127.0.0.1:9/x"  # 거절되는 로컬 포트 (외부로 요청하지 않음)
TARGET_ENV = "MOAJWO_DBCOPY_TEST_TARGET"  # --target-env 시험용 환경 변수 이름
ADMIN = os.environ.get("MOAJWO_TEST_PG_ADMIN_URL", "").strip()


def _small_sqlite(path: Path) -> None:
    con = sqlite3.connect(path)
    with con:
        con.execute("create table t (id integer primary key, v text)")
        con.execute("insert into t (v) values ('원천')")
    con.close()


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    code = dbcopy.main(list(args))
    return code, json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------- 원천 보호 · 보고 파일 (DB 없이)


VARIANTS = ["same", "relative", "dotdot", "journal", "wal", "shm", "case", "hardlink", "symlink"]


@pytest.mark.parametrize("apply", [False, True], ids=["dry-run", "apply"])
@pytest.mark.parametrize("variant", VARIANTS)
def test_report_path_that_is_the_source_is_refused_before_touching_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], apply: bool, variant: str
) -> None:
    """원천과 같은 파일을 가리키는 보고 경로는 DB를 열기 전에 거절하고, 원천 바이트는 그대로다 (예전에는 비워졌다)."""
    src = tmp_path / "data" / "moajwo.db"
    src.parent.mkdir()
    _small_sqlite(src)
    before = src.read_bytes()
    monkeypatch.chdir(tmp_path)
    if variant == "case" and os.name != "nt":
        pytest.skip("대소문자를 구분하지 않는 파일 시스템(Windows)에서만 같은 파일")
    if variant == "hardlink":
        report = tmp_path / "hard.db"
        os.link(src, report)
    elif variant == "symlink":
        report = tmp_path / "sym.db"
        try:
            os.symlink(src, report)
        except OSError:
            pytest.skip("이 환경에서 심볼릭 링크를 만들 권한이 없음")
    else:
        report = {
            "same": src,
            "relative": Path("data/moajwo.db"),
            "dotdot": Path("data/../data/moajwo.db"),
            "journal": Path(f"{src}-journal"),
            "wal": Path(f"{src}-wal"),
            "shm": Path(f"{src}-shm"),
            "case": Path(str(src).upper()),
        }[variant]
    args = ["--source", f"sqlite:///{src.as_posix()}", "--target", UNREACHABLE, "--report", str(report)]
    code, out = _run(capsys, *args, *(["--apply"] if apply else []))
    assert code == dbcopy.EXIT_NOT_APPLIED and out["db_applied"] == "no"
    assert "원천 DB" in out["error"] and "이관하지 않았습니다" in out["error"]
    assert src.read_bytes() == before
    assert not list(tmp_path.rglob("*.tmp"))  # 임시 보고 파일도 만들지 않음


def test_relative_source_and_absolute_report_to_it_are_the_same_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "moajwo.db"
    _small_sqlite(src)
    before = src.read_bytes()
    monkeypatch.chdir(tmp_path)
    args = ["--source", "sqlite:///moajwo.db", "--target", UNREACHABLE, "--report", str(src), "--apply"]
    code, out = _run(capsys, *args)
    assert code == 1 and "원천 DB" in out["error"] and src.read_bytes() == before


def test_existing_file_that_is_not_a_report_is_not_overwritten(tmp_path: Path, capsys) -> None:
    """백업 자료 등 이 도구의 보고서가 아닌 파일은 출력 대상으로 쓰지 않는다."""
    src = tmp_path / "moajwo.db"
    _small_sqlite(src)
    backup = tmp_path / "moajwo-data-20261023.tgz"
    backup.write_bytes(b"\x1f\x8b backup bytes")
    other_db = tmp_path / "other.db"
    _small_sqlite(other_db)
    for target in (backup, other_db, tmp_path):  # 압축 백업 · 다른 SQLite · 폴더
        before = target.read_bytes() if target.is_file() else None
        args = ["--source", f"sqlite:///{src.as_posix()}", "--target", UNREACHABLE, "--report", str(target)]
        code, out = _run(capsys, *args)
        assert code == 1 and "덮어쓰지 않습니다" in out["error"], out
        if before is not None:
            assert target.read_bytes() == before


def _fake_copy(applied: str, calls: list[str] | None = None):
    def run(source: str, target: str, apply: bool = False, *, report: dict[str, Any], **_: Any) -> dict[str, Any]:
        if calls is not None:
            calls.append(target)
        report.update({"tool": dbcopy.TOOL, "db_applied": applied, "result": "시험용"})
        return report

    return run


def test_unwritable_report_location_stops_before_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "moajwo.db"
    _small_sqlite(src)
    calls: list[str] = []
    monkeypatch.setattr(dbcopy, "copy", _fake_copy("yes", calls))
    args = ["--source", f"sqlite:///{src.as_posix()}", "--target", UNREACHABLE, "--apply"]
    # 없는 폴더
    code, out = _run(capsys, *args, "--report", str(tmp_path / "없는 폴더" / "report.json"))
    assert code == 1 and "보고 파일을 쓸 수 없습니다" in out["error"] and "이관하지 않았습니다" in out["error"]

    def denied(*_: Any, **__: Any) -> Any:
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(tempfile, "mkstemp", denied)  # 권한 오류 (플랫폼과 무관하게 같은 경로를 확인)
    code, out = _run(capsys, *args, "--report", str(tmp_path / "report.json"))
    assert code == 1 and "Permission denied" in out["error"] and "이관하지 않았습니다" in out["error"]
    assert calls == []  # 두 경우 모두 이관(copy)을 시작하지 않음


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="POSIX 일반 사용자에서만")
def test_read_only_report_folder_stops_before_the_database(tmp_path: Path, monkeypatch, capsys) -> None:
    src = tmp_path / "moajwo.db"
    _small_sqlite(src)
    calls: list[str] = []
    monkeypatch.setattr(dbcopy, "copy", _fake_copy("yes", calls))
    folder = tmp_path / "ro"
    folder.mkdir()
    folder.chmod(0o500)
    try:
        code, out = _run(capsys, "--source", f"sqlite:///{src.as_posix()}", "--target", UNREACHABLE, "--apply",
                         "--report", str(folder / "report.json"))  # fmt: skip
    finally:
        folder.chmod(0o700)
    assert code == 1 and "이관하지 않았습니다" in out["error"] and calls == []


@pytest.mark.parametrize("applied", ["yes", "no"])
def test_report_write_failure_after_the_run_tells_whether_the_database_was_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], applied: str
) -> None:
    """디스크 부족 등으로 보고만 못 쓰면: 적용했으면 종료 코드 3과 '다시 실행하지 마세요', 확인만이면 1 —
    둘을 섞지 않는다.
    이전 보고서는 반쯤 지워지지 않고 그대로 남는다."""
    src = tmp_path / "moajwo.db"
    _small_sqlite(src)
    old = tmp_path / "report.json"
    old.write_text(json.dumps({"tool": dbcopy.TOOL, "result": "이전 실행"}), encoding="utf-8")
    old_bytes = old.read_bytes()
    monkeypatch.setattr(dbcopy, "copy", _fake_copy(applied))

    def disk_full(self: dbcopy.ReportFile, body: str) -> None:
        self.tmp.write_text(body[:10], encoding="utf-8")  # 쓰다가 멈춤
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(dbcopy.ReportFile, "save", disk_full)
    code, out = _run(capsys, "--source", f"sqlite:///{src.as_posix()}", "--target", UNREACHABLE, "--apply",
                     "--report", str(old))  # fmt: skip
    assert out["report_saved"] is False and "No space left" in out["report_error"]
    if applied == "yes":
        assert code == dbcopy.EXIT_REPORT_FAILED and "다시 실행하지 마세요" in out["note"]
    else:
        assert code == dbcopy.EXIT_NOT_APPLIED and "note" not in out
    assert old.read_bytes() == old_bytes  # 이전 보고서 그대로
    assert not list(tmp_path.glob("*.tmp"))  # 쓰다 만 임시 파일을 남기지 않음


def test_replace_failure_keeps_the_previous_report(tmp_path: Path, monkeypatch, capsys) -> None:
    src = tmp_path / "moajwo.db"
    _small_sqlite(src)
    old = tmp_path / "report.json"
    old.write_text(json.dumps({"tool": dbcopy.TOOL, "result": "이전 실행"}), encoding="utf-8")
    old_bytes = old.read_bytes()
    monkeypatch.setattr(dbcopy, "copy", _fake_copy("yes"))

    def locked(*_: Any) -> None:
        raise PermissionError(errno.EACCES, "Permission denied")  # Windows에서 다른 프로그램이 열어 둔 파일 등

    monkeypatch.setattr(dbcopy.os, "replace", locked)
    code, out = _run(capsys, "--source", f"sqlite:///{src.as_posix()}", "--target", UNREACHABLE, "--apply",
                     "--report", str(old))  # fmt: skip
    assert code == dbcopy.EXIT_REPORT_FAILED and old.read_bytes() == old_bytes
    assert not list(tmp_path.glob("*.tmp"))


def test_errors_name_the_kind_not_the_values(tmp_path: Path, capsys) -> None:
    """DB 오류는 종류·SQLSTATE·제약 이름만 — SQL·매개변수(비밀번호 해시·이름)·주소·비밀번호를 내보내지 않는다."""
    exc = IntegrityError(
        "INSERT INTO users (username, password_hash) VALUES (%(u)s, %(p)s)",
        {"u": "kim-secret-name", "p": "scrypt$hash-secret"},
        Exception("duplicate key value violates unique constraint; Key (username)=(kim-secret-name)"),
    )
    msg = dbcopy.safe_error(exc)
    assert msg.startswith("IntegrityError")
    for secret in ("kim-secret-name", "hash-secret", "INSERT", "password_hash"):
        assert secret not in msg
    src = tmp_path / "moajwo.db"
    _small_sqlite(src)
    code, out = _run(capsys, "--source", f"sqlite:///{src.as_posix()}", "--target", UNREACHABLE)
    raw = json.dumps(out, ensure_ascii=False)
    assert code == 1 and "pw-secret-123" not in raw and "127.0.0.1" not in raw


# ---------------------------------------------------------------- 대상 주소 입력 (DB 없이 — 닿지 않는 로컬 포트)


@pytest.mark.parametrize(
    ("target", "secret"),
    [
        (UNREACHABLE, True),
        ("postgresql://u@127.0.0.1:9/x?sslmode=require&password=pw-secret-123", True),
        ("postgresql:///?service=moajwo-migrate", False),
        ("postgresql://u@127.0.0.1:9/x?sslmode=require", False),
        ("postgresql://u:pw-secret-123@127.0.0.1:9,127.0.0.1:10/x", True),  # libpq 다중 호스트 — SQLAlchemy 해석 실패
        ("postgresql://u:pw-secret-123@127.0.0.1:abc/x", True),  # 잘못된 포트 — 해석 실패
        ("postgresql://u@127.0.0.1:9/x?sslkey=k.pem&sslpassword=pw-secret-123", True),  # 클라이언트 키 암호
        ("postgresql://u@127.0.0.1:9/x?oauth_client_secret=pw-secret-123", True),
    ],
    ids=["userinfo", "query", "service", "no-password", "multi-host", "bad-port", "sslpassword", "oauth-secret"],
)
def test_password_in_the_target_argument_warns_and_is_recorded_only_as_a_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], target: str, secret: bool
) -> None:
    """비밀번호가 든 --target은 DB에 닿기 전에 stderr로 경고하고, 보고에는 참·거짓만 남긴다 (주소·비밀번호 없음).
    원천이 없는 경로라 DB에는 닿지 않는다."""
    code = dbcopy.main(["--source", f"sqlite:///{(tmp_path / 'none.db').as_posix()}", "--target", target])
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert code == dbcopy.EXIT_NOT_APPLIED and out["db_applied"] == "no"
    assert out["target_input"] == "--target" and out["target_secret_in_args"] is secret
    assert ("비밀번호가 든 주소가 있습니다" in captured.err) is secret
    assert "pw-secret-123" not in captured.out + captured.err and "127.0.0.1" not in captured.out + captured.err


def test_password_in_the_target_argument_still_works_for_compatibility(tmp_path: Path, monkeypatch, capsys) -> None:
    """경고하고도 그대로 접속을 시도한다 (호환) — 여기서는 닿지 않는 포트라 연결 실패로 끝난다."""
    src = tmp_path / "moajwo.db"
    _small_sqlite(src)
    monkeypatch.setattr(dbcopy, "DB_CONNECT_TIMEOUT_S", 2)  # 닿지 않는 포트에서 오래 기다리지 않게 (시험 시간)
    code = dbcopy.main(["--source", f"sqlite:///{src.as_posix()}", "--target", UNREACHABLE])
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert code == dbcopy.EXIT_NOT_APPLIED and "OperationalError" in out["error"]  # 대상에 접속을 시도함
    assert out["target_secret_in_args"] is True and "비밀번호가 든 주소가 있습니다" in captured.err
    assert "pw-secret-123" not in captured.out + captured.err


def test_target_env_reads_the_address_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--target-env: 주소(비밀번호 포함)는 환경 변수로만 받는다 — 경고 없음, 보고·출력에 주소·비밀번호 없음.
    변수가 비어 있으면 DB에 닿기 전에 멈춘다."""
    src = tmp_path / "moajwo.db"
    _small_sqlite(src)
    monkeypatch.setattr(dbcopy, "DB_CONNECT_TIMEOUT_S", 2)  # 닿지 않는 포트에서 오래 기다리지 않게 (시험 시간)
    monkeypatch.delenv(TARGET_ENV, raising=False)
    args = ["--source", f"sqlite:///{src.as_posix()}", "--target-env", TARGET_ENV]
    code, out = _run(capsys, *args)
    assert code == dbcopy.EXIT_NOT_APPLIED and out["db_applied"] == "no"
    assert f"환경 변수 {TARGET_ENV}에 대상 주소가 없습니다" in out["error"] and "stage" not in out  # 이관 시작 전
    monkeypatch.setenv(TARGET_ENV, UNREACHABLE)
    code = dbcopy.main(args)
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert code == dbcopy.EXIT_NOT_APPLIED and "OperationalError" in out["error"]  # 환경 변수의 주소로 접속을 시도함
    assert out["target_input"] == "--target-env" and out["target_secret_in_args"] is False
    assert "경고" not in captured.err
    assert "pw-secret-123" not in captured.out + captured.err and "127.0.0.1" not in captured.out + captured.err


def test_an_address_given_as_the_env_name_is_refused_without_repeating_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--target-env에 이름 대신 주소를 넣는 실수(예: --target-env "$MIGRATION_URL") — 출력·보고 파일에 받은 값을
    되풀이하지 않고, 명령 인자에 비밀번호가 있었음을 경고·표시한 뒤 DB에 닿기 전에 멈춘다."""
    src = tmp_path / "moajwo.db"
    _small_sqlite(src)
    report = tmp_path / "report.json"
    args = ["--source", f"sqlite:///{src.as_posix()}", "--target-env", UNREACHABLE, "--report", str(report)]
    code = dbcopy.main(args)
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    saved = report.read_text(encoding="utf-8")
    assert code == dbcopy.EXIT_NOT_APPLIED and out["db_applied"] == "no" and "stage" not in out  # 이관 시작 전
    assert "환경 변수 이름" in out["error"]
    assert out["target_input"] == "--target-env" and out["target_secret_in_args"] is True
    assert "비밀번호" in captured.err
    for text_ in (captured.out, captured.err, saved):
        assert "pw-secret-123" not in text_ and "127.0.0.1" not in text_


def test_target_connection_failure_is_reported_at_the_target_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """대상에 접속하지 못하면 '대상 접속' 단계로 적는다 (원천 문제처럼 보이지 않게)."""
    src = tmp_path / "moajwo.db"
    _small_sqlite(src)
    monkeypatch.setattr(dbcopy, "DB_CONNECT_TIMEOUT_S", 2)
    code, out = _run(capsys, "--source", f"sqlite:///{src.as_posix()}", "--target", "postgresql://u@127.0.0.1:9/x")
    assert code == dbcopy.EXIT_NOT_APPLIED and out["stage"] == "대상 접속" and out["error"].startswith("[대상 접속]")


def test_exactly_one_of_target_and_target_env(tmp_path: Path) -> None:
    src = f"sqlite:///{(tmp_path / 'moajwo.db').as_posix()}"
    for extra in ([], ["--target", UNREACHABLE, "--target-env", TARGET_ENV]):
        with pytest.raises(SystemExit) as exc:
            dbcopy.main(["--source", src, *extra])
        assert exc.value.code == 2  # 사용법 오류 (argparse) — DB에 닿지 않음


# ---------------------------------------------------------------- PostgreSQL (일회용 시험 서버)

pg = pytest.mark.skipif(not ADMIN, reason="MOAJWO_TEST_PG_ADMIN_URL 없음 — PostgreSQL 시험 서버가 있을 때만")


def _db_url(name: str) -> str:
    return make_url(ADMIN).set(database=name).render_as_string(hide_password=False)


def _admin(*sql: str) -> None:
    url = _db_url(make_url(ADMIN).database or "postgres").replace("postgresql://", "postgresql+psycopg://", 1)
    e = create_engine(url, isolation_level="AUTOCOMMIT")
    with e.connect() as c:
        for s in sql:
            c.execute(text(s))
    e.dispose()


def _env(url: str, mode: str = "") -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "MOAJWO_"))}
    data = tempfile.mkdtemp(prefix="dbcopy-mig-")
    env.update({"DATABASE_URL": url, "MOAJWO_DATA_DIR": data, "PYTHONIOENCODING": "utf-8"})
    if mode:
        env["MOAJWO_DB_MODE"] = mode
    return env


def _migrate(url: str, mode: str = "") -> None:
    p = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=_env(url, mode),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")  # fmt: skip
    assert p.returncode == 0, p.stdout[-500:]


def _fill_source(path: Path) -> None:
    """합성 원천: 계정 3(소유자·사용자·승인 대기)·파일·실행(KST 시각)·변경 기록(실수)·자동화·학습 별칭·설정·세션."""
    eng = create_engine(f"sqlite:///{path.as_posix()}")
    now = datetime.now(KST).replace(microsecond=0)
    with Session(eng) as db:
        owner = User(username="dc-owner", display_name="소유자", password_hash=hash_password("dc-pass-owner"),
                     role="owner", is_active=True, status="active")  # fmt: skip
        user = User(username="dc-a", display_name="사용자 A", password_hash=hash_password("dc-pass-a"),
                    role="user", is_active=True, status="active")  # fmt: skip
        pending = User(username="dc-pending", display_name="대기", password_hash=hash_password("dc-pass-p"))
        db.add_all([owner, user, pending])
        db.flush()
        f = SourceFile(original_name="인구.csv", stored_path="uploads/dc-1.csv", sha256="a" * 64, size_bytes=12,
                       uploaded_by=user.id)  # fmt: skip
        db.add(f)
        db.flush()
        files = json.dumps([f.id])
        run = IntegrationRun(status="done", file_ids=files, started_at=now, finished_at=now, user_id=user.id)
        db.add(run)
        db.flush()
        change = {"file_name": "인구.csv", "sheet": "s", "output_row": 2, "column": "인구", "source_column": "인구"}
        db.add(RunChange(run_id=run.id, source_file_id=f.id, kind="number", reason="합성", confidence=0.875, **change))
        db.add(Automation(name="자동", column_rules="{}", entity_rules="{}", validation="{}", output_format="{}",
                          updated_at=now))  # fmt: skip
        alias = {"normalized": "인구수", "alias": "인구수", "field_key": "population"}
        db.add(LearnedAlias(user_id=user.id, source_file_id=f.id, **alias))
        db.add(AppSetting(key="owner_setup", value=json.dumps({"owner": "dc-owner"}), updated_at=datetime.now(UTC)))
        db.add(AuthSession(token_hash="d" * 64, user_id=user.id, created_at=datetime.now(UTC),
                           expires_at=datetime.now(UTC) + timedelta(days=1)))  # fmt: skip
        db.commit()
    eng.dispose()


@pytest.fixture(scope="module")
def pg_env(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if not ADMIN:
        pytest.skip("MOAJWO_TEST_PG_ADMIN_URL 없음")
    base = tmp_path_factory.mktemp("dbcopy")
    source = base / "source.db"
    _migrate(f"sqlite:///{source.as_posix()}")
    _fill_source(source)
    _admin("DROP DATABASE IF EXISTS dbcopy_tmpl WITH (FORCE)", "CREATE DATABASE dbcopy_tmpl")
    _migrate(_db_url("dbcopy_tmpl"), "external")
    yield {"source": source, "base": base}
    _admin("DROP DATABASE IF EXISTS dbcopy_tmpl WITH (FORCE)")


@pytest.fixture
def target(request: pytest.FixtureRequest, pg_env: dict[str, Any]) -> str:
    name = "dbcopy_" + hashlib.sha1(request.node.nodeid.encode()).hexdigest()[:12]
    _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', f'CREATE DATABASE "{name}" TEMPLATE dbcopy_tmpl')
    yield _db_url(name)
    _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _state(url: str) -> dict[str, Any]:
    """대상의 모든 표 내용 해시(기본 자료 표 포함)와 시퀀스 상태."""
    e = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    with e.connect() as c:
        tables = {t: dbcopy._digest(dbcopy._read(c, t), t) for t in dbcopy.Base.metadata.sorted_tables}
        seqs = {}
        for t, col in dbcopy._serial_columns(list(dbcopy.Base.metadata.sorted_tables)):
            seq = c.execute(text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": f'"{t.name}"', "c": col.name}).scalar()
            if seq:
                seqs[t.name] = dbcopy._sequence_state(c, seq)
        marker = c.execute(text("SELECT count(*) FROM app_settings WHERE key = :k"), {"k": dbcopy.MARKER}).scalar()
    e.dispose()
    return {"tables": {t.name: d for t, d in tables.items()}, "sequences": seqs, "marker": marker}


def _next_user_id(url: str) -> int:
    """다음 정상 INSERT가 받는 번호 — 확인한 뒤 되돌린다 (시퀀스 값만 하나 쓴다)."""
    e = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    with e.connect() as c:
        sql = (
            "INSERT INTO users (username, display_name, password_hash, role, is_active, status, auth_version, "
            "failed_logins) VALUES ('dc-probe', 'p', 'x', 'user', false, 'pending', 0, 0) RETURNING id"
        )
        uid = c.execute(text(sql)).scalar()
        c.rollback()
    e.dispose()
    return uid


def _src(pg_env: dict[str, Any]) -> str:
    return f"sqlite:///{pg_env['source'].as_posix()}"


@pg
def test_dry_run_and_apply_keep_the_source_and_copy_everything(pg_env, target, tmp_path, monkeypatch, capsys) -> None:
    source = pg_env["source"]
    src_hash = _digest_file(source)
    before = _state(target)
    report = tmp_path / "dry.json"
    # 확인만: 비밀번호가 든 --target (호환 — 경고하고 그대로 동작)
    code, out = _run(capsys, "--source", _src(pg_env), "--target", target, "--report", str(report))
    assert code == 0 and out["result"].startswith("dry-run") and _state(target) == before
    assert out["target_secret_in_args"] is bool(make_url(target).password) and out["tz_unverified_runs"] == 0
    assert json.loads(report.read_text(encoding="utf-8"))["tool"] == dbcopy.TOOL  # 정상 보고서 저장 유지
    assert out["source"]["unchanged"] and out["snapshot"]["integrity"] == "ok"
    # 적용: 주소는 환경 변수로만 (--target-env)
    monkeypatch.setenv(TARGET_ENV, target)
    code, out = _run(capsys, "--source", _src(pg_env), "--target-env", TARGET_ENV, "--apply", "--report", str(report))
    assert code == 0 and out["db_applied"] == "yes" and out["after_commit"]["verified"], out
    assert out["target_input"] == "--target-env" and out["target_secret_in_args"] is False
    password = make_url(target).password
    if password:  # 시험 서버 주소에 비밀번호가 있으면 출력·보고 어디에도 없어야 한다
        assert password not in json.dumps(out, ensure_ascii=False)
        assert password not in report.read_text(encoding="utf-8")
    assert all(d["source"] == d["target"] for d in out["digests"].values())
    assert out["counts"]["users"]["target_after"] == 3 and "auth_sessions" not in out["counts"]
    assert out["sequences"]["users"]["next"] == 4 and _next_user_id(target) == 4
    assert _digest_file(source) == src_hash and out["source"]["unchanged"]
    saved = json.loads(report.read_text(encoding="utf-8"))
    assert saved["db_applied"] == "yes" and saved["run_id"] == out["run_id"]
    # KST로 기록한 실행 시작 시각이 같은 순간으로 옮겨짐 (9시간 밀림 없음)
    e = create_engine(target.replace("postgresql://", "postgresql+psycopg://", 1))
    with e.connect() as c:
        started = c.execute(text("SELECT started_at FROM runs")).scalar()
        hashed = c.execute(text("SELECT password_hash FROM users WHERE username = 'dc-a'")).scalar()
    e.dispose()
    query = "SELECT started_at, (SELECT password_hash FROM users WHERE username = 'dc-a') FROM runs"
    raw = sqlite3.connect(source).execute(query).fetchone()
    assert started.astimezone(KST).replace(tzinfo=None) == datetime.fromisoformat(raw[0]) and hashed == raw[1]
    # 다시 적용은 거절 (이관 표시) — 대상 그대로
    after = _state(target)
    code, out = _run(capsys, "--source", _src(pg_env), "--target", target, "--apply")
    assert code == 1 and "이관 표시" in out["error"] and "같은 원천 스냅숏: 예" in out["error"]
    assert _state(target) == after


@pg
def test_service_url_target_takes_the_password_from_the_password_file(pg_env, target, tmp_path, service_files,
                                                                      monkeypatch, capsys) -> None:  # fmt: skip
    """--target 'postgresql:///?service=이름': 접속 정보는 서비스 파일, 비밀번호는 비밀번호 파일에서 — 명령 인자에
    비밀번호가 없다 (경고 없음). 비밀번호 파일이 비면 인증에서 실패하고, 없는 서비스 파일을 가리키면 접속하지 않는다."""
    for name in ("PGSERVICE", "PGPASSWORD", "PGHOST", "PGPORT", "PGUSER", "PGDATABASE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in service_files("moajwo-migrate", target).items():
        monkeypatch.setenv(name, value)
    args = ["--source", _src(pg_env), "--target", "postgresql:///?service=moajwo-migrate"]
    before = _state(target)
    code = dbcopy.main(args)
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert code == 0 and out["result"].startswith("dry-run"), out
    assert out["counts"]["users"]["source"] == 3 and out["target_secret_in_args"] is False
    assert "경고" not in captured.err and _state(target) == before
    # 비밀번호를 비밀번호 파일에서 읽었다: 빈 비밀번호 파일로 바꾸면 인증에서 실패한다
    empty = tmp_path / "empty-pgpass.conf"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("PGPASSFILE", str(empty))
    code, out = _run(capsys, *args)
    assert code == dbcopy.EXIT_NOT_APPLIED and "OperationalError" in out["error"]
    # PGSERVICEFILE이 없는 파일을 가리키면 오류 (기본 위치의 파일이 없을 때와 달리 조용히 넘어가지 않음)
    monkeypatch.setenv("PGSERVICEFILE", str(tmp_path / "missing.conf"))
    code, out = _run(capsys, *args)
    assert code == dbcopy.EXIT_NOT_APPLIED and "OperationalError" in out["error"]
    assert _state(target) == before


POINTS = ["table:source_files", "sequence:first", "sequence:all", "compare", "commit_lost_before"]


@pg
@pytest.mark.parametrize("point", POINTS)
def test_failure_before_commit_leaves_tables_and_sequences_as_they_were(pg_env, target, point) -> None:
    before, next_before = _state(target), _next_user_id(target)
    report: dict[str, Any] = {}
    with pytest.raises(dbcopy.CopyError):
        dbcopy.copy(_src(pg_env), target, apply=True, fail_at=point, report=report)
    assert report["db_applied"] == "no", report.get("result")
    if point == "commit_lost_before":
        assert "적용 안 됨" in report["result"] and "새 연결로 확인" in report["result"]
    after = _state(target)
    # 실패 전 _next_user_id가 번호 하나를 썼으므로 시퀀스는 그 상태 그대로여야 한다
    # (setval이었다면 이관한 최대값으로 남는다)
    assert after == {**before, "sequences": {**before["sequences"], "users": (next_before, True)}}
    assert _next_user_id(target) == next_before + 1
    # 같은 대상에 정상 이관을 다시 하면 된다
    ok = dbcopy.copy(_src(pg_env), target, apply=True)
    assert ok["db_applied"] == "yes" and ok["after_commit"]["verified"]


@pg
def test_lost_commit_answer_is_settled_by_reading_the_marker(pg_env, target) -> None:
    report = dbcopy.copy(_src(pg_env), target, apply=True, fail_at="commit_lost_after")
    assert report["db_applied"] == "yes" and "응답은 받지 못했으나" in report["result"], report
    assert report["commit_error"].startswith("OperationalError") and report["after_commit"]["verified"]
    with pytest.raises(dbcopy.CopyError, match="이관 표시"):
        dbcopy.copy(_src(pg_env), target, apply=True)


@pg
def test_two_copies_at_once_apply_once(pg_env, target) -> None:
    gate = threading.Barrier(2)
    results: list[tuple[str, str]] = []

    def run() -> None:
        gate.wait()
        try:
            r = dbcopy.copy(_src(pg_env), target, apply=True)
            results.append(("ok", r["db_applied"]))
        except dbcopy.CopyError as exc:
            results.append(("refused", str(exc)))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(120)
    kinds = sorted(k for k, _ in results)
    assert kinds == ["ok", "refused"], results
    refused = next(m for k, m in results if k == "refused")
    assert "이관 표시" in refused or "이미 자료" in refused
    assert _state(target)["marker"] == 1


@pg
def test_app_writes_wait_while_the_copy_holds_the_tables(pg_env, target, monkeypatch) -> None:
    """복사 중(표 잠금을 쥔 동안) 들어온 가입·최초 설정 같은 쓰기는 섞이지 않고 기다린다
    (여기서는 잠금 대기 제한으로 실패)."""
    copied, release = threading.Event(), threading.Event()
    real = dbcopy._serial_columns

    def pause(tables):  # 표 복사를 마친 뒤, 시퀀스를 맞추기 전에 멈춘다 — 잠금은 쥔 채
        copied.set()
        assert release.wait(60)
        return real(tables)

    monkeypatch.setattr(dbcopy, "_serial_columns", pause)
    out: dict[str, Any] = {}
    th = threading.Thread(target=lambda: out.update(dbcopy.copy(_src(pg_env), target, apply=True)))
    th.start()
    assert copied.wait(60)
    e = create_engine(target.replace("postgresql://", "postgresql+psycopg://", 1))
    errors = []
    for sql in ("INSERT INTO app_settings (key, value) VALUES ('owner_setup', '{}')",
                "SELECT count(*) FROM users"):  # fmt: skip
        with e.connect() as c:
            c.execute(text("SET lock_timeout = '1s'"))
            try:
                c.execute(text(sql))
                errors.append("진행됨")
            except Exception as exc:  # noqa: BLE001
                errors.append(getattr(getattr(exc, "orig", None), "sqlstate", type(exc).__name__))
            c.rollback()
    e.dispose()
    release.set()
    th.join(120)
    assert errors == ["55P03", "55P03"]  # 잠금 대기 제한 초과 — 복사 중인 표를 쓰거나 비어 있는 것으로 읽지 못함
    assert out["db_applied"] == "yes" and out["after_commit"]["verified"]


@pg
def test_runs_whose_time_zone_origin_needs_confirmation_stop_the_copy(pg_env, target, tmp_path) -> None:
    """시작 시각 = 생성 시각인 실행 기록은 시간대 출처 확인이 필요한 후보다 (0009가 생성 시각(UTC)으로 채웠을 수 있음).
    확인만·적용 모두 멈추고 대상은 그대로 — 9시간 보정을 추정해서 하지 않는다."""
    unverified = tmp_path / "tz-unverified.db"
    shutil.copy(pg_env["source"], unverified)
    con = sqlite3.connect(unverified)
    with con:  # 0009가 채웠을 수 있는 모양: 시작·종료 = 생성 시각(UTC)
        con.execute("UPDATE runs SET started_at = created_at, finished_at = created_at")
    con.close()
    before = _state(target)
    for apply in (False, True):
        report: dict[str, Any] = {}
        with pytest.raises(dbcopy.CopyError, match="시간대 출처 확인이 필요한 실행 기록 후보가 1건"):
            dbcopy.copy(f"sqlite:///{unverified.as_posix()}", target, apply=apply, report=report)
        assert report["tz_unverified_runs"] == 1 and report["db_applied"] == "no"
        assert "legacy_0009_runs" not in report
    assert _state(target) == before
