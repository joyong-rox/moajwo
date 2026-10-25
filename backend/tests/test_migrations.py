"""마이그레이션 0011: 기본 광역 항목과 빈 시도·시군구 코드 채우기 (빈 임시 DB에 alembic을 실제로 실행)."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


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
    """④·0015 재검증: 계정이 있는 기존 DB를 올린 뒤 실제 setup 함수가 409로 막히고 계정 수는 그대로다."""
    from fastapi import HTTPException, Response
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import auth_api

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
        try:
            auth_api.setup(
                auth_api.SetupIn(username="late", display_name="late", password="password-123"), Response(), session
            )
            raise AssertionError("setup이 열려 있음")
        except HTTPException as exc:
            assert exc.status_code == 409
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
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0020_run_worker_lease"
    db.close()


def _run_columns(db: sqlite3.Connection) -> set[str]:
    return {r[1] for r in db.execute("PRAGMA table_info(runs)")}


def test_0020_run_worker_lease_up_down_and_up_again(tmp_path: Path) -> None:
    """0020: 실행 기록에 담당·생존 신호 열만 더하고 지운다. 실행·변경 기록·'결과 확인 필요' 행은 그대로이고, 실행 중인
    행이 있으면 내리지 않는다 (이전 판 정리가 그 행을 시각만으로 실패로 바꾸지 않게)."""
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert {"worker", "heartbeat_at"} <= _run_columns(db)
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
    db.execute(run, (3, "running", None, None, None, None))
    db.commit()
    db.close()
    try:
        _alembic(tmp_path, "downgrade", "0019_analysis_runs")
        raise AssertionError("실행 중인 행이 있는데 내려갔다")
    except subprocess.CalledProcessError as exc:
        assert "실행 중인 결과 실행이 1건" in exc.stderr.decode("utf-8", "replace")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0020_run_worker_lease"
    db.execute("DELETE FROM runs WHERE id = 3")
    db.commit()
    db.close()
    _alembic(tmp_path, "downgrade", "0019_analysis_runs")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert not {"worker", "heartbeat_at"} & _run_columns(db)
    assert db.execute("SELECT id, status, output_path FROM runs ORDER BY id").fetchall() == [
        (1, "done", "outputs/a.xlsx"),
        (2, "check", None),
    ]
    assert db.execute("SELECT run_id, kind FROM run_changes").fetchall() == [(1, "number")]
    db.close()
    _alembic(tmp_path, "upgrade", "head")
    db = sqlite3.connect(tmp_path / "moajwo.db")
    assert {"worker", "heartbeat_at"} <= _run_columns(db)
    assert db.execute("SELECT worker, heartbeat_at FROM runs WHERE id = 1").fetchone() == (None, None)
    db.close()
