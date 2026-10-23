from collections.abc import Iterator

from sqlalchemy import create_engine
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
    if url.startswith("sqlite"):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # FastAPI는 요청을 스레드풀에서 처리하므로 SQLite 스레드 검사를 끈다.
        return create_engine(url, connect_args={"check_same_thread": False})
    # 외부 PostgreSQL (psycopg 3). 끊긴 연결은 꺼낼 때 확인해 버리고 새로 맺는다 (실행 전이라 쓰기를 다시 보내지 않음).
    # 문장 단위 자동 재시도는 하지 않는다 — 결과를 모르는 쓰기를 두 번 실행하지 않게. 서버 쪽 준비된 문장(prepare)은
    # 끈다 — 연결 풀러(PgBouncer 트랜잭션 모드)를 거쳐도 연결마다 상태가 남지 않게.
    return create_engine(
        url,
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


engine = _make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
