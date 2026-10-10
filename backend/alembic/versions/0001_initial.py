"""initial schema: source_files

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-05

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("stored_path", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stored_path"),
    )
    op.create_index("ix_source_files_sha256", "source_files", ["sha256"])


def downgrade() -> None:
    op.drop_index("ix_source_files_sha256", table_name="source_files")
    op.drop_table("source_files")
