"""users, roles, ownership; mapping approvals -> promotion queue -> standard_terms; settings

Revision ID: 0010_users_roles
Revises: 0009_automation_runs
Create Date: 2026-10-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_users_roles"
down_revision: str | None = "0009_automation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 계정 도입 전에 만든 기록은 소유자가 없음(NULL) — 관리자·소유자만 조회한다
OWNED = (
    ("source_files", "uploaded_by"),
    ("validation_runs", "created_by"),
    ("review_sessions", "created_by"),
    ("runs", "user_id"),
    ("automations", "owner_id"),
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("failed_logins", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_table(
        "mapping_approvals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("normalized", sa.String(length=255), nullable=False),
        sa.Column("column_name", sa.String(length=255), nullable=False),
        sa.Column("field_key", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized", "field_key", "user_id"),
    )
    op.create_index("ix_mapping_approvals_normalized", "mapping_approvals", ["normalized"])
    op.create_table(
        "promotion_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("normalized", sa.String(length=255), nullable=False),
        sa.Column("column_name", sa.String(length=255), nullable=False),
        sa.Column("field_key", sa.String(length=64), nullable=False),
        sa.Column("approver_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized", "field_key"),
    )
    op.create_table(
        "standard_terms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("normalized", sa.String(length=255), nullable=False),
        sa.Column("term", sa.String(length=255), nullable=False),
        sa.Column("field_key", sa.String(length=64), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["promotion_candidates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized"),
    )
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key"),
    )

    for table, column in OWNED:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column(column, sa.Integer(), nullable=True))
            batch.create_foreign_key(f"fk_{table}_{column}", "users", [column], ["id"], ondelete="SET NULL")
            batch.create_index(f"ix_{table}_{column}", [column])
    with op.batch_alter_table("source_files") as batch:
        batch.add_column(sa.Column("data_rows", sa.Integer(), nullable=True))
    with op.batch_alter_table("automations") as batch:
        batch.add_column(sa.Column("manual_baseline_minutes", sa.Float(), nullable=True))

    # 학습 별칭을 사용자별로: 유일 조건 (normalized) → (normalized, user_id). 기존 별칭은 user_id 없음(공용)으로 옮긴다
    op.create_table(
        "learned_aliases_new",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("normalized", sa.String(length=255), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("field_key", sa.String(length=64), nullable=False),
        sa.Column("source_file_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_file_id"], ["source_files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized", "user_id"),
    )
    op.execute(
        "INSERT INTO learned_aliases_new (id, user_id, normalized, alias, field_key, source_file_id, created_at) "
        "SELECT id, NULL, normalized, alias, field_key, source_file_id, created_at FROM learned_aliases"
    )
    op.drop_index("ix_learned_aliases_field_key", table_name="learned_aliases")
    op.drop_table("learned_aliases")
    op.rename_table("learned_aliases_new", "learned_aliases")
    op.create_index("ix_learned_aliases_field_key", "learned_aliases", ["field_key"])
    op.create_index("ix_learned_aliases_user_id", "learned_aliases", ["user_id"])


def downgrade() -> None:
    op.create_table(
        "learned_aliases_old",
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
    # 이름이 겹치면 공용(user_id 없음) → 가장 먼저 학습한 것 하나만 남긴다
    op.execute(
        "INSERT INTO learned_aliases_old (id, normalized, alias, field_key, source_file_id, created_at) "
        "SELECT id, normalized, alias, field_key, source_file_id, created_at FROM learned_aliases l "
        "WHERE id = (SELECT id FROM learned_aliases m WHERE m.normalized = l.normalized "
        "ORDER BY (m.user_id IS NOT NULL), m.id LIMIT 1)"
    )
    op.drop_index("ix_learned_aliases_user_id", table_name="learned_aliases")
    op.drop_index("ix_learned_aliases_field_key", table_name="learned_aliases")
    op.drop_table("learned_aliases")
    op.rename_table("learned_aliases_old", "learned_aliases")
    op.create_index("ix_learned_aliases_field_key", "learned_aliases", ["field_key"])

    with op.batch_alter_table("automations") as batch:
        batch.drop_column("manual_baseline_minutes")
    with op.batch_alter_table("source_files") as batch:
        batch.drop_column("data_rows")
    for table, column in reversed(OWNED):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_{column}")
            batch.drop_constraint(f"fk_{table}_{column}", type_="foreignkey")
            batch.drop_column(column)
    op.drop_table("app_settings")
    op.drop_table("standard_terms")
    op.drop_table("promotion_candidates")
    op.drop_index("ix_mapping_approvals_normalized", table_name="mapping_approvals")
    op.drop_table("mapping_approvals")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("users")
