"""최초 소유자 설정 완료 표시 (기존 설치)

최초 소유자 설정(/auth/setup)은 계정과 같은 트랜잭션에서 app_settings에 고정 키 'owner_setup'을 넣어,
동시 요청 중 하나만 성공하게 한다 (기본 키 충돌). 이미 계정이 있는 기존 설치에도 이 표시를 넣어
업그레이드 후 설정 화면이 다시 열리지 않게 한다. 계정이 없는 설치는 그대로 둔다.

Revision ID: 0015_owner_setup_marker
Revises: 0014_direction_gu_sejong
Create Date: 2026-10-11

"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0015_owner_setup_marker"
down_revision: str | None = "0014_direction_gu_sejong"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = "owner_setup"
MARK = {"source": "0015_owner_setup_marker"}

settings = sa.table(
    "app_settings",
    sa.column("key", sa.String),
    sa.column("value", sa.Text),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
users = sa.table("users", sa.column("id", sa.Integer))


def upgrade() -> None:
    bind = op.get_bind()
    has_users = bind.execute(sa.select(sa.func.count()).select_from(users)).scalar() or 0
    exists = bind.execute(sa.select(settings.c.key).where(settings.c.key == KEY)).first()
    if has_users and exists is None:
        op.bulk_insert(settings, [{"key": KEY, "value": json.dumps(MARK), "updated_at": datetime.now(UTC)}])


def downgrade() -> None:
    # 이 마이그레이션이 넣은 표시만 지운다 (설정 화면에서 만든 표시는 그대로)
    op.execute(settings.delete().where(settings.c.key == KEY, settings.c.value == json.dumps(MARK)))
