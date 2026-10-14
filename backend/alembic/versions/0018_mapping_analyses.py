"""열 매핑 분석 결과 저장 (여러 파일 공통 검토용)

- mapping_analyses: 파일·사용자마다 마지막 분석 결과 1건 (화면으로 내보낸 마스킹된 JSON). 업무 데이터가 아닌 다시 만들 수
  있는 기록이라, downgrade하면 지우고 다시 분석하면 된다. 매핑 확정(mapping_decisions)·학습 별칭은 바꾸지 않는다.

Revision ID: 0018_mapping_analyses
Revises: 0017_auth_version
Create Date: 2026-10-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_mapping_analyses"
down_revision: str | None = "0017_auth_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mapping_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_file_id", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["source_file_id"], ["source_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_file_id", "created_by"),
    )
    op.create_index("ix_mapping_analyses_source_file_id", "mapping_analyses", ["source_file_id"])


def downgrade() -> None:
    op.drop_index("ix_mapping_analyses_source_file_id", table_name="mapping_analyses")
    op.drop_table("mapping_analyses")
