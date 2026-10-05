import os
from pathlib import Path

# 업로드 원본·결과 파일·SQLite DB가 놓이는 루트. 컨테이너에서는 볼륨(/app/data)으로 마운트된다.
DATA_DIR = Path(os.environ.get("MOAJWO_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

# 초기에는 SQLite. PostgreSQL 전환 시 DATABASE_URL만 바꾼다.
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'moajwo.db').as_posix()}")

# 로컬 Ollama만 사용한다 (외부 LLM API 금지). 컨테이너에서는 host.docker.internal로 호스트의 Ollama에 붙는다.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "bge-m3")
OLLAMA_LLM_MODEL = os.environ.get("OLLAMA_LLM_MODEL", "qwen2.5:7b")
