# APT 보안 수정 — DSA-6607-1 · CVE-2026-109518 (2026-11-05)

backend 실행 이미지의 `apt`·`libapt-pkg7.0`을 Debian trixie 보안 수정판 **3.0.4**로 올리는 변경의 근거·방법·제거 조건입니다. 이 문서는 운영 배포 승인이 아닙니다. 병합과 운영 반영은 따로 판단합니다.

## 1. 결함과 대상

| 항목 | 내용 |
|---|---|
| 공지 | Debian Security Advisory **DSA-6607-1** (2026-11-04 12:07 UTC), CVE-2026-109518. APT가 HTTP 리디렉션 뒤 같은 URI가 된 두 다운로드를 합칠 수 있고, HTTP 다운로드를 재개할 때 파일을 잘못 다뤄 중간자가 **검증이 끝난 패키지를 바꿔치기**할 수 있다(root 권한 코드 실행). trixie 수정판 3.0.4. 같은 갱신이 범위 밖 접근 결함 몇 가지도 고친다. |
| 이 저장소에서 해당하는 것 | backend 이미지(Debian 13 trixie 기반 `python:3.11.18-slim-trixie`)의 `apt`·`libapt-pkg7.0` **3.0.3** — 검증 목록 `deploy/expected-images/backend.json`과 CI Trivy 결과에 둘 다 나온다(같은 CVE, 패키지마다 한 행). frontend(Alpine nginx)에는 APT가 없다. |
| 이 이미지에서 APT를 쓰는 때 | **빌드할 때만** — `Dockerfile`이 `USER app` 전에 `apt-get`으로 `ca-certificates`를 갱신한다. 실행 중인 앱은 APT를 부르지 않고, 컨테이너는 비루트(`app`)로 돈다. 웹 업로드는 APT와 관계없다. |
| 확인하지 않은 것 | 실제 악용, 과거 빌드에서 받은 패키지의 변조. 그런 흔적을 찾은 것은 아니다. Trivy의 `UNKNOWN` 등급은 스캐너 분류이지 수정 여부가 아니다. |

## 2. 기반 이미지 digest만 바꾸지 않은 이유

2026-11-05 10:38 KST에 확인한 결과, 수정판이 든 공식 기반이 없다.

- Docker Hub `python:3.11.18-slim-trixie`의 최신 색인(`sha256:db20d8506e2f…`)도 이미지 생성 시각이 2026-10-28이다. `debian:trixie-slim`도 마지막 갱신이 2026-10-28로, 둘 다 DSA 이전이다.
- 지금 고정한 기반(`sha256:82209229…`)과 최신 색인을 linux/amd64로 받아 네트워크 없는 컨테이너에서 `dpkg-query`로 읽었다. 둘 다 `apt 3.0.3`, `libapt-pkg7.0 3.0.3`이다.

## 3. 방법 — 결함 있는 APT를 거치지 않는 검증 설치

`backend/docker/apt_verified_fetch.py`가 받고 검증하며, `Dockerfile`이 설치한다. 결함판 APT로는 아무것도 받지 않고 APT부터 고친다. 그 뒤의 `apt-get`(`ca-certificates` 갱신)은 3.0.4로 돈다.

1. `https://deb.debian.org/debian-security/dists/trixie-security/InRelease`를 받는다.
   - 기반 이미지에 든 **Debian 저장소 키링**(`debian-archive-keyring` 2025.1)과 **`sqv`**(1.3.0, trixie APT가 쓰는 서명 검증 도구)로 Cleartext 서명을 검증하고, 검증된 본문만 읽는다.
   - `Codename: trixie-security`인지, `Valid-Until`이 지금보다 뒤인지도 확인한다. 옛 메타데이터를 다시 내미는 공격을 막기 위해서다.
2. 그 본문의 SHA256 목록에 적힌 `main/binary-amd64/Packages.xz`를 by-hash 주소로 받아 크기·SHA-256을 대조한다.
3. Packages에서 `libapt-pkg7.0`·`apt` **3.0.4** 항목을 찾는다. 그 SHA-256·크기가 이 저장소에 고정한 값(스크립트의 `PINS`)과 같아야 한다.
4. `.deb` 두 개를 받아 크기·SHA-256을 다시 대조한다.
   - 모든 대조가 맞을 때만 파일을 쓴다. 하나라도 어긋나면 종료 1이고 아무것도 쓰지 않는다.
   - Dockerfile은 `dpkg -i`로 설치한 뒤 **Debian 판 비교**(`dpkg --compare-versions … ge 3.0.4`)로 두 패키지를 확인한다. 못 미치면 빌드가 멈춘다.

| 고정값 (2026-11-05 서명 검증한 trixie-security Packages) | 크기 | SHA-256 |
|---|---|---|
| `pool/updates/main/a/apt/libapt-pkg7.0_3.0.4_amd64.deb` | 1113872 | `577eb8f489de419290102364892dbd13ca256297b61f96d61b114fd4ac18be41` |
| `pool/updates/main/a/apt/apt_3.0.4_amd64.deb` | 1453228 | `15c2030e0fb46580af4f1b4cc303f4bd26da781b004e99763bc9eff7cf8e19b8` |

- **신뢰의 근거**는 두 가지다.
  - (1) digest로 고정한 기반 이미지 안의 키링과 `sqv`.
  - (2) 이 저장소에 커밋한 SHA-256. PyPI 휠을 `#sha256=`으로 고정하는 것과 같은 방식이다.
  - HTTPS 전송은 근거로 삼지 않는다.
- **음성 대조** (2026-11-05, 고정 기반 컨테이너):
  - 서명된 본문의 한 글자를 바꾸면 `sqv`가 종료 1이다("Message has been manipulated").
  - 고정 SHA-256을 바꾸면 스크립트가 종료 1이고, 출력 파일은 0개다.
  - 정상 실행은 위 두 파일을 같은 해시로 쓴다.
- **실패로 닫힘**: 다음 경우에는 빌드가 멈춘다. 그때 `--discover`로 서명된 목록을 다시 보고 고정값을 검토한다.
  - Debian이 더 새 판을 내서 서명된 목록에 3.0.4가 없어진 경우
  - 받은 메타데이터의 `Valid-Until`이 지난 경우
  - 해시가 다른 경우
- 운영 컨테이너에 직접 설치하지 않는다. 스캐너 예외로 덮지 않는다.

## 4. 바뀌는 것

- 실행 환경 항목(`scripts/image_inventory.py`) 차이는 **`os_packages.apt` 3.0.3 → 3.0.4**와 **`os_packages.libapt-pkg7.0` 3.0.3 → 3.0.4**, 두 건이다.
  - Python 판, 앱 의존성 27개, CA 묶음(148개, 같은 해시), OS 패키지 수 87개는 같다.
  - 근거는 2026-11-05 로컬 빌드와 지금 운영 이미지의 목록 비교다. CI 결과는 PR 본문에 적는다.
- 같은 빌드에서 `apt list --upgradable`에 남는 것은 `tzdata`(2026e → 2026f, trixie-updates — 보안 갱신 아님) 하나뿐이고, 이번에 올리지 않는다.
- 검증 목록 `deploy/expected-images/backend.json`은 CI가 검증한 빌드의 목록으로 같은 PR에서 바꾼다(`docs/DEPLOY.md` 11장).

## 5. 이 단계를 뺄 조건

공식 `python:3.11.x-slim-trixie` 기반에 `apt`·`libapt-pkg7.0` ≥ 3.0.4가 들어오면 이 단계를 뺀다. 그때는 한 PR에서 다음을 함께 한다.

- 기반 digest와 `Dockerfile` 설명을 바꾼다.
- 이 단계와 `backend/docker/apt_verified_fetch.py`를 지운다.
- 검증 목록을 갱신하고 CI 검증을 다시 한다.

자동 갱신은 두지 않는다.
