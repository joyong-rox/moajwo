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
   - 값 형식 규칙: 샘플이 모두 **행정안전부 행정동 코드표**(말소 포함)에 실제로 있는 2·5·8·10자리 코드면 `행정구역코드` (LLM 전에 결정). 5자리 이상이고 3개 이상 일치하면 자동 적용, 그 외 확인 대상. 숫자형 셀은 열 이름이 없거나 '코드'가 들어간 경우만
3. **LLM**: 1·2단계 미해결 열만, 한 번에 한 열씩 열 이름 + 샘플 값 5개 + 샘플 형식 힌트를 로컬 Ollama(Qwen 7B)에 보냄. 결과는 항상 사용자 확인 대상(자동 적용하지 않음). 요청당 최대 15열.

- **확정과 학습** (`PUT /files/{id}/mapping/decisions`, `GET /mapping/fields`)
  - 화면에서 열마다 표준 항목(또는 '해당 없음')을 골라 확정하거나, 제안대로 일괄 확정합니다. 확정은 `mapping_decisions`에 저장되고 다시 분석해도 유지됩니다.
  - 확정한 열 이름은 `learned_aliases`에 별칭으로 학습되어, 다른 파일에서도 1단계에서 바로 자동 적용됩니다. 표준 사전 파일은 수정하지 않습니다.
  - 헤더 없는 열('열3'), 이미 사전에 있는 이름, 사전의 다른 항목과 충돌하는 이름은 학습하지 않습니다(확정 자체는 저장).
- Ollama 설정: `OLLAMA_BASE_URL`(기본 `http://localhost:11434`, Compose에서는 `http://host.docker.internal:11434`), `OLLAMA_EMBED_MODEL`(기본 `bge-m3`), `OLLAMA_LLM_MODEL`(기본 `qwen2.5:7b`). 로컬/사설 주소가 아니면 호출을 거부합니다.
- 모델 준비(최초 1회): `ollama pull bge-m3 && ollama pull qwen2.5:7b`
- Ollama가 꺼져 있으면 임베딩 없이 문자열 유사도만 쓰고, LLM 대상 열은 미해결로 남깁니다.

## 통합 전 검증 (`POST /validation`)

검증 대상 파일, 전월 자료, 기준 기간(`YYYY` 또는 `YYYY-MM`), 필수 기관·지역 목록을 받아 결과를 **등급별로 분리**해 돌려줍니다. 원본과 파싱 결과는 바꾸지 않고 상태만 반환하며, 실행 기록은 `validation_runs`에 남습니다.

| 등급 | 항목 | 판단 |
| --- | --- | --- |
| 차단 (통합 불가) | 합계 불일치 | 소계·합계 행 vs 그룹 하위 행 합 (`서울특별시 \| 계`, `영업1팀 소계`, 전국·합계는 그룹 소계 + 미분류 행). 비율 열 제외, 반올림 허용 오차 = 하위 행 수 × 마지막 자리 ½, `-`는 0, `x`·`…`가 있으면 검사 생략 |
| 차단 | 필수 기관 누락 | 목록의 기관·지역이 기관/지역 열(또는 기관별 파일명)에 없음 |
| 차단 | 기간 불일치 | 연·월·기준일 열, 제목 행, 시트명, 파일명의 기간 ≠ 기준 기간 (기준 기간이 없으면 파일 간 기간이 다를 때) |
| 경고 (통합 진행) | 전월 대비 이상치 | 같은 기관·지역(시도+시군구 등) 행의 값 변화율이 임계값(기본 ±50%) 초과 |
| 경고 | 공백 값 | 매핑된 열의 빈 셀 (`-`, `x` 같은 결측 표기는 제외) |
| 경고 | 단위 추정 | 같은 항목의 단위가 다름(백만원 vs 천원), 값 규모가 300배 이상 차이, 금액 항목에 단위 표기 없음 |

- 상태: `blocked`(차단 있음) > `pii_confirmation_required`(미확인 개인정보) > `passed_with_warnings` > `passed`
- 검증에는 확정·자동 적용된 열 매핑만 쓰고 LLM은 호출하지 않습니다.
- 참고(notes): 검사할 수 없었던 이유. 상태에 영향 없음.

### 개인정보 탐지

- 업로드 시 모든 셀을 정규식으로 검사: 주민등록번호(생년월일 유효성 + 성별 1~8), 휴대전화(010/011/016/017/018/019), 이메일
- 원문은 저장·응답·로그하지 않고 **건수, 위치(시트/행/열), 마스킹 값**만 `source_files.pii_summary`에 저장합니다. 미리보기·매핑 샘플(LLM 입력 포함)도 마스킹됩니다.
- 발견 시 화면에 경고를 띄우고, 사용자가 확인(`POST /files/{id}/pii/acknowledge`)하기 전에는 검증 상태가 `pii_confirmation_required`입니다.

## 통합 전 사전 검토 (`/reviews`)

검증 섹션의 「사전 검토 열기」로 같은 검증 조건의 검토 세션을 만듭니다. 화면에서 가장 넓은 패널로 표시됩니다.

- 상단: 데이터 품질 점수(100점)와 한 줄 요약. 점수 = 100 − 차단 25 × 건수 − 남은 확인 필요 5 × 건수 − 경고 2 × 건수(최대 10), 차단이 있으면 최고 59점.
- 중앙: 보정 예정 항목을 `이전 값 → 이후 값` 한 줄로 보여주고, 펼치면 보정 근거 문장이 나옵니다.
  - 차단 오류(빨강): 합계 불일치·필수 기관 누락·기간 불일치 — 원본을 고쳐 다시 검사해야 함
  - 확인 필요(노랑, 승인/거부): 열 매핑 확인·미해결, 용어사전 확인 대상 이름, 개인정보 포함 파일
  - 자동 적용(회색): 확정·자동 열 매핑, 별칭·약칭 이름 정규화, 숫자 문자열 변환, 병합 셀 채움, 2단 헤더 결합
  - 검증 경고(접힘, 참고): 전월 대비 이상치·공백 값·단위 추정
- 근거 문장: 규칙 판정은 고정 템플릿 문장(「규칙 문장」), **LLM 판정 항목만** LLM이 생성한 문장(「LLM 생성 문장」)을 씁니다.
- 승인/거부는 즉시 백엔드에 반영됩니다.
  - 열 매핑: 승인 → 고른 표준 항목으로 확정, 거부 → '해당 없음'으로 확정 (`mapping_decisions`)
  - 이름: 승인 → 고른 7자리 코드를 이번 검토의 선택값으로 저장
  - 개인정보: 승인 → 포함 확인 기록, 거부 → 이 파일을 이번 통합에서 제외
- 하단 「다음부터 이 형식 기억하기」: 선택하고 「검토 완료」하면 승인한 열 이름(학습 별칭)과 기관·지역 이름(용어사전 별칭)을 학습합니다. 선택하지 않으면 이번 검토에만 적용합니다.
- 「검토 완료」는 차단과 남은 확인 필요 항목이 없을 때만 가능하며 세션을 `ready`(통합 준비)로 바꿉니다.
- API: `POST /reviews`, `GET /reviews/{id}`, `POST /reviews/{id}/items/{item_id}/decision`, `PATCH /reviews/{id}`(remember_format), `POST /reviews/{id}/complete`

## 용어사전 (기관·자치단체 이름, `/glossary/*`)

기관·자치단체 이름을 **7자리 코드**로 해석해, 약칭·표기 차이·조직 개편이 있어도 같은 대상으로 비교합니다. 현행 기관끼리 7자리 코드가 겹치지 않으므로 항목은 이 코드 하나로 식별하고, 자치단체는 **시도코드(2자리)·시군구코드(3자리)**를 속성으로 둡니다. 용어사전은 **사용자가 업로드해 관리**하며, 처음에는 중앙부처 6개·자치단체 8개와 별칭 1개(`행안부`)만 **예시**로 들어 있습니다 (마이그레이션 0005).

- 업로드: 화면의 「용어사전 파일 업로드」 또는 `POST /glossary/import` (CSV·xlsx·xls, 10MB). 양식은 `GET /glossary/template.csv`, 현재 사전은 `GET /glossary/export.csv`(다시 올릴 수 있는 형식).

  | 열 | 설명 |
  | --- | --- |
  | 코드 | **7자리** (숫자·영문 대문자, 필수). 예: 행정안전부 `1741000`, 서울특별시 강서구 `3150000` |
  | 정식명칭 | 필수 |
  | 유형 | 선택 (중앙행정기관, 광역자치단체, 기초자치단체 등) |
  | 시도코드 · 시군구코드 | 선택. 자치단체의 2자리 / 3자리 (예: 강서구 `11` · `500`). 엑셀이 숫자로 바꿔 빠진 앞자리 0은 채움 |
  | 시작일 · 종료일 | 존속 기간 (종료일 포함). 기준 기간 말일에 유효한 항목으로 해석 |
  | 이전코드 | 개편 전 항목의 7자리 코드(쉼표 구분). 폐지된 이름의 후보로 먼저 제시 |
  | 별칭 | `;`로 구분 (예: `행안부; 행자부`) |
  | 메모 | 선택 |

  - 같은 코드는 갱신, 새 코드는 추가. **오류가 한 행이라도 있으면 아무것도 반영하지 않고** 오류 행을 모두 알려줍니다.
  - 경고(반영은 함): 시도·시군구 코드 조합이 행정동 코드표에 없음, 이름이 코드표와 다름, 현행 항목끼리 같은 시도·시군구 코드를 씀.
- 해석 순서: 별칭(유효기간 반영) → 정식명칭 → 지역 약칭(`서울`, `서울시`, `서울 강서구` — 시도·시군구 코드로 자동 생성) → 유사 이름 추천. 같은 이름이 여러 항목이면(예: `강서구`) 임의로 고르지 않고 **모호**로 표시합니다.
- 데이터의 지역코드 열(시도 2자리, 시도+시군구 5자리, 10자리 행정동 코드는 앞 5자리)은 항목의 시도·시군구 코드로 7자리 항목에 연결합니다.
- 검증 연동: 필수 기관 누락과 전월 대비 비교는 7자리 코드로 비교합니다. 사전에 없거나 모호하거나 기준일에 유효하지 않은 이름은 **용어사전 확인 대상**으로 따로 보여주며(상태에 영향 없음), 화면에서 후보를 골라 바로 별칭으로 등록할 수 있습니다.
- 기타 API: `GET /glossary/info`, `GET /glossary/entries?scope=all|org|region`, `DELETE /glossary/entries/{id}`(별칭 함께 삭제), `GET|POST /glossary/aliases`, `DELETE /glossary/aliases/{id}`, `GET /glossary/resolve`

## 참조 데이터: 행정동 코드표

- 파일: [backend/app/reference/KIKcd_H.20260930.all.xlsx](backend/app/reference/KIKcd_H.20260930.all.xlsx) — 행정안전부 「행정기관(행정동) 및 관할구역(법정동) 변경내역(2026.9.30. 시행)」 첨부 `jscode20260930(말소코드포함).zip`의 `KIKcd_H`(행정동 코드) 원본을 내용 변경 없이 보관
- 출처·기준일·sha256: [admin_codes.source.json](backend/app/reference/admin_codes.source.json) (테스트가 해시 일치를 확인)
- 말소 코드 포함: 2026-07-01 광주광역시(29)·전라남도(46) → 전남광주통합특별시(12), 강원도(42) → 강원특별자치도(51), 전라북도(45) → 전북특별자치도(52) 등 과거 통계의 코드도 인식
- 갱신: 같은 게시판의 최신 「말소코드포함」 zip에서 `KIKcd_H…xlsx`를 교체하고 `admin_codes.source.json`의 파일명·기준일·해시를 수정

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
