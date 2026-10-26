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

- Render 프런트 이미지는 API 인증서를 검증하지 않습니다 (지금 이미지도 같음) — 켜려면 실제 Render 인증서 체인 시험이 필요.
- 프런트 health `/api/health`는 프런트 가용성을 API에 묶습니다 — `/`를 고를 수도 있음.
- Render 실제 인스턴스(0.1 CPU)에서 메모리·시간은 재지 않았습니다 (격리 512MiB 컨테이너에서 최대 353MiB).

## 7. 승인해 주실 것

1. 경로 (A) 또는 (B)와 그 이유
2. Neon 프로젝트 생성·앱 계정 (소유자 작업)
3. 위 4장 순서의 실행 (설정 저장·배포 각 한 번), 프런트 health 경로 선택
