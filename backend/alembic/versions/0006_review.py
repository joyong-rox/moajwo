"""review sessions and items (pre-integration review panel)

Revision ID: 0006_review
Revises: 0005_glossary_entries
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_review"
down_revision: str | None = "0005_glossary_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("file_ids", sa.Text(), nullable=False),
        sa.Column("excluded_file_ids", sa.Text(), nullable=False),
        sa.Column("validation_run_id", sa.Integer(), nullable=True),
        sa.Column("remember_format", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["validation_run_id"], ["validation_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("sheet", sa.String(length=255), nullable=True),
        sa.Column("row", sa.Integer(), nullable=True),
        sa.Column("column", sa.String(length=255), nullable=True),
        sa.Column("before", sa.Text(), nullable=True),
        sa.Column("after", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reason_source", sa.String(length=8), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("choice", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["review_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["file_id"], ["source_files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_items_session_id", "review_items", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_review_items_session_id", table_name="review_items")
    op.drop_table("review_items")
    op.drop_table("review_sessions")
