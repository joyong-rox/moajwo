"""automations; integration_runs -> runs (execution history)

Revision ID: 0009_automation_runs
Revises: 0008_integration
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_automation_runs"
down_revision: str | None = "0008_integration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("source_run_id", sa.Integer(), nullable=True),
        sa.Column("column_rules", sa.Text(), nullable=False),
        sa.Column("entity_rules", sa.Text(), nullable=False),
        sa.Column("validation", sa.Text(), nullable=False),
        sa.Column("output_format", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # 실행 기록 테이블 이름을 runs로 바꾼다 (run_changes·automations의 참조도 함께 바뀜)
    op.rename_table("integration_runs", "runs")
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("automation_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("trigger", sa.String(length=16), nullable=False, server_default="manual"))
        batch.add_column(sa.Column("input_file_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_runs_automation_id", "automations", ["automation_id"], ["id"], ondelete="SET NULL")
    # 기존 실행은 생성 시각을 시작·종료 시각으로 채운다
    op.execute("UPDATE runs SET started_at = created_at, finished_at = created_at WHERE started_at IS NULL")
    with op.batch_alter_table("review_sessions") as batch:
        batch.add_column(sa.Column("automation_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_review_sessions_automation_id", "automations", ["automation_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("review_sessions") as batch:
        batch.drop_constraint("fk_review_sessions_automation_id", type_="foreignkey")
        batch.drop_column("automation_id")
    with op.batch_alter_table("runs") as batch:
        batch.drop_constraint("fk_runs_automation_id", type_="foreignkey")
        for column in ("finished_at", "started_at", "input_file_count", "trigger", "automation_id"):
            batch.drop_column(column)
    op.rename_table("runs", "integration_runs")
    op.drop_table("automations")
