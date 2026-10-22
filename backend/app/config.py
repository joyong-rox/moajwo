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
