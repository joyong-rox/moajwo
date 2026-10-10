"""mapping decisions and learned aliases

Revision ID: 0002_mapping_decisions
Revises: 0001_initial
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_mapping_decisions"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mapping_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_file_id", sa.Integer(), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=False),
        sa.Column("column_name", sa.String(length=255), nullable=False),
        sa.Column("field_key", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["source_file_id"], ["source_files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_file_id", "sheet_name", "column_name"),
    )
    op.create_index("ix_mapping_decisions_source_file_id", "mapping_decisions", ["source_file_id"])
    op.create_table(
        "learned_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("normalized", sa.String(length=255), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("field_key", sa.String(length=64), nullable=False),
        sa.Column("source_file_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["source_file_id"], ["source_files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized"),
    )
    op.create_index("ix_learned_aliases_field_key", "learned_aliases", ["field_key"])


def downgrade() -> None:
    op.drop_index("ix_learned_aliases_field_key", table_name="learned_aliases")
    op.drop_table("learned_aliases")
    op.drop_index("ix_mapping_decisions_source_file_id", table_name="mapping_decisions")
    op.drop_table("mapping_decisions")
