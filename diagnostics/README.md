# 진단 설정·재실행 방법 (1차 진단 2026-10-20, 같은 날 2차 보완)

결과 보고서: [REPORT-20261020.md](REPORT-20261020.md) — **1차 진단·미수행 항목 포함**. 정정 이력은 보고서 11장에 있습니다.
이후 보고: [REPORT-20261022.md](REPORT-20261022.md) (전체 보완 작업, 정정 표시 포함) · [REPORT-20261023.md](REPORT-20261023.md) (PR #36 검토 후속 — 두 차례 검증·외부 DB·메모리 예산·이미지).
2차: [REPORT-20261023-2.md](REPORT-20261023-2.md) (PR #38·#39 보완 — 이관 도구·장애 응답·PostgreSQL 시험 복원([노드별 표](REPORT-20261023-2-pg-skips.md))·경합·배포 롤백·410 화면·결과 파일 메모리·무료 외부 DB 준비). 격리 러너 스크립트는 [ci/](ci/README.md).
Docker 없이 격리 서버를 띄워 돌린 실행 도구는 [local/](local/README.md), 5장 추가 확인은 `scripts/round5_check.py`입니다.

이 폴더에는 비밀번호·토큰·쿠키·실제 개인정보·DB 사본이 없습니다.
- 시험 계정 비밀번호는 실행 때 무작위로 만들어 Git 밖 증거 폴더의 `creds.json`에만 둡니다.
- `scripts/seed.py`의 개인정보 형식 값은 합성 값입니다(`example.com`, `010-0000-000x`).
- 스크립트에 있는 비밀번호 같은 글자는 실제 계정에 쓰지 않는 값입니다. 로그인 실패, 확인란 불일치, 서버 규칙 위반 거절, 틀린 현재 비밀번호로 변경 시도처럼 **거절되는 입력**에만 씁니다.

## 구성

| 파일 | 용도 | 상태 |
|---|---|---|
| `compose.diag.yml` | 진단 격리 스택 `moajwo-diag` (127.0.0.1:8086, 볼륨 `moajwo-diag-data`, 메모리·CPU 상한, 외부 이름 조회 차단, 모델 호출 차단) | 사용함 |
| `compose.diag.yml`의 `zap` 서비스 | 실행 중 보안 검사용 준비 설정 | **미실행·미검증** |
| `scripts/diag_scope.py` | 진단 요청 허용 범위 (아래 '요청 범위') | 2차 추가 |
| `scripts/diag_common.py` | 공용 (계정·업로드·읽기 전용 DB 조회). 요청은 `diag_scope`를 거침 | 사용함 |
| `scripts/seed.py` | 합성 계정·자료 생성 | 1차에 사용 (2차는 기존 볼륨 재사용) |
| `scripts/monitor.py` | 공유 PC 사용 가능 메모리·운영 health 감시 | 2차에 `127.0.0.1`로 바꿈. **바꾼 뒤 미실행** |
| `scripts/health_timing.py` | 운영 health를 `localhost`·`127.0.0.1`로 단계별 비교 (직렬 소수 요청) | 1차에 사용. 2차는 운영 전용 opener만 정리, **미실행** |
| `scripts/a11y_scan.py` | 72개 화면 상태 Nu·axe 검사. `NU_URL=off`면 DOM·서버 문서만 저장. 4차: 진행 중인 CSS 전환이 끝난 뒤(최대 1초) axe, `ONLY_STATES=`로 상태를 골라 실행 | 사용함 |
| `scripts/nu_check_dom.py` | 저장한 DOM·서버 문서를 로컬 Nu로 검사해 결과 JSON을 채움 | 2차 추가 |
| `scripts/a11y_manual.py` | 키보드·확인창·재배치·확대·간격·대상 크기·상태 안내·로그인 보조 기록. 2차에 `authforms`(가입·최초 설정 화면 재배치·확대) 추가 | 사용함. `keyboard`의 '검토 열기' 단계는 판정 조건 오류로 미확인 (1차) |
| `scripts/a11y_fix_check.py` | **측정만 저장:** D-01~D-07 항목별 전후 기록(접근 가능한 이름·설명, 상태 영역 변경 기록, 테두리·스위치 대비, 가입 도움말, 지난 검사 점수). 3차에 추가한 측정: 업로드 중·완료 뒤 조작 요소, A1 규칙, 상태 안내 범위(혼합 형식·목록 갱신·늦은 응답·로그아웃), 분석 표 로딩·실패, 확인창 본문 연결·초점, 화면에 나타난 입력 경계·초점 외곽선 | 2차 추가, 3차 확장 |
| `scripts/a11y_regress.py` | **판정:** 측정 JSON을 읽어 `--suite`(fix·nav·transition)로 고른 묶음의 필수 기준(ID)을 판정. 통과·실패·미측정·측정 오류·입력 오류·해당 없음을 나누고, 종료 코드 0 전체 완료 / 1 실패 / 2 불완전 / 3 부분 검사 통과 / 4 판정기 자체 오류 / 5 사용법 오류. 출처가 다른 측정을 합치면 `--reuse-reason` 필요 (화면 접속 없음). 5차: `--suite`·`--partial` 목록 규칙(쉼표로 나눔 → 앞뒤 공백 제거 → 빈 항목 버림 → 중복은 처음 것만, 비면 5), 고른 기준 0개는 불완전, 출력은 늘 UTF-8. 6차: 전환 중 기준은 저장된 전체 프레임을 첫 활성 인덱스부터 다시 계산(전체 프레임·인덱스가 없는 5차 판 기록은 미측정) | 3차 추가, 4차 재작성, 5·6차 보완 |
| `scripts/button_transition_check.py` | **측정만 저장:** 버튼이 비활성으로 그려진 뒤 활성이 되는 동안 프레임마다 글자 대비(반올림 전 값)·disabled·hover·초점·글자 크기·굵기·색·투명도. 로그인(시험용 응답·없는 아이디의 실제 응답), 홈 '사전 검토 열기'·'검증 실행', 묶기 '엑셀 만들기'·'위로' — 23개 실행. 틀린 비밀번호를 서버로 보내지 않음. 6차: 첫 활성 프레임의 인덱스와 반올림하지 않은 시각(`t_raw`)을 남기고 프레임을 모두 저장 | 5차 추가, 6차 보완 |
| `scripts/transition_summary.py` | 전환 기록 집계 (첫 활성 프레임 인덱스부터 활성 프레임 모두 — 시각 반올림과 무관). 측정 스크립트와 시험이 같은 함수를 씀 | 6차 추가 |
| `scripts/run_logged.py` | 명령 하나를 실행하고 표준 출력·오류(받은 바이트 그대로)·종료 코드·명령·대상 SHA·작업 트리 변경 여부·출력 인코딩 환경을 파일로 남김 | 5차 추가 |
| `scripts/login_contrast_probe.py` | 로그인 실패 직후 로그인 버튼의 상태·색·투명도·전환을 프레임마다 기록하고 axe를 두 시점에 돌림 (틀린 비밀번호 로그인만 — 여러 번 돌리면 진단 계정이 5분 잠김) | 4차 추가 |
| `scripts/nav_focus_check.py` | **측정만 저장:** 본문 바로가기, 화면 이동 뒤 초점(메뉴·목록↔상세·뒤로/앞으로·주소만 고침), 긴 내용 보기, 분석 표 스크롤 영역. 긴 근거 확인용 합성 검토를 만든다 | 3차 추가 |
| `scripts/a4_split.py` | A4(axe 대비 판정 보류) 대상을 1차 DOM과 대조해 기존 요소와 새로 생긴 요소로 나눔 (저장한 DOM만 읽음) | 3차 추가 |
| `scripts/flow_check.py` | 업로드 → 검토 → 통합 → 내려받기, 묶기 요약 끔·켬 흐름 | 2차 추가 |
| `scripts/compare_a11y.py` | 1차 원시 결과와 나중 72개 상태 결과를 같은 원인 분류로 비교 | 2차 추가 |
| `scripts/scope_selftest.py` | 요청 범위 자체 시험 (루프백 임시 서버만, 진단·운영 주소에 요청 없음) | 2차 추가 |
| `scripts/scroller_check.py`·`toggle_check.py`·`contrast_check.py`·`contrast_calc.py`·`css_support_check.py` | 개별 확인 (표 스크롤 영역, 스위치 상태 색, 계산 색, 대비 계산, CSS 지원) | 사용함. 3차: 대비 계산 경계 0.04045·원시값 판정, CSS 지원에 말줄임 렌더링 비교 추가 |
| `scripts/summarize_trivy.py`·`summarize_a11y.py` | 원시 결과 재집계 (새 검사 없음) | 사용함 |
| `tests/` | `diag_scope` 단위 시험 43개(소켓 연결을 막은 채 실행) + `a11y_regress` 판정 시험 30개(그중 CLI 종료 코드 11개 — 출력 인코딩 환경 변수를 뺀 하위 프로세스) + `transition_summary` 집계 시험 6개 = 79개 | 2차 추가, 3·4·5·6차 확장 |
| `tools/zoomext/` | 실제 브라우저 확대용 최소 확장 (탭 확대 권한만) | 사용함 |
| `reports/` | 부록: API 목록, 72개 상태 결과, Trivy HIGH 전체, WCAG 2.2 A·AA 상태 | — |
| `ruff.toml` | 진단 스크립트 전용 린트 (backend 규칙 상속, JS 문자열 때문에 줄 길이만 완화) | — |

실행 중 권한·보안 확인 스크립트는 없습니다. 작성 중 응답이 안전 분류기에 의해 중단돼 파일이 만들어지지 않았고, 다시 작성하지 않았습니다(REPORT 6장).

## 요청 범위 (`scripts/diag_scope.py`)

- `DIAG_URL`은 `http://127.0.0.1:8086`(진단 격리 스택)만 허용합니다. 비우면 그 주소를 씁니다.
  - 문자열 접두어가 아니라 파싱해서 scheme·hostname·port를 정확히 비교합니다. 사용자 정보(`user@`), 경로, 쿼리, 조각이 붙으면 거절합니다.
  - 허용 밖이면 `assert`가 아니라 `ScopeError`로 멈춥니다(최적화 실행에서도 그대로).
- `NU_URL`은 `http://127.0.0.1:8888`(로컬 Nu)만 허용합니다. `off`면 Nu에 보내지 않습니다.
- Python 요청은 환경 프록시를 쓰지 않습니다. 리다이렉트는 이어지는 주소가 같은 범위일 때만 따르고, 밖이면 `ScopeError`로 멈춥니다.
- 브라우저는 같은 범위를 씁니다.
  - 첫 요청은 `page.route`로 막습니다.
  - 리다이렉트 이후 요청은 아무것도 듣지 않는 프록시(`127.0.0.1:9`)로 보내 실패시킵니다. 예외는 `<-loopback>,127.0.0.1:8086` 하나입니다.
  - `<-loopback>`은 **맨 앞**에 둡니다. Playwright가 뒤에 덧붙이게 두면 허용 규칙까지 무력화됩니다(Edge 155, `scope_selftest.py`로 확인).
- 운영(8080)은 어느 쪽에도 허용하지 않습니다. 시드 생성·업로드 같은 진단용 쓰기 요청은 운영 주소로 보낼 수 없습니다.
- 운영 health 감시는 `monitor.py`·`health_timing.py`의 별도 경로입니다.
  - 고정된 `http://127.0.0.1:8080/api/health`에 GET만 보냅니다.
  - 프록시를 쓰지 않고 리다이렉트를 따르지 않습니다. 진단 모듈을 가져오지 않습니다(단위 시험의 AST 검사로 확인).
- 단위 시험은 운영 주소에 실제 요청을 보내 거절 여부를 시험하지 않습니다.

## 경로 (Git Bash 기준)

```bash
ROOT=/c/Users/juho/Documents/Moajwo                # 저장소 루트 (자기 환경에 맞게)
EV=$ROOT/backups/diag-20261020                     # Git 밖 증거 폴더 (.gitignore의 backups/)
export DIAG_EVIDENCE="$(cygpath -w "$EV")"         # 스크립트도 같은 폴더를 쓰게 (기본값과 같지만 명시)
PY=$ROOT/backend/.venv/Scripts/python.exe           # Python 3.12.10: openpyxl 3.1.5, pytest 9.1.1, ruff 0.16.10
PW=<Playwright 파이썬>                             # Python 3.12.10: playwright 1.63.0, openpyxl 3.1.5 (2차는 Git 밖 임시 venv)
SCRATCH=<Git 밖 임시 폴더>                         # 소스 사본·미러·이미지 tar·axe-core 설치 위치
AXE_JS="$(cygpath -w "$SCRATCH/diag/tools/node/node_modules/axe-core/axe.min.js")"   # axe-core 4.14.0
export PYTHONIOENCODING=utf-8                       # 한글 출력 (2차 실행 때 명령마다 붙임 — 대비 계산 출력 비교도 이 상태). 5차부터 판정기는 이 변수가 없어도 UTF-8로 씀 (없을 때 cp949로 죽어 종료 1이 나던 문제, REPORT 14.3)
mkdir -p "$EV/round2"                               # 출력 폴더는 명령마다 mkdir -p로 먼저 만든다
```

- `diag_common.py`는 `DIAG_EVIDENCE`가 없으면 `<저장소>/backups/diag-20261020`을 씁니다. 셸의 `$EV`와 스크립트가 다른 폴더를 보지 않게 `DIAG_EVIDENCE`를 같은 값으로 내보냅니다.
- 브라우저 스크립트는 `PW`로, 표준 라이브러리·openpyxl만 쓰는 스크립트(`nu_check_dom`·`compare_a11y`·`summarize_*`·`contrast_calc`)와 시험·린트는 `PY`로 실행합니다.
- Docker에 넘기는 호스트 경로는 `cygpath -w`로 바꾸고, `MSYS_NO_PATHCONV=1`을 붙입니다.

## 도구 이미지 (태그 대신 다이제스트로 받기)

| 도구 | 받는 이름 | 버전 | 이미지 ID |
|---|---|---|---|
| Gitleaks | `zricethezav/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f` | v8.30.1 | `sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f` |
| Trivy | `aquasec/trivy@sha256:2416d1f74045b57274df1a340eee6b510cacd73c66a6ccb8da092b117eb7d222` | 0.76.0 (DB v2 UpdatedAt 2026-10-19 19:08:34Z, 볼륨 `trivy-diag-cache`) | `sha256:2416d1f74045b57274df1a340eee6b510cacd73c66a6ccb8da092b117eb7d222` |
| Nu Html Checker | `ghcr.io/validator/validator@sha256:8f21030474e68e06905cfa801b37376e99c0e37867c653c5b6cd7e2dcaf3f503` | 26.10.12 (0a70d65) | `sha256:8f21030474e68e06905cfa801b37376e99c0e37867c653c5b6cd7e2dcaf3f503` |

- 이 PC의 Docker Desktop(containerd 이미지 저장소)에서는 이미지 ID가 받은 다이제스트와 같습니다.

| 검사 대상 | 진단 태그 | 이미지 ID |
|---|---|---|
| 운영 backend (= 진단 backend) | `moajwo-diag-backend:ee1334ec` | `sha256:ee1334ec349fd5c9e504bde57abff17367ac0cabea58e28081743d928b070f40` |
| 운영 frontend (수정 전) | `moajwo-diag-frontend:4e943da6` | `sha256:4e943da6b821395de7168dd5c1b85b6e61892139ad9149e3d8830fd68108035a` |
| 접근성 수정 frontend (`fix/a11y-common-controls` 7b47b21) | `moajwo-diag-frontend:7b47b21` | `sha256:4826cb6978134bf4b4115c208cee65ad3c06d554d14ae5caee4fd4758a34896e` |
| 접근성 수정 frontend (같은 브랜치 95423ca, 2차 최종 확인) | `moajwo-diag-frontend:95423ca` | `sha256:aed42daddb82b354650afc2d0025ae197bdfceb93fa06410e714b8a8c7b0f3ed` |
| 접근성 수정 frontend (같은 브랜치 62895ac, 3차 최종 확인) | `moajwo-diag-frontend:62895ac` | `sha256:3179afc1c1de7b883090ba04bc2cf3d539a8358fa1795d48cac357a3ca915d14` |
| 화면 이동·표 탐색 개선 frontend (`fix/a11y-navigation-focus` bf22d4d, PR #16 위) | `moajwo-diag-frontend:bf22d4d` | `sha256:9c85a73f4ddc4522a946854edac0698c5bc1b9f5ae7e586c49d7c2018c09e724` |
| 같은 브랜치 16bb926 (4차 최종 확인) | `moajwo-diag-frontend:16bb926` | `sha256:7b13efef2676e644fc54126bb560af14e99ff32feb3b18159e7e6482a916b569` |

그 밖의 도구: pip-audit 2.10.1 · npm 11.16.0 · axe-core 4.14.0 · Playwright 1.63.0 · Microsoft Edge 155.0.4283.45.

## 재실행 A — 2차·3차(2026-10-20)에 이 형태로 실행해 확인한 명령

공유 PC에서는 사용 가능 메모리를 먼저 확인하고 도구를 하나씩 띄웁니다. 브라우저 DOM 수집과 Nu 검사는 동시에 돌리지 않고 차례로 합니다.

- 2차 실행 때는 경로를 변수 대신 절대 경로로 적었습니다. 아래는 같은 명령을 위 변수로 바꿔 적은 것입니다.
- `DIAG_EVIDENCE`는 2차 실행 때 내보내지 않았습니다(기본값과 같은 폴더). 내보낸 값이 그대로 쓰이는지는 따로 확인했습니다(`import diag_common; print(diag_common.EV)` — 기본값, 같은 값, 다른 값 세 경우).

```bash
# A1) 범위 단위 시험·린트 (네트워크 없음)
$PY -m pytest "$ROOT/diagnostics/tests" -q -p no:cacheprovider        # 61 passed (4차, CLI 종료 코드 시험 포함)
(cd "$ROOT/diagnostics" && $PY -m ruff check .)

# A2) 범위 자체 시험 (루프백 임시 서버 8086·8099만 — 진단 스택을 멈춘 상태에서)
(cd "$ROOT/diagnostics/scripts" && $PW scope_selftest.py "$(cygpath -w "$EV/round2/scope-selftest.json")")

# A3) 비밀정보 (네트워크 없음, 값 가림)
GL=zricethezav/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f
mkdir -p "$EV/round2/gitleaks" "$SCRATCH/src2"
git clone -q --mirror "$ROOT" "$SCRATCH/mirror2.git"
git -C "$ROOT" archive 183dad3f867956b1bf9112d1d84491a8266e20be | tar -x -C "$SCRATCH/src2"
MSYS_NO_PATHCONV=1 docker run --rm --network none --memory 512m -v "$(cygpath -w "$SCRATCH/mirror2.git"):/repo:ro" -v "$(cygpath -w "$EV/round2/gitleaks"):/out" \
  $GL git /repo --redact --report-format json --report-path /out/history.json --no-banner
MSYS_NO_PATHCONV=1 docker run --rm --network none --memory 512m -v "$(cygpath -w "$SCRATCH/src2"):/src:ro" -v "$(cygpath -w "$EV/round2/gitleaks"):/out" \
  $GL dir /src --redact --report-format json --report-path /out/current.json --no-banner
# 검사 범위 확인: 병합 커밋은 Gitleaks가 건너뛴다 — 병합의 결합 diff가 비어 있는지(충돌 해결로 새로 들어간 내용이 없는지) 따로 본다
for m in $(git -C "$ROOT" rev-list --all --merges); do [ -n "$(git -C "$ROOT" diff-tree --cc -r --no-commit-id "$m")" ] && echo "결합 diff 있음 $m"; done

# A4) 이미지 (DB는 기존 캐시 볼륨, 검사는 네트워크 없이) — 2차는 frontend tar만 다시 검사해 1차와 같음을 확인
TV=aquasec/trivy@sha256:2416d1f74045b57274df1a340eee6b510cacd73c66a6ccb8da092b117eb7d222
mkdir -p "$EV/round2/trivy"
MSYS_NO_PATHCONV=1 docker run --rm --network none --memory 1g --cpus 2 -v trivy-diag-cache:/root/.cache -v "$(cygpath -w "$SCRATCH/images"):/img:ro" -v "$(cygpath -w "$EV/round2/trivy"):/out" \
  $TV image --input /img/frontend.tar --skip-db-update --skip-java-db-update --offline-scan --scanners vuln,secret --format json -o /out/image-frontend.json -q

# A5) 진단 스택 (기존 볼륨의 합성 자료 재사용). 이미지만 바꿔 수정 전·후를 같은 자료로 본다
export DIAG_BE_IMAGE=moajwo-diag-backend:ee1334ec DIAG_FE_IMAGE=moajwo-diag-frontend:4e943da6   # 수정 후: moajwo-diag-frontend:95423ca
MSYS_NO_PATHCONV=1 docker compose -f "$ROOT/diagnostics/compose.diag.yml" -p moajwo-diag up -d --wait
#   수정 브랜치 frontend 이미지 만들기 (브랜치 작업 트리에서): docker build -q -t moajwo-diag-frontend:<커밋> "$(cygpath -w "<작업 트리>/frontend")"

# A6) 항목별 전후 기록 (수정 전 이미지로 한 번, 수정 후 이미지로 한 번)
mkdir -p "$EV/round2/fix-check/before" "$EV/round2/fix-check/after-final"
(cd "$ROOT/diagnostics/scripts" && $PW a11y_fix_check.py "$(cygpath -w "$EV/round2/fix-check/after-final")")   # ONLY_CHECKS=upload,contrast,... 로 골라 실행 가능
#   AXE_JS를 주면 A1 규칙도 실행. 측정 결과에는 출처(meta: 빌드 자산·DIAG_FE_IMAGE·라벨·시각)가 남는다
#   (4차 형태 — 3차에는 --suite 없이 실행했고 판정 기준이 지금과 다름)
(cd "$ROOT/diagnostics/scripts" && AXE_JS="$AXE_JS" $PW a11y_fix_check.py "$(cygpath -w "$EV/round4/fix-check")" after-16bb926)
(cd "$ROOT/diagnostics/scripts" && $PW nav_focus_check.py "$(cygpath -w "$EV/round4/nav-check")" after-16bb926)   # ONLY_CHECKS=skip,menu,reviews,analysis,summary,history,failure,stale,keep,longtext,tables
#   판정: 묶음을 명시한다 (입력으로 범위를 추정하지 않음). 종료 코드 0 전체 완료 / 1 실패 / 2 불완전 / 3 부분 검사 통과 / 4 판정기 자체 오류 / 5 사용법 오류
$PY "$ROOT/diagnostics/scripts/a11y_regress.py" --suite fix,nav --md "$EV/round4/regress-after-16bb926.md" "$EV/round4/fix-check/fix-check-after-16bb926.json" "$EV/round4/nav-check/nav-check-after-16bb926.json"
#   출처가 다른(또는 출처 기록이 없는) 측정을 합칠 때는 재사용 이유를 적는다 — 보고서 출처 표에 남는다
$PY "$ROOT/diagnostics/scripts/a11y_regress.py" --suite fix --reuse-reason "<같은 이미지 등 이유>" --md "$EV/round4/regress-62895ac-with-dialog.md" "$EV/round3/fix-check/fix-check-after-62895ac.json" "$EV/round4/fix-check/fix-check-after-62895ac-dialog.json"
#   5차: 기록하며 실행 (표준 출력·오류·종료 코드·명령·대상 SHA를 $EV/round5/logs/<이름>.* 로) — 시험·판정·측정 모두 이렇게 돌렸다
#     $PY "$ROOT/diagnostics/scripts/run_logged.py" "$(cygpath -w "$EV/round5/logs")" <이름> --cwd <실행 폴더> --sha-of <대상 작업 트리> -- <명령 ...>
#     Windows에서 cmd 내장 명령이 아니라 npm.cmd·npx.cmd를 직접 부른다 (Git Bash는 'cmd /c'의 /c를 경로로 바꿔 명령이 실행되지 않음)
#   5차: 버튼 전환 중 대비 (수정 전·후 이미지마다) → 판정 transition 묶음 (정지 상태·전환 중을 따로, 관측 못 한 실행은 미측정)
#   6차: 첫 활성 프레임 인덱스·전체 프레임을 남기는 판으로 다시 잼 (5차 판 기록은 판정기가 전환 중 기준을 미측정으로 봄)
(cd "$ROOT/diagnostics/scripts" && $PW button_transition_check.py "$(cygpath -w "$EV/round6/transition")" after-a55453e-r6)   # ONLY_CHECKS=login,review,bundle
$PY "$ROOT/diagnostics/scripts/a11y_regress.py" --suite fix,nav,transition --md "$EV/round6/regress-after-a55453e-r6.md" "$EV/round5/fix-check/fix-check-after-a55453e.json" "$EV/round5/nav-check/nav-check-after-a55453e.json" "$EV/round6/transition/transition-after-a55453e-r6.json"
#   로그인 버튼 대비 조사 (4차) — 틀린 비밀번호 로그인을 반복하므로 진단 계정이 잠길 수 있다 (5차는 이 스크립트 대신 button_transition_check.py를 씀)
(cd "$ROOT/diagnostics/scripts" && AXE_JS="$AXE_JS" $PW login_contrast_probe.py "$(cygpath -w "$EV/round4/login-contrast-probe.json")" 5)

# A7) 72개 상태: 브라우저 수집(NU_URL=off) → 브라우저를 닫은 뒤 Nu만 띄워 검사 → Nu 내림
(cd "$ROOT/diagnostics/scripts" && NU_URL=off AXE_JS="$AXE_JS" $PW a11y_scan.py "$(cygpath -w "$EV/round2/a11y-after")")
MSYS_NO_PATHCONV=1 docker run -d --name moajwo-diag-nu --memory 768m --cpus 1 --dns 127.0.0.1 -e JAVA_TOOL_OPTIONS=-Xmx512m -p 127.0.0.1:8888:8888 \
  ghcr.io/validator/validator@sha256:8f21030474e68e06905cfa801b37376e99c0e37867c653c5b6cd7e2dcaf3f503
(cd "$ROOT/diagnostics/scripts" && $PY nu_check_dom.py "$(cygpath -w "$EV/round2/a11y-after")")
docker rm -f moajwo-diag-nu
$PY "$ROOT/diagnostics/scripts/compare_a11y.py" "$EV/raw" "$EV/round2/a11y-after" "$EV/round2/compare-a11y.md"
#   3차: A4 대상을 1차 DOM과 대조 (저장한 DOM만 읽음, 브라우저는 스크립트·네트워크 없이)
(cd "$ROOT/diagnostics/scripts" && $PW a4_split.py "$(cygpath -w "$EV/raw")" "$(cygpath -w "$EV/round3/a11y-62895ac")" "$(cygpath -w "$EV/round3/a4-split-62895ac.md")")

# A8) 재배치 320 CSS px·실제 200% 확대·가입/최초 설정 화면, 기존 업무 흐름
(cd "$ROOT/diagnostics/scripts" && $PW a11y_manual.py "$(cygpath -w "$EV/round2/manual-after")" reflow,zoom,authforms)
(cd "$ROOT/diagnostics/scripts" && $PW flow_check.py "$(cygpath -w "$EV/round2/flow-after")")              # ONLY_FLOWS=bundle 등

# A9) 재집계·대비 계산 (새 검사 없음). 재집계는 함수로 나눈 뒤에도 1차 출력과 바이트 단위로 같음을 cmp로 확인
$PY "$ROOT/diagnostics/scripts/summarize_a11y.py" "$EV/raw" "$SCRATCH/a11y-states-check.md" && cmp "$SCRATCH/a11y-states-check.md" "$ROOT/diagnostics/reports/a11y-states-20261020.md"
#   대비 계산은 3차에 경계값(0.04045)·원시값 판정으로 바꾸고 옛 경계와 나란히 출력한다 — 1차 raw/contrast-calc.txt와는 형식이 다르다
$PY "$ROOT/diagnostics/scripts/contrast_calc.py" > "$EV/round3/contrast-calc.txt"
$PY "$ROOT/diagnostics/scripts/contrast_calc.py" "$EV/raw/a11y-manual/contrast.json" > "$EV/round3/contrast-recalc-round1.txt"   # 1차 기록 재계산 (읽기만)
(cd "$ROOT/diagnostics/scripts" && $PW css_support_check.py "$(cygpath -w "$EV/round3/css-support-edge.json")")   # 빈 페이지, 접속 없음

# A10) 정리 — 진단 프로젝트만 정지 (볼륨·이미지 보존, down·볼륨 삭제 안 함)
MSYS_NO_PATHCONV=1 docker compose -f "$ROOT/diagnostics/compose.diag.yml" -p moajwo-diag stop
docker ps -a --filter name=moajwo-diag-nu --format '{{.Names}}'   # 비어 있어야 함
```

## 재실행 B — 이 문서의 형태로는 다시 실행하지 않은 명령 (미검증)

1차에는 비슷한 명령으로 실행했지만, 다이제스트·경로 변수를 쓴 아래 형태로는 다시 실행하지 않았습니다.

```bash
# B1) 감시 (운영에는 health GET만, 127.0.0.1) — 127.0.0.1로 바꾼 뒤 실행하지 않음
$PY "$ROOT/diagnostics/scripts/monitor.py" "$EV/monitor.jsonl" "$EV/monitor.stop" 240 &
touch "$EV/monitor.stop"                                                    # 끝낼 때

# B2) 이미지 tar·Trivy DB·backend 이미지·config·fs (외부 통신: DB 내려받기만)
mkdir -p "$SCRATCH/images" "$EV/raw/trivy"
docker tag sha256:ee1334ec349fd5c9e504bde57abff17367ac0cabea58e28081743d928b070f40 moajwo-diag-backend:ee1334ec
docker tag sha256:4e943da6b821395de7168dd5c1b85b6e61892139ad9149e3d8830fd68108035a moajwo-diag-frontend:4e943da6
docker save moajwo-diag-backend:ee1334ec -o "$(cygpath -w "$SCRATCH/images/backend.tar")"     # frontend도 같게
MSYS_NO_PATHCONV=1 docker run --rm -v trivy-diag-cache:/root/.cache $TV image --download-db-only
MSYS_NO_PATHCONV=1 docker run --rm --network none -v trivy-diag-cache:/root/.cache -v "$(cygpath -w "$SCRATCH/images"):/img:ro" -v "$(cygpath -w "$EV/raw/trivy"):/out" \
  $TV image --input /img/backend.tar --skip-db-update --skip-java-db-update --offline-scan --scanners vuln,secret --format json -o /out/image-backend.json
#   config: $TV config /src · fs: $TV fs /src --scanners vuln,secret,misconfig --include-dev-deps (소스 사본을 /src로)
$PY "$ROOT/diagnostics/scripts/summarize_trivy.py" "$EV/raw/trivy" "$ROOT/diagnostics/reports/trivy-high-<날짜>.md"

# B3) 의존성 (외부 통신: PyPI·OSV / npm 레지스트리 — 패키지 이름·버전만)
mkdir -p "$EV/raw/pip-audit"
MSYS_NO_PATHCONV=1 docker run --rm --network none --entrypoint pip moajwo-diag-backend:ee1334ec freeze --all > "$EV/raw/pip-audit/image-freeze.txt"
pip-audit -r "$EV/raw/pip-audit/image-freeze.txt" --no-deps --disable-pip --vulnerability-service pypi --format json -o "$EV/raw/pip-audit/image-pypi.json"
#   --vulnerability-service osv 로 한 번 더. 개발 환경은 backend/.venv의 pip freeze로 같게
(cd "$SCRATCH/src/frontend" && npm audit --json && npm audit --omit=dev --json)

# B3-2) contrast_check.py — 3차에 계산식(경계 0.04045, 원시값 ratio_raw)만 바꾸고 다시 실행하지 않음 (1차 기록은 contrast_calc.py로 재계산함)
(cd "$ROOT/diagnostics/scripts" && $PW contrast_check.py "$(cygpath -w "$EV/round3/contrast-check")")

# B4) 새 볼륨에 합성 자료 만들기 (2차·3차는 1차 볼륨을 그대로 씀)
(cd "$ROOT/diagnostics/scripts" && $PY seed.py)

# B5) 도구 설치 (2차는 1차에 만든 임시 venv·node 폴더를 그대로 씀)
python -m venv "$SCRATCH/pwvenv" && "$SCRATCH/pwvenv/Scripts/python.exe" -m pip install playwright==1.63.0 openpyxl==3.1.5
(mkdir -p "$SCRATCH/diag/tools/node" && cd "$SCRATCH/diag/tools/node" && npm install axe-core@4.14.0)
```

## 재실행 C — 미수행 (이번에 실행하지 않음)

- SonarQube, ZAP(`compose.diag.yml`의 `zap` 서비스 포함), 실행 중 권한·보안 확인, 스크린리더 실제 낭독 확인. 사유와 범위는 REPORT 6장에 있습니다.
