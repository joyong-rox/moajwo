"""결과 실행의 요청 번호 — 같은 생성 요청의 재전송·동시 제출을 DB에서 하나로 (공통 key 연결부터)

- runs.request_kind: 요청 번호를 쓰는 작업 종류('keyjoin'). 다른 결과 실행은 비어 있다.
- runs.request_id: 화면이 만든 요청 번호(32자리 16진수). 고유 색인 uq_runs_request(user_id, request_kind, request_id) —
  같은 사용자·같은 종류에서 한 번호는 실행 하나. 서로 다른 프로세스가 동시에 '없음'을 읽어도 실행 행을 넣는 순간 하나만
  들어간다(나머지는 그 실행을 돌려받음). 비어 있는(NULL) 값은 서로 겹치지 않는다(SQLite·PostgreSQL 모두).
- runs.request_hash: 서버가 계산한 요청 내용의 정규형 해시(sha256 — 입력·시트 순서, key 열·순서, 가져올 열·이름·순서, 범위·기준
  입력, 행 결정, 정책). 같은 번호에 다른 내용이 오면 거절하는 근거. 화면·응답·요약에는 내보내지 않는다.
- 요청 기록은 실행 행과 함께 남는다 (실행 행을 지우지 않는 한 만료 없음).
- 이 판 전에 만든 공통 key 연결 실행(요약 JSON의 mode='keyjoin'·request_id)은 request_kind·request_id만 채우고 request_hash는
  비워 둔다 — 그 번호로 다시 보내면 내용을 비교할 수 없어 거절한다(새로 만들기). 아래 경우에는 아무것도 바꾸지 않고 멈춘다
  (지우거나 덮지 않는다 — 실행 번호만 알린다): 같은 사용자·같은 번호가 둘 이상, 'keyjoin'이 든 요약을 JSON으로 읽을 수 없음,
  key 연결 요약에 요청 번호가 없거나 모양이 다름.

downgrade: 고유 색인과 세 열을 지운다. 요청 번호는 요약 JSON에도 있어 0020 판의 코드가 그대로 읽는다(이 판의 '내용 비교'만 없어짐).

Revision ID: 0021_run_request_key
Revises: 0020_run_worker_lease
Create Date: 2026-10-27

"""

import json
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_run_request_key"
down_revision: str | None = "0020_run_worker_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND = "keyjoin"
REQUEST_ID = re.compile(r"[0-9a-f]{32}")
SHOWN = 20  # 멈출 때 알리는 실행 번호 수


def _short_lock_wait() -> None:
    """PostgreSQL: 표 잠금을 오래 기다리지 않는다 (0020과 같은 이유 — 넘으면 이 마이그레이션 전체가 되돌아간다)."""
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '5s'")


def _keyjoin_runs(conn: sa.Connection) -> list[tuple[int, int | None, str]]:
    """이 판 전의 key 연결 실행 (실행 번호, 사용자, 요청 번호). 채울 수 없는 행이 있으면 바꾸기 전에 멈춘다."""
    found: list[tuple[int, int | None, str]] = []
    unreadable: list[int] = []
    no_request: list[int] = []
    for run_id, user_id, summary in conn.execute(sa.text("SELECT id, user_id, summary FROM runs ORDER BY id")):
        raw = summary or ""
        if KIND not in raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            unreadable.append(run_id)
            continue
        if not isinstance(data, dict) or data.get("mode") != KIND:
            continue  # 'keyjoin' 글자가 다른 값(파일 이름 등)에만 든 다른 실행
        rid = data.get("request_id")
        if not isinstance(rid, str) or not REQUEST_ID.fullmatch(rid):
            no_request.append(run_id)
            continue
        found.append((run_id, user_id, rid))
    groups: dict[tuple[int, str], list[int]] = {}
    for run_id, user_id, rid in found:
        if user_id is not None:  # 사용자가 지워진 행(NULL)은 고유 색인에서 서로 겹치지 않는다
            groups.setdefault((user_id, rid), []).append(run_id)
    duplicated = sorted(r for ids in groups.values() if len(ids) > 1 for r in ids)
    problems = []
    if duplicated:
        problems.append(f"같은 사용자·같은 요청 번호의 key 연결 실행 {len(duplicated)}건 (실행 {duplicated[:SHOWN]})")
    if unreadable:
        problems.append(f"'keyjoin'이 든 요약을 읽을 수 없는 실행 {len(unreadable)}건 (실행 {unreadable[:SHOWN]})")
    if no_request:
        problems.append(f"요청 번호가 없거나 모양이 다른 key 연결 실행 {len(no_request)}건 (실행 {no_request[:SHOWN]})")
    if problems:
        raise RuntimeError(
            "0021로 올리지 않습니다 — 요청 번호를 채울 수 없는 실행 기록이 있습니다: " + " · ".join(problems) + ". "
            "아무것도 바꾸지 않았습니다(지우거나 덮지 않음). 백업한 뒤 그 실행 기록을 확인하고 정리 방법을 정하세요 (docs/DEPLOY.md 10장)."
        )
    return found


def upgrade() -> None:
    conn = op.get_bind()
    found = _keyjoin_runs(conn)  # 열을 더하기 전에 모두 확인한다 — 멈추면 아무것도 바뀌지 않는다
    _short_lock_wait()
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("request_kind", sa.String(16), nullable=True))
        batch.add_column(sa.Column("request_id", sa.String(32), nullable=True))
        batch.add_column(sa.Column("request_hash", sa.String(64), nullable=True))
    op.create_index("uq_runs_request", "runs", ["user_id", "request_kind", "request_id"], unique=True)
    for run_id, _user_id, rid in found:
        conn.execute(
            sa.text("UPDATE runs SET request_kind = :kind, request_id = :rid WHERE id = :id"),
            {"kind": KIND, "rid": rid, "id": run_id},
        )


def downgrade() -> None:
    _short_lock_wait()
    op.drop_index("uq_runs_request", table_name="runs")
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("request_hash")
        batch.drop_column("request_id")
        batch.drop_column("request_kind")
