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
- 오프라인 설치: 인터넷이 되는 곳에서 `pip download -r requirements.txt -d wheels --platform manylinux_2_28_x86_64 --python-version 3.11 --only-binary=:all:` 후
  폐쇄망에서 `pip install --no-index --find-links wheels -r requirements.txt`
- 프론트엔드는 `package.json` 정확한 버전 + `package-lock.json`으로 고정합니다.

## 업로드와 파싱 (`POST /files`)

- 필드 `files`로 xlsx / xls / csv 여러 개 업로드 (요청당 20개, 파일당 50MB).
- 원본은 `data/uploads/<uuid>.<ext>`에 새 파일로 저장 후 읽기 전용으로 바꾸고, 메타데이터는 `source_files`에 기록합니다.
- 파싱 규칙 ([backend/app/parsing.py](backend/app/parsing.py)) — 정부부처·시도/시군구 통계표 양식 기준
  - 헤더: 1~3행 중 서로 다른 텍스트 셀이 가장 많은 행 (병합 제목·단위 행은 제외됨)
  - 2단 헤더: 가로 병합 그룹 행(예: `인구`) 아래에 하위 헤더(예: `2025`, `남/여/계`)가 있으면 합쳐 `인구_2025`처럼 열 이름을 만듭니다
  - 병합 셀: 범위 전체를 왼쪽 위 값으로 채움 (ffill). 시도 칸이 세로 병합돼 있으면 동명 시군구(서울/부산 강서구 등)가 구분됩니다
  - 숫자: `1,234`, `2 100 000`, ` 750 ` 등은 숫자로 변환. 천 단위 구분이 틀린 값(전화번호 등)과 앞자리 0 코드값은 문자열 유지
  - 결측 표기: `-`, `x`, `…`, `·` 등은 원본 그대로 두고 결측으로 표시 (0과 구분)
  - CSV 인코딩: chardet 신뢰도 0.5 이상이면 채택, 아니면 EUC-KR → CP949 → UTF-8 순서
  - 행 구분 (행 수는 데이터 행만 셉니다)
    - 빈 행
    - 소계·합계 행: 소계, 합계, 총계, 누계, 계, 전국, total 등
    - 주석 행: 값이 하나뿐이고 `자료:`, `출처:`, `주:`, `주1)`, `※`, `*`, `(단위:` 로 시작
    - 데이터 행

## 열 매핑 (`POST /files/{id}/mapping`)

저장된 원본을 다시 파싱해 시트별 열 이름을 표준 사전([backend/app/standard_dictionary.json](backend/app/standard_dictionary.json))에 매핑합니다.

1. **사전 정확 일치**: 공백·가운뎃점·단위 괄호·연도를 무시하고 비교 → 자동 적용. `인구_2025`는 `인구수` + 연도 `2025`로 분리. 2단 헤더 일부만 일치하면 확인 대상.
2. **유사도**: rapidfuzz 문자열 유사도 0.4 + bge-m3 임베딩 코사인 0.6 가중 평균
   - 0.90 이상이고 1·2위 차이 0.05 이상 → 자동 적용
   - 0.75 이상 → 사용자 확인 대상
   - 값 형식 규칙: 샘플이 모두 시도 코드(11, 26, … 52)로 시작하는 같은 길이(2·5·8·10자리)의 숫자면 `행정구역코드` 확인 대상 (LLM 전에 결정, 숫자형 셀은 열 이름이 없거나 '코드'가 들어간 경우만)
3. **LLM**: 1·2단계 미해결 열만, 한 번에 한 열씩 열 이름 + 샘플 값 5개 + 샘플 형식 힌트를 로컬 Ollama(Qwen 7B)에 보냄. 결과는 항상 사용자 확인 대상(자동 적용하지 않음). 요청당 최대 15열.

- **확정과 학습** (`PUT /files/{id}/mapping/decisions`, `GET /mapping/fields`)
  - 화면에서 열마다 표준 항목(또는 '해당 없음')을 골라 확정하거나, 제안대로 일괄 확정합니다. 확정은 `mapping_decisions`에 저장되고 다시 분석해도 유지됩니다.
  - 확정한 열 이름은 `learned_aliases`에 별칭으로 학습되어, 다른 파일에서도 1단계에서 바로 자동 적용됩니다. 표준 사전 파일은 수정하지 않습니다.
  - 헤더 없는 열('열3'), 이미 사전에 있는 이름, 사전의 다른 항목과 충돌하는 이름은 학습하지 않습니다(확정 자체는 저장).
- Ollama 설정: `OLLAMA_BASE_URL`(기본 `http://localhost:11434`, Compose에서는 `http://host.docker.internal:11434`), `OLLAMA_EMBED_MODEL`(기본 `bge-m3`), `OLLAMA_LLM_MODEL`(기본 `qwen2.5:7b`). 로컬/사설 주소가 아니면 호출을 거부합니다.
- 모델 준비(최초 1회): `ollama pull bge-m3 && ollama pull qwen2.5:7b`
- Ollama가 꺼져 있으면 임베딩 없이 문자열 유사도만 쓰고, LLM 대상 열은 미해결로 남깁니다.

## 데이터 초기화

검증용으로 올린 파일·확정·학습 별칭까지 모두 지우려면 (되돌릴 수 없음):

```sh
docker compose down -v && docker compose up -d
```

## DB 마이그레이션

```sh
cd backend
alembic revision --autogenerate -m "설명"   # app/models.py 변경 후
alembic upgrade head
```
