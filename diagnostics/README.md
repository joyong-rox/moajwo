# 진단 설정·재실행 방법 (1차 진단 2026-10-20)

결과 보고서: [REPORT-20261020.md](REPORT-20261020.md) — **1차 진단·미수행 항목 포함**.

이 폴더에는 비밀번호·토큰·쿠키·실제 개인정보·DB 사본이 없습니다.
- 시험 계정 비밀번호는 실행 때 무작위로 만들어 Git 밖 증거 폴더의 `creds.json`에만 둡니다.
- `scripts/seed.py`의 개인정보 형식 값은 합성 값입니다(`example.com`, `010-0000-000x`).
- 스크립트에 있는 비밀번호 같은 글자는 실제 계정에 쓰지 않는 값입니다. 로그인 실패를 만들거나, 틀린 현재 비밀번호로 변경을 시도해 거절되게 하는 데만 씁니다.

## 구성

| 파일 | 용도 | 상태 |
|---|---|---|
| `compose.diag.yml` | 진단 격리 스택 `moajwo-diag` (127.0.0.1:8086, 볼륨 `moajwo-diag-data`, 메모리·CPU 상한, 외부 이름 조회 차단, 모델 호출 차단) | 사용함 |
| `compose.diag.yml`의 `zap` 서비스 | 실행 중 보안 검사용 준비 설정 | **미실행·미검증** |
| `scripts/diag_common.py` | 공용 (진단 주소만 허용, 계정·업로드·읽기 전용 DB 조회) | 사용함 |
| `scripts/seed.py` | 합성 계정·자료 생성 | 사용함 |
| `scripts/monitor.py` | 공유 PC 사용 가능 메모리·운영 health 감시 | 사용함. `localhost`를 써서 IPv6 연결 대기가 섞일 수 있음 → `127.0.0.1` 권장 (REPORT M-11) |
| `scripts/a11y_scan.py` | 72개 화면 상태 Nu·axe 검사 | 사용함 |
| `scripts/a11y_manual.py` | 키보드·확인창·재배치·확대·간격·대상 크기·상태 안내·로그인 보조 기록 | 사용함. `keyboard`의 '검토 열기' 단계는 판정 조건 오류로 미확인 |
| `scripts/scroller_check.py`·`toggle_check.py`·`contrast_check.py`·`contrast_calc.py`·`css_support_check.py` | 개별 확인 (표 스크롤 영역, 스위치 상태 색, 계산 색, 대비 계산, CSS 지원) | 사용함 |
| `scripts/health_timing.py` | 운영 health를 `localhost`·`127.0.0.1`로 단계별 비교 (직렬 소수 요청) | 사용함 |
| `scripts/summarize_trivy.py`·`summarize_a11y.py` | 원시 결과 재집계 (새 검사 없음) | 사용함 |
| `tools/zoomext/` | 실제 브라우저 확대용 최소 확장 (탭 확대 권한만) | 사용함 |
| `reports/` | 부록: API 목록, 72개 상태 결과, Trivy HIGH 전체, WCAG 2.2 A·AA 상태 | — |
| `ruff.toml` | 진단 스크립트 전용 린트 (backend 규칙 상속, JS 문자열 때문에 줄 길이만 완화) | — |

실행 중 권한·보안 확인 스크립트는 없습니다. 작성 중 응답이 안전 분류기에 의해 중단돼 파일이 만들어지지 않았고, 다시 작성하지 않았습니다(REPORT 6장).

## 재실행 (Git Bash, 저장소 루트)

공유 PC에서는 사용 가능 메모리를 먼저 확인하고, 도구를 하나씩 띄웁니다.

```bash
EV=backups/diag-20261020            # Git 밖 증거 폴더 (DIAG_EVIDENCE로 바꿀 수 있음)
PY=backend/.venv/Scripts/python.exe  # 표준 라이브러리·openpyxl만 쓰는 스크립트
PW=<playwright가 설치된 파이썬>      # a11y·브라우저 스크립트 (Playwright 1.63, Edge)

# 0) 감시 (선택) — 운영에는 health GET만
$PY diagnostics/scripts/monitor.py $EV/monitor.jsonl $EV/monitor.stop 240 &

# 1) 소스 사본 (작업 폴더의 backups/·.venv·node_modules를 읽지 않게)
git archive 183dad3f867956b1bf9112d1d84491a8266e20be | tar -x -C <스크래치>/src
git clone --mirror . <스크래치>/mirror.git

# 2) 비밀정보 (네트워크 없음, 값 가림)
MSYS_NO_PATHCONV=1 docker run --rm --network none -v "<스크래치>/mirror.git:/repo:ro" -v "$EV/raw/gitleaks:/out" \
  zricethezav/gitleaks:latest git /repo --redact --report-format json --report-path /out/history.json
MSYS_NO_PATHCONV=1 docker run --rm --network none -v "<스크래치>/src:/src:ro" -v "$EV/raw/gitleaks:/out" \
  zricethezav/gitleaks:latest dir /src --redact --report-format json --report-path /out/current.json

# 3) 이미지 (운영 이미지 ID에 진단 태그 → tar → DB만 내려받고 검사는 네트워크 없이)
docker tag sha256:ee1334ec… moajwo-diag-backend:ee1334ec && docker tag sha256:4e943da6… moajwo-diag-frontend:4e943da6
docker save moajwo-diag-backend:ee1334ec -o <스크래치>/images/backend.tar   # frontend도 같게
MSYS_NO_PATHCONV=1 docker run --rm -v trivy-diag-cache:/root/.cache aquasec/trivy:latest image --download-db-only
MSYS_NO_PATHCONV=1 docker run --rm --network none -v trivy-diag-cache:/root/.cache -v "<스크래치>/images:/img:ro" -v "$EV/raw/trivy:/out" \
  aquasec/trivy:latest image --input /img/backend.tar --skip-db-update --skip-java-db-update --offline-scan --scanners vuln,secret --format json -o /out/image-backend.json
#   config: trivy config /src · fs: trivy fs /src --scanners vuln,secret,misconfig --include-dev-deps
$PY diagnostics/scripts/summarize_trivy.py $EV/raw/trivy diagnostics/reports/trivy-high-<날짜>.md

# 4) 의존성 (외부 통신: PyPI·OSV / npm 레지스트리 — 패키지 이름·버전)
MSYS_NO_PATHCONV=1 docker run --rm --network none --entrypoint pip moajwo-diag-backend:ee1334ec freeze --all > $EV/raw/pip-audit/image-freeze.txt
pip-audit -r $EV/raw/pip-audit/image-freeze.txt --no-deps --disable-pip --vulnerability-service pypi --format json -o $EV/raw/pip-audit/image-pypi.json
#   --vulnerability-service osv 로 한 번 더. 개발 환경은 backend/.venv의 pip freeze로 같게
(cd <스크래치>/src/frontend && npm audit --json && npm audit --omit=dev --json)

# 5) 진단 스택 + 합성 자료
export DIAG_BE_IMAGE=moajwo-diag-backend:ee1334ec DIAG_FE_IMAGE=moajwo-diag-frontend:4e943da6
MSYS_NO_PATHCONV=1 docker compose -f diagnostics/compose.diag.yml up -d --wait
(cd diagnostics/scripts && $PY seed.py)

# 6) 웹 표준·접근성 (Nu는 로컬 컨테이너, axe는 npm 패키지의 axe.min.js)
MSYS_NO_PATHCONV=1 docker run -d --name moajwo-diag-nu --memory 768m --cpus 1 -e JAVA_TOOL_OPTIONS=-Xmx512m -p 127.0.0.1:8888:8888 ghcr.io/validator/validator:latest
(cd diagnostics/scripts && AXE_JS=<axe-core>/axe.min.js $PW a11y_scan.py $EV/raw/a11y)
(cd diagnostics/scripts && $PW a11y_manual.py $EV/raw/a11y-manual)      # 검사 이름을 쉼표로 골라 실행 가능
$PY diagnostics/scripts/summarize_a11y.py $EV/raw diagnostics/reports/a11y-states-<날짜>.md
$PY diagnostics/scripts/contrast_calc.py

# 7) 정리 — 진단 프로젝트만 정지 (볼륨·이미지 보존)
MSYS_NO_PATHCONV=1 docker compose -f diagnostics/compose.diag.yml -p moajwo-diag stop
docker rm -f moajwo-diag-nu
touch $EV/monitor.stop
```

## 도구 버전 (1차 진단에 쓴 것)

- Gitleaks v8.30.1 · Trivy 0.76.0 (DB UpdatedAt 2026-10-19 19:08:34Z)
- pip-audit 2.10.1 · npm 11.16.0
- Nu Html Checker 26.10.12 (0a70d65) · axe-core 4.14.0
- Playwright 1.63.0 · Edge 155.0.4283.45

이미지 다이제스트는 REPORT 1장에 있습니다. `latest` 태그로 받으면 버전이 바뀔 수 있으니, 재현할 때는 다이제스트로 받으세요.
