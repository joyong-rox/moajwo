"""pii summary on source files, validation runs

Revision ID: 0003_validation
Revises: 0002_mapping_decisions
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_validation"
down_revision: str | None = "0002_mapping_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_files") as batch:
        batch.add_column(sa.Column("pii_summary", sa.Text(), nullable=True))
        batch.add_column(sa.Column("pii_acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "validation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("validation_runs")
    with op.batch_alter_table("source_files") as batch:
        batch.drop_column("pii_acknowledged_at")
        batch.drop_column("pii_summary")
