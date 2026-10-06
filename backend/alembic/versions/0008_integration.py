"""integration runs and run_changes

Revision ID: 0008_integration
Revises: 0007_review_recheck
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_integration"
down_revision: str | None = "0007_review_recheck"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("review_session_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("output_path", sa.String(length=1024), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("file_ids", sa.Text(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("change_count", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["review_session_id"], ["review_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "run_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("source_file_id", sa.Integer(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("sheet", sa.String(length=255), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("output_row", sa.Integer(), nullable=False),
        sa.Column("column", sa.String(length=255), nullable=False),
        sa.Column("source_column", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("before", sa.Text(), nullable=True),
        sa.Column("after", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["integration_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_file_id"], ["source_files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_changes_run_id", "run_changes", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_run_changes_run_id", table_name="run_changes")
    op.drop_table("run_changes")
    op.drop_table("integration_runs")
