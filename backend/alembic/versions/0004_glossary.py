"""glossary aliases

Revision ID: 0004_glossary
Revises: 0003_validation
Create Date: 2026-10-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_glossary"
down_revision: str | None = "0003_validation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "glossary_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("normalized", sa.String(length=255), nullable=False),
        sa.Column("target_code", sa.String(length=7), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized", "target_code", "valid_from"),
    )
    op.create_index("ix_glossary_aliases_normalized", "glossary_aliases", ["normalized"])


def downgrade() -> None:
    op.drop_index("ix_glossary_aliases_normalized", table_name="glossary_aliases")
    op.drop_table("glossary_aliases")
