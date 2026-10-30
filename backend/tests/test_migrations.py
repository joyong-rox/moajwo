"""마이그레이션 0011: 기본 광역 항목과 빈 시도·시군구 코드 채우기 (빈 임시 DB에 alembic을 실제로 실행)."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
# 이 파일은 빈 SQLite 파일에 마이그레이션을 실제로 실행해 그 파일을 읽는다 (예전 리비전에서 head까지·downgrade).
# PostgreSQL의 마이그레이션은 test_migrations_postgres.py가 맡는다 (빈 DB → head, 모델과 차이 없음, 기본 자료 같음).
pytestmark = pytest.mark.sqlite_only("SQLite 파일에 마이그레이션을 실행해 읽음 (PostgreSQL은 test_migrations_postgres)")


def _alembic(data_dir: Path, *args: str) -> None:
    env = {**os.environ, "MOAJWO_DATA_DIR": str(data_dir), "PYTHONIOENCODING": "utf-8"}
    env.pop("DATABASE_URL", None)
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env, check=True, capture_output=True)


def test_base_sido_entries_seeded_and_removed(tmp_path: Path) -> None:
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    rows = {r[0]: r[1:] for r in db.execute("SELECT code, name, sido_code, valid_to, source FROM glossary_entries")}
    # 현행 광역 16개 + 폐지 광역 4개가 모두 있다 (예시 항목과 겹치는 코드는 예시를 그대로 둠)
    for code in ("6110000", "5690000", "6430000", "6130000", "6530000", "6540000", "6290000", "6420000"):
        assert code in rows, code
    assert rows["5690000"][:2] == ("세종특별자치시", "36")
    assert rows["6290000"][2] == "2026-06-30"  # 광주광역시: 통합 전날까지
    # 예시 기초자치단체 항목도 시도 코드가 채워져 있다
    assert (
        db.execute(
            "SELECT count(*) FROM glossary_entries WHERE type_label LIKE '%자치단체' AND sido_code IS NULL"
        ).fetchone()[0]
        == 0
    )
    db.close()

    _alembic(tmp_path, "downgrade", "0010_users_roles")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT count(*) FROM glossary_entries WHERE source = 'base'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM glossary_entries WHERE source = 'example'").fetchone()[0] > 0
    db.close()


def test_reorganization_entries_and_successor_aliases(tmp_path: Path) -> None:
    from app.glossary import AliasEntry, Entry, Glossary, period_end

    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    entries = [
        Entry(code, name, type_label or "", sido, sigungu, _d(start), _d(end))
        for code, name, type_label, sido, sigungu, start, end in db.execute(
            "SELECT code, name, type_label, sido_code, sigungu_code, valid_from, valid_to FROM glossary_entries"
        )
    ]
    aliases: dict[str, list[AliasEntry]] = {}
    for normalized, target, start, source in db.execute(
        "SELECT normalized, target_code, valid_from, source FROM glossary_aliases"
    ):
        aliases.setdefault(normalized, []).append(AliasEntry(target, _d(start), base=source == "base"))
    assert (
        db.execute("SELECT count(*) FROM glossary_entries WHERE code IN ('3500000', '3501000', '5805000')").fetchone()[
            0
        ]
        == 3
    )
    db.close()

    g = Glossary(entries, aliases)
    for name, period, code in [
        ("인천 동구", "2025-04", "3500000"),
        ("인천 동구", "2026-08", "3501000"),
        ("광주 동구", "2026-08", "5805000"),
        ("전남 목포시", "2026-08", "5780000"),
        ("광주", "2024-03", "6290000"),
        ("전남", "2024-03", "6460000"),
        ("강원 춘천시", "2022-12", "4180000"),
        ("경북 군위군", "2026-08", "5141000"),
    ]:
        res = g.resolve(name, period_end(period), "region")
        assert (res.status, res.ref.code if res.ref else None) == ("resolved", code), (name, period)

    _alembic(tmp_path, "downgrade", "0011_sido_base_entries")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT count(*) FROM glossary_aliases WHERE source = 'base'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM glossary_entries WHERE code = '3500000'").fetchone()[0] == 0
    assert (
        db.execute("SELECT count(*) FROM glossary_entries WHERE code = '6290000'").fetchone()[0] == 1
    )  # 0011 항목은 유지
    db.close()


def _d(value: str | None):
    from datetime import date

    return date.fromisoformat(value) if value else None


def test_general_gu_entries(tmp_path: Path) -> None:
    from app.glossary import AliasEntry, Entry, Glossary, period_end

    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    gu = db.execute(
        "SELECT count(*), sum(sido_code IS NULL) FROM glossary_entries WHERE type_label = '일반구'"
    ).fetchone()
    assert gu == (41, 0)
    entries = [
        Entry(code, name, type_label or "", sido, sigungu, _d(start), _d(end))
        for code, name, type_label, sido, sigungu, start, end in db.execute(
            "SELECT code, name, type_label, sido_code, sigungu_code, valid_from, valid_to FROM glossary_entries"
        )
    ]
    aliases: dict[str, list[AliasEntry]] = {}
    for normalized, target, start, source in db.execute(
        "SELECT normalized, target_code, valid_from, source FROM glossary_aliases"
    ):
        aliases.setdefault(normalized, []).append(AliasEntry(target, _d(start), base=source == "base"))
    db.close()
    g = Glossary(entries, aliases)
    for name, period, code in [
        ("경기 고양시 덕양구", "2025-04", "3950000"),
        ("고양시 덕양구", None, "3950000"),
        ("경남 창원시 마산합포구", "2026-08", "5670156"),
        ("충남 천안시 서북구", "2026-08", "5660000"),
        ("전북 전주시 완산구", "2023-05", "4650000"),
        ("전북 전주시 완산구", "2026-08", "4651000"),
    ]:
        res = g.resolve(name, period_end(period), "region")
        assert (res.status, res.ref.code if res.ref else None) == ("resolved", code), (name, period)

    _alembic(tmp_path, "downgrade", "0012_reorg_base_entries")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT count(*) FROM glossary_entries WHERE type_label = '일반구'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM glossary_aliases WHERE source = 'base'").fetchone()[0] == 180  # 0012 유지
    db.close()


def test_direction_gu_are_ambiguous_and_sejong_duplicates_resolve(tmp_path: Path) -> None:
    """시도 없는 '남구'·'중구'는 여러 항목 → 모호, '세종 세종특별자치시' 같은 중복 표기는 세종으로 (0014)."""
    from app.glossary import AliasEntry, Entry, Glossary, period_end

    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    entries = [
        Entry(code, name, type_label or "", sido, sigungu, _d(start), _d(end))
        for code, name, type_label, sido, sigungu, start, end in db.execute(
            "SELECT code, name, type_label, sido_code, sigungu_code, valid_from, valid_to FROM glossary_entries"
        )
    ]
    aliases: dict[str, list[AliasEntry]] = {}
    for normalized, target, start, source in db.execute(
        "SELECT normalized, target_code, valid_from, source FROM glossary_aliases"
    ):
        aliases.setdefault(normalized, []).append(AliasEntry(target, _d(start), base=source == "base"))
    db.close()
    g = Glossary(entries, aliases)
    for name in ("남구", "중구", "동구", "서구", "북구"):
        for period in (None, "2024-03"):
            res = g.resolve(name, period_end(period), "region")
            assert res.status == "ambiguous" and len(res.candidates) >= 4, (name, period, res)
    assert g.resolve("부산 남구", None, "region").ref.code == "3310000"
    for name in ("세종 세종특별자치시", "세종시 세종특별자치시", "세종특별자치시 세종특별자치시"):
        res = g.resolve(name, period_end("2026-08"), "region")
        assert (res.status, res.ref.code if res.ref else None) == ("resolved", "5690000"), name

    _alembic(tmp_path, "downgrade", "0013_general_gu_entries")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT count(*) FROM glossary_entries WHERE code = '3310000'").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM glossary_aliases WHERE target_code = '5690000'").fetchone()[0] == 0
    db.close()


def test_0011_fills_only_empty_region_fields(tmp_path: Path) -> None:
    """교차 검증 ⑧: 시도·시군구 코드는 각각 빈 칸만 채우고, 사용자가 넣은 값은 추정값과 달라도 그대로 둔다."""
    _alembic(tmp_path, "upgrade", "0010_users_roles")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    rows = [  # (코드, 정식명칭, 시도, 시군구)
        ("9000001", "서울특별시 강서구", None, "999"),  # 시도만 빔, 시군구는 사용자 값(코드표 추정 500과 다름)
        ("9000002", "서울특별시 강남구", "11", None),  # 시군구만 빔
        ("9000003", "부산광역시 해운대구", None, None),  # 둘 다 빔
        ("9000004", "서울특별시 종로구", "26", "777"),  # 둘 다 입력 (추정과 다름)
    ]
    for code, name, sido, sigungu in rows:
        db.execute(
            "INSERT INTO glossary_entries (code, name, normalized, type_label, sido_code, sigungu_code, source)"
            " VALUES (?, ?, ?, '기초자치단체', ?, ?, 'upload')",
            (code, name, name.replace(" ", ""), sido, sigungu),
        )
    db.commit()
    db.close()
    _alembic(tmp_path, "upgrade", "0011_sido_base_entries")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    got = {
        code: (sido, sigungu, source)
        for code, sido, sigungu, source in db.execute(
            "SELECT code, sido_code, sigungu_code, source FROM glossary_entries WHERE code LIKE '90000%'"
        )
    }
    db.close()
    assert got["9000001"] == ("11", "999", "upload")
    assert got["9000002"] == ("11", "680", "upload")
    assert got["9000003"] == ("26", "350", "upload")
    assert got["9000004"] == ("26", "777", "upload")


def test_downgrade_keeps_entries_users_refer_to(tmp_path: Path) -> None:
    """추가 확인 ⑬: downgrade는 그 revision이 넣은 데이터만 지우고, 사용자 별칭이 가리키는 항목은 남긴다."""
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.execute(  # 사용자가 0012 기본 항목(인천 제물포구)·0011 기본 항목(부산광역시)에 별칭을 붙였다
        "INSERT INTO glossary_aliases (alias, normalized, target_code, source) VALUES"
        " ('제물포', '제물포', '3501000', 'user'), ('부산시청', '부산시청', '6260000', 'user')"
    )
    db.commit()
    db.close()
    _alembic(tmp_path, "downgrade", "0010_users_roles")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    left = {r[0] for r in db.execute("SELECT code FROM glossary_entries WHERE code IN ('3501000', '6260000')")}
    aliases = {r[0] for r in db.execute("SELECT normalized FROM glossary_aliases WHERE source = 'user'")}
    others = db.execute("SELECT count(*) FROM glossary_entries WHERE source = 'base'").fetchone()[0]
    db.close()
    assert left == {"3501000", "6260000"} and aliases == {"제물포", "부산시청"}
    # 남은 기본 항목은 사용자 별칭이 가리키는 제물포구 하나뿐 (부산광역시는 0005 예시 항목이라 원래 base가 아님)
    assert others == 1


def test_0015_marks_existing_installs_and_keeps_new_ones_open(tmp_path: Path) -> None:
    """④ 관련: 계정이 있는 기존 설치는 업그레이드 후에도 최초 설정이 다시 열리지 않고, 빈 설치는 열려 있다."""
    old = tmp_path / "old"
    _alembic(old, "upgrade", "0014_direction_gu_sejong")
    db = sqlite3.connect(old / "moajwo.db")
    db.execute(
        "INSERT INTO users (username, display_name, password_hash, role, is_active, failed_logins)"
        " VALUES ('boss', 'BOSS', 'x', 'owner', 1, 0)"
    )
    db.commit()
    db.close()
    _alembic(old, "upgrade", "head")
    db = sqlite3.connect(old / "moajwo.db")
    assert db.execute("SELECT count(*) FROM app_settings WHERE key = 'owner_setup'").fetchone()[0] == 1
    db.close()
    _alembic(old, "downgrade", "0014_direction_gu_sejong")
    db = sqlite3.connect(old / "moajwo.db")
    assert db.execute("SELECT count(*) FROM app_settings WHERE key = 'owner_setup'").fetchone()[0] == 0
    db.close()

    new = tmp_path / "new"
    _alembic(new, "upgrade", "head")
    db = sqlite3.connect(new / "moajwo.db")
    assert db.execute("SELECT count(*) FROM app_settings WHERE key = 'owner_setup'").fetchone()[0] == 0
    db.close()


def test_setup_stays_closed_after_0015_upgrade(tmp_path: Path) -> None:
    """④·0015 재검증: 계정이 있는 기존 DB를 올린 뒤 초기화(create_owner)가 '이미 초기화됨'으로 막히고 계정 수는
    그대로다."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import auth

    _alembic(tmp_path, "upgrade", "0014_direction_gu_sejong")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.execute(
        "INSERT INTO users (username, display_name, password_hash, role, is_active, failed_logins)"
        " VALUES ('boss', 'BOSS', 'x', 'owner', 1, 0)"
    )
    db.commit()
    db.close()
    _alembic(tmp_path, "upgrade", "head")
    Session = sessionmaker(create_engine(f"sqlite:///{(tmp_path / 'moajwo.db').as_posix()}"))
    with Session() as session:
        assert auth.setup_done(session)
        with pytest.raises(auth.SetupDone):
            auth.create_owner(session, "late", "late", "password-123", reason="시험")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT username, role FROM users").fetchall() == [("boss", "owner")]
    db.close()


def test_kept_base_entries_do_not_duplicate_on_reupgrade(tmp_path: Path) -> None:
    """⑬ 재검증: downgrade에서 남긴 기본 항목·사용자가 고친 항목이 다시 upgrade해도 중복·충돌하지 않는다."""
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.execute(
        "INSERT INTO glossary_aliases (alias, normalized, target_code, source)"
        " VALUES ('제물포', '제물포', '3501000', 'user')"
    )
    db.execute("UPDATE glossary_entries SET name = '서울특별시(수정)', source = 'upload' WHERE code = '6110000'")
    db.commit()
    db.close()
    _alembic(tmp_path, "downgrade", "0010_users_roles")
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    dup = db.execute("SELECT code, count(*) FROM glossary_entries GROUP BY code HAVING count(*) > 1").fetchall()
    seoul = db.execute("SELECT name, source FROM glossary_entries WHERE code = '6110000'").fetchone()
    alias = db.execute(
        "SELECT count(*) FROM glossary_aliases WHERE normalized = '제물포' AND source = 'user'"
    ).fetchone()[0]
    db.close()
    assert dup == [] and seoul == ("서울특별시(수정)", "upload") and alias == 1


def test_0016_converts_existing_accounts_and_downgrade_restores_roles(tmp_path: Path) -> None:
    """0016: 기존 소유자·활성 사용자는 이용 중, 비활성은 이용 중지(다시 켜지 않음), 관리자는 일반 사용자 + 세션 종료.
    소유자 여럿·알 수 없는 역할은 바꾸지 않고 확인 필요로 남긴다. 계정 ID·파일 소유 관계는 그대로.
    downgrade는 관리자 역할을 되돌린다."""
    _alembic(tmp_path, "upgrade", "0014_direction_gu_sejong")  # 운영처럼: 계정이 있는 상태에서 0015·0016을 올린다
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.executemany(
        "INSERT INTO users (id, username, display_name, password_hash, role, is_active, failed_logins)"
        " VALUES (?, ?, ?, 'x', ?, ?, 0)",
        [
            (1, "boss", "BOSS", "owner", 1),
            (2, "worker", "W", "user", 1),
            (3, "manager", "M", "admin", 1),
            (4, "old-admin", "OA", "admin", 0),
            (5, "ops-user-20261012", "검증", "user", 0),
            (6, "boss2", "B2", "owner", 1),
            (7, "weird", "?", "auditor", 1),
        ],
    )
    db.executemany(
        "INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at)"
        " VALUES (?, ?, '2026-10-12', '2099-01-01')",
        [("h-boss", 1), ("h-worker", 2), ("h-manager", 3)],
    )
    db.execute(
        "INSERT INTO source_files (original_name, stored_path, sha256, size_bytes, uploaded_by)"
        " VALUES ('관리자파일.xlsx', 'uploads/x.xlsx', 'abc', 1, 3)"
    )
    db.commit()
    db.close()

    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    users = {r[0]: r[1:] for r in db.execute("SELECT username, id, role, status, is_active FROM users")}
    assert users == {
        "boss": (1, "owner", "active", 1),
        "worker": (2, "user", "active", 1),  # 재승인 없이 이용 중
        "manager": (3, "user", "active", 1),  # 관리자 → 일반 사용자 (활성 유지)
        "old-admin": (4, "user", "suspended", 0),
        "ops-user-20261012": (5, "user", "suspended", 0),  # 비활성 검증 계정은 다시 켜지 않는다
        "boss2": (6, "owner", "active", 1),  # 소유자 여럿: 고르거나 강등하지 않음
        "weird": (7, "auditor", "active", 1),  # 알 수 없는 역할: 바꾸지 않음
    }
    assert sorted(r[0] for r in db.execute("SELECT token_hash FROM auth_sessions")) == ["h-boss", "h-worker"]
    assert db.execute("SELECT uploaded_by FROM source_files").fetchall() == [(3,)]
    events = db.execute(
        "SELECT target_name, action, before_role, after_role, result FROM account_events ORDER BY id"
    ).fetchall()
    assert ("manager", "role_migrated", "admin", "user", "ok") in events
    assert ("old-admin", "role_migrated", "admin", "user", "ok") in events
    assert ("weird", "migration_check", "auditor", "auditor", "denied") in events
    assert {e[0] for e in events if e[1] == "migration_check" and e[2] == "owner"} == {"boss", "boss2"}
    assert db.execute("SELECT count(*) FROM app_settings WHERE key = 'owner_setup'").fetchone()[0] == 1
    db.close()

    _alembic(tmp_path, "downgrade", "0015_owner_setup_marker")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    roles = dict(db.execute("SELECT username, role FROM users").fetchall())
    assert roles["manager"] == roles["old-admin"] == "admin" and roles["worker"] == "user"
    cols = [r[1] for r in db.execute("PRAGMA table_info(users)")]
    assert "status" not in cols
    assert not db.execute("SELECT name FROM sqlite_master WHERE name = 'account_events'").fetchall()
    assert dict(db.execute("SELECT username, is_active FROM users").fetchall())["ops-user-20261012"] == 0
    db.close()


def test_0017_keeps_existing_sessions_and_downgrades(tmp_path: Path) -> None:
    """0017: 기존 사용자·세션은 버전 0으로 맞춰져 지금 로그인이 그대로 유효하다. downgrade는 두 열만 지운다."""
    import hashlib
    from datetime import UTC, datetime, timedelta

    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.auth import COOKIE_NAME, current_user

    _alembic(tmp_path, "upgrade", "0016_account_status_roles")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.execute(
        "INSERT INTO users (id, username, display_name, password_hash, role, is_active, failed_logins, status)"
        " VALUES (1, 'boss', 'B', 'x', 'owner', 1, 0, 'active')"
    )
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    token_hash = hashlib.sha256(b"existing-token").hexdigest()
    db.execute(
        "INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) VALUES (?, 1, ?, ?)",
        (token_hash, datetime.now(UTC).isoformat(), expires),
    )
    db.commit()
    db.close()

    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT auth_version FROM users").fetchall() == [(0,)]
    assert db.execute("SELECT auth_version FROM auth_sessions").fetchall() == [(0,)]
    db.close()

    class Req:
        cookies = {COOKIE_NAME: "existing-token"}

    Session = sessionmaker(create_engine(f"sqlite:///{(tmp_path / 'moajwo.db').as_posix()}"))
    with Session() as session:
        assert current_user(Req(), session).username == "boss"  # 전환 후에도 기존 로그인 유지
        try:
            Req.cookies = {COOKIE_NAME: "unknown-token"}
            current_user(Req(), session)
            raise AssertionError("없는 토큰이 통과함")
        except HTTPException as exc:
            assert exc.status_code == 401

    _alembic(tmp_path, "downgrade", "0016_account_status_roles")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert "auth_version" not in [r[1] for r in db.execute("PRAGMA table_info(users)")]
    assert "auth_version" not in [r[1] for r in db.execute("PRAGMA table_info(auth_sessions)")]
    assert db.execute("SELECT count(*) FROM auth_sessions").fetchone()[0] == 1
    db.close()


def test_0018_mapping_analyses_up_and_down(tmp_path: Path) -> None:
    """0018: 분석 기록 테이블만 만들고 지운다. 매핑 확정(mapping_decisions)은 건드리지 않는다."""
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT name FROM sqlite_master WHERE name = 'mapping_analyses'").fetchall()
    db.execute(
        "INSERT INTO source_files (id, original_name, stored_path, sha256, size_bytes) VALUES (1, 'a', 'x', 's', 1)"
    )
    db.execute(
        "INSERT INTO mapping_decisions (source_file_id, sheet_name, column_name, field_key)"
        " VALUES (1, '현황', '열', 'note')"
    )
    db.execute("INSERT INTO mapping_analyses (source_file_id, created_by, result) VALUES (1, NULL, '{}')")
    db.commit()
    db.close()
    _alembic(tmp_path, "downgrade", "0017_auth_version")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert not db.execute("SELECT name FROM sqlite_master WHERE name = 'mapping_analyses'").fetchall()
    assert db.execute("SELECT field_key FROM mapping_decisions").fetchall() == [("note",)]
    db.close()


def test_0019_analysis_runs_up_down_and_up_again(tmp_path: Path) -> None:
    """0019: 분석 결과 테이블만 만들고 지운다. 매핑 확정·분석 기록(0018)·업로드 정보는 그대로, 다시 올리면 빈 테이블."""
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.execute(
        "INSERT INTO source_files (id, original_name, stored_path, sha256, size_bytes) VALUES (1, 'a', 'x', 's', 1)"
    )
    db.execute(
        "INSERT INTO mapping_decisions (source_file_id, sheet_name, column_name, field_key)"
        " VALUES (1, '현황', '인구수', 'population')"
    )
    db.execute("INSERT INTO mapping_analyses (source_file_id, created_by, result) VALUES (1, NULL, '{}')")
    db.execute(
        "INSERT INTO analysis_runs (created_by, field_key, summary, conditions, inputs, result)"
        " VALUES (NULL, 'population', '{}', '{}', '[]', '{}')"
    )
    db.commit()
    db.close()
    _alembic(tmp_path, "downgrade", "0018_mapping_analyses")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert not db.execute("SELECT name FROM sqlite_master WHERE name = 'analysis_runs'").fetchall()
    assert db.execute("SELECT field_key FROM mapping_decisions").fetchall() == [("population",)]
    assert db.execute("SELECT count(*) FROM mapping_analyses").fetchone()[0] == 1
    assert db.execute("SELECT sha256 FROM source_files").fetchall() == [("s",)]
    db.close()
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT count(*) FROM analysis_runs").fetchone()[0] == 0
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD
    db.close()


HEAD = "0022_temp_passwords"  # 지금 head — 바뀌면 여기와 docs/DEPLOY.md를 함께
REQUEST_COLUMNS = {"request_kind", "request_id", "request_hash"}


def _run_columns(db: sqlite3.Connection) -> set[str]:
    return {r[1] for r in db.execute("PRAGMA table_info(runs)")}


def test_0020_run_worker_lease_up_down_and_up_again(tmp_path: Path) -> None:
    """0020: 실행 기록에 담당·생존 신호 열만 더하고 지운다. 실행·변경 기록·'결과 확인 필요' 행은 그대로이고, 이 판에서
    시작해 실행 중인 행(담당 있음)이 있으면 내리지 않는다 (이전 판은 그 행을 정리하지 못함). 이 판 전에 시작한 실행 중
    행(담당 없음)은 이전 판이 남긴 그대로라 막지 않는다 — 막으면 서버를 멈춘 롤백에서 정리할 길이 없다."""
    _alembic(tmp_path, "upgrade", "head")
    _alembic(tmp_path, "downgrade", "0020_run_worker_lease")  # 0020 자체의 경계를 본다 (0021은 아래 시험)
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert {"worker", "heartbeat_at"} <= _run_columns(db) and not REQUEST_COLUMNS & _run_columns(db)
    run = (
        "INSERT INTO runs (id, status, trigger, input_file_count, file_ids, row_count, change_count,"
        " warning_count, summary, worker, heartbeat_at, output_path, error)"
        " VALUES (?, ?, 'manual', 0, '[]', 0, 0, 0, '{}', ?, ?, ?, ?)"
    )
    db.execute(run, (1, "done", "aaaaaaaaaaaa-bbbbbbbbbbbb", "2026-10-25 01:00:00", "outputs/a.xlsx", None))
    db.execute(run, (2, "check", None, None, None, "확인 필요"))
    db.execute(
        "INSERT INTO run_changes (run_id, file_name, sheet, output_row, column, source_column, kind, reason,"
        " confidence) VALUES (1, 'a.xlsx', 's', 2, '인구수', '인구', 'number', '숫자 변환', 1.0)"
    )
    db.execute(run, (3, "running", None, None, None, None))  # 이 판 전에 시작 (담당 없음)
    db.execute(run, (4, "running", "aaaaaaaaaaaa-cccccccccccc", "2026-10-25 02:00:00", None, None))
    db.commit()
    db.close()
    try:
        _alembic(tmp_path, "downgrade", "0019_analysis_runs")
        raise AssertionError("이 판에서 시작한 실행 중인 행이 있는데 내려갔다")
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode("utf-8", "replace")
        assert "실행 중인 결과 실행이 1건" in err and "python -m app.recovery recover" in err
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0020_run_worker_lease"
    db.execute("DELETE FROM runs WHERE id = 4")
    db.commit()
    db.close()
    _alembic(tmp_path, "downgrade", "0019_analysis_runs")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert not {"worker", "heartbeat_at"} & _run_columns(db)
    assert db.execute("SELECT id, status, output_path FROM runs ORDER BY id").fetchall() == [
        (1, "done", "outputs/a.xlsx"),
        (2, "check", None),
        (3, "running", None),
    ]
    assert db.execute("SELECT run_id, kind FROM run_changes").fetchall() == [(1, "number")]
    db.close()
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert {"worker", "heartbeat_at"} | REQUEST_COLUMNS <= _run_columns(db)
    assert db.execute("SELECT worker, heartbeat_at FROM runs WHERE id = 1").fetchone() == (None, None)
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD
    db.close()


RUN_SQL = (
    "INSERT INTO runs (id, user_id, status, trigger, input_file_count, file_ids, row_count, change_count,"
    " warning_count, summary) VALUES (?, ?, ?, 'manual', 0, '[]', 0, 0, 0, ?)"
)
RID1, RID2 = "a" * 32, "b" * 32


def _keyjoin_summary(rid: str | None) -> str:
    data = {"mode": "keyjoin", "mode_label": "공통 key 연결", "basis": "x" * 64}
    if rid is not None:
        data["request_id"] = rid
    return json.dumps(data, ensure_ascii=False)


def _at_0020(tmp_path: Path) -> sqlite3.Connection:
    _alembic(tmp_path, "upgrade", "0020_run_worker_lease")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.execute(
        "INSERT INTO users (id, username, display_name, password_hash, role, is_active, status, failed_logins,"
        " auth_version) VALUES (1, 'u1', 'u1', 'scrypt$synthetic', 'user', 1, 'active', 0, 0)"
    )
    db.execute(
        "INSERT INTO users (id, username, display_name, password_hash, role, is_active, status, failed_logins,"
        " auth_version) VALUES (2, 'u2', 'u2', 'scrypt$synthetic', 'user', 1, 'active', 0, 0)"
    )
    db.commit()
    return db


def _request_index(db: sqlite3.Connection) -> bool:
    return bool(
        db.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'uq_runs_request'").fetchall()
    )


def test_0021_request_key_backfills_keyjoin_runs_and_downgrades_cleanly(tmp_path: Path) -> None:
    """0021: 요청 번호 열 3개와 고유 색인(user_id, request_kind, request_id). 이 판 전의 key 연결 실행은 요약의 요청
    번호로 request_kind·request_id만 채우고 해시는 비워 둔다. 다른 실행(묶기 — 'keyjoin' 글자가 파일 이름에만 든 것
    포함)은 비워 둔다. 색인이 같은 사용자의 중복을 막고, 내리면 열·색인만 빠지고 행은 그대로다."""
    db = _at_0020(tmp_path)
    db.execute(RUN_SQL, (1, 1, "done", _keyjoin_summary(RID1)))
    db.execute(RUN_SQL, (2, 2, "done", _keyjoin_summary(RID1)))  # 다른 사용자 — 같은 번호여도 된다
    db.execute(RUN_SQL, (3, 1, "failed", _keyjoin_summary(RID2)))
    db.execute(RUN_SQL, (4, 1, "done", json.dumps({"mode": "bundle", "files": ["keyjoin 결과.xlsx"]})))
    db.execute(RUN_SQL, (5, 1, "done", "{}"))
    db.execute(RUN_SQL, (6, None, "done", _keyjoin_summary(RID2)))  # 사용자가 지워진 행 — NULL은 겹치지 않는다
    db.commit()
    before = db.execute("SELECT id, user_id, status, summary FROM runs ORDER BY id").fetchall()
    db.close()
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD
    assert REQUEST_COLUMNS <= _run_columns(db) and _request_index(db)
    rows = db.execute("SELECT id, request_kind, request_id, request_hash FROM runs ORDER BY id").fetchall()
    assert rows == [
        (1, "keyjoin", RID1, None),
        (2, "keyjoin", RID1, None),
        (3, "keyjoin", RID2, None),
        (4, None, None, None),
        (5, None, None, None),
        (6, "keyjoin", RID2, None),
    ]
    assert db.execute("SELECT id, user_id, status, summary FROM runs ORDER BY id").fetchall() == before
    try:
        db.execute(
            "INSERT INTO runs (id, user_id, status, trigger, input_file_count, file_ids, row_count, change_count,"
            " warning_count, summary, request_kind, request_id) VALUES (7, 1, 'running', 'manual', 0, '[]', 0, 0, 0,"
            " '{}', 'keyjoin', ?)",
            (RID1,),
        )
        raise AssertionError("같은 사용자·같은 요청 번호의 둘째 행이 들어갔다")
    except sqlite3.IntegrityError:
        db.rollback()
    db.execute(
        "INSERT INTO runs (id, user_id, status, trigger, input_file_count, file_ids, row_count, change_count,"
        " warning_count, summary, request_kind, request_id) VALUES (8, 1, 'done', 'manual', 0, '[]', 0, 0, 0, '{}',"
        " NULL, NULL)"
    )
    db.execute(
        "INSERT INTO runs (id, user_id, status, trigger, input_file_count, file_ids, row_count, change_count,"
        " warning_count, summary, request_kind, request_id) VALUES (9, 1, 'done', 'manual', 0, '[]', 0, 0, 0, '{}',"
        " NULL, NULL)"
    )  # 번호가 없는 실행은 몇 개든 (NULL끼리는 겹치지 않음)
    db.commit()
    after = db.execute("SELECT id, user_id, status, summary FROM runs ORDER BY id").fetchall()
    db.close()
    _alembic(tmp_path, "downgrade", "0020_run_worker_lease")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert not REQUEST_COLUMNS & _run_columns(db) and not _request_index(db)
    assert {"worker", "heartbeat_at"} <= _run_columns(db)
    assert db.execute("SELECT id, user_id, status, summary FROM runs ORDER BY id").fetchall() == after
    db.close()
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    rows = db.execute("SELECT id, request_kind, request_id FROM runs WHERE id <= 3 ORDER BY id").fetchall()
    assert rows == [(1, "keyjoin", RID1), (2, "keyjoin", RID1), (3, "keyjoin", RID2)]  # 다시 올려도 같은 채움
    db.close()


def test_0021_refusal_in_the_chain_from_0019_leaves_sqlite_at_0020(tmp_path: Path) -> None:
    """0019에서 'upgrade head'를 돌리다 0021이 거절되면 SQLite는 **0020까지 올라가 있다**(0020의 DDL·리비전 기록이
    그대로 남음 — SQLite는 마이그레이션마다 바로 반영되고 PostgreSQL처럼 전체가 되돌아가지 않음). 0021의 열·색인은 없고
    행은 그대로다.
    거절됐다는 이유로 시작 리비전(0019)과 같다고 추정하면 안 된다 — docs/DEPLOY.md 10장."""
    _alembic(tmp_path, "upgrade", "0019_analysis_runs")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.execute(
        "INSERT INTO users (id, username, display_name, password_hash, role, is_active, status, failed_logins,"
        " auth_version) VALUES (1, 'u1', 'u1', 'scrypt$synthetic', 'user', 1, 'active', 0, 0)"
    )
    db.execute(RUN_SQL, (1, 1, "done", _keyjoin_summary(RID1)))
    db.execute(RUN_SQL, (2, 1, "done", _keyjoin_summary(RID1)))  # 같은 사용자·같은 번호 둘 → 0021이 멈춘다
    db.commit()
    before = db.execute("SELECT id, user_id, status, summary FROM runs ORDER BY id").fetchall()
    db.close()
    try:
        _alembic(tmp_path, "upgrade", "head")
        raise AssertionError("0021이 올라갔다")
    except subprocess.CalledProcessError as exc:
        assert "0021로 올리지 않습니다" in exc.stderr.decode("utf-8", "replace")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0020_run_worker_lease"
    assert {"worker", "heartbeat_at"} <= _run_columns(db) and not REQUEST_COLUMNS & _run_columns(db)
    assert not _request_index(db)
    assert db.execute("SELECT id, user_id, status, summary FROM runs ORDER BY id").fetchall() == before
    db.close()


def test_0021_refuses_to_downgrade_while_a_run_of_this_version_is_running(tmp_path: Path) -> None:
    """이 판에서 시작해 실행 중인 행(담당 있음)이 있으면 0021도 내리지 않는다 — 'downgrade 0019'가 맨 위 리비전에서 멈춰
    리비전·열·색인·행이 그대로다(보호가 0020에만 있으면 0021→0020은 내려가고 0020→0019에서 거절돼 0020에 남는다 —
    롤백 리허설 10차에서 발견). 0022가 head인 지금은 같은 보호가 0022에 먼저 걸려 head 그대로 남고(0022 열도 그대로),
    0021에서 시작하면 0021이 멈춘다. 정리한 뒤에는 내린다."""
    db = _at_0020(tmp_path)
    db.commit()
    db.close()
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.execute(RUN_SQL, (1, 1, "running", "{}"))
    db.execute("UPDATE runs SET worker = 'abcdef012345-live', heartbeat_at = '2026-10-27 09:00:00' WHERE id = 1")
    db.commit()
    held = db.execute("SELECT id, user_id, status, summary FROM runs ORDER BY id").fetchall()
    db.close()
    for target in ("0019_analysis_runs", "0020_run_worker_lease", "0021_run_request_key"):
        try:
            _alembic(tmp_path, "downgrade", target)
            raise AssertionError(f"{target}: 담당 있는 실행 중 행이 있는데 내려갔다")
        except subprocess.CalledProcessError as exc:
            err = exc.stderr.decode("utf-8", "replace")
            assert "0021로 내리지 않습니다" in err and "app.recovery recover" in err, err[-400:]
        db = sqlite3.connect(tmp_path / "moajwo.db")
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD
        assert REQUEST_COLUMNS <= _run_columns(db) and _request_index(db)
        assert "must_change_password" in {r[1] for r in db.execute("pragma table_info(users)")}
        assert db.execute("SELECT id, user_id, status, summary FROM runs ORDER BY id").fetchall() == held
        db.close()
    # 0021 자체의 보호: 실행을 끝내 0021로 내린 뒤 다시 실행 중으로 두면 'downgrade 0019'가 0021에서 멈춘다
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.execute("UPDATE runs SET status = 'done', finished_at = '2026-10-27 09:01:00' WHERE id = 1")
    db.commit()
    db.close()
    _alembic(tmp_path, "downgrade", "0021_run_request_key")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0021_run_request_key"
    db.execute("UPDATE runs SET status = 'running', finished_at = NULL WHERE id = 1")
    db.commit()
    db.close()
    for target in ("0019_analysis_runs", "0020_run_worker_lease"):
        try:
            _alembic(tmp_path, "downgrade", target)
            raise AssertionError(f"{target}: 담당 있는 실행 중 행이 있는데 0021이 내려갔다")
        except subprocess.CalledProcessError as exc:
            err = exc.stderr.decode("utf-8", "replace")
            assert "0020으로 내리지 않습니다" in err and "app.recovery recover" in err, err[-400:]
        db = sqlite3.connect(tmp_path / "moajwo.db")
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0021_run_request_key"
        assert REQUEST_COLUMNS <= _run_columns(db) and _request_index(db)
        assert db.execute("SELECT id, user_id, status, summary FROM runs ORDER BY id").fetchall() == held
        db.close()
    db = sqlite3.connect(tmp_path / "moajwo.db")
    db.execute("UPDATE runs SET status = 'done', finished_at = '2026-10-27 09:01:00' WHERE id = 1")
    db.commit()
    db.close()
    _alembic(tmp_path, "downgrade", "0020_run_worker_lease")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0020_run_worker_lease"
    assert not REQUEST_COLUMNS & _run_columns(db) and not _request_index(db)
    db.close()


@pytest.mark.parametrize(
    ("case", "rows", "told"),
    [
        (
            "duplicate",
            [(1, 1, _keyjoin_summary(RID1)), (2, 1, _keyjoin_summary(RID1))],
            "같은 요청 번호의 key 연결 실행 2건",
        ),
        ("unreadable", [(1, 1, '{"mode": "keyjoin", "request_id": ')], "읽을 수 없는 실행 1건"),
        (
            "no_request",
            [(1, 1, _keyjoin_summary(None)), (2, 1, _keyjoin_summary("짧음"))],
            "모양이 다른 key 연결 실행 2건",
        ),
    ],
)
def test_0021_stops_before_changing_anything_when_request_ids_cannot_be_filled(
    tmp_path: Path, case: str, rows: list[tuple], told: str
) -> None:
    """채울 수 없는 실행 기록이 있으면 열·색인을 만들기 전에 멈춘다 — 리비전·열·행 모두 그대로, 지우거나 덮지 않는다."""
    db = _at_0020(tmp_path)
    for run_id, user_id, summary in rows:
        db.execute(RUN_SQL, (run_id, user_id, "done", summary))
    db.commit()
    before = db.execute("SELECT id, user_id, status, summary FROM runs ORDER BY id").fetchall()
    db.close()
    try:
        _alembic(tmp_path, "upgrade", "head")
        raise AssertionError(f"{case}: 채울 수 없는 행이 있는데 올라갔다")
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode("utf-8", "replace")
        assert "0021로 올리지 않습니다" in err and told in err and "실행 [1" in err, err[-600:]
        assert "example" not in err and "basis" not in err  # 요약 원문은 알리지 않는다 — 실행 번호만
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0020_run_worker_lease"
    assert not REQUEST_COLUMNS & _run_columns(db) and not _request_index(db)
    assert db.execute("SELECT id, user_id, status, summary FROM runs ORDER BY id").fetchall() == before
    db.close()


def test_0022_temp_password_columns_default_false_and_downgrade_guard(tmp_path: Path) -> None:
    """0022: 기존 계정은 임시 비밀번호 상태가 아니다(False·NULL — 비밀번호·상태 그대로). downgrade는 **이용 중·이용
    중지·만료와 관계없이** 임시 비밀번호 상태인 계정이 하나라도 있으면 거절하고(리비전·열·계정·업무 행 그대로), 실행
    중인 결과 실행(담당 있음)이 있어도 거절한다. 모든 계정이 본인 비밀번호 변경을 끝낸(must_change_password = 0)
    뒤에만 두 열을 지운다 — 이용 중지는 임시 상태를 풀지 않는다."""
    _alembic(tmp_path, "upgrade", "0021_run_request_key")
    db = tmp_path / "moajwo.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "insert into users (username, display_name, password_hash, role, is_active, status, auth_version, "
            "failed_logins) "
            "values ('rox', 'R', 'scrypt$x', 'owner', 1, 'active', 0, 0)"
        )
    _alembic(tmp_path, "upgrade", "head")
    with sqlite3.connect(db) as con:
        cols = {r[1] for r in con.execute("pragma table_info(users)")}
        assert {"must_change_password", "temp_password_expires_at"} <= cols
        assert con.execute("select must_change_password, temp_password_expires_at from users").fetchall() == [(0, None)]
        assert con.execute("select version_num from alembic_version").fetchone() == ("0022_temp_passwords",)
        # 임시 상태 세 가지(이용 중·이용 중지·만료) + 변경 완료 하나, 업무 행 하나
        future, past = "2099-01-01 00:00:00.000000", "2020-01-01 00:00:00.000000"
        for name, active, status_, must, exp in (
            ("t-active", 1, "active", 1, future), ("t-susp", 0, "suspended", 1, future),
            ("t-expired", 1, "active", 1, past), ("done", 1, "active", 0, None),
        ):  # fmt: skip
            con.execute(
                "insert into users (username, display_name, password_hash, role, is_active, status, auth_version, "
                "failed_logins, must_change_password, temp_password_expires_at) "
                "values (?, ?, 'scrypt$y', 'user', ?, ?, 0, 0, ?, ?)",
                (name, name.upper(), active, status_, must, exp),
            )
        con.execute(RUN_SQL, (1, 1, "done", "{}"))

    def snapshot() -> tuple:
        with sqlite3.connect(db) as con:
            return (
                con.execute("select version_num from alembic_version").fetchone(),
                {r[1] for r in con.execute("pragma table_info(users)")},
                con.execute("select * from users order by id").fetchall(),
                con.execute("select id, status, worker, summary from runs order by id").fetchall(),
            )

    def refused(*needles: str) -> None:
        held = snapshot()
        with pytest.raises(subprocess.CalledProcessError) as info:
            _alembic(tmp_path, "downgrade", "0021_run_request_key")
        err = info.value.stderr.decode("utf-8", "replace")
        for needle in needles:
            assert needle in err, (needle, err[-400:])
        assert snapshot() == held  # 리비전·열·계정·업무 행 그대로

    refused("임시 비밀번호 상태인 계정이 3개", "상태와 관계없이", "이용 중지", "아무것도 바꾸지 않았습니다")
    # 이용 중·만료 계정이 변경을 끝내도(플래그 0) 중지된 임시 계정이 남으면 거절 — 중지만으로는 풀리지 않는다
    with sqlite3.connect(db) as con:
        con.execute(
            "update users set must_change_password = 0, temp_password_expires_at = null "
            "where username in ('t-active', 't-expired')"
        )
    refused("임시 비밀번호 상태인 계정이 1개")
    # 임시 계정이 없어도 실행 중인 결과 실행(담당 있음)이 있으면 거절 (0020·0021과 같은 보호)
    with sqlite3.connect(db) as con:
        con.execute("update users set must_change_password = 0, temp_password_expires_at = null")
        con.execute(
            "update runs set status = 'running', worker = 'abcdef012345-live', heartbeat_at = '2026-10-27 09:00:00' "
            "where id = 1"
        )
    refused("0021로 내리지 않습니다", "app.recovery recover")
    with sqlite3.connect(db) as con:
        con.execute("update runs set status = 'done', worker = null, finished_at = '2026-10-27 09:01:00' where id = 1")
    _alembic(tmp_path, "downgrade", "0021_run_request_key")
    with sqlite3.connect(db) as con:
        cols = {r[1] for r in con.execute("pragma table_info(users)")}
        assert not ({"must_change_password", "temp_password_expires_at"} & cols)
        assert con.execute("select version_num from alembic_version").fetchone() == ("0021_run_request_key",)
        assert con.execute("select username, password_hash, status from users order by id").fetchall() == [
            ("rox", "scrypt$x", "active"), ("t-active", "scrypt$y", "active"), ("t-susp", "scrypt$y", "suspended"),
            ("t-expired", "scrypt$y", "active"), ("done", "scrypt$y", "active"),
        ]  # fmt: skip
