import os
from pathlib import Path

from sqlalchemy.engine import make_url

# 업로드 원본·결과 파일·SQLite DB가 놓이는 루트. 컨테이너에서는 볼륨(/app/data)으로 마운트된다.
DATA_DIR = Path(os.environ.get("MOAJWO_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))


def _database(raw: str, mode: str) -> tuple[str, str]:
    """(모드, SQLAlchemy URL). 오류 문구에는 주소·비밀번호를 넣지 않는다.

    - sqlite (로컬·폐쇄망 기본): DATA_DIR의 SQLite 파일. DATABASE_URL에 sqlite 주소를 줄 수도 있다.
    - external (인터넷 시연용 외부 PostgreSQL — Neon 등, docs/EXTERNAL_DB.md): DATABASE_URL이 반드시 있어야 한다.
      없거나 PostgreSQL이 아니면 기동하지 않는다. 빈 SQLite로 대신 열면 계정이 없는 것처럼 보여 최초 관리자 설정이
      다시 열리기 때문이다. postgres:// · postgresql:// 주소는 psycopg 3 드라이버로 바꾸고 나머지(비밀번호의 특수문자는
      퍼센트 인코딩 그대로, sslmode·channel_binding 같은 연결 옵션)는 그대로 둔다.
    """
    if mode not in ("", "sqlite", "external"):
        raise ValueError(f"MOAJWO_DB_MODE는 sqlite 또는 external이어야 합니다 (지금 값: {mode!r})")
    if not raw:
        if mode == "external":
            raise ValueError("MOAJWO_DB_MODE=external인데 DATABASE_URL이 없습니다 — 빈 SQLite로 열지 않고 멈춥니다")
        return "sqlite", f"sqlite:///{(DATA_DIR / 'moajwo.db').as_posix()}"
    try:
        url = make_url(raw)
    except Exception as exc:  # 주소를 오류 문구에 남기지 않는다
        raise ValueError("DATABASE_URL을 해석하지 못했습니다 (형식 확인)") from exc
    backend = url.get_backend_name()
    if backend == "sqlite":
        if mode == "external":
            raise ValueError("MOAJWO_DB_MODE=external인데 DATABASE_URL이 SQLite입니다")
        return "sqlite", raw
    if backend not in ("postgresql", "postgres"):
        raise ValueError(f"지원하지 않는 DB입니다: {backend} (SQLite·PostgreSQL만)")
    return "external", url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


DB_MODE, DATABASE_URL = _database(
    os.environ.get("DATABASE_URL", "").strip(), os.environ.get("MOAJWO_DB_MODE", "").strip().lower()
)
# 마이그레이션(스키마 변경)용 주소 — 외부 DB는 연결 풀러(PgBouncer 트랜잭션 모드)를 거치지 않는 직접 연결과, 스키마를
# 바꿀 권한이 있는 계정을 쓴다. 비우면 DATABASE_URL을 쓴다.
_migration = os.environ.get("MOAJWO_MIGRATION_DATABASE_URL", "").strip()
MIGRATION_DATABASE_URL = _database(_migration, DB_MODE)[1] if _migration else DATABASE_URL

# 외부 DB 연결 풀 (작게 — Neon 무료 0.25CU의 앱용 연결 97개, 무료 웹 서비스 인스턴스 1개 기준).
# 휴면(5분 무활동 뒤 Neon 컴퓨트 정지)으로 끊긴 연결은 꺼낼 때 확인(pre-ping)하고, 그보다 짧게 새 연결로 바꾼다.
DB_POOL_SIZE = int(os.environ.get("MOAJWO_DB_POOL_SIZE") or 3)
DB_MAX_OVERFLOW = int(os.environ.get("MOAJWO_DB_MAX_OVERFLOW") or 2)
DB_POOL_TIMEOUT_S = int(os.environ.get("MOAJWO_DB_POOL_TIMEOUT_S") or 10)  # 풀에서 연결을 기다리는 최대 시간
DB_POOL_RECYCLE_S = int(os.environ.get("MOAJWO_DB_POOL_RECYCLE_S") or 240)
DB_CONNECT_TIMEOUT_S = int(os.environ.get("MOAJWO_DB_CONNECT_TIMEOUT_S") or 15)  # 휴면에서 깨는 시간 포함
# 마이그레이션(alembic/env.py)과 이관 도구(app/dbcopy.py)가 함께 쓰는 PostgreSQL 잠금 키 — 둘이 동시에 돌지 않게.
# 기동 때 마이그레이션은 이 잠금과 표 잠금을 최대 MIGRATION_LOCK_TIMEOUT_S초 기다린 뒤 실패한다
# (무한 대기로 기동 중처럼 보이지 않게)
MIGRATION_LOCK_KEY = 727_274
MIGRATION_LOCK_TIMEOUT_S = int(os.environ.get("MOAJWO_MIGRATION_LOCK_TIMEOUT_S") or 60)

# 기본은 로컬 Ollama (폐쇄망). 외부 LLM API는 소유자가 「설정」에서 직접 고른 경우에만 쓴다 (app/llm.py).
# 컨테이너에서는 host.docker.internal로 호스트의 Ollama에 붙는다. 임베딩은 항상 로컬 Ollama.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3")
OLLAMA_LLM_MODEL = os.environ.get("OLLAMA_LLM_MODEL", "qwen2.5:7b")
