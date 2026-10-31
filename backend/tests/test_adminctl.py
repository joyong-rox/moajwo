"""설치 담당자 명령 `python -m app.adminctl` (init · transition · recover) — 빈 SQLite(마이그레이션 head)에서 실제
프로세스로.

비밀번호는 파일·표준 입력으로만 넘기고(명령 인자 없음), 출력·DB에 평문이 남지 않는지도 본다. 실제 Neon·운영 DB는 쓰지
않는다.
"""

import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.account_markers import GENERATED_TEMP_PASSWORD_NOTE

BACKEND = Path(__file__).resolve().parents[1]
PW = "Install-pass-123!"


def _env(data: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MOAJWO_", "DATABASE_URL"))}
    env.update({"MOAJWO_DATA_DIR": str(data), "PYTHONIOENCODING": "utf-8"})
    return env


def _run(data: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    """표준 입력은 늘 파이프(기본 빈 입력)로 — 대화식(tty)이 아닌 상황을 확실히 한다."""
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "app.adminctl", *args], cwd=BACKEND, env=_env(data), input=stdin,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )  # fmt: skip


def _migrate(data: Path) -> None:
    data.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=_env(data),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr[-800:]


def _q(data: Path, sql: str, *args: Any) -> list[tuple]:
    con = sqlite3.connect(data / "moajwo.db")
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _x(data: Path, sql: str, *args: Any) -> None:
    con = sqlite3.connect(data / "moajwo.db")
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()


def _pwfile(tmp: Path, password: str) -> Path:
    p = tmp / "pw.txt"
    p.write_text(password + "\n", encoding="utf-8")
    if os.name != "nt":
        p.chmod(0o600)
    return p


def _user(data: Path, username: str, role: str, password: str = "legacy-pass-1", status: str = "active") -> int:
    from app.auth import hash_password

    _x(data,
    "insert into users (username, display_name, password_hash, role, is_active, status, auth_version, failed_logins) "
             "values (?, ?, ?, ?, ?, ?, 0, 0)",
             username, username.upper(), hash_password(password), role, status == "active", status)  # fmt: skip
    return _q(data, "select id from users where username = ?", username)[0][0]


def _session(data: Path, uid: int) -> None:
    now = datetime.now(UTC)
    _x(data,
    "insert into auth_sessions (token_hash, user_id, auth_version, created_at, expires_at) values (?, ?, 0, ?, ?)",
       f"{uid:064d}", uid, now.isoformat(), (now + timedelta(hours=1)).isoformat())  # fmt: skip


def _verify(data: Path, username: str, password: str) -> bool:
    from app.auth import verify_password

    return verify_password(password, _q(data, "select password_hash from users where username = ?", username)[0][0])


# ---------------------------------------------------------------- init


def test_init_refuses_without_database_or_schema(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    r = _run(data, "init", "--yes", "--password-file", str(_pwfile(tmp_path, PW)))
    assert r.returncode == 2 and "SQLite 파일이 없습니다" in r.stdout, r.stdout + r.stderr
    assert not (data / "moajwo.db").exists()  # 빈 DB를 새로 만들지 않는다
    sqlite3.connect(data / "moajwo.db").close()  # 파일만 있고 표가 없는 경우
    r = _run(data, "init", "--yes", "--password-file", str(_pwfile(tmp_path, PW)))
    assert r.returncode == 2 and "스키마가 준비되지" in r.stdout, r.stdout
    assert not _q(data, "select name from sqlite_master where name = 'users'")
    assert PW not in r.stdout + r.stderr


def test_init_creates_admin_once_without_printing_the_password(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _migrate(data)
    pwfile = _pwfile(tmp_path, PW)
    r = _run(data, "init", "--yes", "--password-file", str(pwfile))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "admin" in r.stdout and PW not in r.stdout + r.stderr
    assert _q(data, "select username, display_name, role, status, is_active, must_change_password from users") == [
        ("admin", "최고 관리자", "owner", "active", 1, 0)
    ]
    assert _verify(data, "admin", PW) and PW not in repr(_q(data, "select * from users"))
    (marker,) = _q(data, "select value from app_settings where key = 'owner_setup'")[0]
    assert json.loads(marker)["owner"] == "admin"
    assert _q(data, "select action, after_status, after_role, actor_id from account_events") == [
        ("setup", "active", "owner", None)
    ]
    # 다시 실행하면 아무것도 바꾸지 않는다 (비밀번호도 덮지 않음)
    again = _run(data, "init", "--yes", "--password-file", str(_pwfile(tmp_path, "Other-pass-456")))
    assert again.returncode == 2 and "이미 초기화" in again.stdout
    assert _q(data, "select count(*) from users") == [(1,)] and _verify(data, "admin", PW)
    # 대화식이 아닌데 --yes가 없으면 확인 없이 바꾸지 않는다 / 비밀번호 입력 수단이 없어도 멈춘다
    data2 = tmp_path / "data2"
    _migrate(data2)
    r = _run(data2, "init", "--password-file", str(pwfile))
    assert r.returncode == 2 and "--yes" in r.stdout and _q(data2, "select count(*) from users") == [(0,)]
    r = _run(data2, "init", "--yes")
    assert r.returncode == 2 and "비밀번호를 받을 방법" in r.stdout
    r = _run(data2, "init", "--yes", "--password-stdin", stdin="short\n")
    assert r.returncode == 2 and "8자 이상" in r.stdout and _q(data2, "select count(*) from users") == [(0,)]


def test_init_concurrent_processes_make_one_admin(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _migrate(data)
    pwfile = _pwfile(tmp_path, PW)
    procs = [
        subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "app.adminctl", "init", "--yes", "--password-file", str(pwfile)],
            cwd=BACKEND,
            env=_env(data),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )  # fmt: skip
        for _ in range(2)
    ]
    outs = [p.communicate(timeout=300)[0] for p in procs]
    assert sorted(p.returncode for p in procs) == [0, 2], outs
    assert _q(data, "select count(*) from users") == [(1,)]
    assert _q(data, "select count(*) from app_settings where key = 'owner_setup'") == [(1,)]
    assert all(PW not in o for o in outs)


def test_init_generate_shows_temp_password_once_and_requires_change(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _migrate(data)
    r = _run(data, "init", "--yes", "--generate", "--display-name", "설치 관리자")
    assert r.returncode == 0, r.stdout
    lines = [ln.strip() for ln in r.stdout.splitlines()]
    shown = lines[lines.index(next(ln for ln in lines if ln.startswith("임시 비밀번호"))) + 1]
    assert len(shown) == 14 and _verify(data, "admin", shown)
    assert _q(data, "select display_name, must_change_password, temp_password_expires_at is not null from users") == [
        ("설치 관리자", 1, 1)
    ]


# ---------------------------------------------------------------- transition (기존 owner → admin)


def test_transition_check_then_apply_keeps_identity_and_ends_only_owner_sessions(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _migrate(data)
    rox, bob = _user(data, "rox", "owner"), _user(data, "bob", "user")
    _x(data, "insert into app_settings (key, value) values ('owner_setup', ?)", json.dumps({"owner": "rox"}))
    _session(data, rox)
    _session(data, bob)
    (hash_before,) = _q(data, "select password_hash from users where id = ?", rox)[0]

    check = _run(data, "transition")
    assert check.returncode == 0 and "확인만" in check.stdout and "rox → admin" in check.stdout, check.stdout
    assert _q(data, "select username from users where id = ?", rox) == [("rox",)]  # 확인만 — 바꾸지 않음
    refused = _run(data, "transition", "--apply")  # 대화식이 아닌데 --yes 없음
    assert refused.returncode == 2 and _q(data, "select username from users where id = ?", rox) == [("rox",)]

    applied = _run(data, "transition", "--apply", "--yes")
    assert applied.returncode == 0 and "전환했습니다: rox → admin" in applied.stdout, applied.stdout
    assert _q(data, "select id, username, role, status, password_hash, auth_version from users where id = ?", rox) == [
        (rox, "admin", "owner", "active", hash_before, 1)
    ]
    assert _verify(data, "admin", "legacy-pass-1")  # 비밀번호는 그대로
    assert _q(data, "select username from users where id = ?", bob) == [("bob",)]
    assert _q(data, "select user_id from auth_sessions") == [(bob,)]  # 관리자 세션만 끝났다
    marker = json.loads(_q(data, "select value from app_settings where key = 'owner_setup'")[0][0])
    assert marker["owner"] == "admin" and marker["renamed_from"] == "rox"
    assert _q(data, "select action, target_id, target_name, before_role, after_role, actor_id from account_events") == [
        ("owner_renamed", rox, "admin", "owner", "owner", None)
    ]
    again = _run(data, "transition", "--apply", "--yes")
    assert again.returncode == 0 and "이미 'admin'" in again.stdout
    assert _q(data, "select count(*) from account_events") == [(1,)]


def test_transition_refuses_conflict_missing_or_multiple_owners(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _migrate(data)
    r = _run(data, "transition", "--apply", "--yes")
    assert r.returncode == 2 and "owner 역할 계정이 없습니다" in r.stdout
    rox = _user(data, "rox", "owner")
    taken = _user(data, "admin", "user", status="suspended")  # 다른 계정이 admin을 쓰는 중 (상태와 무관하게 충돌)
    r = _run(data, "transition", "--apply", "--yes")
    assert r.returncode == 2 and "충돌" in r.stdout
    assert _q(data, "select id, username, role from users order by id") == [
        (rox, "rox", "owner"),
        (taken, "admin", "user"),
    ]
    _x(data, "update users set username = 'old-admin' where id = ?", taken)
    second = _user(data, "boss2", "owner")
    r = _run(data, "transition", "--apply", "--yes")
    assert r.returncode == 2 and "2개" in r.stdout
    assert {u for (u,) in _q(data, "select username from users")} == {"rox", "old-admin", "boss2"}
    assert _q(data, "select count(*) from account_events") == [(0,)]
    assert second > rox


# ---------------------------------------------------------------- recover (관리자 비밀번호 분실)


def test_recover_resets_only_the_admin_password_and_sessions(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _migrate(data)
    r = _run(data, "init", "--yes", "--password-file", str(_pwfile(tmp_path, PW)))
    assert r.returncode == 0
    (admin_id,) = _q(data, "select id from users where username = 'admin'")[0]
    bob = _user(data, "bob", "user")
    _session(data, admin_id)
    _session(data, bob)
    new = "Recovered-pass-9"
    r = _run(data, "recover", "--yes", "--password-stdin", stdin=new + "\n")
    assert r.returncode == 0 and "재설정" in r.stdout and new not in r.stdout + r.stderr, r.stdout
    assert _verify(data, "admin", new) and not _verify(data, "admin", PW)
    assert _q(data, "select must_change_password, auth_version from users where id = ?", admin_id) == [(0, 1)]
    assert _q(data, "select user_id from auth_sessions") == [(bob,)]
    assert _q(data, "select action, target_name, actor_id from account_events where action = 'admin_recover'") == [
        ("admin_recover", "admin", None)
    ]
    # 담당자가 고른 비밀번호 → 임시 상태가 아니므로 사유에 관찰 도구의 '발급' 표식이 없다
    reasons_sql = "select reason from account_events where action = 'admin_recover' order by id"
    reasons = [reason for (reason,) in _q(data, reasons_sql)]
    assert len(reasons) == 1 and GENERATED_TEMP_PASSWORD_NOTE not in reasons[0], reasons
    assert _verify(data, "bob", "legacy-pass-1")  # 다른 계정은 그대로
    # 임시 비밀번호로 복구 → 첫 로그인에서 변경
    r = _run(data, "recover", "--yes", "--generate")
    assert r.returncode == 0
    lines = [ln.strip() for ln in r.stdout.splitlines()]
    shown = lines[lines.index(next(ln for ln in lines if ln.startswith("임시 비밀번호"))) + 1]
    assert _verify(data, "admin", shown) and _q(
        data, "select must_change_password from users where id = ?", admin_id
    ) == [(1,)]
    # 생성 비밀번호 → 사유 끝에 표식 — 관찰 도구(scripts/observe_db.py)가 이 표식으로 '발급'을 센다
    reasons = [reason for (reason,) in _q(data, reasons_sql)]
    assert len(reasons) == 2 and reasons[1].endswith(" · " + GENERATED_TEMP_PASSWORD_NOTE), reasons
    # owner가 아닌 아이디는 복구하지 않는다
    r = _run(data, "recover", "--yes", "--username", "bob", "--password-stdin", stdin=new + "\n")
    assert r.returncode == 2 and "owner 역할 계정이 아닙니다" in r.stdout and _verify(data, "bob", "legacy-pass-1")


def test_recover_temp_note_is_defined_once_and_shared_with_the_observe_tool() -> None:
    """복구 명령의 '임시 비밀번호' 사유 표식은 app/account_markers.py 한 곳에서만 정의한다.

    관찰 도구(scripts/observe_db.py)와 그 시험 사례(scripts/test_observe_cases.py)는
    그 파일을 경로로 읽고, 문자열을 따로 적지 않는다."""
    root = BACKEND.parent
    assert "임시 비밀번호" in GENERATED_TEMP_PASSWORD_NOTE  # 7-2차까지 관찰 도구가 찾던 부분 문자열을 포함한다
    for rel in ("scripts/observe_db.py", "scripts/test_observe_cases.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "account_markers" in src and "GENERATED_TEMP_PASSWORD_NOTE" in src, rel
        assert GENERATED_TEMP_PASSWORD_NOTE not in src, f"{rel}: 표식 문자열을 따로 정의하지 말 것"
    adminctl_src = (BACKEND / "app" / "adminctl.py").read_text(encoding="utf-8")
    assert "GENERATED_TEMP_PASSWORD_NOTE" in adminctl_src and GENERATED_TEMP_PASSWORD_NOTE not in adminctl_src


def test_init_with_generated_password_marks_the_setup_event(tmp_path: Path) -> None:
    """초기화 명령의 생성 비밀번호도 같은 표식.

    설치 뒤에는 다시 생기지 않지만 관찰 도구가 setup 이력을 같은 규칙으로 센다."""
    data = tmp_path / "data"
    _migrate(data)
    r = _run(data, "init", "--yes", "--generate")
    assert r.returncode == 0, r.stdout + r.stderr[-300:]
    (reason,) = _q(data, "select reason from account_events where action = 'setup'")[0]
    assert reason.endswith(" · " + GENERATED_TEMP_PASSWORD_NOTE), reason
    assert _q(data, "select must_change_password from users where username = 'admin'") == [(1,)]


@pytest.mark.skipif(not os.environ.get("MOAJWO_TEST_PG_ADMIN_URL"), reason="빈 PostgreSQL DB를 만들 관리자 주소가 없음")
def test_init_on_postgresql(tmp_path: Path) -> None:
    """같은 명령이 외부 PostgreSQL(앱 계정 주소)에서도 한 번만 만든다 — 일회용 DB."""
    from tests.conftest import pg_database

    url = pg_database("moajwo_adminctl")
    try:
        env = {**_env(tmp_path), "MOAJWO_DB_MODE": "external", "DATABASE_URL": url}
        for args in (["-m", "alembic", "upgrade", "head"], ["-m", "app.adminctl", "init", "--yes", "--password-stdin"]):
            r = subprocess.run([sys.executable, "-X", "utf8", *args], cwd=BACKEND, env=env, input=PW + "\n",
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=300)  # fmt: skip
            assert r.returncode == 0, r.stdout + r.stderr[-500:]
        r = subprocess.run([sys.executable, "-X", "utf8", "-m", "app.adminctl", "init", "--yes", "--password-stdin"],
        cwd=BACKEND,
                           env=env, input=PW + "\n", capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=300)  # fmt: skip
        assert r.returncode == 2 and "이미 초기화" in r.stdout
    finally:
        pg_database("moajwo_adminctl", drop=True)
