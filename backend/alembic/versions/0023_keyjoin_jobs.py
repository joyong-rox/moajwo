"""공통 key 연결의 저장한 작업(keyjoin_jobs) — 연결 설정 재사용

- keyjoin_jobs: 소유자별 작업 이름·설정 버전·설정 JSON(입력 역할의 시트·열 대응 기준, key 열·복합 key 순서,
  가져올 열·결과 이름·순서, 기준 입력·범위, key 결과 이름, 집계 설정). 파일 자체·파일 번호·추정 행의 포함/제외
  결정·개인정보 확인·요청 번호·확인 결과(basis)는 넣지 않는다 (app/keyjoin_jobs.py).
- 기존 표(계정·업로드 원본·실행 기록·결과 파일·자동화·검토)는 바꾸지 않는다. upgrade는 빈 표 하나를 만들 뿐
  어떤 행도 바꾸지 않는다. 기존 공통 key 연결(plan/run)·표준 통합 자동화는 이 표 없이도 그대로 동작한다.
- downgrade: 이 표(저장한 작업 설정)만 지운다 — 계정·원본·실행 기록·결과 파일·자동화는 그대로. 지운 설정은 같은
  구성을 화면에서 다시 저장하면 다시 만들 수 있다(저장 때의 파일이 남아 있을 때). 'alembic downgrade
  0022_temp_passwords'(이 한 단계)는 실행 중인 결과 실행·임시 비밀번호 계정이 있어도 막지 않는다 — 이 표와 무관하다.
  그보다 아래(0021 이하)로 내리는 연쇄 downgrade는 0020~0022와 같은 보호(담당 있는 실행 중 행·임시 비밀번호 상태
  계정이 있으면 거절)를 **맨 위(0023)에서 먼저** 건다 — 중간(0022)에서 멈춰 이 표만 사라진 채 남지 않게. 이미지만
  이전 판으로 바꾸는 롤백은 이 리비전에서 멈춘다(이전 판은 0023을 모름 — 기동 때 alembic upgrade head 실패) —
  내리려면 서버를 멈추고 백업한 뒤 위 명령으로.

Revision ID: 0023_keyjoin_jobs
Revises: 0022_temp_passwords
Create Date: 2026-11-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_keyjoin_jobs"
down_revision: str | None = "0022_temp_passwords"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _short_lock_wait() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")


def upgrade() -> None:
    _short_lock_wait()
    op.create_table(
        "keyjoin_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "name", name="uq_keyjoin_jobs_owner_name"),
    )
    op.create_index("ix_keyjoin_jobs_owner_id", "keyjoin_jobs", ["owner_id"])


ONE_STEP_HINT = "이 표(keyjoin_jobs)만 내리려면 'alembic downgrade 0022_temp_passwords'. 아무것도 바꾸지 않았습니다."
CHAIN = "(0022 아래로 가는 연쇄 downgrade를 맨 위에서 멈춤)"


def _destination() -> str:
    """이번 downgrade 명령의 목표 리비전 인자 (alembic downgrade <목표>)."""
    try:
        from alembic import context

        return str(context.get_revision_argument() or "")
    except Exception:  # noqa: BLE001 — 알 수 없으면 아래(연쇄)로 보고 보호를 건다
        return ""


def downgrade() -> None:
    target = _destination()
    one_step = target == "-1" or target.startswith("0022")
    if not one_step:
        # 0021 이하로 내리는 연쇄 downgrade: 0020~0022의 보호를 맨 위에서 먼저 — 아래에서 거절되면 리비전이 0022에
        # 남고 이 표만 사라진 상태가 되므로, 같은 조건이면 여기서 아무것도 바꾸지 않고 멈춘다
        running = (
            op.get_bind()
            .execute(sa.text("SELECT count(*) FROM runs WHERE status = 'running' AND worker IS NOT NULL"))
            .scalar()
            or 0
        )
        if running:
            raise RuntimeError(
                f"이 판에서 시작한 실행 중인 결과 실행이 {running}건 있어 "
                f"0021로 내리지 않습니다 {CHAIN} — 서버를 멈추고 백업한 뒤 "
                "'python -m app.recovery recover'로 정리하고 'python -m app.recovery status'로 "
                "확인한 다음 다시 하세요 (docs/DEPLOY.md 10장). " + ONE_STEP_HINT
            )
        pending = op.get_bind().execute(sa.text("SELECT count(*) FROM users WHERE must_change_password")).scalar() or 0
        if pending:
            raise RuntimeError(
                f"임시 비밀번호 상태인 계정이 {pending}개 있어 0021로 내리지 않습니다 {CHAIN} — "
                "이용 중·이용 중지·만료 상태와 관계없이 셉니다 (0022의 보호와 같음, "
                "docs/DEPLOY.md 12-4). " + ONE_STEP_HINT
            )
    _short_lock_wait()
    op.drop_index("ix_keyjoin_jobs_owner_id", table_name="keyjoin_jobs")
    op.drop_table("keyjoin_jobs")
