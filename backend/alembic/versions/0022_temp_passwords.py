"""임시 비밀번호 상태 — 관리자가 발급·재발급한 계정은 첫 로그인에서 본인 비밀번호로 바꾼다

- users.must_change_password: 임시 비밀번호로 로그인하는 상태. True이면 비밀번호 변경·본인 조회·로그아웃 외의 업무 API는
  403이고, 본인 비밀번호로 바꾸면 False가 된다(app/auth.py require_full_access).
- users.temp_password_expires_at: 임시 비밀번호의 만료 시각(UTC, 시간대 포함). 지나면 그 비밀번호로 로그인하지 못한다 —
  관리자가 재발급.
- 기존 계정은 False·NULL — 현재 비밀번호·상태가 그대로이고, 어떤 계정도 임시 비밀번호 상태로 바뀌지 않는다.
- 공개 최초 설정·회원가입은 이 판의 앱에서 종료된다(410). 계정은 설치 담당자의 초기화 명령(app.adminctl init — 아이디
  admin)과 관리자의 계정 발급으로만 생긴다. 스키마 변경 자체는 계정을 개명하거나 비밀번호를 바꾸지 않는다(전환은
  app.adminctl transition).

downgrade: 임시 비밀번호 상태(must_change_password)인 계정이 — 이용 중·이용 중지·만료와 **관계없이** — 하나라도 있으면
내리지 않는다. 열을 지우면 그 계정이 임시 비밀번호로 업무 기능을 쓸 수 있게 되고(중지 계정도 구판에서 재개되면 마찬가지),
이전 판의 앱은 공개 가입·최초 설정도 다시 연다. 이용 중지·만료는 임시 상태를 풀지 않는다 — 내릴 수 있는 조건은 모든 임시
상태 계정이 본인 비밀번호 변경을 끝낸 것뿐이며, 변경을 끝내지 않은 계정이나 변경을 요청할 수 없는 중지 계정이 있으면 이
경로의 downgrade는 쓸 수 없다. 거절을 피하려고 플래그를 지우거나 중지 계정을 검사에서 빼지 않는다(docs/DEPLOY.md 12-4).
이미지만 이전 판으로 바꾸는 롤백은 이 리비전에서 멈춘다(이전 판은 0022를 모름 — 기동 때 alembic upgrade head 실패).

Revision ID: 0022_temp_passwords
Revises: 0021_run_request_key
Create Date: 2026-10-30

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_temp_passwords"
down_revision: str | None = "0021_run_request_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _short_lock_wait() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")


def upgrade() -> None:
    _short_lock_wait()
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("must_change_password", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("temp_password_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # 0020·0021과 같은 보호: 이 판에서 시작해 실행 중인 결과 실행(담당 있음)이 있으면 아무것도 바꾸지 않는다 —
    # 'alembic downgrade 0020'은 0022 → 0021 → 0020 순서라, 맨 위(0022)에서 멈춰야 리비전이 head 그대로 남는다
    # (0021 머리말의 롤백 리허설 교훈).
    running = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM runs WHERE status = 'running' AND worker IS NOT NULL"))
        .scalar()
        or 0
    )
    if running:
        raise RuntimeError(
            f"이 판에서 시작한 실행 중인 결과 실행이 {running}건 있어 0021로 내리지 않습니다 — 서버를 멈추고 백업한 뒤 "
            "'python -m app.recovery recover'로 정리하고 'python -m app.recovery status'로 확인한 다음 다시 하세요 "
            "(docs/DEPLOY.md 10장)."
        )
    pending = op.get_bind().execute(sa.text("SELECT count(*) FROM users WHERE must_change_password")).scalar() or 0
    if pending:
        raise RuntimeError(
            f"임시 비밀번호 상태인 계정이 {pending}개 있어 0021로 내리지 않습니다 — 이용 중·이용 중지·만료 "
            "상태와 관계없이 셉니다. 열을 지우면 그 계정이 임시 비밀번호로 업무 기능을 쓰게 되고(이용 중지 계정도 "
            "구판에서 재개되면 마찬가지) 이전 판의 앱은 공개 가입도 다시 엽니다. 그 계정들이 본인 비밀번호 변경을 끝낸 "
            "뒤에만 내릴 수 있습니다 — 이용 중지·만료는 임시 상태를 풀지 않으며, 플래그를 직접 지우거나 중지 계정을 "
            "빼고 내리지 마세요 (docs/DEPLOY.md 12-4). 아무것도 바꾸지 않았습니다."
        )
    _short_lock_wait()
    with op.batch_alter_table("users") as batch:
        batch.drop_column("temp_password_expires_at")
        batch.drop_column("must_change_password")
