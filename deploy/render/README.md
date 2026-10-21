# Render 검토용 배포

기준 소스: `main`의 `703fdcbda6ec3600fbdab8c0ab01b9398562bc07`.
기존 `backend/`, `frontend/`, Docker Compose의 동작 코드는 변경하지 않고 배포 파일만 추가합니다.

## 구성

- 하나의 Docker 서비스에서 Vite 빌드 결과(`/`)와 기존 FastAPI(`/api/*`)를 제공합니다.
- `PORT` 환경변수에 바인딩하며 기본값은 `10000`입니다. Health Check는 `/api/health`입니다.
- Python 3.11, Node 24 및 저장소의 잠금 파일을 사용합니다.
- API와 화면은 같은 HTTPS 호스트를 사용하므로 별도 CORS 허용 설정이 필요하지 않습니다.
- SQLite와 업로드 파일은 컨테이너의 `/app/preview-data`에 저장됩니다.
- 외부 요청을 받기 전에 마이그레이션과 검토용 소유자 생성을 완료합니다.
- 기존 검토 DB가 있는 프로세스 재시작에서는 계정과 비밀번호를 덮어쓰지 않습니다.
- 로컬 Ollama 서버는 포함하지 않습니다. AI 임베딩·LLM 처리는 검증 범위에서 제외합니다.
- 샘플 `sample.csv`의 기관명과 수치는 검토용으로 만든 가상 데이터입니다.

## Render에서 배포

1. Render 계정의 GitHub 연결에서 비공개 저장소 `joyong-rox/moajwo`에 대한 접근을 허용합니다.
2. **New → Blueprint**에서 이 저장소와 배포 파일이 포함된 브랜치를 선택합니다.
   현재 준비 브랜치는 `deploy/render-preview-20261021`이며 위 main 커밋을 기반으로 합니다.
   main에 이 변경이 병합된 뒤에는 main을 선택할 수 있습니다. 병합 전 main에는 이 배포 파일이 없습니다.
3. 루트의 `render.yaml`을 적용합니다. 서비스 설정을 확인합니다.
   - 서비스: `moajwo-review`
   - Runtime: Docker
   - Instance: **Free**
   - Region: Singapore
   - Dockerfile: `deploy/render/Dockerfile`
   - Docker context: 저장소 루트
   - Health Check: `/api/health`
   - Auto Deploy: Off
   - 영구 디스크와 별도 유료 DB는 만들지 않습니다.
4. Blueprint의 `generateValue: true`가 `MOAJWO_PREVIEW_OWNER_PASSWORD`를 생성합니다.
   소스 코드에 비밀번호를 넣지 않습니다. Blueprint를 사용하지 않고 Web Service를 직접 만든다면
   Render의 비밀 환경변수에 20~128자 임의 비밀번호를 직접 설정해야 합니다.
5. 빌드와 Health Check가 성공하면 Render가 반환한 실제 HTTPS URL을 사용합니다.
6. 사용자 이름은 `preview-owner`입니다. 비밀번호는 Render의 서비스 환경변수에서 확인합니다.
   비밀번호·세션 쿠키를 Git이나 채팅에 붙여넣지 않습니다.
7. `sample.csv`로 로그인, 업로드, 기본 매핑·검토 동작을 확인합니다.
   사이트에서 비밀번호를 바꾼 경우 현재 DB에서는 변경한 값이 유효합니다.
   무료 환경의 파일 시스템이 초기화되면 환경변수의 값으로 새 검토용 계정을 생성합니다.

## 무료 환경의 제한

Render 무료 Web Service는 비활성 시 중지될 수 있고, 중지·재시작·재배포 시 SQLite와 업로드 파일이
사라집니다. 운영 데이터나 지속 보관해야 할 자료를 넣지 않고 작은 합성 파일로만 검토합니다.
이 구성은 데이터 보존, 대용량 파일 처리, AI 처리 성능을 검증하는 운영 환경이 아닙니다.
월간 사용량 제한은 계정 상태에 따라 확인해야 하며, Free 선택만으로 모든 초과 사용 비용 정책을
대신 확인한 것은 아닙니다. 배포 시 현재 Render 요금/사용량 설정을 확인합니다.

## 검증

프론트엔드:

```sh
cd frontend
npm ci
npm run build
```

Python 3.11 가상환경에 `backend/requirements.txt`와 `httpx`를 설치한 후 저장소 루트에서:

```sh
python deploy/render/verify.py
sh -n deploy/render/start.sh
```

`verify.py`는 임시 폴더의 새 DB에 마이그레이션을 적용한 뒤 아래 8개 테스트 메서드를 실행합니다.
기존 운영 DB를 읽거나 수정하지 않고, 임시 계정 비밀번호를 출력하지 않습니다.

1. API와 SPA 경로 분리, 인증 없는 파일 접근 거부
2. 정적 자산 응답, 없는 파일·상위 경로 접근의 404
3. DB를 포함한 Health Check
4. 불완전한 초기 설정에서 시작 중단
5. 로그인, Secure/HttpOnly/SameSite 쿠키, CSV 파싱·업로드, 로그아웃
6. 공개 최초 관리자 생성 API 차단
7. 재시작 시 기존 계정 비밀번호 보존
8. 프론트엔드 하위 주소 직접 접근

2026-10-21 준비 환경 결과:

- Node 24.19.0: `npm run build` 성공(타입 검사 포함).
- Python 3.11.17: 위 8개 테스트 통과, 새 DB 리비전 `0019_analysis_runs` 적용.
- `sh -n deploy/render/start.sh` 통과.
- 원본으로 가져온 136개 파일의 Git blob SHA가 기준 커밋과 일치함을 확인.
- 준비 환경에는 Docker가 없어 Docker 이미지 빌드 자체는 아직 검증하지 못했습니다.
- Render 계정 연결·실제 배포·외부 브라우저 검증은 아직 완료되지 않았습니다.
- upstream 전체 회귀 테스트를 다시 실행한 결과는 아닙니다.

## 공식 문서

- https://render.com/docs/blueprint-spec
- https://render.com/docs/docker
- https://render.com/docs/web-services
- https://render.com/docs/free
