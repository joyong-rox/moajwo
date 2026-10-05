# Moajwo — 문서취합 자동화 도구

로컬 폐쇄망 환경에서 동작하는 문서취합 자동화 도구입니다.

## 원칙

- 외부 API 미사용. LLM은 로컬 Ollama 엔드포인트만 사용합니다.
- 업로드된 원본 파일은 읽기 전용이며, 결과물은 항상 새 파일로 생성합니다.

## 기술 스택

| 영역 | 스택 |
| --- | --- |
| Backend | Python 3.11, FastAPI, pandas, openpyxl, rapidfuzz, bge-m3, Ollama (Qwen 7B) |
| Frontend | React, Vite, TypeScript, TanStack Table |
| DB | SQLite (초기) → PostgreSQL |
| Infra | Docker Compose |

사용하지 않는 기술: Celery, Redis, Kubernetes, VectorDB, 스케줄러.
