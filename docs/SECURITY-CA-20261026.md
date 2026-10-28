# 인증서 신뢰 저장소 점검과 보완 — 2026-10 eMudhra(emSign) 인증서 사고 (2026-10-26)

PR `fix/tls-trust-emudhra`(#50)의 조사표·변경·검증 기록입니다. 기준 트리는 #42 HEAD `185f0ba0de2f698fe1cf21a9c6cd7f349f71b5d2`(코드 `77c9b8f7ee73732ef4a06712ab8b86f9e14f794b`)이고, 격리 러너(GitHub Actions ubuntu-24.04, 공개 저장소 무료) 결과만 근거로 씁니다. **main 병합·운영 배포·Render 설정 변경은 하지 않았습니다.** 이 문서는 운영 배포 승인이 아닙니다.

## 1. 사고와 공식 권고 (2026-10-26 확인)

| 출처 | 내용 |
|---|---|
| Mozilla 보안 블로그·Bugzilla 2083449 (2026-10-20) | eMudhra가 운영하는 중간 인증서 8개(TLS 6·코드 서명 2)의 개인 키 손상. TLS 중간 인증서는 **`emSign ECC Root CA - G3`** 아래이며 NSS 3.131.1이 그 뿌리의 신뢰를 긴급 제거. `emSign Root CA - G1`은 (그 시점에) 제거하지 않음 |
| Render 안내 `render.com/docs/advisory-emudhra` (2026-10-22) | Node 22.23.4 / **24.22.1** / 26.12.1 이상, Python certifi **2026.10.19** 이상, Docker 이미지는 배포판 `ca-certificates`와 언어별 CA 의존성을 올려 다시 빌드·배포. 공식 시험 끝점은 없고 커뮤니티 시험 호스트 `dv-g3b-emudhra.we-have-a-problem.com`·`dv-g3b-aaa-emudhra.we-have-a-problem.com`(연결이 **실패해야** 정상) |
| Python 공식 안내 discuss.python.org (2026-10-20~21) | pip는 항상 내장 certifi를 함께 쓰므로 **pip 26.2.2 이상** 필요. 안전한 올림: PyPI 파일 서버 고정 주소 + `#sha256=`으로 설치. CPython 3.11.18 등 보안 판. Debian은 `ca-certificates` 보안 갱신 배포 |
| Node.js 24.22.1 (2026-10-21, 보안 판) | 뿌리 인증서를 NSS 3.131.1로 갱신 |
| nginx 문서 `ngx_http_proxy_module` | `proxy_ssl_verify` 기본값 **off**. `proxy_ssl_server_name`은 SNI만 보냄. 검증에는 `proxy_ssl_trusted_certificate`가 필요 |
| Oracle CVE-2026-91403 (Java) | 2026-10-26: 이 환경에서 Oracle 안내 페이지를 읽지 못함(403)·검색 결과 없음. 저장소는 Java를 **직접 쓰지 않으므로**(아래 2장) 적용 대상이 없음 — Java 범위는 '직접 사용 없음'으로만 기록 (10-26 기록 그대로 둠). **2026-10-27 보완은 아래 1-1** |

### 1-1. 2026-10-27 다시 확인한 것

| 출처 | 내용 | 확인 수준 |
|---|---|---|
| Oracle CVE-2026-91403 공식 안내 `oracle.com/security-alerts/alert-cve-2026-91403.html` | 검토자(10-27 지시문)는 읽을 수 있었다고 했으나 **이 작업 환경에서는 다시 403** — 공식 원문은 인용하지 않는다. 2차 출처(CVE Watchtower, 게시 2026-10-21·수정 10-24): **Java SE 8u511·11.0.33·17.0.21·21.0.13·25.0.5·27.0.1, GraalVM for JDK 17.0.21·21.0.13, GraalVM EE 21.3.20의 JSSE 구성 요소**, CVSS 3.1 9.1(AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N), 인증 없는 네트워크 공격자. 그 요약에 eMudhra·cacerts 언급은 없음 | 2차 출처 요약 — 공식 원문 미확인 |
| Oracle 공식 안내 — **2026-10-27 2차(검토자 전달값)** | 검토자(2차 지시문)가 공식 Security Alert 원문에 접근해 전달: CVE-2026-91403은 Oracle Java SE/GraalVM의 **JSSE**(Java의 TLS 구현)에 대한 권고이며, 영향을 받는 eMudhra 중간 CA의 불신 조치를 설명함. **이 작업 환경에서는 같은 날 다시 시도해도 403**이라 원문 인용은 전달값에 한정하고 위 403 기록은 당시 사실로 보존 | 검토자 전달(2026-10-27) — 이 환경 미접근 |
| 구분 | **Java/JSSE 제품 권고**(Java 런타임의 TLS 구현을 쓰는 프로그램이 대상)와 **공통 CA 사고**(eMudhra 중간 인증서 키 손상 — Node·Python certifi·OS CA·Java cacerts 등 *모든* TLS 클라이언트의 신뢰 저장소가 대상)는 다르다. 이 저장소의 이미지·앱에는 Java가 없어 앞의 것은 적용 대상이 없지만, **뒤의 것은 Java 유무와 상관없이 Node 빌드·pip/certifi·OS CA 묶음에 영향이 있어 이 PR(2·3장)로 보완했다** — 'Java가 없으니 영향 없음'으로 읽지 않는다 | 판단 |
| Java가 있는 곳 | 이미지·compose·CI 워크플로에는 없음(2장 표). GitHub 호스트 러너 이미지에는 JDK가 깔려 있으나 이 저장소의 워크플로는 Java를 실행하지 않음. ZAP·Nu HTML 검사기(둘 다 Java)는 실행하지 않았거나 로컬 진단 전용 — 다시 쓸 때는 그 JRE를 위 수정판 이상으로. 이 개발 PC에는 PATH의 `java`가 없음(10-27 확인) | 저장소·PC 확인 |
| Render 안내 `render.com/docs/advisory-emudhra` (10-27 다시 읽음) | 변경 이력은 10-22 게시 한 건뿐 — 권장 판(Node 24.22.1·certifi 2026.10.19 등)·Docker 할 일(배포판 `ca-certificates`·언어별 CA 갱신 뒤 다시 빌드)은 같음. "재배포 후 인증서 거부를 확인하는 것을 강하게 권장", "테스트는 **실행 중인 Render 서비스의 HTTP 클라이언트**에서"(셸의 curl은 다른 CA 저장소일 수 있음) — 그래서 이 문서의 격리 러너 확인(4장)·후보 이미지의 실제 상류 확인(6장)은 실제 Render 인스턴스 확인을 대신하지 않는다 | 공식 문서 |
| 앱 런타임의 certifi | 앱 실행 의존성(`backend/requirements.txt`)에 certifi·requests·httpx가 없음 — 앱의 python TLS는 OS CA 묶음(Debian `ca-certificates`)을 씀. certifi는 빌드 때 pip 내장분(2026.10.19)만 | 저장소 확인 |

## 2. 조사표 — 런타임·빌드·CI·진단 도구별 TLS 클라이언트와 신뢰 저장소

| 구역 | 구성요소 | TLS로 연결하는 곳 | 신뢰 저장소 | 사고 전 상태 (185f0ba) | 이번 보완 |
|---|---|---|---|---|---|
| 앱 런타임 | Render 프런트 nginx `/api/` → API (`frontend/nginx.render.conf`) | `https://moajwo-test-api.onrender.com/` | OS CA(Alpine `ca-certificates-bundle`) — **쓰지 않음**(`proxy_ssl_verify` 기본 off, 자체 서명 대역 API도 통과) | 암호화만, 상대 확인 없음 | `proxy_ssl_verify on` + 신뢰 묶음·이름·깊이·프로토콜 (3장) |
| 앱 런타임 | 로컬 프런트 nginx → backend | `http://backend:8000` (compose 내부망) | 해당 없음 | — | 없음 (묶음 갱신만 같이) |
| 앱 런타임 | backend `urllib` → Ollama / (소유자가 고른 경우) 외부 LLM | 기본 `http://`(Ollama) · 외부 LLM은 https | OS CA(Debian `ca-certificates`) — certifi 패키지 없음 | Debian 20250419+deb13u1 | `apt --only-upgrade ca-certificates`(이미 최신이면 그대로) |
| 앱 런타임 | backend psycopg → 외부 PostgreSQL(제안, 아직 연결 안 함) | 문서 기본안은 `sslmode=verify-full`(+ `sslrootcert=system`·`channel_binding=require`, `docs/EXTERNAL_DB.md` 3-2; 2026-10-27 2차에서 앞 표의 `require` 잔재를 정리) | 주소의 옵션 그대로 — **프로그램이 verify-full을 강제하지는 않음**(`config._database`는 문자열을 전달만 함): 기본안은 문서·승인 묶음·환경 변수 값으로 지킨다 | — | 격리 시험(유효·만료 인증서 매트릭스, 원인별 판정, 앱·dbcheck·Alembic·dbcopy 진입 경로)은 별도 PR(`docs/pg-tls-verify-full`) |
| 빌드 | backend `pip install` | PyPI | pip 내장 certifi (기반 이미지 pip **24.0** — 옛 묶음) | 해시 검증 없음 | 의존성 전에 **pip 26.2.2**를 고정 주소+sha256으로 설치 → `--require-hashes`로 의존성 설치 (3장) |
| 빌드 | frontend `npm ci` (`node:24-alpine`) | npm registry | Node 내장 뿌리 저장소 | 떠도는 태그(2026-10-22 실제 판 v24.22.1) | `node:24.22.1-alpine@sha256:06d7adf3…` 색인 digest 고정, 판·emSign/eMudhra 뿌리 수 기록 |
| 빌드 | nginx 스테이지 `apk upgrade` | Alpine CDN | 이미지 `ca-certificates-bundle` + apk 서명 | brotli-libs·curl·libcurl만 | `ca-certificates ca-certificates-bundle` 추가 |
| 빌드 | Docker 데몬의 기반 이미지 pull | docker.io | 데몬 호스트 OS CA(CI 러너 / 운영 PC Docker Desktop) | 떠도는 태그(digest는 CI가 기록) | Node만 digest 고정; nginx·python은 기록 유지 |
| 개발·CI | GitHub Actions 러너·액션(SHA 고정)·Trivy·apt·docker | github.com·ghcr.io·docker.io·pypi.org·apt.postgresql.org | 러너 OS CA / 러너 Node / setup-python의 pip | GitHub 관리 | 없음(러너 운영 사안). 진단 워크플로 `hashes.yml`은 같은 pip 부트스트랩을 씀 |
| 개발·CI | 개발 PC `pip install -r requirements-dev.txt`·`npm ci` | PyPI·npm | 개발 PC의 pip·Node | 저장소가 정하지 않음 | 없음(권고: 개발 PC pip ≥ 26.2.2) |
| 진단 도구 | `diagnostics/ci/*`·`diagnostics/scripts/*`(curl·urllib·Playwright) | `127.0.0.1` http 격리 스택 | 해당 없음 | — | 없음 |
| Java | — | — | — | **직접 사용 없음**: 소스·Dockerfile·compose·requirements·package.json·scripts·ci 워크플로에 Java/JDK/JRE/Maven/Gradle/jar 0건. 언급은 ZAP 프로필(미실행), Nu HTML 검사기 컨테이너(로컬 진단 2차, 외부 조회 차단), Trivy `--skip-java-db-update`뿐 | 해당 없음 |

## 3. 바꾼 것

| 파일 | 변경 |
|---|---|
| `frontend/nginx.render.conf` | `/api/`: `proxy_ssl_verify on; proxy_ssl_verify_depth 3; proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt; proxy_ssl_server_name on; proxy_ssl_name moajwo-test-api.onrender.com; proxy_ssl_protocols TLSv1.2 TLSv1.3;` 검증 실패는 502로 끝나고 검증을 끄거나 http로 돌아가는 우회는 없음. 깊이 3은 시험한 체인(뿌리 → 중간 1 → 서버)과 공개 CA의 보통 체인(중간 1~2)을 받는 값 |
| `frontend/Dockerfile.render`·`frontend/Dockerfile` | 빌드 이미지 `node:24.22.1-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53`(2026-10-22 Docker Hub 조회, 그날의 `24-alpine`과 같은 색인), `node --version`·`npm --version`·내장 뿌리 가운데 emSign/eMudhra 수를 빌드 기록에, `apk upgrade`에 `ca-certificates ca-certificates-bundle` |
| `backend/Dockerfile` | `apt-get --only-upgrade ca-certificates` → pip 26.2.2를 `https://files.pythonhosted.org/…/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa…`로 설치(pip가 받은 내용의 sha256을 대조 — 전송 구간이 가로채여도 다른 내용은 설치되지 않음) → python·pip·pip 내장 certifi 판 기록 → `pip install --require-hashes -r requirements-linux-hashes.txt` → 설치 도구 제거(기존) |
| `backend/requirements-linux-hashes.txt` | `requirements.txt`(185f0ba와 같음)의 26개 패키지에 linux x86_64/CPython 3.11 휠 sha256을 붙인 목록 — 진단 워크플로 `hashes.yml`(pip 26.2.2로 `pip download` → sha256, run 40282942270)이 만듦. Windows 개발 설치는 `requirements.txt` 그대로 |
| `diagnostics/ci/image_check.sh` | 대역 API를 시험 CA 체인으로 서명하고 앞단의 신뢰 묶음 자리에 시험 뿌리만 든 파일을 읽기 전용으로 덮어 검증 매트릭스(4장) 실행, 공개 검증 호스트, CA 묶음·빌드 기록 조사 |

바꾸지 않은 것: nginx·python 기반 태그(떠도는 태그 + digest 기록 유지), requirements.txt의 판(해시만 붙임), 앱 코드, Render 서비스 설정(실제 서비스 `moajwo-test`는 다른 분기 `test/render-20261021`의 `Dockerfile.render-test`를 쓰며 그 설정에는 `proxy_ssl_verify`가 없음 — 저장소 표준 Dockerfile을 고친 것과 실제 서비스에 적용된 것은 다름; 적용은 Render 전환 승인 뒤).

## 4. 검증 — 격리 러너의 실제 최종 이미지 (run 40283935369, 후보 `415c114`)

2차 실행 run 40283935369(후보 `415c114`, 2026-10-26 07:08~07:15 UTC). 1차 run 40282839145(`b9e9d08`)는 같은 결과였으나 하네스의 판정 두 곳(backend 도달을 쿼리로 세어 항상 0, 대조군 분류가 entrypoint 줄에 걸림)을 고친 뒤 다시 돌린 것이 2차입니다. 두 run 모두 `backups/review-20261026b/ci-image-check-tls-*`에 있습니다.

### 4-1. 검증 기능 자체 — 시험 CA 체인(뿌리 → 중간 → 서버), 앞단 신뢰 묶음 = 시험 뿌리 하나

| 경우 | 기대 | 결과 (앞단 응답/재시도 · backend 도달 · nginx 오류 기록) |
|---|---|---|
| 신뢰 CA + 올바른 서버 이름 | 전달됨 (backend가 404로 답함) | 404/404 · backend 도달 2 · 오류 기록 없음 |
| 신뢰하지 않는 CA(자체 서명 뿌리가 서명) | 거절·미전달 | 502/502 · backend 도달 0 · [error] 23#23: *4 upstream SSL certificate verify error: (21:unable to verify the first certificate) while SSL handshaking to upstream, client: 1 |
| 만료 인증서 | 거절 | 502/502 · backend 도달 0 · [error] 23#23: *4 upstream SSL certificate verify error: (10:certificate has expired) while SSL handshaking to upstream, client: 172.18.0.1, serv |
| 서버 이름 불일치 | 거절 | 502/502 · backend 도달 0 · [error] 24#24: *4 upstream SSL certificate does not match "moajwo-test-api.onrender.com" while SSL handshaking to upstream, client: 172.18.0.1, s |
| 필수 중간 인증서 누락(서버 인증서만 보냄) | 거절 | 502/502 · backend 도달 0 · [error] 23#23: *4 upstream SSL certificate verify error: (21:unable to verify the first certificate) while SSL handshaking to upstream, client: 1 |
| 인증서 검증 실패 뒤 재시도 | 같은 거절(우회 없음) | 위 네 경우 모두 `502/502` — 둘째가 재시도. 설정에 `proxy_ssl_verify off` 0개 (`1 · off 0`) |
| 정상 체인으로 복구 | 전달됨 | 404/404 · backend 도달 2 · 오류 기록 없음 |
| 복구 뒤 기존 흐름 | 로그인·쿠키·헤더·업로드·내려받기 | health `{"status":"ok","database":"ok"}` · 로그인 `200 · me 200` · 쿠키 `1개 · set-cookie: moajwo_session=<값>; HttpOnly; Max-Age=43200; Path=/; SameSite=strict` · 헤더 `4/4`/`4/4` · 업로드 `id=2` · 내려받기 `200 application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| 시험 체인 깊이 | 뿌리→중간→서버 통과 | /tmp/tmp.OW6FvqKn9o/good.crt: OK · 체인 뿌리→중간→서버 (중간 1) |
| nginx 설정 | 지시어 5종 | proxy_ssl_verify_depth 3; proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt; proxy_ssl_server_name on; proxy_ssl_name moajwo-test-api.onrender.com; proxy_s |

### 4-2. 손상된 체인 거절 — 공식 안내가 가리키는 공개 시험 호스트 (인증 정보 없이 GET 한 번씩)

| 클라이언트 | 호스트 | 기대 | 결과 |
|---|---|---|---|
| Render 앞단 이미지의 nginx (자기 OS CA 묶음) | dv-g3b-emudhra.we-have-a-problem.com | verify 실패·502 | 502 · verify 실패 · [error] 21#21: *1 upstream SSL certificate verify error: (20:unable to get local issuer certificate) while SSL handshaking to upstre |
| 같음 | dv-g3b-aaa-emudhra.we-have-a-problem.com | verify 실패·502 | 502 · verify 실패 · [error] 21#21: *1 upstream SSL certificate verify error: (20:unable to get local issuer certificate) while SSL handshaking to upstre |
| 같음 | example.com (대조군) | 200 | 200 · 연결됨(오류 없음) ·  |
| backend 이미지의 python `urllib`(앱이 쓰는 클라이언트, OS CA) | dv-g3b-emudhra… | CERTIFICATE_VERIFY_FAILED | verify 실패 · URLError <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1016)> |
| 같음 | dv-g3b-aaa-emudhra… | CERTIFICATE_VERIFY_FAILED | verify 실패 · URLError <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate (_ssl.c:1016)> |
| 같음 | example.com (대조군) | status 200 | 연결됨 · status 200 |

DNS 실패·연결 시간 초과·404는 인증서 거절로 세지 않도록 오류 기록의 verify 문구로만 판정했습니다. 이 결과는 '손상된 중간 인증서 체인을 믿지 않음'의 확인이며 사고 전체의 차단 증명은 아닙니다.

### 4-3. 신뢰 저장소·빌드 기록 (실제 판)

| 항목 | 결과 |
|---|---|
| Render 앞단·로컬 앞단 OS CA 묶음 (Alpine) | 인증서 118개 · emSign/eMudhra 1개 [emSign Root CA - G1;] · sha256 5961c0a3c6545993 — 패키지 ca-certificates-20261020-r0 ca-certificates-bundle-20261020-r0  (손상된 중간을 서명한 `emSign ECC Root CA - G3` 없음; 남은 `emSign Root CA - G1`은 Mozilla가 신뢰를 유지한 뿌리) |
| backend OS CA 묶음 (Debian) | 인증서 148개 · emSign/eMudhra 3개 [emSign ECC Root CA - C3;emSign Root CA - C1;emSign Root CA - G1;] · sha256 e9c7b63ee2623386 — 패키지 ca-certificates 20250419+deb13u1 (`emSign ECC Root CA - G3` 없음; C1·C3·G1은 Mozilla 긴급 제거 대상이 아님 — Bugzilla 2083449 기준) |
| 빌드 기록 | 11.19.1 Python 3.11.17 ca-certificates 20250419+deb13u1 ca-certificates is node root certificates 118 emSign/eMudhra 1 pip 26.2.2 from pip-vendored certifi 2026.10.19 v24.22.1  |
| 이전 레이어 재사용 | backend CACHED=0 frontend CACHED=0 frontend-render CACHED=8  — 새 러너라 재사용 없음, frontend-render는 같은 run의 frontend 빌드 단계만 공유 |
| 기반 nginx digest | ["nginx@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951"] |
| 최종 이미지 ID (이 run) | frontend `sha256:07e2fb79b3a4` · frontend-render `sha256:d18351edaeaf` · backend (images.txt) |
| 설치 패키지 변경(기반 대비) | < brotli-libs-1.2.0-r1 > brotli-libs-1.2.1-r0 < curl-8.22.0-r0 > curl-8.23.0-r0 < libcurl-8.22.0-r0 > libcurl-8.23.0-r0  |
| 메모리 경계 컨테이너 | low 0 high 0 max 0 oom 0 oom_kill 0 oom_group_kill 0  · running=true OOMKilled=false restarts=0 |

### 4-4. 스캐너 — Trivy 0.76.0, DB 2026-10-26T01:20:01Z (이미지 digest는 위 표)

| 대상 | 결과 |
|---|---|
| frontend (nginx) | HIGH+CRITICAL 0개 (항목 0, 수정판 있음 0) · 대상 CVE 검출 없음 · 모든 심각도 0 개 · OS alpine 3.24.3 · 검사 대상 ['alpine'] · DB 2026-10-26T01:20:01 |
| frontend-render (nginx) | HIGH+CRITICAL 0개 (항목 0, 수정판 있음 0) · 대상 CVE 검출 없음 · 모든 심각도 0 개 · OS alpine 3.24.3 · 검사 대상 ['alpine'] · DB 2026-10-26T01:20:01 |
| backend (python:3.11-slim, Debian 13.7) | HIGH+CRITICAL 8개 (항목 44, 수정판 있음 0) — 이번 CA 조치로 바뀌지 않음. 고유 ID·사용 경로·미조치 사유는 아래 |

backend 잔여 HIGH·CRITICAL (고유 ID, 수정판 있는 것 0 — Debian 안정판에 수정이 없어 미조치; 앱은 이 라이브러리를 직접 호출하지 않고 기반 이미지의 OS 패키지로만 들어 있음. 기반 이미지가 수정판을 담으면 재빌드로 반영):

| ID | 심각도 | 패키지·설치판 | 수정판 | 제목 |
|---|---|---|---|---|
| CVE-2025-69720 | HIGH | libncursesw6 6.5+20250216-2, libtinfo6 6.5+20250216-2, ncurses-base 6.5+20250216-2, ncurses-bin 6.5+20250216-2 | 없음 | ncurses: ncurses: Buffer overflow vulnerability may lead to arbitrary  |
| CVE-2026-16742 | HIGH | libsystemd0 257.13-1~deb13u1, libudev1 257.13-1~deb13u1 | 없음 | systemd: systemd-homed: Local privilege escalation via missing home-re |
| CVE-2026-54369 | HIGH | libacl1 2.3.2-2+b1 | 없음 | acl: Symlink traversal privilege escalation via libacl functions |
| CVE-2026-76642 | HIGH | bsdutils 1:2.41.5-0+deb13u1, libblkid1 2.41.5-0+deb13u1, liblastlog2-2 2.41.5-0+deb13u1, libmount1 2.41.5-0+deb13u1, libsmartcols1 2.41.5-0+deb13u1, libuuid1 2.41.5-0+deb13u1, login 1:4.16.0-2+really2.41.5-0+deb13u1, mount 2.41.5-0+deb13u1, util-linux 2.41.5-0+deb13u1 | 없음 | util-linux: util-linux: failed external mount helper still runs privil |
| CVE-2026-78408 | HIGH | bsdutils 1:2.41.5-0+deb13u1, libblkid1 2.41.5-0+deb13u1, liblastlog2-2 2.41.5-0+deb13u1, libmount1 2.41.5-0+deb13u1, libsmartcols1 2.41.5-0+deb13u1, libuuid1 2.41.5-0+deb13u1, login 1:4.16.0-2+really2.41.5-0+deb13u1, mount 2.41.5-0+deb13u1, util-linux 2.41.5-0+deb13u1 | 없음 | util-linux: util-linux: nsenter --join-cgroup leaks root cgroup migrat |
| CVE-2026-78409 | HIGH | bsdutils 1:2.41.5-0+deb13u1, libblkid1 2.41.5-0+deb13u1, liblastlog2-2 2.41.5-0+deb13u1, libmount1 2.41.5-0+deb13u1, libsmartcols1 2.41.5-0+deb13u1, libuuid1 2.41.5-0+deb13u1, login 1:4.16.0-2+really2.41.5-0+deb13u1, mount 2.41.5-0+deb13u1, util-linux 2.41.5-0+deb13u1 | 없음 | util-linux: util-linux: X-mount.subdir detached-tree resolution can es |
| CVE-2026-78410 | HIGH | bsdutils 1:2.41.5-0+deb13u1, libblkid1 2.41.5-0+deb13u1, liblastlog2-2 2.41.5-0+deb13u1, libmount1 2.41.5-0+deb13u1, libsmartcols1 2.41.5-0+deb13u1, libuuid1 2.41.5-0+deb13u1, login 1:4.16.0-2+really2.41.5-0+deb13u1, mount 2.41.5-0+deb13u1, util-linux 2.41.5-0+deb13u1 | 없음 | util-linux: util-linux: restricted bind mounts do not pin the source,  |
| CVE-2026-9538 | HIGH | perl-base 5.40.1-6+deb13u1 | 없음 | perl-Archive-Tar: perl-Archive-Tar: Denial of Service via crafted tar  |

스캐너 검출 0은 OS 패키지 취약점 검사 결과일 뿐 인증서 검증·전체 보안 완료를 뜻하지 않습니다 — 인증서 검증은 4-1·4-2로 따로 보입니다.

## 5. 남은 것·미검증·후속

- (10-26 기록) 실제 Render API 서비스의 인증서 발급 기관·체인 길이는 조회하지 않았습니다(외부 요청 금지 범위) — **10-27에 후보 이미지로 실제 체인을 확인했습니다(6장)**. 깊이 3은 여유값이며, 적용 뒤 첫 배포에서 `/api/health`가 200인지로 확인합니다. 체인이 더 길면 502가 나며 그때 깊이를 근거와 함께 올립니다.
- 커뮤니티 시험 호스트 거절은 '손상된 체인을 믿지 않음'의 확인이지, eMudhra 사고 전체의 차단 증명이 아닙니다. 공식 시험 끝점은 없습니다.
- backend 기반 이미지의 잔여 취약점(Trivy HIGH·CRITICAL 8, 수정판 0)은 이번 CA 조치로 바뀌지 않습니다 — 5장 표.
- 외부 PostgreSQL 연결의 기본 정책은 `sslmode=verify-full`입니다(2026-10-27 지시) — 격리 시험·설정 예시·도구별 확인은 별도 PR `docs/pg-tls-verify-full`. `require`는 서버 신원 검증이 아닙니다.
- 운영 PC Docker Desktop의 데몬이 Docker Hub에 어느 신뢰 저장소로 붙는지는 확인하지 않았습니다(호스트 OS 갱신 사안).
- 보안 수정 전 이미지(롤백 태그)로 되돌리면 상류 검증이 없는 상태로 돌아갑니다 — `docs/DEPLOY.md` 8장 참고.

## 6. 실제 Render 경로 (2026-10-27)

### 6-1. 서비스 표 — 2026-10-25 관측(이 환경) + 2026-10-27 13:54 KST 검토자 조회 전달값

2026-10-27 2차 지시문의 검토자가 Render 관리 API로 다시 조회한 값(서비스 상태·배포 기록 조회만, 설정·환경 변수·배포 변경 없음)을 `docs/EXTERNAL_DB.md`
'Render 서비스 — 현재값' 절에 날짜·출처를 붙여 더했습니다: 세 서비스 모두 Auto-Deploy off·plan free·HTTP healthCheckPath 비어 있음, live 배포 커밋은
`moajwo-test` 60bcae8(test/render-20261021) · `moajwo-test-web` 66405ef(main) · `moajwo-test-api` 703fdcb(main). 이 환경에서는 여전히 관리 API로 읽지
못했으므로 아래 표의 '10-25 관측'은 그대로 두고, 전달값은 **검토자 조회 전달값**으로 구분합니다. 저장소에는 GitHub Actions 워크플로·`render.yaml`·배포 훅 호출이
없어(main 기준 `.github/` 없음) Auto-Deploy off 외의 자동 배포 경로는 없으나, Render 쪽 배포 훅 URL의 존재 여부는 관리 화면에서 전환 직전에 확인합니다(값은 비밀값 — 기록하지 않음).

관리 API로 서비스 ID·연결 분기·Dockerfile·Auto-Deploy·공개 주소·상류 API를 읽기 전용으로 다시 확인하라는 지시가 있었으나, **이 작업 환경에는 사용이 승인된 Render 접근 수단이 없어 다시 읽지 못했습니다**(키를 찾는 동작은 하지 않음). 아래는 2026-10-25 관측(승인 자료 `APPROVAL-render-switch-20261026.md` 1장)이며, 전환·배포 직전에 소유자가 대시보드나 승인된 API 키로 다시 확인해야 합니다.

| 서비스 | 역할 | 10-25 관측 | 이 PR이 닿는 경로 |
|---|---|---|---|
| `moajwo-test` | 사용자가 써 온 공개 프런트 | 분기 `test/render-20261021` · `frontend/Dockerfile.render-test` · Auto-Deploy Off | **닿지 않음** — 다른 분기·다른 Dockerfile. 이 PR의 `frontend/Dockerfile.render`·`nginx.render.conf`는 승인된 전환(브랜치·Dockerfile·문맥을 함께 바꿈) 뒤에야 적용됨 |
| `moajwo-test-api` | API(계정 DB 원천 — 지금 SQLite) | 분기 main · `backend/Dockerfile` · Off | 이 PR이 main에 들어간 뒤 **수동 배포할 때** backend 이미지(pip 26.2.2·OS CA 갱신)가 적용됨 — 배포는 원천 보존 결정 뒤 별도 승인 |
| `moajwo-test-web` | 같은 API를 쓰는 다른 프런트 | 분기 main · `frontend/Dockerfile.render` · Off | main 반영 뒤 수동 배포할 때 적용 — 이번 전환 대상 아님(바꾸지 않음) |

Render의 기존 SQLite 원천 보존 여부가 정해지기 전에는 API를 재시작·재배포하지 않습니다(A: 검증된 백업 이관 / B: 새 계정 시작 — 별도 승인).

### 6-2. 후보 이미지가 실제 상류와 통신 (격리 러너 run 40439111560, 분기 `ci/image-check-real-tls-ce2aeaa`, 2026-10-27)

후보 frontend-render 이미지를 **그대로**(설정·신뢰 묶음을 덮지 않고) 써서 실제 공개 API와 통신했습니다. 업무 변경이 없는 health 경로 GET만 했고 쿠키·업로드·계정·설정 변경은 하지 않았습니다.

| 확인 | 결과 |
|---|---|
| TLS 핸드셰이크만(HTTP 요청 없음) — 러너 openssl + 이미지의 OS CA 묶음, 이름 검증 | **통과** — `Verify return code: 0 (ok)`. 서버가 보낸 인증서 3개: `CN=onrender.com` ← `Google Trust Services WE1` ← `GTS Root R4`(발급자 GlobalSign Root CA), 깊이 2 → `proxy_ssl_verify_depth 3` 안. 이미지 OS CA 묶음 118개(emSign은 G1만) |
| 앞단 nginx(이미지 그대로, `proxy_ssl_verify on`) → 실제 API `GET /api/health` | **200** · 첫 시도 · 본문 `{"status":"ok","database":"ok"}` · nginx 오류 기록 없음 (검증 통과·이름 일치). 같은 run의 4-1 대역 매트릭스·4-2 공개 시험 호스트 결과는 10-26과 같음(거절 4종 502·복구 404, eMudhra 호스트 nginx 502(20)·python CERTIFICATE_VERIFY_FAILED). 빌드 기록의 Python은 3.11.18(기반 이미지 `python:3.11-slim`이 10-26 3.11.17에서 올라감 — 고정하지 않은 기반 태그의 변화, 2장 '빌드' 행) |

이것은 "후보 이미지가 실제 상류의 정상 체인·호스트 이름을 검증하고 통신한다"는 확인입니다. **실제 Render 인스턴스가 새 CA·설정을 쓴다는 확인이 아니며**, 실제 인스턴스에서 손상 체인을 거절하는지(Render 안내가 권하는 '실행 중인 서비스의 HTTP 클라이언트'로 시험 호스트 요청)는 승인된 배포 뒤 항목으로 남깁니다. DNS 실패·시간 초과·HTTP 오류는 인증서 거절로 세지 않습니다.

### 6-3. 검증 실패 때 하지 않는 것

앞단의 상류 인증서 검증이 실패하면 `/api/*`는 502로 끝납니다. `proxy_ssl_verify off`, `curl -k`, HTTP로 되돌리기 같은 우회로 복구하지 않습니다 — 원인(상류 인증서·이미지 CA 묶음·이름)을 고친 이미지로 다시 배포합니다.
