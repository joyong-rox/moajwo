"""앱 주소와 마이그레이션 주소가 같은 DB에 닿는지 확인한다 (읽기 전용, docs/EXTERNAL_DB.md 8-3).

외부 DB 모드에서 앱(DATABASE_URL)은 연결 풀러·앱 계정으로, 마이그레이션과 이관(MOAJWO_MIGRATION_DATABASE_URL,
app.dbcopy)은 직접 연결·소유 계정으로 붙는다. 두 주소가 다른 DB(다른 프로젝트·분기·DB 이름)를 가리키면
마이그레이션·이관이 성공해도 앱은 빈 DB를 읽는다 — 전환 전에 같은 DB인지 확인한다.

사용법 (backend 폴더에서):
    python -m app.dbcheck [--app-env DATABASE_URL | --app-service 이름]
                          [--migration-env MOAJWO_MIGRATION_DATABASE_URL | --migration-service 이름]

- 주소는 환경 변수, 또는 libpq 서비스 이름(postgresql:///?service=이름 — 비밀번호는 비밀번호 파일)으로만 받는다.
  명령 인자로 주소를 받지 않는다. 출력(JSON)에는 주소·호스트·비밀번호를 넣지 않는다 (계정 이름은 넣는다).
- 두 연결에서 각각 읽어 비교한다: current_database(), DB OID, pg_postmaster_start_time(), server_version_num,
  alembic_version, Neon 식별자(neon.tenant_id·neon.timeline_id — 설정이 있을 때만 값이 있음).
  읽기 전용 트랜잭션(SET TRANSACTION READ ONLY)에서 조회만 하고 되돌린다 — 쓰지 않는다.
- 어느 한쪽에서 읽지 못한 항목(권한 등)은 비교에서 빼고 not_compared에 적는다.
- 서버가 그 사이에 다시 시작되면 pg_postmaster_start_time()이 바뀐다 — 그 값만 다르면 다시 실행한다.
- 연결 풀러(PgBouncer·Neon 풀러)를 거친 동작은 로컬에서 확인하지 않았다 (로컬 시험은 PostgreSQL 직접 연결).

종료 코드: 0 같은 DB · 1 다른 DB · 2 확인하지 못함 (주소 없음·해석 실패·연결 실패)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC
from typing import Any
from urllib.parse import quote

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

TOOL = "moajwo.dbcheck"
EXIT_SAME, EXIT_DIFFERENT, EXIT_UNKNOWN = 0, 1, 2


def _seconds(name: str, default: int) -> int | None:
    """정수가 아니면 None — 가져올 때 실패하면 파이썬 일반 실패(종료 1)가 '다른 DB'(1)와 섞이므로 main이 2로 끝낸다."""
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return None


# 접속 제한 시간 — 휴면에서 깨는 시간 포함 (app.config의 MOAJWO_DB_CONNECT_TIMEOUT_S와 같은 값)
CONNECT_TIMEOUT_S = _seconds("MOAJWO_DB_CONNECT_TIMEOUT_S", 15)
# 이관·마이그레이션이 표를 잠근 동안 끝없이 기다리지 않는다 (트랜잭션 범위 — 트랜잭션 모드 풀러에서도 안전)
LOCK_TIMEOUT = "5s"
# 이름 자리에 주소를 넣는 실수(--app-env "$DATABASE_URL")를 값을 되풀이하지 않고 거절한다
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SERVICE_NAME = re.compile(r"[A-Za-z0-9_.-]+")
# 비교하는 항목 (같은 DB면 모두 같아야 한다)
COMPARED = (
    "database",
    "database_oid",
    "postmaster_start_time",
    "server_version_num",
    "alembic_version",
    "neon_tenant_id",
    "neon_timeline_id",
)
CORE = text(
    "SELECT current_database() AS database,"
    " (SELECT oid FROM pg_database WHERE datname = current_database()) AS database_oid,"
    " pg_postmaster_start_time() AS postmaster_start_time,"
    " current_setting('server_version_num')::int AS server_version_num,"
    " current_user AS user_name"
)
NEON = {"neon_tenant_id": "neon.tenant_id", "neon_timeline_id": "neon.timeline_id"}


class CheckError(Exception):
    """확인하지 못한 이유 (주소·비밀번호 없는 문구)"""


def safe_error(exc: BaseException) -> str:
    """오류는 종류·SQLSTATE만 — 주소·호스트·비밀번호가 든 원문 메시지를 내보내지 않는다."""
    if isinstance(exc, CheckError):
        return str(exc)
    parts = [type(exc).__name__]
    orig = getattr(exc, "orig", None)
    if orig is not None:
        parts.append(type(orig).__name__)
        if getattr(orig, "sqlstate", None):
            parts.append(f"SQLSTATE {orig.sqlstate}")
    return " · ".join(parts)


def resolve(env_name: str | None, service: str | None, default_env: str) -> tuple[str, str]:
    """(SQLAlchemy 주소, 어디서 받았는지). 서비스 이름이면 비밀번호 없는 서비스 주소를 만든다."""
    if service:
        if not SERVICE_NAME.fullmatch(service):
            raise CheckError(
                "서비스 이름 자리에 이름이 아닌 값이 있습니다 — 주소가 아니라 서비스 이름(영문·숫자·_ . -)을 넣으세요"
            )
        raw, source = f"postgresql:///?service={quote(service, safe='')}", f"service:{service}"
    else:
        name = env_name or default_env
        if not ENV_NAME.fullmatch(name):
            raise CheckError("환경 변수 이름 자리에 이름이 아닌 값이 있습니다 — 주소가 아니라 변수 이름을 넣으세요")
        raw, source = os.environ.get(name, "").strip(), f"env:{name}"
        if not raw:
            raise CheckError(f"환경 변수 {name}에 주소가 없습니다")
    try:
        url = make_url(raw)
    except Exception as exc:  # 주소를 오류 문구에 남기지 않는다
        raise CheckError(f"{source}의 주소를 해석하지 못했습니다 (형식 확인)") from exc
    if url.get_backend_name() not in ("postgresql", "postgres"):
        raise CheckError(f"{source}의 주소가 PostgreSQL이 아닙니다")
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False), source


def _optional(conn: Connection, sql: str) -> tuple[bool, Any]:
    """(읽었는지, 값). 권한이 없으면(42501) 앞의 조회를 깨지 않게 저장점 안에서 '읽지 못함'으로 둔다. 그 밖의 오류
    (잠금 대기 제한 55P03 등)는 삼키지 않는다 — 읽지 못한 항목을 빼고 같음으로 답하지 않게 확인 못 함(2)으로."""
    try:
        with conn.begin_nested():
            return True, conn.execute(text(sql)).scalar()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "42501":
            raise
        return False, None


def probe(url: str) -> dict[str, Any]:
    """한 주소로 붙어 비교 항목을 읽는다 (읽기 전용 트랜잭션, 끝에 되돌림)."""
    connect = {"connect_timeout": CONNECT_TIMEOUT_S, "application_name": TOOL, "prepare_threshold": None}
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args=connect)
    try:
        with engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
            row = dict(conn.execute(CORE).mappings().one())
            info: dict[str, Any] = {"user": row.pop("user_name"), **row}
            # 세션 시간대가 계정마다 달라도 같은 순간이면 같은 글자가 되게 UTC로 맞춘다
            info["postmaster_start_time"] = info["postmaster_start_time"].astimezone(UTC).isoformat()
            unreadable = []
            ok, exists = _optional(conn, "SELECT to_regclass('alembic_version') IS NOT NULL")
            info["alembic_version"] = None  # 표 없음 (마이그레이션 전)
            if ok and exists:
                ok, info["alembic_version"] = _optional(
                    conn, "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version"
                )
            if not ok:
                unreadable.append("alembic_version")
            for key, name in NEON.items():
                ok, info[key] = _optional(conn, f"SELECT current_setting('{name}', true)")
                if not ok:
                    unreadable.append(key)
            info["unreadable"] = unreadable
            conn.rollback()
    finally:
        engine.dispose()
    return info


def compare(app: dict[str, Any], migration: dict[str, Any]) -> dict[str, Any]:
    """같은 DB인지: 양쪽에서 읽은 비교 항목이 모두 같아야 한다. 한쪽이라도 읽지 못한 항목은 빼고 알린다."""
    skipped = sorted(set(app.get("unreadable", [])) | set(migration.get("unreadable", [])))
    differences = [k for k in COMPARED if k not in skipped and app.get(k) != migration.get(k)]
    same = not differences
    if same:
        message = "같은 DB입니다 — 두 주소가 같은 서버의 같은 데이터베이스를 가리킵니다"
    else:
        message = (
            f"다른 DB입니다 (다른 항목: {', '.join(differences)}) — 전환하지 말고 두 주소(프로젝트·분기·DB 이름)를 "
            "다시 확인하세요"
        )
        if differences == ["postmaster_start_time"]:
            message += ". 서버 시작 시각만 다르면 그 사이 서버가 다시 시작됐을 수 있으니 한 번 더 실행하세요"
    return {
        "result": "same" if same else "different",
        "message": message,
        "compared": [k for k in COMPARED if k not in skipped],
        "differences": differences,
        "not_compared": skipped,
    }


def _emit(report: dict[str, Any], code: int) -> int:
    # 콘솔이 UTF-8이 아니면(Windows 파이프 cp949 등) ASCII로 이스케이프한다 — 출력 단계에서 죽지 않게
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "")
    print(json.dumps(report, ensure_ascii=encoding != "utf8", indent=1, default=str))
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.dbcheck", description=__doc__.splitlines()[0])
    app_side = ap.add_mutually_exclusive_group()
    app_side.add_argument("--app-env", metavar="NAME", help="앱 주소를 읽을 환경 변수 (기본 DATABASE_URL)")
    app_side.add_argument("--app-service", metavar="NAME", help="앱 쪽 libpq 서비스 이름")
    mig_side = ap.add_mutually_exclusive_group()
    mig_side.add_argument(
        "--migration-env",
        metavar="NAME",
        help="마이그레이션 주소를 읽을 환경 변수 (기본 MOAJWO_MIGRATION_DATABASE_URL)",
    )
    mig_side.add_argument("--migration-service", metavar="NAME", help="마이그레이션 쪽 libpq 서비스 이름")
    a = ap.parse_args(argv)
    report: dict[str, Any] = {"tool": TOOL}
    if CONNECT_TIMEOUT_S is None:
        report.update({"result": "unknown", "error": "MOAJWO_DB_CONNECT_TIMEOUT_S가 정수(초)가 아닙니다"})
        report["message"] = f"확인하지 못했습니다 — {report['error']}"
        return _emit(report, EXIT_UNKNOWN)
    sides = (
        ("app", a.app_env, a.app_service, "DATABASE_URL"),
        ("migration", a.migration_env, a.migration_service, "MOAJWO_MIGRATION_DATABASE_URL"),
    )
    for side, env_name, service, default in sides:
        try:
            url, report[f"{side}_input"] = resolve(env_name, service, default)
            report[side] = probe(url)
        except Exception as exc:  # 주소 없음·해석 실패·연결 실패 — 종류만 적는다
            report.update({"result": "unknown", "failed": side, "error": safe_error(exc)})
            report["message"] = f"확인하지 못했습니다 — {side} 쪽: {report['error']}"
            return _emit(report, EXIT_UNKNOWN)
    report.update(compare(report["app"], report["migration"]))
    return _emit(report, EXIT_SAME if report["result"] == "same" else EXIT_DIFFERENT)


if __name__ == "__main__":
    sys.exit(main())
