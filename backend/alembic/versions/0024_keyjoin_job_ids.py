"""저장한 작업(keyjoin_jobs)의 번호를 다시 쓰지 않는다 — SQLite AUTOINCREMENT

- 문제: 0023의 keyjoin_jobs.id는 SQLite에서 AUTOINCREMENT 없는 INTEGER PRIMARY KEY(rowid)라, 가장 큰 번호의 행을
  지우면 다음에 저장한 행이 그 번호를 다시 받는다. 화면·API는 작업을 번호로만 가리키므로(삭제·갱신·맞추기), 다른 창에서
  지우고 새로 저장한 작업을 옛 화면의 확인·갱신이 같은 번호로 지우거나 덮을 수 있다 (2026-11-04 격리 재현 — 같은
  계정·같은 이름·같은 버전이어도). 번호가 생성 한 건을 가리키게 하면 옛 화면이 보낸 번호는 404가 된다.
- upgrade (SQLite): 표를 AUTOINCREMENT로 다시 만든다(batch 재생성 — 행·열·기본값·고유 제약·외래 키·색인 그대로
  복사). sqlite_sequence의 이 표 값은 남은 최대 번호로 맞춘다(빈 표면 0). 그 뒤 새 번호는 그 값보다 크고, SQLite가
  쓰기 잠금 안에서 sqlite_sequence를 올리므로 여러 프로세스에서도 지운 번호를 다시 주지 않는다.
  PostgreSQL: 0023의 id는 이미 시퀀스(nextval은 같은 값을 두 번 주지 않음)라 바꾸는 것 없음 — 리비전만 오른다.
- 남는 경계: 이 리비전 전에 지워진 번호 중 그때 남은 최대보다 큰 번호는 기록이 없어 알 수 없고, 한 번 다시 쓰일 수 있다
  (반영 전에 열어 둔 화면이 그런 번호를 들고 있을 때만 해당). 백업 복원·dbcopy로 행을 새 DB에 옮기면 순번은 그 DB의
  남은 최대 번호에서 다시 시작한다(docs/KEYJOIN.md).
- downgrade (0023): 같은 행을 AUTOINCREMENT 없이 다시 만든다 — 행·번호 그대로(번호 재사용 방지만 사라짐).
  0022 아래(0021 이하)로 가는 연쇄 downgrade는 0023의 보호(담당 있는 실행 중 행·임시 비밀번호 상태 계정이 있으면 거절)를
  **맨 위(0024)에서 먼저** 건다 — 0023으로 내려간 뒤 거절돼 중간에서 멈추지 않게(아무것도 바꾸지 않고 멈춤).
  이미지만 이전 판(0023까지 아는 코드)으로 바꾸는 롤백은 기동 때 alembic upgrade head가 0024를 몰라 멈춘다 —
  새 이미지로 'alembic downgrade 0023_keyjoin_jobs'를 먼저 (docs/DEPLOY.md).

Revision ID: 0024_keyjoin_job_ids
Revises: 0023_keyjoin_jobs
Create Date: 2026-11-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_keyjoin_job_ids"
down_revision: str | None = "0023_keyjoin_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ONE_STEP_HINT = "번호 재사용 방지만 내리려면 'alembic downgrade 0023_keyjoin_jobs'. 아무것도 바꾸지 않았습니다."
CHAIN = "(0022 아래로 가는 연쇄 downgrade를 맨 위에서 멈춤)"


def _rebuild(autoincrement: bool) -> None:
    """keyjoin_jobs를 같은 열·제약·색인으로 다시 만든다 (SQLite만 — 행은 번호까지 그대로 복사)."""
    with op.batch_alter_table("keyjoin_jobs", recreate="always", table_kwargs={"sqlite_autoincrement": autoincrement}):
        pass


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return  # PostgreSQL: id는 시퀀스 — 번호를 다시 쓰지 않는다
    _rebuild(True)
    # 번호를 넣어 복사하면 SQLite가 sqlite_sequence를 그 최대 번호로 올리지만, 빈 표에도 행이 있도록 맞춰 둔다
    top = bind.execute(sa.text("SELECT COALESCE(MAX(id), 0) FROM keyjoin_jobs")).scalar() or 0
    have = bind.execute(sa.text("SELECT seq FROM sqlite_sequence WHERE name = 'keyjoin_jobs'")).fetchone()
    if have is None:
        bind.execute(sa.text("INSERT INTO sqlite_sequence (name, seq) VALUES ('keyjoin_jobs', :top)"), {"top": top})
    elif (have[0] or 0) < top:
        bind.execute(sa.text("UPDATE sqlite_sequence SET seq = :top WHERE name = 'keyjoin_jobs'"), {"top": top})


def _destination() -> str:
    """이번 downgrade 명령의 목표 리비전 인자 (alembic downgrade <목표>)."""
    try:
        from alembic import context

        return str(context.get_revision_argument() or "")
    except Exception:  # noqa: BLE001 — 알 수 없으면 아래(연쇄)로 보고 보호를 건다
        return ""


def downgrade() -> None:
    target = _destination()
    within = target == "-1" or target.startswith("0023") or target.startswith("0022")
    if not within:
        # 0021 이하로 내리는 연쇄 downgrade: 0023이 거는 보호를 맨 위에서 먼저 — 여기서 표를 다시 만든 뒤 0023에서
        # 거절되면 리비전이 0023에 남으므로, 같은 조건이면 아무것도 바꾸지 않고 멈춘다
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
    if op.get_bind().dialect.name != "sqlite":
        return
    _rebuild(False)
