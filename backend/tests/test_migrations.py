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
