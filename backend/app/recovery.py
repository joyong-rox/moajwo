"""결과 파일을 만드는 도중 서버가 멈춘 뒤(메모리 부족 종료·강제 재시작) 남은 것을 기동할 때 정리한다.

묶기·통합은 실행 기록을 '실행 중'으로 남긴 뒤 결과를 임시 파일(*.xlsx.tmp)에 쓰고, 다 쓰면 이름을 바꿔
'완료'로 바꾼다. 그 사이에 프로세스가 죽으면 기록은 '실행 중', 임시 파일은 쓰다 만 채로 남는다 (512MiB 격리
컨테이너에서 재현, 재시작 뒤에도 그대로였음 — docs/MEMORY.md).
- 이 프로세스가 뜨기 전에 '실행 중'이 된 기록 → '실패'와 이유. 기록·변경 내역은 지우지 않는다 (결과 파일은 없음).
- outputs 폴더의 임시 파일 중 이 프로세스가 뜨기 전에 만든 것 → 지운다 (완성된 결과는 이름을 바꾼 뒤에만 생긴다).
DB를 함께 쓰는 서버가 잠시 둘이면(외부 DB + 무중단 재배포의 겹침) 다른 서버의 진행 중 실행을 '실패'로 볼 수
있다 — 그 서버가 끝나면 '완료'로 다시 바뀐다. 로컬 반영은 기존 서버를 멈춘 뒤 새 서버를 띄운다 (scripts/deploy.sh).
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integration import KST, OUTPUT_DIR
from app.models import IntegrationRun

logger = logging.getLogger(__name__)

STARTED = datetime.now(KST)  # 이 프로세스가 뜬 시각
INTERRUPTED = (
    "서버가 결과 파일을 만드는 중에 멈췄습니다 (메모리 부족·재시작 등) — 결과 파일은 만들어지지 않았습니다. "
    "다시 실행하세요."
)


def recover_interrupted(db: Session, before: datetime = STARTED) -> dict[str, int]:
    """before 전에 시작해 '실행 중'으로 남은 실행을 '실패'로 바꾸고, before 전에 만든 임시 결과 파일을 지운다."""
    runs = db.scalars(
        select(IntegrationRun).where(IntegrationRun.status == "running", IntegrationRun.started_at < before)
    ).all()
    now = datetime.now(KST)
    for run in runs:
        run.status, run.error, run.finished_at = "failed", INTERRUPTED, now
    db.commit()
    removed = 0
    for tmp in OUTPUT_DIR.glob("*.xlsx.tmp") if OUTPUT_DIR.is_dir() else []:
        try:
            if datetime.fromtimestamp(tmp.stat().st_mtime, KST) < before:
                tmp.unlink()
                removed += 1
        except OSError:  # 이미 지워졌거나 다른 곳에서 쓰는 중 — 다음 기동 때 다시 본다
            continue
    if runs or removed:
        logger.warning("interrupted runs marked failed: %d, partial result files removed: %d", len(runs), removed)
    return {"runs": len(runs), "temp_files": removed}
