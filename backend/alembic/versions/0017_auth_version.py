"""인증 버전: 로그인과 비밀번호 변경·초기화·이용 중지의 경합에서 세션이 살아남지 않게

- users.auth_version: 비밀번호 변경·초기화, 가입 반려·이용 중지 때 같은 UPDATE 안에서 1씩 올린다.
- auth_sessions.auth_version: 세션을 만들 때의 사용자 버전. 요청마다 users.auth_version과 같아야 유효하다.
- 기존 사용자·세션은 둘 다 0으로 시작하므로 지금 로그인한 세션은 그대로 유효하다 (전환 시 로그아웃 없음).

Revision ID: 0017_auth_version
Revises: 0016_account_status_roles
Create Date: 2026-10-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_auth_version"
down_revision: str | None = "0016_account_status_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False))
    with op.batch_alter_table("auth_sessions") as batch:
        batch.add_column(sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("auth_sessions") as batch:
        batch.drop_column("auth_version")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("auth_version")
