# zlib 보안 수정 — backend zlib1g(DSA-6613-1) · frontend zlib 1.4-r0 · 로컬 nginx 기반 정렬 (2026-11-09)

backend·frontend 실행 이미지의 zlib을 배포판 보안 수정판으로 올리는 변경의 근거·방법·바뀌는 것을 적는다. 같은 변경에서 로컬 `frontend/Dockerfile`의 nginx 기반을 Render용 `frontend/Dockerfile.render`와 같은 색인으로 맞춘다. 운영 반영은 소유자 승인 뒤 `scripts/deploy.sh`로 한다([DEPLOY.md](DEPLOY.md) 11장의 반영 전 이미지 검증 그대로).

## 1. 결함과 대상

| 이미지 | 패키지 (수정 전) | CVE | 심각도 (Trivy) | 수정판 |
|---|---|---|---|---|
| backend (Debian 13 trixie 기반) | `zlib1g` `1:1.3.dfsg+really1.3.1-1+b1` | CVE-2026-76844 · CVE-2026-85091 | HIGH | `1:1.3.dfsg+really1.3.1-1+deb13u1` (trixie-security, DSA-6613-1) |
| 〃 | 〃 | CVE-2026-27171 | MEDIUM | 같은 판 |
| frontend (Alpine nginx) | `zlib` `1.3.2-r1` | CVE-2026-4738 | MEDIUM | `1.4-r0` (Alpine v3.24 main) |

- **backend에서 쓰는 곳**: Python 표준 `zlib` 모듈이 이 라이브러리에 동적으로 연결된다. 2026-11-09에 지금 이미지에서 확인한 결과는 다음과 같다.
  - `lib-dynload/zlib.cpython-311-x86_64-linux-gnu.so` → `/lib/x86_64-linux-gnu/libz.so.1`
  - `zlib.ZLIB_RUNTIME_VERSION` 1.3.1
  - xlsx 같은 zip 형식 업로드를 읽고 결과 파일을 만들 때 압축·해제가 이 경로를 지난다.
- **frontend에서 쓰는 곳**: nginx가 연결하는 라이브러리다. 이 저장소의 nginx 설정(`frontend/nginx.conf`·`nginx.render.conf`)에는 gzip 지시어가 없다.
- 각 CVE의 세부 악용 조건은 판단하지 않았다. 배포판이 수정판을 냈으므로 그 판으로 올린다.

## 2. 방법

| 파일 | 변경 | 기존 선례 |
|---|---|---|
| `backend/Dockerfile` | 기존 `apt-get install --only-upgrade ca-certificates` 줄에 `zlib1g`를 더한다. 설치 뒤 `dpkg --compare-versions … ge 1:1.3.dfsg+really1.3.1-1+deb13u1`로 확인하고, 못 미치면 빌드가 멈춘다 | 2026-10 ca-certificates 보안 판과 같은 줄. 이 단계 전에 APT는 이미 3.0.4로 검증 설치된다([SECURITY-APT-20261105.md](SECURITY-APT-20261105.md)) |
| `frontend/Dockerfile` | `apk upgrade` 목록에 `zlib`을 더한다. 실행 기반을 `nginx:1.30-alpine@sha256:e9b2d762…`(Alpine 3.24.4)로 바꾼다 | brotli-libs·curl·libexpat과 같은 줄. 기반 색인은 2026-11-06부터 `Dockerfile.render`가 쓰던 것 |
| `frontend/Dockerfile.render` | `apk upgrade` 목록에 `zlib`을 더한다 | Render는 2026-11-06부터 정지 상태다. 다음에 Render를 다시 켜고 배포할 때 적용된다 |
| `deploy/expected-images/{backend,frontend}.json` | CI가 이 변경으로 빌드한 완성 이미지의 목록으로 바꾼다 | DEPLOY.md 11장 — 손으로 맞추지 않는다 |
| `docs/DEPLOY.md` 11장 | 기반 표의 frontend 실행 기반 줄 | 기반을 바꿀 때 같은 PR에서 |

- **backend 기반 digest**: 바꾸지 않는다. 기반을 바꾸면 다른 패키지도 함께 바뀌므로, 이번에는 대상 패키지만 올린다.
- **로컬 nginx 기반만 맞춰서는 zlib이 고쳐지지 않는다**: Alpine 3.24.4 기반에도 zlib은 1.3.2-r1이고, 고치는 것은 apk 줄이다. 기반을 맞춘 것은 로컬과 Render가 같은 판을 쓰게 하려는 것이다(소유자 결정 2026-11-09). 새 기반에는 2026-11-06 Render 쪽 검증에서 본 brotli-libs·curl·libexpat 수정판이 이미 들어 있어서, 기존 apk 목록의 그 항목들은 아무것도 바꾸지 않는다.
- 운영 컨테이너에 직접 설치하지 않는다. 스캐너 예외로 덮지 않는다.

## 3. 바뀌는 것 — CI image-gate run 42634852866 (후보 `977e5fd`, 검증 목록 갱신 전)

완성 이미지 목록(`scripts/image_inventory.py`)을 이전 검증 목록과 대조했다.

| 이미지 | 다른 항목 |
|---|---|
| backend | **1건** — `os_packages.zlib1g` `1:1.3.dfsg+really1.3.1-1+b1` → `1:1.3.dfsg+really1.3.1-1+deb13u1` |
| frontend | **7건** — `os_release` 3.24.3 → 3.24.4(`detail`·`version_id`) · `alpine-release` 3.24.3-r0 → 3.24.4-r0 · `apk-tools`·`libapk` 3.0.8-r0 → 3.0.9-r0 · `tzdata` 2026e-r0 → 2026f-r0 · **`zlib` 1.3.2-r1 → 1.4-r0** |

- 그 밖의 실행 환경은 같다. Python 판, 의존성, CA 묶음, psycopg 내장 라이브러리, nginx 판이 해당한다.
- 같은 run의 격리 기동 점검은 22/22 통과했다.
- 같은 run의 Trivy 0.76.0(DB 2026-11-09T13:58Z)을 직전 후보 run 42625127617(같은 날 DB 06:57Z)과 비교했다.
  - backend: 189 → 186항목. 사라진 것은 위 zlib1g 세 건이고, **새로 생긴 항목은 0**, 수정판이 있는 항목도 0이다.
  - frontend: 1 → **0항목**.
- 검증 목록 두 개를 이 run의 목록으로 바꿨다. 출처는 `verified.ci_run`·`commit`에 적혀 있다.

## 4. 이 변경을 걷어낼 조건

- **backend**: 공식 `python:3.11.x-slim-trixie` 기반에 `zlib1g` ≥ `…+deb13u1`이 들어오면, 기반 digest를 바꾸는 PR에서 다음을 함께 한다.
  - apt 줄의 `zlib1g`와 판 비교를 뺀다.
  - 검증 목록을 갱신하고 CI 검증을 다시 한다.
- **frontend**: apk 줄은 기반이 수정판을 담으면 아무것도 바꾸지 않으므로 그대로 둬도 된다. 정리는 다음에 기반을 바꿀 때 함께 한다.
- 자동 갱신은 두지 않는다.

## 5. 이 변경에 들지 않은 것

- 같은 검사에서 backend `libsqlite3-0` 항목은 Debian에 수정판이 없어 이번에 바꾸지 않았다. 수정판이 나오면 같은 방식(보안 갱신 줄 + 검증 목록 갱신)으로 다룬다.
- 같은 검사의 나머지 backend 항목(util-linux 계열·ncurses 등)도 수정판이 없다(Trivy `affected`·`fix_deferred`).
