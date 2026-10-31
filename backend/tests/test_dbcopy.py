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
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
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
            # Windows에서 심볼릭 링크 권한(개발자 모드·SeCreateSymbolicLinkPrivilege)이 없으면 권한이 필요 없는 디렉터리
            # 정션으로 원천 폴더를 가리켜 같은 파일을 다른 경로로 만든다 — 검사 대상(링크를 거친 같은 파일 거절)은 같다
            if os.name != "nt":
                raise
            import _winapi

            link_dir = tmp_path / "linkdir"
            _winapi.CreateJunction(str(src.parent), str(link_dir))
            report = link_dir / src.name
            assert os.path.samefile(report, src)  # 정션을 거친 경로가 원천과 같은 파일
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
    assert out["too_long_values"] == []
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


def _dbcopy_backends(url: str) -> list[dict[str, Any]]:
    """이 DB에 붙은 이관 도구 백엔드(application_name = dbcopy.TOOL)의 상태와 쥔 표 잠금(granted relation lock) 수."""
    e = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1), isolation_level="AUTOCOMMIT")
    sql = (
        "SELECT a.pid, a.state, a.wait_event_type, (SELECT count(*) FROM pg_locks l WHERE l.pid = a.pid"
        " AND l.locktype = 'relation' AND l.granted) AS held FROM pg_stat_activity a"
        " WHERE a.datname = current_database() AND a.application_name = :app AND a.pid <> pg_backend_pid()"
    )
    with e.connect() as c:
        rows = [dict(r) for r in c.execute(text(sql), {"app": dbcopy.TOOL}).mappings().all()]
    e.dispose()
    return rows


@pg
def test_the_check_stage_holds_no_locks_and_the_lock_stage_rechecks_inside_the_lock(
    pg_env, target, monkeypatch
) -> None:
    """확인 단계(표별 건수까지 센 뒤)에는 트랜잭션도 표 잠금도 들고 있지 않다(자동 커밋 읽기) — 같은 순간 잠금 단계에 든
    다른 이관과 서로 기다리는 교착(SQLSTATE 40P01, 2026-10-26 R12 관찰)의 조건을 없앤다. 잠금문은 alembic_version을
    먼저 잠근다. 확인 뒤·잠금 전에 대상에 행이 생기면(가입 등) 잠금 안 재확인이 거절하고 아무것도 쓰지 않는다 —
    진단 게이트 자리(_gate)를 바꿔 그 순간을 잡는다 (MOAJWO_DBCOPY_GATE 환경 변수는 쓰지 않음)."""
    seen: dict[str, list[dict[str, Any]]] = {}
    statements: list[str] = []
    real_text = dbcopy.text
    writer = create_engine(target.replace("postgresql://", "postgresql+psycopg://", 1), isolation_level="AUTOCOMMIT")

    def capture(sql: str):  # noqa: ANN202
        statements.append(sql)
        return real_text(sql)

    def at(stage: str) -> None:
        seen[stage] = _dbcopy_backends(target)
        if (
            stage == "checked"
        ):  # 확인이 끝난 순간 다른 연결이 가입 행을 넣는다 — 이관이 잠금을 들고 있지 않아 바로 들어간다
            with writer.connect() as c:
                c.execute(text("SET lock_timeout = '2s'"))
                c.execute(text(
                    "INSERT INTO users (username, display_name, password_hash, role, is_active, status, auth_version,"
                    " failed_logins) VALUES ('dc-late', 'l', 'x', 'user', false, 'pending', 0, 0)"
                ))  # fmt: skip

    monkeypatch.setattr(dbcopy, "text", capture)
    monkeypatch.setattr(dbcopy, "_gate", at)
    report: dict[str, Any] = {}
    with pytest.raises(dbcopy.CopyError, match=r"이미 자료가 있습니다 \(users\)"):
        dbcopy.copy(_src(pg_env), target, apply=True, report=report)
    writer.dispose()
    assert report["stage"] == "잠금" and report["db_applied"] == "no"
    checked = seen["checked"]  # 확인 단계 끝: 연결은 트랜잭션 밖(idle)이고 쥔 표 잠금이 없다
    assert checked and all(b["state"] == "idle" and b["held"] == 0 for b in checked), checked
    locked = seen["locked"]  # 잠금 단계: 쓰기 연결 하나가 트랜잭션 안에서 모든 표를 쥐고 있다
    holders = [b for b in locked if b["held"]]
    assert len(holders) == 1 and holders[0]["state"] == "idle in transaction", locked
    assert holders[0]["held"] >= len(list(dbcopy.Base.metadata.sorted_tables)) + 1
    lock = next(s for s in statements if s.startswith("LOCK TABLE"))
    assert lock.startswith('LOCK TABLE "alembic_version", ') and lock.endswith(" IN ACCESS EXCLUSIVE MODE")
    e = create_engine(target.replace("postgresql://", "postgresql+psycopg://", 1))
    with e.connect() as c:
        users = c.execute(text("SELECT username FROM users ORDER BY 1")).scalars().all()
    e.dispose()
    assert users == ["dc-late"] and _state(target)["marker"] == 0  # 가입 행만 남고 이관은 쓰지 않음


@pg
def test_a_lock_wait_timeout_while_reading_the_revision_is_reported_as_such(pg_env, target, capsys) -> None:
    """다른 이관이 표를 잠근 동안 확인 단계가 리비전을 읽다 잠금 대기 한도를 넘으면 그 오류(LockNotAvailable
    55P03)로 멈춘다 — '리비전이 최신이 아닙니다'로 보이지 않는다 (앞 판은 _revision이 모든 오류를 None으로 삼켰다)."""
    holder = create_engine(target.replace("postgresql://", "postgresql+psycopg://", 1))
    hold = holder.connect()
    hold.execute(text('LOCK TABLE "alembic_version" IN ACCESS EXCLUSIVE MODE'))  # 트랜잭션을 열어 둔 채
    try:
        reader = create_engine(
            target.replace("postgresql://", "postgresql+psycopg://", 1), isolation_level="AUTOCOMMIT"
        )
        with reader.connect() as c:
            c.execute(text("SET lock_timeout = '1s'"))
            with pytest.raises(OperationalError) as exc:
                dbcopy._revision(c)
            assert exc.value.orig.sqlstate == "55P03"
        reader.dispose()
        code, out = _run(capsys, "--source", _src(pg_env), "--target", target, "--lock-timeout-s", "1")
        assert code == 1 and out["stage"] == "확인" and out["db_applied"] == "no"
        assert "LockNotAvailable" in out["error"] and "SQLSTATE 55P03" in out["error"]
        assert "최신" not in out["error"]
    finally:
        hold.rollback()
        hold.close()
        holder.dispose()


def test_the_diagnostic_gate_does_nothing_without_its_variable_and_waits_for_the_go_file(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("MOAJWO_DBCOPY_GATE", raising=False)
    t0 = time.monotonic()
    dbcopy._gate("checked")
    assert time.monotonic() - t0 < 0.5 and not list(tmp_path.iterdir())
    monkeypatch.setenv("MOAJWO_DBCOPY_GATE", str(tmp_path))
    (tmp_path / "go-checked").write_text("x", encoding="utf-8")
    dbcopy._gate("checked")  # go 파일이 이미 있으면 도착 표시만 남기고 지나간다
    assert (tmp_path / f"arrived-checked-{os.getpid()}").exists()
    threading.Timer(0.3, lambda: (tmp_path / "go-locked").write_text("x", encoding="utf-8")).start()
    t0 = time.monotonic()
    dbcopy._gate("locked")  # go 파일이 생길 때까지 기다린다
    assert 0.25 <= time.monotonic() - t0 < 5


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


def _with_long_values(path: Path) -> dict[str, int]:
    """SQLite(길이를 강제하지 않음)에 대상 열 길이를 넘는 값을 넣는다 — 경계 255자(한글 765바이트)는 들어갈 값."""
    eng = create_engine(f"sqlite:///{path.as_posix()}")
    with Session(eng) as db:
        fid = db.query(SourceFile.id).order_by(SourceFile.id).first()[0]
        rows = {"edge": "세" + "가" * 254, "over": "예" + "가" * 255, "spaces": "끝" + "가" * 254 + " "}
        ids = {}
        for name, column in rows.items():
            db.execute(
                text("INSERT INTO mapping_decisions (source_file_id, sheet_name, column_name, field_key) "
                     "VALUES (:f, '현황', :c, 'note')"),
                {"f": fid, "c": column},
            )  # fmt: skip
            ids[name] = db.execute(text("SELECT max(id) FROM mapping_decisions")).scalar()
        db.add(AppSetting(key="k" * 65, value="{}", updated_at=datetime.now(UTC)))  # 문자열 기본 키 (원문) 초과
        db.commit()
    eng.dispose()
    return ids


def test_values_longer_than_the_target_columns_are_found_without_reading_them(tmp_path) -> None:
    """DB 없이: 대상 열 길이(varchar n)를 글자 수로 넘는 원천 값만 찾는다 — 255자 한글은 통과, 256자·끝 공백으로 넘는
    값은 보고. 문자열 기본 키 표는 키가 원문이라 행 번호 없이 건수만. 보고에 값은 없다."""
    source = tmp_path / "long.db"
    _migrate(f"sqlite:///{source.as_posix()}")
    _fill_source(source)
    ids = _with_long_values(source)
    eng = create_engine(f"sqlite:///{source.as_posix()}")
    with eng.connect() as c:
        found = dbcopy._too_long_values(c)
    eng.dispose()
    by = {(v["table"], v["column"]): v for v in found}
    assert set(by) == {("mapping_decisions", "column_name"), ("app_settings", "key")}, found
    decisions = by[("mapping_decisions", "column_name")]
    assert (decisions["limit"], decisions["count"], decisions["longest"]) == (255, 2, 256)
    assert decisions["ids"] == sorted([ids["over"], ids["spaces"]])  # 경계 255자 행은 없음
    assert by[("app_settings", "key")] == {"table": "app_settings", "column": "key", "limit": 64, "count": 1,
                                           "longest": 65}  # fmt: skip
    assert "가" * 20 not in json.dumps(found, ensure_ascii=False) and "k" * 65 not in json.dumps(found)


@pg
def test_values_longer_than_the_target_columns_stop_the_copy(pg_env, target, tmp_path, capsys) -> None:
    """원천 SQLite에 대상 열 길이를 넘는 값이 있으면 확인만·적용 모두 '확인' 단계에서 멈추고(종료 1, 적용 안 함) 대상은
    그대로 — 지금(이 검사 전)은 확인만은 통과하고 적용 때 복사 중 22001로 되돌렸고, 보고에 표·열이 남지 않았다."""
    long = tmp_path / "long.db"
    shutil.copy(pg_env["source"], long)
    ids = _with_long_values(long)
    before = _state(target)
    for args in ((), ("--apply",)):
        code, out = _run(capsys, "--source", f"sqlite:///{long.as_posix()}", "--target", target, *args)
        assert code == dbcopy.EXIT_NOT_APPLIED and out["stage"] == "확인" and out["db_applied"] == "no", out
        assert (
            "mapping_decisions.column_name(최대 255자) 2건" in out["error"]
            and "app_settings.key(최대 64자) 1건" in out["error"]
        )
        decisions = next(v for v in out["too_long_values"] if v["column"] == "column_name")
        assert decisions["ids"] == sorted([ids["over"], ids["spaces"]]) and decisions["longest"] == 256
        assert "가" * 20 not in json.dumps(out, ensure_ascii=False)
    assert _state(target) == before


def _with_nul_values(path: Path) -> dict[str, int]:
    """SQLite(NUL도 그대로 저장)에 NUL이 든 값을 넣는다 — varchar 열(짧은 값, NUL 뒤가 긴 값 — ASCII·한글, BLOB으로
    저장된 값), Text 열, 문자열 기본 키 표. 함께 넣는 정상 값(한글 255자·끝 공백·탭·줄바꿈·NUL 없는 BLOB)은 어느
    검사에도 걸리지 않아야 한다."""
    eng = create_engine(f"sqlite:///{path.as_posix()}")
    with Session(eng) as db:
        fid = db.query(SourceFile.id).order_by(SourceFile.id).first()[0]
        rows = {"short": "인구\x00수", "hidden": "x\x00" + "a" * 300, "after": "가" * 100 + "\x00" + "나" * 200,
                "blob": b"BLOBNUL\x00x", "edge": "경" + "가" * 254, "spaces": "끝 공백 ", "tabs": "탭\t줄\n바꿈",
                "blob_clean": b"BLOBCLEAN"}  # fmt: skip
        ids = {}
        for name, column in rows.items():
            db.execute(
                text("INSERT INTO mapping_decisions (source_file_id, sheet_name, column_name, field_key) "
                     "VALUES (:f, '현황', :c, 'note')"),
                {"f": fid, "c": column},
            )  # fmt: skip
            ids[name] = db.execute(text("SELECT max(id) FROM mapping_decisions")).scalar()
        ids["run"] = db.execute(text("SELECT min(id) FROM runs")).scalar()
        db.execute(text("UPDATE runs SET error = :e WHERE id = :i"), {"e": "실패\x00사유", "i": ids["run"]})
        db.add(AppSetting(key="nul_setting", value='{"a": "b\x00"}', updated_at=datetime.now(UTC)))
        db.commit()
    eng.dispose()
    return ids


def test_nul_values_are_found_apart_from_the_length_check_without_reading_them(tmp_path) -> None:
    """DB 없이: NUL이 든 원천 글자를 길이 검사와 따로 찾는다 — SQLite length()는 NUL 앞까지만 세서 'x + NUL + a 300개'
    (302바이트)를 1자로 보므로 길이 검사로는 보이지 않는다. BLOB으로 저장된 값과 Text 열도 본다. NUL이 든 값은 글자 수를
    셀 수 없어, 바이트 수가 열 한도를 넘는 행을 length_unverified로 센다 (NUL을 지운 뒤 길이 검사에 걸릴 수 있음).
    보고에는 표·열·한도·건수·정수 행 번호만 (값·바이트 수·문자열 기본 키 원문 없음)."""
    source = tmp_path / "nul.db"
    _migrate(f"sqlite:///{source.as_posix()}")
    _fill_source(source)
    ids = _with_nul_values(source)
    eng = create_engine(f"sqlite:///{source.as_posix()}")
    with eng.connect() as c:
        found = dbcopy._nul_values(c)
        too_long = dbcopy._too_long_values(c)
    eng.dispose()
    assert too_long == []  # NUL 뒤가 긴 값(302·901바이트)도 length()로는 1·100자 — 길이 검사만으로는 통과했을 값
    by = {(v["table"], v["column"]): v for v in found}
    assert set(by) == {("mapping_decisions", "column_name"), ("runs", "error"), ("app_settings", "value")}, found
    decisions = by[("mapping_decisions", "column_name")]
    assert decisions == {"table": "mapping_decisions", "column": "column_name", "count": 4, "limit": 255,
                         "length_unverified": 2,  # x + NUL + a 300개(302바이트)·한글 300자 + NUL(901바이트)
                         "ids": sorted(ids[k] for k in ("short", "hidden", "after", "blob"))}  # fmt: skip
    assert by[("runs", "error")] == {"table": "runs", "column": "error", "count": 1, "ids": [ids["run"]]}  # Text 열
    assert by[("app_settings", "value")] == {"table": "app_settings", "column": "value", "count": 1}  # 문자열 키 표
    shown = json.dumps(found, ensure_ascii=False)
    for raw in ("a" * 20, "가" * 20, "나" * 20, "nul_setting", "인구", "사유", "BLOB"):
        assert raw not in shown, raw


def _sqlite_target(monkeypatch: pytest.MonkeyPatch, sent: list[str]) -> None:
    """PostgreSQL 없이 명령줄 경로(main → copy → _copy)를 돌린다: 대상만 마이그레이션한 빈 SQLite로 바꾸고(접속 인자만
    뺌) 대상에 보낸 SQL을 모은다. '확인' 단계의 거절은 대상 리비전 조회 하나 뒤라 PostgreSQL 전용 동작에 닿지 않는다."""
    real = dbcopy.create_engine

    def engine(url: str, **kw: Any):  # noqa: ANN202
        if not str(url).startswith("sqlite:///"):  # 원천 스냅숏은 creator로 여는 'sqlite://'
            return real(url, **kw)
        kw.pop("connect_args", None)
        kw.pop("poolclass", None)
        eng = real(url, **kw)
        event.listen(eng, "before_cursor_execute", lambda _c, _cur, statement, *_: sent.append(statement))
        return eng

    monkeypatch.setattr(dbcopy, "_database", lambda raw, mode: ("external", raw))
    monkeypatch.setattr(dbcopy, "create_engine", engine)


def test_nul_values_stop_the_command_at_the_check_stage_without_postgresql(tmp_path, monkeypatch, capsys) -> None:
    """PostgreSQL 없이(대상만 빈 SQLite): NUL이 든 원천은 확인만·적용 모두 '확인' 단계에서 멈춘다 (종료 1, 적용 안 함).
    원천·대상 파일이 그대로이고, 대상에는 리비전 조회 하나만 갔으며, 출력에 값·키 원문이 없다."""
    source, target = tmp_path / "nul.db", tmp_path / "target.db"
    for p in (source, target):
        _migrate(f"sqlite:///{p.as_posix()}")
    _fill_source(source)
    _with_nul_values(source)
    before = {"source": _digest_file(source), "target": _digest_file(target)}
    for args in ((), ("--apply",)):
        sent: list[str] = []
        _sqlite_target(monkeypatch, sent)
        code, out = _run(capsys, "--source", f"sqlite:///{source.as_posix()}", "--target",
                         f"sqlite:///{target.as_posix()}", *args)  # fmt: skip
        assert code == dbcopy.EXIT_NOT_APPLIED and out["stage"] == "확인" and out["db_applied"] == "no", out
        assert out["source"]["unchanged"] and out["source_encoding"] == "UTF-8", out
        error = out["error"]
        assert "mapping_decisions.column_name 4건" in error and "(최대 255자) 2건은" in error, error
        assert sent == ["SELECT version_num FROM alembic_version"], sent
        shown = json.dumps(out, ensure_ascii=False)
        for raw in ("a" * 20, "가" * 20, "nul_setting", "인구", "사유"):
            assert raw not in shown, raw
    assert {"source": _digest_file(source), "target": _digest_file(target)} == before


def test_a_source_that_is_not_utf8_stops_for_that_reason_not_as_nul(tmp_path, monkeypatch, capsys) -> None:
    """UTF-16 원천에서는 바이트로 찾는 NUL 검사에 NUL이 없는 값('abc'·'가'·숫자)도 걸린다 — 인코딩을 이유로 '확인'
    단계에서 먼저 멈추고 NUL로 보고하지 않는다 (앱·alembic이 만든 DB는 UTF-8)."""
    source, target = tmp_path / "utf16.db", tmp_path / "target.db"
    con = sqlite3.connect(source)
    con.execute("PRAGMA encoding = 'UTF-16le'")  # 첫 표를 만들 때 정해지고 바뀌지 않는다
    con.execute("CREATE TABLE enc (a)")
    con.execute("DROP TABLE enc")
    con.close()
    for p in (source, target):
        _migrate(f"sqlite:///{p.as_posix()}")
    _fill_source(source)
    sent: list[str] = []
    _sqlite_target(monkeypatch, sent)
    code, out = _run(capsys, "--source", f"sqlite:///{source.as_posix()}", "--target", f"sqlite:///{target.as_posix()}")
    assert code == dbcopy.EXIT_NOT_APPLIED and out["stage"] == "확인" and out["source_encoding"] == "UTF-16le", out
    assert "글자 인코딩이 UTF-16le" in out["error"] and "NUL" not in out["error"].split("—")[0], out["error"]
    assert "nul_values" not in out and "too_long_values" not in out and out["source"]["unchanged"]


@pg
def test_nul_values_stop_the_copy_before_writing(pg_env, target, tmp_path, capsys) -> None:
    """원천에 NUL이 든 글자가 있으면 확인만·적용 모두 '확인' 단계에서 멈추고(종료 1, 적용 안 함) 대상 표·시퀀스는 그대로
    — 지금(이 검사 전)은 길이 검사·확인만을 통과하고 적용 때 복사 중 드라이버 DataError로 되돌렸다."""
    source = tmp_path / "nul.db"
    shutil.copy(pg_env["source"], source)
    ids = _with_nul_values(source)
    before, source_hash = _state(target), _digest_file(source)
    for args in ((), ("--apply",)):
        code, out = _run(capsys, "--source", f"sqlite:///{source.as_posix()}", "--target", target, *args)
        assert code == dbcopy.EXIT_NOT_APPLIED and out["stage"] == "확인" and out["db_applied"] == "no", out
        assert "NUL(0x00) 문자가 든 값" in out["error"] and "mapping_decisions.column_name 4건" in out["error"]
        assert "runs.error 1건" in out["error"] and "app_settings.value 1건" in out["error"]
        assert "mapping_decisions.column_name(최대 255자) 2건은" in out["error"], out["error"]
        decisions = next(v for v in out["nul_values"] if v["column"] == "column_name")
        assert decisions["ids"] == sorted(ids[k] for k in ("short", "hidden", "after", "blob"))
        assert decisions["length_unverified"] == 2 and out["too_long_values"] == []
        assert "a" * 20 not in json.dumps(out, ensure_ascii=False) and "nul_setting" not in json.dumps(out)
        assert out["source"]["unchanged"], out["source"]
    assert _state(target) == before and _digest_file(source) == source_hash


@pg
def test_length_nul_and_time_zone_problems_are_reported_together(pg_env, target, tmp_path, capsys) -> None:
    """길이 초과·NUL·시간대 출처 확인 후보가 함께 있으면 셋 다 보고에 채운 뒤 한 번에 멈춘다 (하나를 고친 뒤 다른
    것으로 다시 멈추지 않게). 대상은 그대로."""
    source = tmp_path / "mixed.db"
    shutil.copy(pg_env["source"], source)
    _with_long_values(source)
    _with_nul_values(source)
    con = sqlite3.connect(source)
    with con:
        con.execute("UPDATE runs SET started_at = created_at, finished_at = created_at")
    con.close()
    before, source_hash = _state(target), _digest_file(source)
    for args in ((), ("--apply",)):
        code, out = _run(capsys, "--source", f"sqlite:///{source.as_posix()}", "--target", target, *args)
        assert code == dbcopy.EXIT_NOT_APPLIED and out["stage"] == "확인", out
        assert out["too_long_values"] and out["nul_values"] and out["tz_unverified_runs"] == 1, out
        assert out["error"].count(" / ") == 2  # 세 가지 문제를 한 번에
        for part in ("대상 열 길이를 넘는 값", "NUL(0x00)", "NUL을 지운 뒤에도 길이를 넘을 수", "시간대 출처"):
            assert part in out["error"], out["error"]
        assert out["source"]["unchanged"], out["source"]
    assert _state(target) == before and _digest_file(source) == source_hash
