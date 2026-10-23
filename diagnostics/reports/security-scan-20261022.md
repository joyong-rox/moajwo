# 의존성·이미지·비밀정보 점검 (S-04, 2026-10-22)

- 대상: main `adebb6c`의 추적 소스, 로컬 저장소 전체 이력, 실행 이미지의 기반 이미지(레지스트리의 같은 태그)
- 앞선 점검: `diagnostics/REPORT-20261020.md` (ID M-01~M-04·F-01은 그 보고의 번호)
- 결과는 그날의 취약점 DB 기준입니다. 검출 0건을 보안 보증으로 쓰지 않습니다.

## 환경과 제약

- **Docker 정지:** 점검하는 동안 Docker Desktop은 2026-10-21 20:24에 메모리 부족으로 멈춘 상태였습니다. 운영 재시작 금지 지시에 따라 켜지 않았습니다.
  - (2026-10-23 추가) 마감 확인에서 운영은 다시 동작 중이었습니다. 10-22 20:24:58에 기동되었고, 이 작업이 켠 것이 아닙니다. 실행 이미지는 위와 같은 `ee1334ec…`·`b01c0ea6…`입니다. 그래도 이 보고의 검사는 다시 하지 않았습니다 (PR #36 보고 0-1장).
  - 그래서 도구는 컨테이너 대신 공식 단독 실행 파일로 돌렸습니다.
  - 운영 실행 이미지는 직접 검사하지 못했습니다 (아래 '한계').
- **공유 PC 메모리:** 가용 0.7~0.9GB였습니다. 무거운 단계는 메모리 보호 실행(`guarded_run.py`, 바닥 350MB)으로 하나씩 돌렸고, 중단된 단계는 없습니다.

## 도구·규칙·버전

| 도구 | 버전·출처 | 규칙·옵션 | 외부 통신 |
|---|---|---|---|
| Gitleaks | 8.30.1 — GitHub 릴리스 `windows_x64` (SHA256 `d29144de…`, 공식 체크섬과 같음) | 기본 규칙, `--redact` | 없음 |
| Trivy | 0.76.0 — GitHub 릴리스 `windows-64bit` (SHA256 `7b4762a5…`, 같음). DB v2 UpdatedAt 2026-10-22 07:10:52Z | fs: vuln·secret·misconfig (Trivy 기본: 개발 의존성 제외), image(원격): vuln·secret, config | DB·검사 규칙 내려받기, Docker Hub에서 이미지 받기 |
| pip-audit | 2.10.1 (PyPI) | `-r requirements*.txt --no-deps --disable-pip` | PyPI 취약점 조회 (패키지 이름·버전) |
| npm audit | npm 11.16.0 | `--json`, `--omit=dev` | npm 레지스트리 권고 조회 |
| Bandit | 1.8.6 (PyPI) | 기본 규칙. 보조 점검이며 SonarQube를 대신하지 않음 | 없음 |
| nginx | 1.30.6 Windows (nginx.org) | 설정 문법 `nginx -t` | 없음 |

## 결과

| 대상 | 결과 | 판정 | 조치 |
|---|---|---|---|
| Git 이력 (미러: 참조 95, 커밋 166, 병합 21) · main 추적 소스 | generic-api-key 2건씩. 위치: `test_llm.py:14`, `test_crosscheck.py:251` (이력은 226) | **오탐 (F-01과 같음).** 키 가림을 시험하는 합성 값 `sk-test-0123456789abcdef…`입니다. 병합 커밋 21개는 결합 diff가 모두 비어 있습니다. | 없음 |
| Python 실행 의존성 24개 (`requirements.txt`) · 개발 3개 | 0건 | — | — |
| npm 실행 의존성 22개 (`--omit=dev`) | 0건 | — | — |
| npm 전체 128개 (개발 포함) | vite 8.3.0, HIGH 1행 = 고유 권고 3개 (아래) | 권고는 2026-10-20 공개입니다 (앞선 점검 뒤). **운영 영향 없음:** 개발 서버(`npm run dev`)와 진단용 미리보기에서만 쓰고, 운영은 nginx가 빌드 결과만 내보냅니다. | **vite 8.3.4로 갱신.** 권고 범위(8.3.0~8.3.2) 밖이고 2026-10-08 공개판입니다. 갱신 뒤 npm audit 0건, 빌드·린트 통과 |
| Dockerfile (Trivy config) | backend 0건. frontend·Render 각 2건: DS-0002 root 실행 HIGH, DS-0026 HEALTHCHECK 없음 LOW | M-04와 같음 | **결정 필요** (아래) |
| frontend 기반 `nginx:1.29-alpine` | 183행 / 고유 114개 (HIGH 33·MEDIUM 52·LOW 27·UNKNOWN 2). **고유 114개 모두 수정판 있음** | 1.29 태그는 2026-04-15 이후 갱신되지 않습니다. 메인라인이 1.31로 넘어가 다시 빌드해도 해소되지 않습니다 (M-01). nginx가 링크하는 pcre2(HIGH 4)·libssl3·libcrypto3(HIGH 2)도 포함됩니다. | **`nginx:1.30-alpine`(안정판)으로 변경.** 같은 방식 검사 0건 |
| backend 기반 `python:3.11-slim` (2026-10-21 빌드) | 173행 / 고유 80개 (HIGH 10). 수정판 있는 고유 9개는 모두 Python 설치 도구입니다: pip 24.0(6개), setuptools 79.0.1, setuptools에 묶인 jaraco.context 5.3.0(HIGH), wheel 0.45.1(HIGH) | 설치 도구는 실행에 쓰지 않습니다. 앱·의존성의 import 0이고, setuptools·wheel이 없는 3.12 가상환경에서 backend 전체 시험이 통과합니다 (M-03). | **설치 뒤 pip·setuptools·wheel 제거** |
| 같은 이미지의 Debian 패키지 | HIGH 고유 8개, 수정판 없음 | M-02와 같음 | 근거·완화·재확인 조건 기록 (아래) |
| Bandit (`backend/app` 40개 파일) | 10건: B101 assert 8개(LOW), B310 urlopen 2개(MEDIUM) | **assert:** 타입 좁히기입니다. `-O`로 실행하지 않고, 인증을 통과한 뒤의 조회라 우회 경로가 없습니다. **urlopen — LLM 설정:** http·https 검증이 이미 있습니다 (`llm.validate`). **urlopen — Ollama 주소:** 환경 변수로만 정하고 호스트만 검사해 `file://localhost/…`가 통과했습니다. | **Ollama 주소도 http·https만 허용** (설정 실수 대비, 시험 추가) |

**vite 권고 3개.** vite가 묶은 launch-editor 문제로, Windows 개발 서버의 '편집기 열기' 기능에 해당합니다.
- GHSA-587h-7wmj-h5pm (high): Windows에서 파일 존재 확인을 우회
- GHSA-698v-4r3x-3vcv (medium): `%` 이스케이프 누락
- GHSA-r825-w8mm-5pxw (medium): 편집기 인자 해석

**검사한 기반 이미지** (레지스트리 다이제스트):

| 태그 | 다이제스트 | OS | 빌드 | 결과 |
|---|---|---|---|---|
| `nginx:1.29-alpine` (지금 frontend) | `sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de` | Alpine 3.23.4 | 2026-04-15 | 183행 / 114 |
| `nginx:1.30-alpine` (바꿀 기반) | `sha256:c59d1f8b1e0e42da75be973424cd72e23f7f8adfb54e982f4d1cd0216a5a5576` | Alpine 3.24.3 | 2026-10-21 | 0 |
| `nginxinc/nginx-unprivileged:1.30-alpine` (비루트 후보) | `sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03` | Alpine 3.24.2 | 2026-10-19 | 3행 / 2 (MEDIUM 1·UNKNOWN 1, 수정판 있음) |
| `python:3.11-slim` (backend) | `sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8` | Debian 13.7 | 2026-10-21 | 173행 / 80 |

## 수정판이 없는 Debian HIGH 8개

| 묶음 | CVE | 근거 |
|---|---|---|
| util-linux 계열 (bsdutils·libblkid1·liblastlog2-2·libmount1·libsmartcols1·libuuid1·login·mount·util-linux) | CVE-2026-76642·78408·78409·78410 | mount helper·nsenter·bind mount 관련입니다. 컨테이너에서 mount·nsenter를 실행하지 않습니다. |
| ncurses (libncursesw6·libtinfo6·ncurses-base·ncurses-bin) | CVE-2025-69720 | 터미널 화면을 쓰지 않습니다. |
| systemd (libsystemd0·libudev1) | CVE-2026-16742 | systemd-homed가 돌지 않습니다. |
| libacl1 | CVE-2026-54369 | 앱이 ACL 함수를 부르지 않습니다. |
| perl-base | CVE-2026-9538 | perl(Archive::Tar)을 쓰지 않습니다. |

- **공통 근거:** 앱은 외부 명령을 실행하지 않습니다 (`subprocess`·`os.system` 0). 컨테이너는 비루트(uid 1000)입니다.
- **완화:** 기존 실행 방식 유지 (비루트, 쓰기 가능한 경로는 `/app/data`만 소유).
- **재확인 조건:** Debian 보안 갱신이 나오면 `python:3.11-slim`을 다시 받아 빌드하고 Trivy로 다시 검사합니다.

## 한계 — 하지 못한 것

| 항목 | 상태 | 사유·대신 한 것 |
|---|---|---|
| 운영 실행 이미지 검사 (backend `ee1334ec…`, frontend `b01c0ea6…`) | **미수행** | Docker 정지. 위 결과는 지금 레지스트리의 같은 태그입니다. frontend 기반 태그는 4월 이후 그대로라 운영과 같을 가능성이 높습니다 (10-20 운영 이미지 검사의 HIGH 고유 33개와 같은 수). backend 운영 이미지는 더 오래된 기반이라 실제 결과가 다를 수 있습니다. |
| 바꾼 Dockerfile 빌드·기동 | **미검증** | Docker 정지. 병합 전 격리 빌드 → 기동 → `/health` → Trivy 재검사가 필요합니다. nginx 설정은 1.30.6으로 문법을 확인했습니다. |
| SonarQube Community Build | **미수행** | 서버(Elasticsearch 포함)에 2GB 이상이 필요한데 가용 메모리가 0.7~0.9GB이고 Docker도 정지. Bandit은 보조일 뿐입니다. |
| ZAP 수동적 검사 | **미수행** | Java 런타임 없음, 메모리 부족, Docker 정지. 응답 헤더·쿠키·API 문서 노출은 S-03 후속에서 직접 확인합니다. |
| frontend 비루트 실행 (M-04) | **결정 필요** | `nginx-unprivileged:1.30-alpine`(사용자 101)은 8080을 듣습니다. 포트·compose·Render 설정이 함께 바뀝니다. 다른 안은 공식 이미지에 `cap_drop`·`read_only`를 거는 것입니다. 이번에는 바꾸지 않았습니다. |
| HEALTHCHECK (frontend, DS-0026 LOW) | 보류 | 비루트 결정과 함께 정합니다. |

## 원시 결과

저장소 밖 `backups/review-20261022/s04/`에 있습니다.
- Gitleaks: 값을 가린 상태입니다.
- Trivy·pip-audit·npm audit·Bandit의 JSON
