import os
from pathlib import Path

# 업로드 원본·결과 파일·SQLite DB가 놓이는 루트. 컨테이너에서는 볼륨(/app/data)으로 마운트된다.
DATA_DIR = Path(os.environ.get("MOAJWO_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

# 초기에는 SQLite. PostgreSQL 전환 시 DATABASE_URL만 바꾼다.
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'moajwo.db').as_posix()}")

# 기본은 로컬 Ollama (폐쇄망). 외부 LLM API는 소유자가 「설정」에서 직접 고른 경우에만 쓴다 (app/llm.py).
# 컨테이너에서는 host.docker.internal로 호스트의 Ollama에 붙는다. 임베딩은 항상 로컬 Ollama.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3")
OLLAMA_LLM_MODEL = os.environ.get("OLLAMA_LLM_MODEL", "qwen2.5:7b")

# API 문서(/docs·/redoc)와 스키마(/openapi.json)는 로그인 없이 열리는 경로라 운영에서는 끈다 (S-03).
# 개발·진단에서만 MOAJWO_API_DOCS=1로 켠다.
API_DOCS = os.environ.get("MOAJWO_API_DOCS", "") == "1"

def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """정수 설정. 비어 있으면 기본값, 숫자가 아니거나 최솟값보다 작으면 기동을 멈춘다 (잘못된 한도로 돌지 않게)."""
    raw = os.environ.get(name, "").strip()
    value = int(raw) if raw else default
    if value < minimum:
        raise ValueError(f"{name}={value}: {minimum} 이상이어야 합니다")
    return value


# 파일 읽기 한도 — 환경별 메모리 예산 (docs/MEMORY.md에 측정 근거와 환경별 권장값). 크기 단위는 MiB(2^20 바이트).
# 기본값은 로컬 운영 구성(Docker, 컨테이너 메모리 제한 없음, 다른 작업과 함께 쓰는 PC) 기준이다.
MAX_UPLOAD_MIB = _env_int("MOAJWO_MAX_UPLOAD_MIB", 50)  # 파일 하나의 크기 (올린 그대로 — xlsx는 압축된 크기)
# xlsx를 풀었을 때의 크기 — 읽는 동안 1MiB당 약 10MiB를 쓴다 (docs/MEMORY.md)
MAX_XLSX_UNCOMPRESSED_MIB = _env_int("MOAJWO_MAX_XLSX_UNCOMPRESSED_MIB", 32)
MAX_FILE_CELLS = _env_int("MOAJWO_MAX_FILE_CELLS", 800_000)  # 파일 하나의 칸 수 (모든 시트, 빈 칸 포함)
PARSE_SLOTS = _env_int("MOAJWO_PARSE_SLOTS", 1)  # 동시에 읽는(파싱하는) 파일 수 — 서로 다른 파일도 포함
PARSE_WAIT_S = _env_int("MOAJWO_PARSE_WAIT_S", 60)  # 읽기 차례를 기다리는 최대 시간 (초)
