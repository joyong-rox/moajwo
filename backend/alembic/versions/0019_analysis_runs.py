"""데이터 분석 결과 저장 (확정된 열로 하는 합계·평균 등 1차)

- analysis_runs: 분석 조건·사용한 파일 해시·열·확정 매핑·제외 정책·결과를 함께 저장한다 (과거 결과 재현용).
  기존 테이블은 바꾸지 않는다. downgrade하면 이 테이블(분석 결과)만 지운다 — 매핑 확정·학습 별칭·업로드 원본·통합 실행은
  그대로 남고, 지운 분석은 같은 조건으로 다시 실행하면 다시 만들 수 있다
  (그 사이 매핑이 바뀌었으면 결과가 다를 수 있음).

Revision ID: 0019_analysis_runs
Revises: 0018_mapping_analyses
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_analysis_runs"
down_revision: str | None = "0018_mapping_analyses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("field_key", sa.String(64), nullable=False),
        sa.Column("partial", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("conditions", sa.Text(), nullable=False),
        sa.Column("inputs", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_runs_created_by", "analysis_runs", ["created_by"])


def downgrade() -> None:
    op.drop_index("ix_analysis_runs_created_by", table_name="analysis_runs")
    op.drop_table("analysis_runs")
