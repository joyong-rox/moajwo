# Docker 없는 로컬 격리 실행 도구 (2026-10-22·23 두 차례 확인에 쓴 것)

공유 PC에서 운영 Docker 엔진을 쓰지 않고 격리 서버를 띄워 `diagnostics/scripts/`의 진단을 돌린 도구입니다.
운영 주소·운영 DB에는 닿지 않습니다 (요청 범위는 `diagnostics/scripts/diag_scope.py` — 127.0.0.1:8086만).

| 파일 | 하는 일 |
|---|---|
| `iso_backend.sh` | 격리 backend: 새 데이터 폴더, 모델 주소 차단(127.0.0.1:9), 127.0.0.1:8096 (`PY`에 파이썬 경로) |
| `iso_frontend.sh` | 빌드본을 vite preview로 127.0.0.1:8086, `/api`는 격리 backend로 |
| `r5_stack.sh` | 위 둘을 띄우고 끔. `be-start pg …`는 외부 DB 모드(격리 PostgreSQL, 앱 계정·소유 계정 분리), `pg-fresh`는 빈 시험 DB |
| `r5_round.sh` | 5장 한 차례: 1차(새 계정 A 최초 사용 → 합성 자료 → 흐름·정책·권한·자동화…) / 2차(재시작·재배포 뒤 재진입·반복) |
| `r5_read.py` | 차례 폴더의 결과를 기대와 비교해 표로 |
| `local_diag.py` | 진단 스크립트의 DB 읽기를 격리 SQLite(읽기 전용) 또는 격리 PostgreSQL(읽기 전용 트랜잭션)로 |
| `guarded_run.py` | 공유 PC 보호 — 가용 메모리가 바닥값 아래로 떨어지면 그 명령을 멈춤 (멈추면 통과로 세지 않음) |
| `s9_reentry.py`·`s9_demo_expected.py`·`a02_automation.py`·`session_probe.py`·`u05_responsive.py` | 재진입·시연 5개 독립 기대값·자동화·세션·반응형 확인 |
| `pgurl.py` | 격리 PostgreSQL 주소 만들기 (비밀번호는 작업 폴더의 `pgsecret/`에만, 출력은 셸 변수로만) |

- 실행할 때는 이 폴더의 파일을 작업 폴더의 `tools/`에 두고, 경로는 `R5_WORK`(작업 폴더)·`PY`(파이썬)로 줍니다. 계정 비밀번호는 실행 때 만들고 Git 밖 증거 폴더에만 둡니다.
- 결과·증거는 저장소 밖 `backups/review-20261023/`에 두고, 이 저장소에는 비식별 요약만 (`diagnostics/REPORT-20261023.md`).
