import os
from pathlib import Path

# 업로드 원본·결과 파일·SQLite DB가 놓이는 루트. 컨테이너에서는 볼륨(/app/data)으로 마운트된다.
DATA_DIR = Path(os.environ.get("MOAJWO_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

# 초기에는 SQLite. PostgreSQL 전환 시 DATABASE_URL만 바꾼다.
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'moajwo.db').as_posix()}")
