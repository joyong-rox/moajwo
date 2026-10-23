"""SQLite → PostgreSQL 이관 (O-03, docs/EXTERNAL_DB.md 8장) — 계정·권한·관계 기록을 그대로 옮긴다.

사용법 (backend 폴더에서):
    python -m app.dbcopy --source <SQLite 주소> --target <PostgreSQL 직접 연결 주소> [--apply] [--report 파일]

- 기본은 확인만 한다 (dry-run): 원천 스냅숏의 리비전·무결성·표별 건수, 대상 리비전·건수, 대상이 비었는지,
  지원 범위.
- 원천 보호: 원천 파일은 읽기 전용으로만 연다. SQLite 백업 API(한 읽기 트랜잭션으로 모든 페이지 복사 — 한 시점)로
  임시 스냅숏을 만들고, 스냅숏의 무결성을 검사한 뒤 스냅숏에서 읽는다 (쓰기 중인 파일을 그대로 복사하지 않음).
  스냅숏 해시와 원천 파일 해시(앞뒤)를 보고한다. 원천 앱의 쓰기를 멈추는 일은 이 도구가 아니라 전환 절차의 단계다.
- 보고 파일: 원천 DB와 그 저널·WAL·SHM, 그리고 같은 파일을 가리키는 다른 경로(상대 경로·대소문자·링크)는 DB를 열기
  전에 거절한다. 이미 있는 파일은 이 도구의 보고서일 때만 바꾼다. 같은 폴더에 임시 파일을 먼저 만들어 쓸 수 있는지
  확인하고, 끝에 내용을 다 쓴 뒤 교체한다 (도중에 실패해도 이전 보고서가 반쯤 지워지지 않음).
- --apply: 트랜잭션 하나로 한다.
  이관 잠금(이 도구·마이그레이션끼리 하나씩) → 모든 표 ACCESS EXCLUSIVE 잠금(앱의 읽기·쓰기는 끝날 때까지 기다림)
  → 잠금 안에서 리비전·대상이 비었는지 다시 확인 → 복사 → 시퀀스 RESTART → 표별 건수·내용 해시·시퀀스 비교
  → 이관 표시 → 커밋. 시퀀스는 setval이 아니라 ALTER SEQUENCE … RESTART로 맞춘다 — setval은 rollback으로
  되돌아가지 않는다 (PostgreSQL 17에서 확인). 커밋 전 어느 단계에서 실패해도 rollback으로 표·시퀀스 모두 그대로다.
- 커밋 응답을 받지 못하면(연결 끊김) 되돌렸다고 단정하지 않는다: 새 연결로 이관 표시(app_settings
  'dbcopy_applied')와 내용 해시를 다시 읽어 '적용됨 / 적용 안 됨 / 확인 불가'로 판정한다. 확인 불가면 다시 실행하지
  말고 대상을 조회한다.
- 대상에 업무 자료가 하나라도 있으면 덮어쓰지 않고 멈춘다 (같은 이관을 두 번 실행해도 여기서 막힘 — 이관 표시가
  있으면 같은 원천인지 함께 알린다). 마이그레이션이 넣는 기본 자료(용어사전 기본 항목·설정 표시)는 원천 값으로 바꾼다.
- 지원 범위: 0009(실행 기록 시각) 이전에 만든 실행 기록이 있는 원천은 거절한다. 0009가 그 기록의 시작·종료 시각을
  생성 시각(UTC)으로 채웠는데 앱은 KST로 읽으므로 시간대 출처가 불명확하다 — 추정해서 고치지 않는다.
- 비밀번호는 해시 그대로 옮긴다 (평문을 알아내거나 초기화하지 않음). 로그인 세션(auth_sessions)은 옮기지 않는다
  (이관 뒤 다시 로그인).
- 시간: SQLite는 시간대 없이 저장한다. 통합 실행 시작·끝과 자동화 수정 시각은 KST 벽시계, 나머지는 UTC다
  (app/timefmt.py). 이 규칙으로 시간대를 붙여 옮겨 9시간 밀리지 않게 한다.
- 보고·오류에는 건수·해시·표 이름·오류 종류(SQLSTATE·제약 이름)만 적는다 (원문 값·SQL 매개변수·주소·비밀번호 없음).

종료 코드: 0 성공 (확인만, 또는 적용하고 보고 저장) · 1 적용하지 않음 (확인됨) · 3 적용했으나 보고 파일 저장 실패
          · 4 적용 여부를 확인하지 못함 (다시 실행하지 말고 대상을 조회)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Boolean, Connection, DateTime, Engine, Integer, Table, create_engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

from app import models  # noqa: F401  (메타데이터에 모델 등록)
from app.config import DB_CONNECT_TIMEOUT_S, MIGRATION_LOCK_KEY, _database
from app.db import Base
from app.models import AppSetting
from app.timefmt import KST

TOOL = "moajwo.dbcopy"
MARKER = "dbcopy_applied"  # app_settings 키: 이 이관이 커밋됐는지 새 연결로 다시 확인하는 표시 (내용 비교에서는 뺀다)
# SQLite에 KST 벽시계로 저장하는 열 (그 밖의 시각 열은 UTC)
KST_COLUMNS = {("runs", "started_at"), ("runs", "finished_at"), ("automations", "updated_at")}
SKIPPED = {"auth_sessions": "로그인 세션 — 옮기지 않음 (이관 뒤 다시 로그인, 계정은 그대로)"}
SEEDED = {"glossary_entries", "glossary_aliases", "app_settings"}  # 마이그레이션이 기본 자료를 넣는 표
BATCH = 1000
BACKEND_DIR = Path(__file__).resolve().parent.parent
EXIT_OK, EXIT_NOT_APPLIED, EXIT_REPORT_FAILED, EXIT_UNKNOWN = 0, 1, 3, 4
# 시험용 실패 지점 (명령줄에는 없음): 표 복사 뒤 · 첫 시퀀스 뒤 · 모든 시퀀스 뒤 · 내용 비교 · 커밋 직전 연결 끊김
# · 커밋 응답 유실
FAIL_POINTS = ("table:", "sequence:first", "sequence:all", "compare", "commit_lost_before", "commit_lost_after")


class CopyError(Exception):
    """적용하지 않고 멈춘 이유 (사람이 읽는 문구 — 원문 값 없음)"""


# ---------------------------------------------------------------- 경로·보고 파일


def source_path(url: str) -> Path:
    if not url.startswith("sqlite:///"):
        raise CopyError("원천은 SQLite 주소(sqlite:///경로)여야 합니다")
    raw = url.removeprefix("sqlite:///").split("?")[0].removeprefix("file:")
    if not raw or raw == ":memory:":
        raise CopyError("원천 SQLite 파일 경로가 없습니다")
    path = Path(raw)
    if not path.is_file():
        raise CopyError("원천 SQLite 파일이 없습니다")
    return path


def _same_file(a: Path, b: Path) -> bool:
    try:
        if os.path.samefile(a, b):  # 하드링크·심볼릭 링크·다른 표기의 같은 파일
            return True
    except OSError:  # 한쪽이 아직 없음 — 경로 자체를 비교한다
        pass
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def protected_paths(source: Path) -> list[Path]:
    """보고 파일로 쓰면 안 되는 경로: 원천 DB와 SQLite가 옆에 두는 파일 (주어진 경로와 실제 경로 모두)."""
    out: list[Path] = []
    for base in (source, Path(os.path.realpath(source))):
        out += [base, *(base.with_name(base.name + s) for s in ("-journal", "-wal", "-shm"))]
    return out


def _is_report(path: Path) -> bool:
    if path.stat().st_size > 5 * 1024 * 1024:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("tool") == TOOL


class ReportFile:
    """보고 파일. 열 때 경로를 검사하고 같은 폴더에 임시 파일을 만들어 둔다 (쓸 수 없으면 DB에 닿기 전에 멈춘다).
    저장은 임시 파일에 다 쓴 뒤 교체한다."""

    def __init__(self, path: Path, source: Path) -> None:
        if any(_same_file(path, p) for p in protected_paths(source)):
            raise CopyError("보고 파일 경로가 원천 DB(또는 그 저널·WAL·SHM)와 같은 파일입니다")
        if path.exists() and (not path.is_file() or not _is_report(path)):
            raise CopyError("보고 파일 경로에 이 도구의 보고서가 아닌 파일이 있습니다 — 덮어쓰지 않습니다")
        self.path = path
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        os.close(fd)
        self.tmp = Path(tmp)

    def save(self, body: str) -> None:
        with open(self.tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(self.tmp, self.path)

    def discard(self) -> None:
        try:
            self.tmp.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- 원천 스냅숏


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ro_uri(path: Path) -> str:
    return "file:" + quote(path.resolve().as_posix(), safe="/:") + "?mode=ro"


def _snapshot(source: Path, workdir: Path) -> tuple[Path, dict[str, Any]]:
    snap = workdir / "snapshot.db"
    src = sqlite3.connect(_ro_uri(source), uri=True)
    try:
        dst = sqlite3.connect(snap)
        try:
            src.backup(dst)  # 한 번에 모든 페이지 — 한 읽기 트랜잭션이라 한 시점의 내용
        finally:
            dst.close()
    finally:
        src.close()
    chk = sqlite3.connect(_ro_uri(snap), uri=True)
    try:
        result = [r[0] for r in chk.execute("PRAGMA integrity_check")]
    finally:
        chk.close()
    if result != ["ok"]:
        raise CopyError("원천 스냅숏 무결성 검사 실패 — 백업에서 복원한 사본으로 다시 하세요")
    return snap, {"sha256": _sha256(snap), "bytes": snap.stat().st_size, "integrity": "ok"}


# ---------------------------------------------------------------- 표 읽기·비교


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


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


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
    query = select(table).order_by(*table.primary_key)
    if table.name == "app_settings":
        query = query.where(table.c.key != MARKER)  # 이관 표시는 원천에 없는 행
    return [_convert(table, dict(r._mapping)) for r in conn.execute(query)]


def _count(conn: Connection, name: str) -> int:
    return conn.execute(text(f"SELECT count(*) FROM {_q(name)}")).scalar() or 0


def _legacy_runs(conn: Connection) -> int:
    """0009 이전 실행 기록 수: 0009가 시작·종료 시각을 생성 시각(UTC)으로 채운 행은 시작 = 생성 시각이다
    (그 뒤 기록은 시작이 KST 벽시계, 생성이 UTC라 같아지지 않는다)."""
    return conn.execute(
        text("SELECT count(*) FROM runs WHERE started_at IS NOT NULL AND started_at = created_at")
    ).scalar() or 0  # fmt: skip


def _marker(conn: Connection) -> dict[str, Any] | None:
    raw = conn.execute(select(AppSetting.value).where(AppSetting.key == MARKER)).scalar()
    return json.loads(raw) if raw else None


def _busy(conn: Connection) -> list[str]:
    """대상에 있는 업무 자료 표 (기본 자료 표 제외, 옮기지 않는 세션 표 포함)."""
    names = [t.name for t in _tables() if t.name not in SEEDED] + list(SKIPPED)
    return [n for n in names if _count(conn, n)]


def _serial_columns(tables: list[Table]) -> list[tuple[Table, Any]]:
    out = []
    for t in tables:
        pk = list(t.primary_key.columns)
        if len(pk) == 1 and isinstance(pk[0].type, Integer):
            out.append((t, pk[0]))
    return out


def _sequence_state(conn: Connection, seq: str) -> tuple[int, bool]:
    last, called = conn.execute(text(f"SELECT last_value, is_called FROM {seq}")).one()  # seq: 카탈로그가 준 이름
    return int(last), bool(called)


def _target_digests(conn: Connection) -> dict[str, str]:
    return {t.name: _digest(_read(conn, t), t) for t in _tables()}


# ---------------------------------------------------------------- 이관


def _check_fail_point(fail_at: str | None) -> None:
    if fail_at is not None and not fail_at.startswith(FAIL_POINTS):
        raise ValueError(f"알 수 없는 시험용 실패 지점: {fail_at}")


def copy(
    source: str,
    target: str,
    apply: bool = False,
    *,
    fail_at: str | None = None,
    lock_timeout_s: int = 30,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """확인(기본) 또는 적용. report에 단계마다 결과를 채운다 (예외로 끝나도 남은 값을 보고에 쓸 수 있게).
    fail_at: 시험용 — 그 지점에서 일부러 실패시켜 되돌림·커밋 불확실성 처리를 확인한다 (FAIL_POINTS)."""
    _check_fail_point(fail_at)
    started = time.monotonic()
    report = {} if report is None else report
    report.update({"tool": TOOL, "run_id": uuid.uuid4().hex, "apply": apply, "db_applied": "no", "skipped": SKIPPED,
                   "kst_columns": sorted(".".join(c) for c in KST_COLUMNS)})  # fmt: skip
    report["stage"] = "준비"
    path = source_path(source)
    _, target_url = _database(target, "external")
    work = Path(tempfile.mkdtemp(prefix="moajwo-dbcopy-"))
    report["source"] = {"sha256_before": _sha256(path)}
    try:
        report["stage"] = "원천 스냅숏"
        snap, report["snapshot"] = _snapshot(path, work)
        src = create_engine("sqlite://", creator=lambda: sqlite3.connect(_ro_uri(snap), uri=True), hide_parameters=True)
        connect = {"connect_timeout": DB_CONNECT_TIMEOUT_S, "application_name": TOOL}
        dst = create_engine(target_url, poolclass=NullPool, hide_parameters=True, connect_args=connect)
        try:
            _copy(src, dst, report, apply, fail_at, lock_timeout_s)
        finally:
            src.dispose()
            dst.dispose()
    finally:
        shutil.rmtree(work, ignore_errors=True)  # 스냅숏(계정·업무 자료)을 남기지 않는다
        after = _sha256(path)
        report["source"].update({"sha256_after": after, "unchanged": after == report["source"]["sha256_before"]})
        report["seconds"] = round(time.monotonic() - started, 2)
    return report


def _copy(src: Engine, dst: Engine, report: dict[str, Any], apply: bool, fail_at: str | None, lock_s: int) -> None:
    head, tables = _head(), _tables()
    with src.connect() as s, dst.connect() as d:
        report["stage"] = "확인"
        report["revision"] = {"head": head, "source": _revision(s), "target": _revision(d)}
        if report["revision"]["source"] != head or report["revision"]["target"] != head:
            raise CopyError(
                "원천·대상 리비전이 최신(head)이 아닙니다 — 둘 다 'alembic upgrade head'로 맞춘 뒤 다시 하세요"
            )
        report["legacy_0009_runs"] = legacy = _legacy_runs(s)
        if legacy:
            raise CopyError(
                f"원천에 0009 이전 실행 기록이 {legacy}건 있습니다 — 시작·종료 시각의 시간대 출처가 불명확해 이 도구의 "
                "지원 범위 밖입니다 (추정해서 고치지 않음)"
            )
        counts = {t.name: {"source": _count(s, t.name), "target_before": _count(d, t.name)} for t in tables}
        report["counts"] = counts
        _refuse_if_busy(d, report)
        if not apply:
            report["result"] = "dry-run — 쓰지 않음"
            return
        d.rollback()  # 위 확인 조회가 연 트랜잭션을 닫고, 쓰기는 새 트랜잭션 하나로 한다
        pid = d.execute(text("SELECT pg_backend_pid()")).scalar()
        d.rollback()
        tx = d.begin()
        try:
            report["stage"] = "잠금"
            d.execute(text(f"SET LOCAL lock_timeout = '{int(lock_s)}s'"))  # 잠금을 오래 기다리지 않고 멈춘다
            d.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
            names = [t.name for t in Base.metadata.sorted_tables] + ["alembic_version"]
            d.execute(text(f"LOCK TABLE {', '.join(_q(n) for n in names)} IN ACCESS EXCLUSIVE MODE"))
            # 잠금 안에서 다시 확인 — 앞의 확인과 복사 사이에 다른 이관·가입·최초 설정이 끼어들지 않았는지
            if _revision(d) != head:
                raise CopyError("잠금 뒤 대상 리비전이 바뀌었습니다 — 멈춥니다")
            _refuse_if_busy(d, report)
            report["stage"] = "복사"
            for t in reversed(tables):  # 기본 자료를 원천 값으로 바꾸기 전에 비운다 (참조하는 표 먼저)
                if t.name in SEEDED:
                    d.execute(t.delete())
            digests: dict[str, dict[str, str]] = {}
            for t in tables:
                rows = _read(s, t)
                for i in range(0, len(rows), BATCH):
                    d.execute(t.insert(), rows[i : i + BATCH])
                digests[t.name] = {"source": _digest(rows, t)}
                if fail_at == f"table:{t.name}":
                    raise CopyError(f"시험용 실패 ({t.name} 복사 뒤)")
            report["stage"] = "시퀀스"
            sequences: dict[str, dict[str, Any]] = {}
            for t, col in _serial_columns(tables):
                seq = d.execute(
                    text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": _q(t.name), "c": col.name}
                ).scalar()
                if seq is None:
                    continue
                nxt = (d.execute(select(func.max(col))).scalar() or 0) + 1
                d.execute(text(f"ALTER SEQUENCE {seq} RESTART WITH {int(nxt)}"))  # 트랜잭션과 함께 되돌려진다
                sequences[t.name] = {"sequence": seq, "next": nxt}
                if fail_at == "sequence:first":
                    raise CopyError(f"시험용 실패 (첫 시퀀스 {t.name} 뒤)")
            if fail_at == "sequence:all":
                raise CopyError("시험용 실패 (모든 시퀀스 뒤)")
            report["sequences"] = sequences
            report["stage"] = "비교"
            if fail_at == "compare":  # 복사한 자료 한 행을 바꿔 내용 비교가 잡는지 본다
                d.execute(text("UPDATE users SET display_name = display_name || '*' "
                               "WHERE id = (SELECT min(id) FROM users)"))  # fmt: skip
            bad = []
            for t in tables:
                after = _read(d, t)
                digests[t.name]["target"] = _digest(after, t)
                counts[t.name]["target_after"] = len(after)
                if len(after) != counts[t.name]["source"] or digests[t.name]["source"] != digests[t.name]["target"]:
                    bad.append(t.name)
            for name, info in sequences.items():
                if _sequence_state(d, info["sequence"]) != (info["next"], False):
                    bad.append(f"{name} 시퀀스")
            report["digests"] = digests
            if bad:
                raise CopyError(f"복사 뒤 건수·내용·시퀀스가 원천과 다릅니다 ({', '.join(bad)}) — 되돌렸습니다")
            value = {
                "run_id": report["run_id"],
                "snapshot_sha256": report["snapshot"]["sha256"],
                "rows": sum(c["source"] for c in counts.values()),
                "at": datetime.now(UTC).isoformat(),
            }
            d.execute(AppSetting.__table__.insert(), {"key": MARKER, "value": json.dumps(value)})
        except BaseException:  # 커밋을 보내기 전 — 되돌리면(또는 연결이 끊겼으면 서버가 버리면) 대상은 그대로
            _rollback(tx)
            raise
        report["stage"] = "커밋"
        try:
            if fail_at == "commit_lost_before":  # 커밋을 보내기 직전에 서버가 이 연결을 끊은 경우
                with dst.connect() as other:
                    other.execute(text("SELECT pg_terminate_backend(:p, 5000)"), {"p": pid})
                    other.commit()
            tx.commit()
            if fail_at == "commit_lost_after":  # 커밋은 됐는데 응답을 받지 못한 경우
                raise OperationalError("COMMIT", None, ConnectionError("시험용 — 커밋 응답 유실"))
        except Exception as exc:  # 커밋 결과를 모른다 — 되돌렸다고 단정하지 않고 새 연결로 판정한다
            report["db_applied"] = "unknown"
            report["commit_error"] = safe_error(exc)
            _settle(dst, report, lock_s)
            return
    report["db_applied"] = "yes"
    report["result"] = f"적용함 — 표 {len(tables)}개, 행 {sum(c['source'] for c in counts.values()):,}개"
    _verify_after_commit(dst, report, lock_s)


def _rollback(tx: Any) -> None:
    try:
        tx.rollback()
    except Exception:  # 연결이 이미 끊겼으면 서버가 커밋 전 트랜잭션을 버린다 — 적용되지 않음
        pass


def _refuse_if_busy(conn: Connection, report: dict[str, Any]) -> None:
    marker = _marker(conn)
    if marker is not None:
        same = marker.get("snapshot_sha256") == report.get("snapshot", {}).get("sha256")
        run, answer = str(marker.get("run_id"))[:8], "예" if same else "아니오"
        raise CopyError(
            f"대상에 이미 이관 표시가 있습니다 (실행 {run}, 같은 원천 스냅숏: {answer}) — 다시 적용하지 않습니다"
        )
    busy = _busy(conn)
    if busy:
        names = ", ".join(busy)
        raise CopyError(f"대상에 이미 자료가 있습니다 ({names}) — 덮어쓰지 않습니다 (이미 이관했거나 다른 DB)")


def _reader(dst: Engine, lock_s: int) -> Connection:
    """다시 읽기용 새 연결. 앞 트랜잭션이 서버에서 아직 끝나지 않아 표 잠금이 남아 있으면 오래 기다리지 않는다."""
    d = dst.connect()
    d.execute(text(f"SET lock_timeout = '{int(lock_s)}s'"))
    return d


def _settle(dst: Engine, report: dict[str, Any], lock_s: int) -> None:
    """커밋 결과를 모를 때: 새 연결로 이 실행의 이관 표시와 내용을 읽어 적용 여부를 정한다."""
    try:
        with _reader(dst, lock_s) as d:
            marker = _marker(d)
            if marker and marker.get("run_id") == report["run_id"]:
                same = _target_digests(d) == {n: v["source"] for n, v in report["digests"].items()}
                report["db_applied"] = "yes" if same else "unknown"
                report["result"] = (
                    "적용함 — 커밋 응답은 받지 못했으나 새 연결로 이관 표시·내용 해시를 확인함" if same
                    else "확인 불가 — 이관 표시는 있으나 내용이 원천과 다름 (다시 실행하지 말고 대상을 조회)"
                )  # fmt: skip
            elif marker is None and not _busy(d):
                report["db_applied"] = "no"
                report["result"] = "적용 안 됨 — 커밋되지 않았음을 새 연결로 확인함 (대상 비어 있음, 다시 실행해도 됨)"
            else:
                report["result"] = "확인 불가 — 이관 표시가 없는데 대상에 자료가 있음 (다시 실행하지 말고 대상을 조회)"
    except Exception as exc:  # 다시 읽기도 실패 — 판정하지 않는다
        report["result"] = f"확인 불가 — 다시 읽지 못함 ({safe_error(exc)}). 다시 실행하지 말고 대상을 조회"
    if report["db_applied"] != "yes":
        raise CopyError(report["result"])
    _verify_after_commit(dst, report, lock_s)


def _verify_after_commit(dst: Engine, report: dict[str, Any], lock_s: int) -> None:
    """커밋 뒤 새 연결로 다시 읽어 내용 해시·시퀀스가 커밋 전 비교와 같은지 남긴다."""
    try:
        with _reader(dst, lock_s) as d:
            same = _target_digests(d) == {n: v["source"] for n, v in report["digests"].items()}
            seqs = all(_sequence_state(d, v["sequence"]) == (v["next"], False) for v in report["sequences"].values())
    except Exception as exc:
        report["after_commit"] = {"verified": False, "error": safe_error(exc)}
        raise CopyError("적용함 — 커밋 뒤 다시 읽기에 실패 (다시 실행하지 말고 대상을 조회)") from exc
    report["after_commit"] = {"verified": same and seqs, "digests": same, "sequences": seqs}
    if not (same and seqs):
        raise CopyError("적용함 — 커밋 뒤 다시 읽은 내용·시퀀스가 커밋 전 비교와 다름 (다시 실행하지 말고 대상을 조회)")


# ---------------------------------------------------------------- 명령줄


def safe_error(exc: BaseException) -> str:
    """오류를 종류·SQLSTATE·제약 이름으로만 적는다 — SQL·매개변수·원문 값·주소·비밀번호를 남기지 않는다."""
    if isinstance(exc, CopyError):
        return str(exc)
    parts = [type(exc).__name__]
    orig = getattr(exc, "orig", None)
    if orig is not None:
        parts.append(type(orig).__name__)
        if getattr(orig, "sqlstate", None):
            parts.append(f"SQLSTATE {orig.sqlstate}")
        constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
        if constraint:
            parts.append(f"제약 {constraint}")
    elif isinstance(exc, OSError) and exc.strerror:
        parts.append(exc.strerror)  # 파일 이름 없이 이유만
    return " · ".join(parts)


def _dump(report: dict[str, Any], ascii_only: bool = False) -> str:
    return json.dumps(report, ensure_ascii=ascii_only, indent=1, default=str)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.dbcopy", description=__doc__.splitlines()[0])
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report")
    ap.add_argument("--lock-timeout-s", type=int, default=30, help="잠금 대기 최대 시간 (초)")
    a = ap.parse_args(argv)
    report: dict[str, Any] = {"tool": TOOL}
    sink = None
    try:  # 보고 파일 경로부터 확인한다 — DB에 닿기 전에 (원천을 덮어쓰거나, 이관을 커밋한 뒤 보고만 못 쓰는 일이 없게)
        path = source_path(a.source)
        sink = ReportFile(Path(a.report), path) if a.report else None
    except CopyError as exc:
        return _emit({**report, "db_applied": "no", "error": f"{exc} — 이관하지 않았습니다"}, None, EXIT_NOT_APPLIED)
    except OSError as exc:
        error = f"보고 파일을 쓸 수 없습니다 ({safe_error(exc)}) — 이관하지 않았습니다"
        return _emit({**report, "db_applied": "no", "error": error}, None, EXIT_NOT_APPLIED)
    try:
        copy(a.source, a.target, apply=a.apply, lock_timeout_s=a.lock_timeout_s, report=report)
        code = EXIT_OK
    except Exception as exc:  # 알 수 없는 오류도 종류만 적는다
        stage = report.get("stage", "준비")
        report["error"] = safe_error(exc) if isinstance(exc, CopyError) else f"[{stage}] {safe_error(exc)}"
        code = EXIT_UNKNOWN if report.get("db_applied") in ("yes", "unknown") else EXIT_NOT_APPLIED
    return _emit(report, sink, code)


def _emit(report: dict[str, Any], sink: ReportFile | None, code: int) -> int:
    if sink is not None:
        try:
            sink.save(_dump(report))
        except OSError as exc:
            sink.discard()
            report["report_saved"] = False
            report["report_error"] = safe_error(exc)
            if report.get("db_applied") == "yes" and code == EXIT_OK:
                code = EXIT_REPORT_FAILED
                report["note"] = "DB에는 적용됐습니다 (커밋 확인) — 다시 실행하지 마세요. 이 출력이 보고입니다."
            elif code == EXIT_OK:
                code = EXIT_NOT_APPLIED
    # 콘솔이 UTF-8이 아니면(Windows 파이프 cp949 등) ASCII로 이스케이프한다 — 출력 단계에서 죽지 않게
    # (JSON은 그대로 유효)
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "")
    print(_dump(report, ascii_only=encoding != "utf8"))
    return code


if __name__ == "__main__":
    sys.exit(main())
