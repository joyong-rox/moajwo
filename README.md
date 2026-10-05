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

## 구조

```
backend/    FastAPI 앱 (app/), Alembic 마이그레이션 (alembic/), 테스트 (tests/)
frontend/   React + Vite + TypeScript, 운영 시 nginx로 서빙 (/api → backend 프록시)
docker-compose.yml
```

## 실행 (Docker Compose)

```sh
docker compose up -d --build
curl http://localhost:8000/health   # {"status":"ok","database":"ok"}
# 웹 화면: http://localhost:8080
```

- 백엔드 컨테이너는 기동 시 `alembic upgrade head`를 실행한 뒤 서버를 띄웁니다.
- SQLite DB와 업로드 파일은 `moajwo-data` 볼륨(`/app/data`)에 저장됩니다.
- 포트는 `127.0.0.1`에만 바인딩됩니다.

## 로컬 개발

```sh
# backend
cd backend
python -m venv .venv && .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
ruff check . && ruff format --check . && pytest

# frontend (개발 서버가 /api 를 localhost:8000 으로 프록시)
cd frontend
npm ci
npm run dev
npm run lint && npm run build
```

## 의존성 관리

- `backend/requirements.in`: 직접 의존성 (버전 고정)
- `backend/requirements.txt`: Python 3.11 / linux 기준 전이 의존성까지 고정한 설치 목록
- 오프라인 설치: 인터넷이 되는 곳에서 `pip download -r requirements.txt -d wheels` 후
  폐쇄망에서 `pip install --no-index --find-links wheels -r requirements.txt`
- 프론트엔드는 `package.json` 정확한 버전 + `package-lock.json`으로 고정합니다.

## 업로드와 파싱 (`POST /files`)

- 필드 `files`로 xlsx / xls / csv 여러 개 업로드 (요청당 20개, 파일당 50MB).
- 원본은 `data/uploads/<uuid>.<ext>`에 새 파일로 저장 후 읽기 전용으로 바꾸고, 메타데이터는 `source_files`에 기록합니다.
- 파싱 규칙 ([backend/app/parsing.py](backend/app/parsing.py))
  - 헤더: 1~3행 중 서로 다른 텍스트 셀이 가장 많은 행 (병합 제목 행은 제외됨)
  - 병합 셀: 범위 전체를 왼쪽 위 값으로 채움 (ffill)
  - 숫자: `1,234`, `2 100 000`, ` 750 ` 등은 숫자로 변환. 천 단위 구분이 틀린 값(전화번호 등)과 앞자리 0 코드값은 문자열 유지
  - CSV 인코딩: chardet 신뢰도 0.5 이상이면 채택, 아니면 EUC-KR → CP949 → UTF-8 순서
  - 행 구분: 빈 행 / 소계·합계 행(소계, 합계, 총계, 누계, 계, total 등) / 데이터 행. 행 수는 데이터 행만 셉니다.

## DB 마이그레이션

```sh
cd backend
alembic revision --autogenerate -m "설명"   # app/models.py 변경 후
alembic upgrade head
```
