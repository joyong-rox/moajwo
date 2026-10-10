"""review session parent (re-check sessions)

Revision ID: 0007_review_recheck
Revises: 0006_review
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_review_recheck"
down_revision: str | None = "0006_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("review_sessions") as batch:
        batch.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_review_sessions_parent_id", "review_sessions", ["parent_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("review_sessions") as batch:
        batch.drop_constraint("fk_review_sessions_parent_id", type_="foreignkey")
        batch.drop_column("parent_id")
