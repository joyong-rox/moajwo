import sqlite3
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy import exc as sa_exc
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import (
    DATA_DIR,
    DATABASE_URL,
    DB_CONNECT_TIMEOUT_S,
    DB_MAX_OVERFLOW,
    DB_POOL_RECYCLE_S,
    DB_POOL_SIZE,
    DB_POOL_TIMEOUT_S,
)


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    # hide_parameters: DB 오류가 기록(로그)에 남을 때 SQL 매개변수(비밀번호 해시·이름 등 값)를 넣지 않는다
    if url.startswith("sqlite"):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # FastAPI는 요청을 스레드풀에서 처리하므로 SQLite 스레드 검사를 끈다.
        return create_engine(url, connect_args={"check_same_thread": False}, hide_parameters=True)
    # 외부 PostgreSQL (psycopg 3). 끊긴 연결은 꺼낼 때 확인해 버리고 새로 맺는다 (실행 전이라 쓰기를 다시 보내지 않음).
    # 문장 단위 자동 재시도는 하지 않는다 — 결과를 모르는 쓰기를 두 번 실행하지 않게. 서버 쪽 준비된 문장(prepare)은
    # 끈다 — 연결 풀러(PgBouncer 트랜잭션 모드)를 거쳐도 연결마다 상태가 남지 않게.
    return create_engine(
        url,
        hide_parameters=True,
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_timeout=DB_POOL_TIMEOUT_S,
        pool_recycle=DB_POOL_RECYCLE_S,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": DB_CONNECT_TIMEOUT_S,
            "application_name": "moajwo",
            "prepare_threshold": None,
        },
    )


# 연결·자원 장애로 보는 SQLSTATE (앞부분 일치) — 연결(08)·인증(28)·DB 없음(3D)·서버 종료(57P01~03) /
# 자원 부족(53)·잠금 대기 초과(55P03)·문장 시간 초과(57014)·직렬화 실패(40001)·교착(40P01)
_UNREACHABLE_STATES = ("08", "28", "3D", "57P01", "57P02", "57P03")
_BUSY_STATES = ("53", "55P03", "57014", "40001", "40P01")


def db_failure(exc: BaseException) -> str | None:
    """DB에 닿지 못했으면 'unreachable', 바빠서 처리하지 못했으면 'busy', 그 밖(데이터·코드·스키마 오류)은 None.
    None인 오류는 연결 장애로 감추지 않는다 (main이 500으로 그대로 둔다)."""
    if isinstance(exc, sa_exc.TimeoutError):  # 연결 풀에서 빈 연결을 기다리다 시간 초과
        return "busy"
    if not isinstance(exc, sa_exc.DBAPIError):
        return None
    if exc.connection_invalidated:
        return "unreachable"
    orig = exc.orig
    state = getattr(orig, "sqlstate", None)
    if state:
        if state.startswith(_UNREACHABLE_STATES):
            return "unreachable"
        return "busy" if state.startswith(_BUSY_STATES) else None
    if isinstance(orig, sqlite3.Error):
        message = str(orig).lower()
        if "database is locked" in message or "database table is locked" in message:
            return "busy"
        if "unable to open database file" in message or "disk i/o error" in message:
            return "unreachable"
        return None  # 'no such table' 등은 스키마·코드 문제
    # psycopg의 연결 단계 오류(연결 실패·인증 실패·끊긴 연결)는 SQLSTATE가 없다
    return "unreachable" if isinstance(exc, sa_exc.OperationalError | sa_exc.InterfaceError) else None


engine = _make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
