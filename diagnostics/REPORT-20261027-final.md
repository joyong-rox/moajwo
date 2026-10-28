# 최종 승인 준비 — 보고 (2026-10-27 2차)

지시문: `Moajwo_다음작업_최종승인준비_20261027.md` (2026-10-27). 기준은 key 포함 후보 `434d101591da155ddaeda3a23d04a8d57bf9ee5b`였고, 이 지시의 보완을 더한 **최종 승인 대상 HEAD는 이 보고서·승인 자료를 더한 문서 커밋**(값은 최종 Draft PR 본문 머리 — 실행 코드는 `c078bdd` = `c078bdddbef9622ab3075d43d7413232d74adf4d`)입니다. 보안 우선 후보 `b4dedf77bc79779dd771bb61d252df80cc16ff58`는 비교·대안으로 보존합니다(#42 + #50 `d7c0cb2`만 — #51~#57의 기능·이관 보완 없음). **main 병합·로컬 운영 배포·Render 재배포·환경 변수 변경·실제 Neon 생성·이관·운영 자료 수정은 하지 않았습니다.** 1차 보고(`REPORT-20261027.md`, 고정본 `8c02574`)는 그대로 두고 정정이 필요한 두 곳에 '2차 정정' 표시만 더했습니다.

## 1. 실제 수정한 결함 / 시험 판정 보완 / 문구·문서 변경 (지시 9장 1번)

| 구분 | 내용 | 어디 |
|---|---|---|
| **시험 판정 보완** | TLS 매트릭스 판정기 — 앞 판은 예외를 모두 'fail'로 만들어 부정 시험에서 측정 오류(RuntimeError)·비밀번호 오류·연결 거부도 '예상한 거절'로 셈(수정 전 재현 `backups/review-20261027b/pg-tls/before-fix-judge/`: 가짜 드라이버가 RuntimeError만 내는데 거절 기대 6건이 모두 '기대 충족'). 이제 `pg_tls_judge.py`가 정상 연결(서버가 TLS 사용 확인) / 예상한 TLS 거절(원인 일치) / 원인 불일치·거절돼야 하는데 연결됨(**검증 실패**) / 예상 밖 실패·측정 오류(**측정 불완전**)로 나눈다. 원인은 libpq·OpenSSL 핵심 문구로만 가리되, 문구가 같은 두 원인은 문맥으로 가른다 — 만료는 서버가 보낸 인증서를 psycopg와 무관하게 받아(`SSLRequest` 뒤 검증 없는 TLS) `openssl x509 -checkend`로 확인한 값으로, 비암호 거절은 서버 로캘이 달라 문구가 없을 때 SQLSTATE 28000 + 비암호 요청 사실로. 방어 시험 `test_pg_tls_judge.py` 16건 | #56 `diagnostics/ci/pg_tls_judge.py`·`test_pg_tls_judge.py`·`pg_tls_matrix.py` |
| **시험 범위 추가** | 만료 서버 인증서 판(시험 CA가 2024-01-01~02로 서명 — `openssl ca -startdate/-enddate`, 시스템 시각 변경 없음)과 실제 진입 경로 4종(앱 `/health`·`app.dbcheck`·Alembic·`app.dbcopy`: 정상 1건 + 호스트 이름 불일치 거절 1건) | #56 `pg_tls_check.sh`·`pg_tls_paths.py`, 워크플로 `pg-tls (diagnostic)` |
| **시험 중 드러난 실제 사실** | (1) OpenSSL은 만료도 CA 불신도 `certificate verify failed`로만 알림 → 독립 확인 없이는 가를 수 없음(위). (2) `sslmode=require`(root 파일 없음)는 **만료된 서버 인증서에도 연결**됨 — require가 인증서를 검증하지 않는다는 직접 증거. (3) 시험 서버의 메시지 로캘(한국어)에서 pg_hba 거절 문구가 다름 → 시험 서버 `lc_messages=C` + SQLSTATE 보조 판정 | 매트릭스 결과 2장 |
| **실제 수정한 결함(코드)** | 없음 — 앱·마이그레이션·복구·이관 코드는 `434d101` 그대로(`git diff 434d101 c078bdd -- backend/app/*.py backend/alembic`: 문구·docstring만). 이번 지시에서 새로 재현된 앱 결함 없음 | — |
| **문구 변경** | 공통 key 화면·요약 시트·실행 기록·문서의 연결 대상 '사용자' → key·자료: 범위 라벨 '기준 파일의 key만 / 모든 파일의 key / 모든 파일에 공통인 key만', 집계 '결과 n행 · 전체 key n개 · 공통 key n개 · 선택한 범위에 없어 제외된 key n개', 기능 설명·도움말·'기준 파일·시트'. 로그인 사용자·소유자·권한의 '사용자'는 그대로(일괄 치환 아님). API 값·요청 해시·정책(keyjoin-policy-2)·key 비교·저장 구조 불변 | #52 `8dec3d9`·`406b980` |
| **문서 변경** | EXTERNAL_DB: 기본안 표의 `require` 잔재 정리(#50 분기 행)·'프로그램은 강제하지 않음' 명시·풀러 `prefer` 완화 절차 삭제 → 오류 원인 구분 표와 별도 예외 절차·이관 운영 절차(한 번에 하나, 기록 항목, 커밋 불확실 시 재실행 금지, 직접 연결만)·Render 10-27 13:54 KST 전달값·readiness 경로 제안·자동 배포 경로 없음·8-5 12번. SECURITY-CA: Oracle 공식 안내(검토자 전달값, 403 기록 보존)·외부 DB 행·Render 전달값 안내. DEPLOY 10장: 거절 뒤 DB별 상태·멈추는 자료 처리 원칙. KEYJOIN: 정책/안내 구분. APPROVAL 두 건: prefer 정정·후보 선택 | #50 `fd4bd2a`, #56 `bc1cf2d`, #52, #36 |

## 2. 수정 전·후 같은 조건의 결과 — 실제 실행한 것과 재사용한 것 (9장 2번)

| 항목 | 수정 전 | 수정 후 | 실행/재사용 |
|---|---|---|---|
| TLS 판정기 오판 | 가짜 드라이버(RuntimeError) → 거절 기대 6/6 '기대 충족'(`before-fix-judge/`) | 같은 입력 → 6/6 '측정 코드 오류'(충족 0), 요약에 측정 불완전 6 (`test_pg_tls_judge.py`) | 실행 |
| 매트릭스 — 유효 인증서 11가지 | run 40446062371 11/11(당시 판정) | 로컬 11/11 · CI run 40456939092(#56 `bc1cf2d`)·40456979750(후보 `c078bdd`): 11/11 (원인별 판정) | 실행 |
| 매트릭스 — 만료 인증서 4가지 | 없었음 | 로컬 4/4 · CI 4/4: verify-full·verify-ca·root 파일 있는 require는 만료 거절, root 파일 없는 require는 **연결됨** | 실행(새 범위) |
| 진입 경로(앱·dbcheck·Alembic·dbcopy) | 없었음 | 로컬 8/8 · CI 8/8: 정상 TLS에서 조회·마이그레이션·dry-run 성공, 호스트 이름 불일치에서는 실패하며 성공 응답·빈 SQLite 대체·스키마 변경·업무 자료 변경·비밀값 노출 없음 | 실행(새 범위). 1차 실행은 시험 쪽 설정 오류(dbcopy에 DATABASE_URL 미지정 → 도구가 import에서 멈춤)로 2건 어긋남 → 시험 수정 |
| 용어 변경 뒤 화면·Excel | — | 대표 key 흐름 전체(13항목 + 공통) SQLite 한 차례 **통과**(`backups/review-20261027b/ui-terms-sqlite/check.log` — 3·4·5번 범위 흐름과 요약 Excel 라벨, 9번 320·390px·키보드 초점·가로 넘침 포함), backend 관련 시험 94 통과, tsc·oxlint 0 | 실행(SQLite). PostgreSQL 화면은 1차 결과 재사용(문구 변경은 DB와 무관) |
| 0021 연쇄 거절 상태 | 미확인 | SQLite 0020에 남음 / PostgreSQL 0019 그대로 — 시험 2건 추가(통과) | 실행 |
| 0021 사전 점검 | — | 운영 백업 격리 복사본(`moajwo-data-20261019-234406.tgz`, sha256 785db148…, 기준 2026-10-19 23:44 KST): 0019→0020→0021 6초, runs 16건 중 key 연결 실행 0건 → 채움 0·멈춤 0 (`backups/review-20261027b/0021-precheck/precheck.txt`, 복사본 삭제) | 실행 |
| 롤백 리허설 59건 | 2차 후보 run 40449908227 59/59 | 재사용 — 마이그레이션·복구 코드 변경 없음(`git diff 434d101 c078bdd -- backend/alembic backend/app/recovery.py` 0줄) | 재사용 |
| 이미지·Trivy·메모리 | run 40449891138 | 재사용 — Dockerfile·requirements·CA 묶음 변경 없음(diff 0; 앱 문구 변경은 이미지 보안·메모리 특성과 무관) | 재사용 |
| 요청 번호 S1~S9·이관 경합 | 13/13 ×2 · 18/18 | 재사용 — 해당 코드 변경 없음 | 재사용 |
| 전체 pytest·정적 검사 | 434d101 | 최종 코드 SHA로 CI 재실행: pytest run 40456963269 성공(SQLite 1067·PostgreSQL 1071 passed) · static run 40456971043 성공(7개 검사 종료 0) | 실행 |

## 3. 최종 Draft PR · 전체 SHA · DB 리비전 · 이미지 · CI 대응표 (9장 3번)

| | 값 |
|---|---|
| 최종 Draft PR | #58 (`rc/review-20261027` → `main`, 한 번에 반영) — #42(185f0ba 기준)는 그대로 두고 본문 머리에 안내만 |
| 최종 HEAD(승인 대상) | 코드 `c078bdd` 위에 이 보고서·승인 자료만 더한 문서 커밋 — 값은 PR 본문 머리(`git diff c078bdd <HEAD> --stat`는 `diagnostics/*.md`뿐) |
| 포함 PR HEAD | #50 `fd4bd2a` · #51 `c90d9a0` · #52 `406b980` · #53 `65f0a8f` · #54 `b91c3f4` · #55 `e3e7597` · #56 `bc1cf2d` · #57 `ecc3c1f` (base 모두 `185f0ba`) |
| 트리 | backend `db9b98531b30` · frontend `638b0e1193f2` |
| DB 리비전 | 0019 → 0020 → **0021_run_request_key** (0021 채우기 거절 조건·downgrade 보호·연쇄 거절 상태 — DEPLOY 10장) |
| 이미지 | 격리 러너 빌드(run 40449891138, `434d101`): backend `sha256:f6bc7ae2…` · frontend `sha256:dfa0347b…` · frontend-render `sha256:21990433…` — 이후 이미지 입력(Dockerfile·requirements·CA) 변경 없음. 배포 때 다시 빌드됨 |
| CI(후보 SHA 그대로; 워크플로용 자식 커밋은 후보와 SHA가 다름) | `c078bdd`: pytest(SQLite 1067·PostgreSQL 1071 passed) run 40456963269 성공 · static(ruff·진단 ruff·oxlint·tsc·build 모두 0) run 40456971043 성공 · pg-tls(판정기 방어 16·유효 11/11·진입 경로 8/8·만료 4/4·전달 2) run 40456979750 성공 · **재사용(`434d101`에서 실행 — 이미지 입력·마이그레이션·복구·이관 코드는 `c078bdd`와 차이 0)**: image-check(Trivy·TLS 매트릭스·메모리 E·K·K5·KL1) run 40449891138 · 실제 상류 읽기 run 40449899331 · 롤백 리허설 59/59 run 40449908227 · pytest-diag(+외부 DB 46행) run 40449874743 · 단독 PR: #56 pg-tls run 40456939092 · #52 pytest run 40456236695·static run 40456246010 |

## 4. 0021 사전 점검 결과와 자료 보존 (9장 4번)

- 원칙: 자료를 임의로 고치지 않고 중단. 같은 요청 번호의 기존 실행 삭제·병합·번호 변경으로 색인을 억지로 만들지 않음(DEPLOY 10장).
- 사전 점검(위 2장): 검증된 백업의 격리 복사본에서 0019→0020→0021 성공, key 연결 실행 0건 → **별도 정리 불필요**. 현재 운영 자료는 백업 시각(2026-10-19 23:44 KST) 이후 바뀌었을 수 있으나 공통 key 연결은 아직 배포되지 않아 운영 DB에도 key 연결 실행은 없음(0021 채움 대상 0건).
- 거절 뒤 상태는 DB마다 다름(SQLite 0020 / PostgreSQL 0019)을 시험·문서로 못 박았고, 멈추면 실행 번호·사유·영향 수만 보고(원문 없음), 실제 사례가 있을 때만 정리안.
- 미해결 대상: 없음.

## 5. 로컬 배포 준비 여부 / Render·Neon 준비 여부 (9장 5번 — 따로 판정)

| 대상 | 판정 | 근거·남은 것 |
|---|---|---|
| **로컬 운영 배포(SQLite, `deploy.sh <최종 HEAD> 0019_analysis_runs`)** | **승인 검토 가능** | 전체 시험·정적·롤백 리허설(59/59)·0021 사전 점검(대상 0)·화면 두 차례 + 용어 한 차례. 남은 것: 소유자의 HEAD 승인·반영 시각 공지. 이 PC의 Docker Desktop에서 4장 B 전체는 돌리지 않음(격리 러너 결과) |
| **Render 전환(외부 PostgreSQL)** | **준비 미완(소유자 결정 대기)** | 원천 A/B 미결정, 실제 Neon 미접근(인증서·풀러 channel_binding 미확인 — 격리 시험만), Render 서비스 표는 검토자 전달값(이 환경 재조회 불가), readiness 경로는 제안만(현 설정 미변경). 준비물: 무료 조건 확인·원천·백업·복구 위치·대상 DB·앱/이관 계정 분리·직접/풀러·TLS verify-full·최종 SHA(EXTERNAL_DB 8-5) |

## 6. 소유자가 결정할 것 (9장 6번)

1. 최종 HEAD(코드 `c078bdd` + 문서 커밋)의 main 병합과 로컬 배포 승인.
2. Render 전환 시점과 계정 보존 원천 A/B (`APPROVAL-render-switch-20261026.md`).
3. 실제로 필요한 보안 예외 — 현재 없음. TLS `verify-full`·`channel_binding=require`가 기본이며, 예외는 실제 연결에서 채널 바인딩 미지원이 확인된 끝점에 한해 근거와 함께 별도 항목.

## 7. 하지 않은 것 (완료 조건에서 제외된 항목 — 미검증 표에 유지)

K7 고의 OOM, SonarQube·ZAP 전체 진단, 새 브라우저 설치, 실제 Excel·스크린리더, 미접근 Neon 실환경, Render 관리 API 재조회(이 환경 접근 수단 없음), Oracle 공식 안내 원문(이 환경 403 — 검토자 전달값만).
