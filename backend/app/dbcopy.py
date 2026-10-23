"""SQLite → PostgreSQL 이관 (O-03, docs/EXTERNAL_DB.md '이관') — 계정·권한·관계 기록을 그대로 옮긴다.

사용법 (backend 폴더에서):
    python -m app.dbcopy --source <SQLite 주소> --target <PostgreSQL 직접 연결 주소> [--apply] [--report 파일]

- 기본은 확인만 한다 (dry-run): 두 DB의 리비전, 원천 무결성, 표별 건수, 대상이 비었는지, 시간대 변환 규칙.
- --apply: 한 트랜잭션으로 복사 → 시퀀스 재설정 → 표별 건수·내용 해시 비교 → 모두 같을 때만 커밋한다.
  어느 단계에서든 실패하면 되돌려 대상은 그대로다 (중간 실패 복구).
- 대상에 업무 자료(계정·파일·검토·실행 등)가 하나라도 있으면 덮어쓰지 않고 멈춘다 — 같은 이관을 두 번 실행하는 것도
  여기서 막힌다. 마이그레이션이 넣는 기본 자료(용어사전 기본 항목·설정 표시)는 원천 값으로 바꾼다.
- 원천은 읽기 전용(mode=ro)으로 연다. 비밀번호는 해시 그대로 옮긴다 (평문을 알아내거나 초기화하지 않음).
- 로그인 세션(auth_sessions)은 옮기지 않는다 — 이관 뒤에는 다시 로그인한다 (계정은 그대로).
- 시간: SQLite는 시간대 없이 저장한다. 통합 실행 시작·끝과 자동화 수정 시각은 KST 벽시계, 나머지는 UTC다
  (app/timefmt.py). 이 규칙으로 시간대를 붙여 옮겨 9시간 밀리지 않게 한다.
- 보고에는 건수·해시·표 이름만 적는다 (원문 값·주소·비밀번호 없음).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Boolean, Connection, DateTime, Integer, Table, create_engine, func, select, text

from app import models  # noqa: F401  (메타데이터에 모델 등록)
from app.config import _database
from app.db import Base
from app.timefmt import KST

# SQLite에 KST 벽시계로 저장하는 열 (그 밖의 시각 열은 UTC)
KST_COLUMNS = {("runs", "started_at"), ("runs", "finished_at"), ("automations", "updated_at")}
SKIPPED = {"auth_sessions": "로그인 세션 — 옮기지 않음 (이관 뒤 다시 로그인, 계정은 그대로)"}
SEEDED = {"glossary_entries", "glossary_aliases", "app_settings"}  # 마이그레이션이 기본 자료를 넣는 표
BATCH = 1000
BACKEND_DIR = Path(__file__).resolve().parent.parent


class CopyError(Exception):
    pass


def _source_url(url: str) -> str:
    if not url.startswith("sqlite:///"):
        raise CopyError("원천은 SQLite 주소(sqlite:///경로)여야 합니다")
    path = url.removeprefix("sqlite:///").split("?")[0]
    return f"sqlite:///file:{path}?mode=ro&uri=true"  # 읽기 전용


def _head() -> str:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _revision(conn: Connection) -> str | None:
    try:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        return None


def _tables() -> list[Table]:
    return [t for t in Base.metadata.sorted_tables if t.name not in SKIPPED]  # 외래 키 순서 (참조되는 표 먼저)


def _convert(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for col in table.columns:
        value = out.get(col.name)
        if isinstance(col.type, DateTime) and isinstance(value, datetime) and value.tzinfo is None:
            out[col.name] = value.replace(tzinfo=KST if (table.name, col.name) in KST_COLUMNS else UTC)
        elif isinstance(col.type, Boolean) and isinstance(value, int) and not isinstance(value, bool):
            out[col.name] = bool(value)
    return out


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal | float):
        return repr(float(value))
    return value


def _digest(rows: list[dict[str, Any]], table: Table) -> str:
    keys = [c.name for c in table.columns]
    ordered = sorted(rows, key=lambda r: json.dumps([_canonical(r.get(k)) for k in table.primary_key.columns.keys()]))
    h = hashlib.sha256()
    for r in ordered:
        h.update(json.dumps([_canonical(r.get(k)) for k in keys], ensure_ascii=False).encode())
    return h.hexdigest()


def _read(conn: Connection, table: Table) -> list[dict[str, Any]]:
    return [_convert(table, dict(r._mapping)) for r in conn.execute(select(table).order_by(*table.primary_key))]


def copy(source: str, target: str, apply: bool = False, fail_after: str | None = None) -> dict[str, Any]:
    """fail_after: 시험용 — 그 표를 복사한 뒤 일부러 실패시켜 되돌림을 확인한다."""
    started = time.monotonic()
    _, target_url = _database(target, "external")
    report: dict[str, Any] = {"apply": apply, "skipped": SKIPPED}
    report["kst_columns"] = sorted(".".join(c) for c in KST_COLUMNS)
    src = create_engine(_source_url(source))
    dst = create_engine(target_url)
    head = _head()
    with src.connect() as s, dst.connect() as d:
        report["revision"] = {"head": head, "source": _revision(s), "target": _revision(d)}
        if report["revision"]["source"] != head or report["revision"]["target"] != head:
            raise CopyError(
                "원천·대상 리비전이 최신(head)이 아닙니다 — 둘 다 'alembic upgrade head'로 맞춘 뒤 다시 하세요"
            )
        check = s.execute(text("PRAGMA integrity_check")).scalar()
        if check != "ok":
            raise CopyError("원천 SQLite 무결성 검사 실패 — 백업에서 복원한 사본으로 다시 하세요")
        counts: dict[str, dict[str, int]] = {}
        for t in _tables():
            counts[t.name] = {
                "source": s.execute(select(func.count()).select_from(t)).scalar() or 0,
                "target_before": d.execute(select(func.count()).select_from(t)).scalar() or 0,
            }
        report["counts"] = counts
        busy = [n for n, c in counts.items() if c["target_before"] and n not in SEEDED]
        busy += [n for n in SKIPPED if (d.execute(text(f'SELECT count(*) FROM "{n}"')).scalar() or 0)]
        if busy:
            raise CopyError(
                f"대상에 이미 자료가 있습니다 ({', '.join(busy)}) — 덮어쓰지 않습니다 (이미 이관했거나 다른 DB)"
            )
        if not apply:
            report["result"] = "dry-run — 쓰지 않음"
            return report
        d.rollback()  # 위 확인 조회가 연 트랜잭션을 닫고, 쓰기는 새 트랜잭션 하나로 한다
        tx = d.begin()
        try:
            digests: dict[str, dict[str, str]] = {}
            for t in reversed(_tables()):  # 기본 자료를 원천 값으로 바꾸기 전에 비운다 (참조하는 표 먼저)
                if t.name in SEEDED:
                    d.execute(t.delete())
            for t in _tables():
                rows = _read(s, t)
                for i in range(0, len(rows), BATCH):
                    d.execute(t.insert(), rows[i : i + BATCH])
                if fail_after == t.name:
                    raise CopyError(f"시험용 실패 ({t.name} 복사 뒤)")
                digests[t.name] = {"source": _digest(rows, t)}
            for t in _tables():  # 직접 넣은 번호 다음부터 새 번호가 나오게
                pk = list(t.primary_key.columns)
                if len(pk) == 1 and isinstance(pk[0].type, Integer):
                    d.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('\"{t.name}\"', '{pk[0].name}'), "
                        f"COALESCE((SELECT MAX(\"{pk[0].name}\") FROM \"{t.name}\"), 1), "
                        f"(SELECT MAX(\"{pk[0].name}\") FROM \"{t.name}\") IS NOT NULL)"
                    ))  # fmt: skip
            bad = []
            for t in _tables():
                after = _read(d, t)
                digests[t.name]["target"] = _digest(after, t)
                counts[t.name]["target_after"] = len(after)
                if len(after) != counts[t.name]["source"] or digests[t.name]["source"] != digests[t.name]["target"]:
                    bad.append(t.name)
            report["digests"] = digests
            if bad:
                raise CopyError(f"복사 뒤 건수·내용이 원천과 다릅니다 ({', '.join(bad)}) — 되돌렸습니다")
            tx.commit()
        except BaseException:
            tx.rollback()
            raise
        report["result"] = f"적용함 — 표 {len(_tables())}개, 행 {sum(c['source'] for c in counts.values()):,}개"
    report["seconds"] = round(time.monotonic() - started, 2)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.dbcopy", description=__doc__.splitlines()[0])
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    try:
        report = copy(a.source, a.target, apply=a.apply)
        code = 0
    except (CopyError, ValueError) as exc:
        report, code = {"error": str(exc)}, 1
    text_out = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(text_out)
    print(text_out)
    return code


if __name__ == "__main__":
    sys.exit(main())
