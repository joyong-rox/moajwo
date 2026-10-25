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
# 파일 하나의 글자 수 합 (모든 칸의 글자 길이) — 읽은 값을 정리할 때 칸마다 글자 사본이 생긴다. Excel의 공유 문자열
# (같은 글자를 파일에 한 번만 저장)·병합 범위는 작은 파일로도 수천 칸이 같은 긴 글자를 가리키게 해, 칸 수·압축 해제
# 한도로는 막지 못한다 (10.7KB 파일의 32,000자 × 800칸이 읽을 때 최고 98.8MiB — docs/MEMORY.md). Render 권장 16,000,000
MAX_FILE_TEXT_CHARS = _env_int("MOAJWO_MAX_FILE_TEXT_CHARS", 64_000_000)
PARSE_SLOTS = _env_int("MOAJWO_PARSE_SLOTS", 1)  # 동시에 읽는(파싱하는) 파일 수 — 서로 다른 파일도 포함
PARSE_WAIT_S = _env_int("MOAJWO_PARSE_WAIT_S", 60)  # 읽기 차례를 기다리는 최대 시간 (초)
# 결과 파일(묶기·통합 Excel) 하나에 넣는 칸 수 — 결과는 메모리에 다 만든 뒤 저장한다 (칸 1천 개당 약 0.45MiB,
# docs/MEMORY.md). 넘으면 실행 기록을 만들기 전에 거절한다. Render(512MiB) 권장 300,000
MAX_OUTPUT_CELLS = _env_int("MOAJWO_MAX_OUTPUT_CELLS", 1_000_000)
# 결과 파일 하나에 넣는 글자 수 합 (결과 칸 + 통합의 변경 기록 앞뒤 값·이유). 칸 수 예산은 글자 양을 보지 않아 긴 글자
# 칸 몇천 개로도 메모리가 넘친다 (512MiB 격리 컨테이너에서 칸 몇천 개·약 6,390만 자 통합이 메모리 부족으로 종료 —
# docs/MEMORY.md). 통합을 새 컨테이너에서: 셈한 글자 약 4,790만 → 346MiB, 약 9,580만 → 593MiB, 약 1억 4,370만 →
# 800MiB. 묶기 약 9,580만 → 407MiB. Render(512MiB) 권장 48,000,000
MAX_OUTPUT_TEXT_CHARS = _env_int("MOAJWO_MAX_OUTPUT_TEXT_CHARS", 96_000_000)
# 결과 만들기(묶기·통합·통계 요약·데이터 분석) 동시 실행 수 — 프로세스마다 (app/output_slot.py). 칸 예산은 요청 하나만
# 막으므로, 한도 가까운 결과 둘이 동시에 만들어지지 않게 한다. 차례가 없으면 기다리지 않고 거절한다
OUTPUT_SLOTS = _env_int("MOAJWO_OUTPUT_SLOTS", 1)
