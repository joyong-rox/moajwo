"""결과 실행의 담당 서버 프로세스와 생존 신호 — 멈춘 실행 정리가 살아 있는 실행을 건드리지 않게

- runs.worker: 그 실행을 만드는 서버 프로세스 ("<결과 폴더 식별자>-<프로세스 식별자>", app/runlease.py).
  같은 결과 폴더를 쓰는 다른 프로세스는 그 프로세스의 잠금 파일로 살아 있는지를 확인한다 (운영체제 잠금 — 프로세스가
  죽으면 풀린다). 예전처럼 '이 서버가 뜨기 전에 시작한 실행'이라는 시각만으로 멈췄다고 보지 않는다.
- runs.heartbeat_at: 담당 프로세스가 마지막으로 살아 있다고 기록한 시각 (UTC — 이 시각은 정리 판단의 보조 근거이고,
  다른 서버(다른 디스크)의 실행은 이 시각이 오래돼도 '실패'가 아니라 '확인 필요'로만 둔다).
- 기존 행은 둘 다 비어 있다 (이 판 전에 시작한 실행 — 정리는 '확인 필요'로만 둔다).

downgrade: 두 열을 지운다. 이 판에서 시작해 아직 실행 중인 행(담당 있음)이 있으면 내리지 않는다 — 서버를 멈춘 뒤
`python -m app.recovery recover`로 끝난 실행을 근거대로 정리하고 `status`로 확인한다. 담당이 없는 실행 중 행(이 판 전에
시작)은 이전 판이 남긴 그대로라 막지 않는다. 이 판이 남긴 '확인 필요'(check) 행은 그대로 남아 이전 판 화면에는 원문
'check'로 보이므로, 되돌리기 전에 `resolve RUN_ID --failed`로 정리할지 정한다 (docs/DEPLOY.md).
이전 판 이미지는 기동 때 `alembic upgrade head`가 모르는 리비전(0020)에서 멈추므로, 이전 이미지로 되돌리려면 DB를 먼저
0019로 내린다 (새 이미지로 `alembic downgrade 0019_analysis_runs`).

Revision ID: 0020_run_worker_lease
Revises: 0019_analysis_runs
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_run_worker_lease"
down_revision: str | None = "0019_analysis_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _short_lock_wait() -> None:
    """PostgreSQL: 표 잠금을 오래 기다리지 않는다 — 무중단 배포에서 이전 서버의 열린 트랜잭션 뒤에 줄을 서면, 그 서버의
    실행 기록 조회까지 함께 막힌다. 넘으면 이 마이그레이션이 실패하고(트랜잭션 DDL이라 바뀐 것 없음) 나중에 다시 배포한다."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")


def upgrade() -> None:
    _short_lock_wait()
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("worker", sa.String(40), nullable=True))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # 이 판에서 시작해 실행 중인 행(담당 있음)이 있으면 내리지 않는다 — 이전 판은 담당·생존 신호를 몰라 그 행을 근거대로
    # 정리하지 못한다 (계속 '실행 중'으로 두거나 시각만으로 실패로 바꾼다). 담당이 없는 행은 이 판 전에 시작한 것이라
    # 이전 판이 남긴 그대로다 — 막으면 서버를 멈춘 롤백에서 정리할 길이 없다
    running = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM runs WHERE status = 'running' AND worker IS NOT NULL"))
        .scalar()
        or 0
    )
    if running:
        raise RuntimeError(
            f"이 판에서 시작한 실행 중인 결과 실행이 {running}건 있어 0019로 내리지 않습니다 — 서버를 멈춘 뒤 "
            "'python -m app.recovery recover'로 정리하고 'python -m app.recovery status'로 확인한 다음 다시 하세요 "
            "(docs/DEPLOY.md)."
        )
    _short_lock_wait()
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("heartbeat_at")
        batch.drop_column("worker")
