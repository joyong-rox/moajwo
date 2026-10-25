# 격리 러너 진단 스크립트 (GitHub Actions — 운영 PC·운영 Docker 엔진에서 돌리지 않음)

진단 브랜치(`ci/*`)에 워크플로 파일과 함께 얹어 돌린 스크립트의 사본입니다. main·PR의 CI가 아닙니다. 합성 자료만 쓰고, 비밀번호는 실행 때 만들어 출력하지 않습니다.

| 스크립트 | 무엇을 | 보고 |
|---|---|---|
| `image_check.sh` | 배포 이미지 빌드·비루트 실행·프록시·업로드/내려받기·512MiB 읽기 한도·Trivy | REPORT-20261023 6장 |
| `output_memory.sh` · `output_memory.py` · `shared_xlsx.py` | 결과 파일(묶기·통합) 메모리 — A·A3 예산 끔(메모리 부족 재현·재기동 뒤 정리), B Render 예산, C 통계 요약·한도 가까운 통합, D1~D11 동시 생성·긴 글자(공유 문자열 — Excel 방식으로 직접 만든 xlsx)·넓은 표·큰 통계 요약·분석 Excel·큰 병합·결과 하나의 글자 양 (`PARTS=…`, 이미지 `IMAGE=…` — 수정 전·후를 같은 명령으로) | REPORT-20261023-2 8장, REPORT-20261025 |
| `deploy_rollback_check.sh` · `deploy_probe.py` | 같은 Compose 프로젝트로 이전(main) → 후보(`scripts/deploy.sh`) → 롤백(이전 이미지 + 이전 포트, 후보가 리비전을 올렸으면 서버 정지 → 정리 전 downgrade 거절 확인 → `recover` → 새 이미지로 downgrade) → 후보 → 트리가 다른 승인 SHA | REPORT-20261023-2 7장, REPORT-20261025, `docs/DEPLOY.md` 리허설 |

리눅스 러너 맞춤: `deploy.sh`의 `pwd -W`(Git Bash)를 `pwd`로, 백업 컨테이너가 러너의 `backups/`에 쓸 수 있게 폴더 권한을 엽니다 (운영 PC의 Docker Desktop에는 필요 없음).
