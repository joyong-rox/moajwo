# 승인 자료 — Render 전환 (계정 보존용 외부 DB, 2026-10-26)

이 문서는 승인을 요청하는 자료입니다. **Render 설정·환경 변수·배포는 바꾸지 않았고, Render·Neon에 접근하지 않았습니다.** 로컬 운영 배포는 따로 승인합니다 — [APPROVAL-local-deploy-20261026.md](APPROVAL-local-deploy-20261026.md). 절차 원문은 `docs/EXTERNAL_DB.md` 8장.

## 1. 대상 서비스 (2026-10-25 관리 API 읽기 조회 — 당시 관측, 전환 직전에 다시 확인)

| 서비스 | 역할 | 지금 | 전환에서 |
|---|---|---|---|
| `moajwo-test` | 프런트 — 사용자가 써 온 https://moajwo-test.onrender.com/ | `test/render-20261021` · `frontend/Dockerfile.render-test`(nginx 1.29 root·80, 보안 헤더 없음) · Auto-Deploy Off · health 없음 | 승인된 릴리스(main)의 `frontend/Dockerfile.render`(nginx 1.30·비루트·8080·보안 헤더, 기반 이미지 취약 판 brotli-libs·curl·libcurl은 수정판으로) — **브랜치·Dockerfile·문맥을 함께**, health `/api/health` 또는 `/` |
| `moajwo-test-api` | API — 계정 DB(지금 SQLite)의 원천 | main · `backend/Dockerfile` · Off · health 없음 | 같은 릴리스, 외부 DB 환경 변수("Save only"), health `/health` |
| `moajwo-test-web` | 프런트 (같은 API) | main · `frontend/Dockerfile.render` · Off | **바꾸지 않음** (삭제·중지하지 않음 — 별도 과제) |

Auto-Deploy Off는 이 조회 기준으로 병합만으로 재배포되지 않는다는 뜻이며, 수동 배포·환경 변수 적용의 승인이 아닙니다.

## 2. 원천과 경로 — 소유자 결정

| | (A) 기존 계정 이관 | (B) 새 계정 등록 |
|---|---|---|
| 조건 | `moajwo-test-api`가 쓰던 Render DB의 **검증된 백업**이 있을 때만 | 백업이 없거나 새로 시작하기로 정했을 때 |
| 지금 | 이 저장소 기록에 그 백업 없음. 현재 앱에서 해시째 꺼낼 무료 수단 없음 (내보내려고 재배포하면 SQLite가 지워짐) | — |
| 전환 뒤 | `setup_required: false`, 이관한 계정 로그인 (세션은 옮기지 않음 — 다시 로그인) | `setup_required: true`가 정상, 최초 소유자부터 새로 |

- **구분**: 원천 *서비스*는 `moajwo-test-api`의 Render DB로 정해져 있습니다. 그러나 그 DB의 **이관할 수 있는 검증된 백업이 확보됐다는 뜻은 아니며**, (A) 기존 계정 이관 / (B) 새 계정 등록의 경로는 아직 결정되지 않았습니다(원천이 정해진 것과 백업이 있는 것은 다른 문제).
- 로컬 운영 SQLite 백업을 Render 계정의 백업으로 보지 않고, 두 DB를 합치지 않습니다. 로컬 운영 사용자를 동의 없이 클라우드로 복제하지 않습니다.
- 결정 전에는 API 서비스를 재시작·재배포하지 않습니다 (Render SQLite·파일이 지워질 수 있음).

## 3. 준비 (Render는 그대로) — `docs/EXTERNAL_DB.md` 8-3 1~8

Neon `moajwo-test`(PostgreSQL 17, 무료 — 그날 조건 재확인)·앱 계정 → 사용자 PC의 서비스 파일·비밀번호 파일 → 직접 연결·소유 계정으로 마이그레이션(0020까지) → 같은 DB 확인(`app.dbcheck` `result: same`) → (A) 원천 확보·`app.dbcopy` dry-run(`too_long_values`·`nul_values` 빈 목록, `tz_unverified_runs: 0`, `source_encoding: UTF-8`)·적용·다른 빈 DB로 복원 확인 / (B) 빈 DB 그대로 → Render 설정 읽기 확인(위 표가 그대로인지).

**이번에 확인한 것과 못 한 것**: 이관 도구·같은 DB 확인·0020 상승·거절·하강은 로컬 일회용 PostgreSQL 17에서 확인했습니다. **실제 Neon(연결 풀러·Neon 식별자·휴면 복귀 시간과 health 5초 제한)은 연결하지 않아 미검증**입니다 (계정 접근 수단 없음).

## 4. 전환 순서 (승인 뒤 한 번에) — 8-3 9~13

1. 설정 다시 읽기 (바뀌었으면 멈추고 이 묶음을 고쳐 다시 승인)
2. API 서비스 환경 변수 "Save only" (`MOAJWO_DB_MODE=external`, `DATABASE_URL`=풀러·앱 계정, `MOAJWO_MIGRATION_DATABASE_URL`=직접 연결·소유 계정) — 저장과 함께 배포되는 선택지는 쓰지 않음
3. 프런트(`moajwo-test`) 설정(브랜치·Dockerfile·문맥·health) 저장 → 승인된 커밋 배포 → API 배포 (화면이 이전 API 상태값을 처리 — 순서가 바뀌어도 자료는 바뀌지 않음)
4. 점검: `/health`, `/auth/status` — (A) 이관 계정 로그인 / (B) 최초 소유자 설정, 쓰인 계정·시각 기록
5. 쓰기 재개 시각 기록, 전환 뒤 첫 백업 (`docs/EXTERNAL_DB.md` 7장)
6. health 경로를 넣은 뒤: 요청 없이 15~30분 뒤 서비스가 멈추는지·무료 시간 사용량 확인 — 안 멈추면 health 경로를 비워 되돌림 (상시 기동용 요청은 넣지 않음)

## 5. 보존 범위

- 보존: 계정·비밀번호 해시·역할·업무 기록(A의 경우 이관, B는 새로 만든 것부터) — 외부 DB에.
- **보존하지 않음**: Render 로컬의 원본·통합·묶기 결과 파일 (재배포·재시작·휴면 때 사라질 수 있음 — 파일 보존은 별도 과제). 기록은 남는데 파일이 없으면 '원본 파일이 서버에 없습니다' 안내(410).
- 되돌리기: 외부 DB를 유지한 채 PostgreSQL을 지원하는 이미지로 (SQLite로 돌아가는 복구는 쓰지 않음, 0020을 모르는 이미지로는 되돌릴 수 없음) — 8-4.

## 6. 남은 위험·결정

- Render 프런트 이미지(#42 후보 `77c9b8f`)는 API 인증서를 검증하지 않습니다 (지금 Render 이미지도 같음). 이미지 검사의 Trivy 검출 0은 OS 패키지 취약점 검사 결과일 뿐이며 **인증서 검증이나 전체 보안 완료를 뜻하지 않습니다**. 검증을 켜는 보완은 2026-10-26 후속 지시로 별도 PR(`fix/tls-trust-emudhra`)에서 진행합니다 — 그 PR이 승인·반영되기 전의 이 후보는 검증 없는 상태입니다.
- 프런트 health `/api/health`는 프런트 가용성을 API에 묶습니다 — `/`를 고를 수도 있음.
- Render 실제 인스턴스(0.1 CPU)에서 메모리·시간은 재지 않았습니다. 격리 512MiB 컨테이너의 최대값은 대상·조건별로 다릅니다 — 최종 후보 `77c9b8f`(run 40276874025) **364MiB**(E2: 통합 1건 약 3,168만 자 + 큰 xlsx 올리기 동시, 최종 Render 권장값), 앞 후보 `2b1a995`(run 40265923382) 353MiB(같은 자료·조건). 둘 다 그 자료·겹침 조건의 관측값이며 모든 입력의 최대 메모리 보장값이 아닙니다.

## 7. 승인해 주실 것

1. 경로 (A) 또는 (B)와 그 이유
2. Neon 프로젝트 생성·앱 계정 (소유자 작업)
3. 위 4장 순서의 실행 (설정 저장·배포 각 한 번), 프런트 health 경로 선택

## 8. 2026-10-26 후속 — 보안·NUL·공통 key 취합을 더한 새 후보 (승인 자료 갱신)

앞 장들은 #42 후보 `77c9b8f`/`185f0ba` 기준입니다. 2026-10-26 후속 지시로 세 PR(#50 보안·#51 NUL 입력·#52 공통 key 취합)을 만들었고, 통합 검증 후보는 `rc/review-20261026b` = `8d40c69`(`8d40c6919c765e28941b21b22ac35b998f5b0ecf`, 185f0ba + #50 `5af1c79` + #51 `c90d9a0` + #52 `8594cf1`)입니다. 상세는 `REPORT-20261026b.md`. **#42는 갱신하지 않았고, 병합·배포·Render 전환은 하지 않았습니다.**

- 스키마: 변경 없음(0020 그대로 — #52는 기존 `runs`에 `summary.mode="keyjoin"`으로 기록). 3·4장의 마이그레이션·되돌리기 순서는 그대로 적용됩니다.
- 이미지: #50으로 기반·빌드 방식이 바뀝니다 — node 24.22.1 digest 고정, pip 26.2.2 해시 부트스트랩 + `--require-hashes` 의존성, OS CA 갱신(`emSign ECC Root CA - G3` 없음), Render 앞단의 상류 인증서 검증 `on`(검증 실패면 `/api/*` 502, 자동 우회 없음). #52는 코드만. 6장의 "Render 프런트 이미지가 API 인증서를 검증하지 않음"은 이 후보에서 해소됩니다(실제 Render API 체인은 첫 배포 뒤 `/api/health`로 확인).
- 메모리(512MiB 격리 컨테이너, 보장값 아님): 후보 `8d40c69` run 40303255462 → 40305183443 — E2 1차 416MiB·2차 366MiB, K3 286~295MiB, KL1 396MiB, OOM 0 (1차·2차 값을 모두 적음 — 겹침 타이밍에 따라 달라짐). #52의 key 연결은 경계 바로 아래 생성 185MiB·큰 업로드와 겹침 최대 286MiB(run 40293458078, `dbc1f29` 이미지; 예산식·한도는 그 뒤 바뀌지 않음).
- 검증: 후보 pytest 전체 run 40303213454 SQLite 1025·PG 1028 통과 · 정적 run 40303226195 모두 0 · 롤백 리허설 run 40303239021(판정 1건 리허설 타이밍) → 재실행 40304800343 52건 모두 yes · 이관 46행 run 40303268041 46행 동일(REPORT-20261026b 7-4).
- 롤백 위험(새로 명시): 보안 수정 전 이미지(`185f0ba` 계열)로 되돌리면 앞단 검증·CA 갱신·해시 설치가 없는 상태로 돌아갑니다 — 롤백 판단 때 함께 봐 주세요(`docs/DEPLOY.md` 8장). #52만 되돌릴 때는 만들어진 실행 기록·결과 파일이 남아도 기존 화면이 읽습니다.
- 추가로 승인해 주실 것: (1) 세 PR의 병합 방식(개별 / 후보 한 번에)과 순서 — 보안 수정은 기능을 기다리지 않도록 분리돼 있음, (2) Render PG 주소의 `sslmode=verify-full`·`sslrootcert`(코드 변경 없이 주소로), (3) #52 정책 결정 사항(REPORT-20261026b 7-7 '새 정책'). 2장의 원천·경로 결정은 그대로 남아 있습니다.

## 9. 2026-10-27 후속 — 보안 승인 준비·TLS verify-full·후보 선택 (REPORT-20261027.md 7·9장)

- **후보**: 보안 우선 `b4dedf7`(#42 + #50) 또는 key 포함 코드 `c078bdd`(최종 HEAD는 문서 커밋 — 최종 Draft PR 본문; 1차 `2a95036`·2차 `434d101`을 거친 3차). Render에 올릴 이미지는 그 후보의 `backend/Dockerfile`·`frontend/Dockerfile.render`(둘 다 #50 포함 — 앞단 `proxy_ssl_verify on`, node 24.22.1 digest, pip 26.2.2 해시 설치, OS CA 갱신).
- **실제 상류 확인**: 격리 러너에서 후보 이미지가 실제 `moajwo-test-api.onrender.com`과 체인·이름 검증으로 통신했습니다(run 40439111560·40447071375: `onrender.com ← GTS WE1 ← GTS Root R4`, `/api/health` 200). 이것은 후보 이미지의 확인이지 실제 인스턴스가 바뀐 것이 아닙니다. 전환 뒤 첫 배포에서 `/api/health` 200·앞단 로그에 SSL 오류 없음을 확인합니다.
- **외부 DB 주소(전환 때 입력할 값)**: `sslmode=verify-full&sslrootcert=system&channel_binding=require` — `require`가 아닙니다(`docs/EXTERNAL_DB.md` 3-2). 직접 연결(마이그레이션 `MOAJWO_MIGRATION_DATABASE_URL`·이관·백업)과 풀러(앱 `DATABASE_URL`)를 구분하고, 풀러에서 `channel_binding=require`가 안 되면 **먼저 오류 원인을 구분**하고(`docs/EXTERNAL_DB.md` 3-2 표 — 인증서·호스트 이름·만료·인증·네트워크·채널 바인딩), 실제로 채널 바인딩 미지원이 확인된 끝점만 근거(오류 문구·날짜)와 대안을 붙여 승인 묶음 12번에 **별도 예외**로 올립니다. 자동 재시도나 스크립트가 보안 설정을 낮추는 일은 없고 `verify-full`을 낮추는 예외도 없습니다 *(2026-10-27 2차 정정 — 앞 판의 '풀러 주소에서만 prefer로 낮춘다'는 포괄 절차를 삭제)*. 실제 Neon 끝점의 인증서·풀러 동작은 전환 때 `pg_stat_ssl` 명령으로 처음 확인합니다.
- **Render 서비스 표(1장)**는 10-25 관측 그대로이며 10-27에 다시 읽지 못했습니다(관리 API 접근 수단 없음) — 전환 직전 읽기 확인은 그대로 필요합니다. `moajwo-test-web`은 삭제·중지하지 않습니다.
- **Oracle CVE-2026-91403**은 Java/JSSE 제품 권고로 구분해 기록했고(공식 안내는 이 환경에서 403), Java가 없다는 사실을 Node·Python·OS CA의 영향 없음으로 쓰지 않습니다 — 그쪽은 #50의 CA 갱신·검증이 다룹니다.
- 두 후보 중 어느 쪽을 Render에 올릴지는 로컬 운영 배포 승인(APPROVAL-local-deploy 9장)과 같은 결정입니다 — 서로 다른 HEAD를 로컬과 Render에 올리지 않습니다.

