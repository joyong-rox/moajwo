"""계정 상태(가입 승인)와 역할 단순화: owner(최고 관리자) / user(일반 사용자)

- users.status: pending(승인 대기) / active(이용 중) / rejected(가입 반려) / suspended(이용 중지)
  기존 활성 계정은 재승인 없이 active, 기존 비활성 계정은 suspended (다시 활성화하지 않는다).
- 예전 중간 관리자(admin)는 user로 바꾸고 그 세션을 끝낸다. 바꾼 사실은 account_events에 남겨 downgrade가 되돌린다.
- 소유자가 여러 명이거나 알 수 없는 역할이 있으면 고르거나 강등·삭제하지 않고 그대로 두며, account_events에
  migration_check(result=denied)로 남긴다 (관리 이력 화면에서 확인).
- is_active는 status == 'active'와 같게 맞춘다 (이전 코드도 승인 대기·반려 계정을 막도록).

Revision ID: 0016_account_status_roles
Revises: 0015_owner_setup_marker
Create Date: 2026-10-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_account_status_roles"
down_revision: str | None = "0015_owner_setup_marker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KNOWN_ROLES = ("owner", "user", "admin")


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("status", sa.String(length=16), server_default="pending", nullable=False))
        batch.add_column(sa.Column("status_reason", sa.String(length=500), nullable=True))
        batch.add_column(sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "account_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_name", sa.String(length=32), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("target_name", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("before_status", sa.String(length=16), nullable=True),
        sa.Column("after_status", sa.String(length=16), nullable=True),
        sa.Column("before_role", sa.String(length=16), nullable=True),
        sa.Column("after_role", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_account_events_target_id", "account_events", ["target_id"])

    bind = op.get_bind()
    users = bind.execute(sa.text("SELECT id, username, role, is_active FROM users ORDER BY id")).fetchall()
    events = sa.table(
        "account_events",
        sa.column("target_id", sa.Integer),
        sa.column("target_name", sa.String),
        sa.column("action", sa.String),
        sa.column("before_status", sa.String),
        sa.column("after_status", sa.String),
        sa.column("before_role", sa.String),
        sa.column("after_role", sa.String),
        sa.column("reason", sa.String),
        sa.column("result", sa.String),
    )
    rows: list[dict] = []
    for uid, username, role, is_active in users:
        status = "active" if is_active else "suspended"
        bind.execute(sa.text("UPDATE users SET status = :s WHERE id = :i"), {"s": status, "i": uid})
        if role == "admin":
            bind.execute(sa.text("UPDATE users SET role = 'user' WHERE id = :i"), {"i": uid})
            bind.execute(sa.text("DELETE FROM auth_sessions WHERE user_id = :i"), {"i": uid})
            rows.append({"target_id": uid, "target_name": username, "action": "role_migrated",
                         "before_status": status, "after_status": status, "before_role": "admin", "after_role": "user",
                         "reason": "역할 단순화: 중간 관리자 → 일반 사용자, 기존 세션 종료", "result": "ok"})  # fmt: skip
        elif role not in KNOWN_ROLES:
            rows.append({"target_id": uid, "target_name": username, "action": "migration_check",
                         "before_status": status, "after_status": status, "before_role": role, "after_role": role,
                         "reason": "알 수 없는 역할 — 바꾸지 않음 (일반 사용자 권한으로만 동작). 확인 필요",
                         "result": "denied"})  # fmt: skip
    owners = [(uid, username) for uid, username, role, _ in users if role == "owner"]
    if len(owners) > 1:
        for uid, username in owners:
            rows.append({"target_id": uid, "target_name": username, "action": "migration_check",
                         "before_status": None, "after_status": None, "before_role": "owner", "after_role": "owner",
                         "reason": f"소유자 {len(owners)}명 — 고르거나 강등하지 않음. 확인 필요", "result": "denied"})  # fmt: skip
    if rows:
        op.bulk_insert(events, rows)


def downgrade() -> None:
    bind = op.get_bind()
    # 이 리비전이 user로 바꾼 예전 관리자만 되돌린다 (이후 다시 바뀐 계정은 role이 user일 때만)
    migrated = bind.execute(
        sa.text("SELECT target_id FROM account_events WHERE action = 'role_migrated' AND target_id IS NOT NULL")
    ).fetchall()
    for (uid,) in migrated:
        bind.execute(sa.text("UPDATE users SET role = 'admin' WHERE id = :i AND role = 'user'"), {"i": uid})
    # is_active는 status와 같게 유지해 왔으므로 그대로 둔다 (승인 대기·반려·중지 계정은 비활성으로 남는다)
    op.drop_index("ix_account_events_target_id", table_name="account_events")
    op.drop_table("account_events")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("status_changed_at")
        batch.drop_column("status_reason")
        batch.drop_column("status")
