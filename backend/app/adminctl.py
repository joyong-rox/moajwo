"""설치 담당자용 관리자 계정 명령 (docs/DEPLOY.md 12장 · README '계정과 권한').

공개 최초 설정(/auth/setup)과 공개 회원가입(/auth/signup)은 종료됐다 — 계정은 이 명령과 최고 관리자의 계정 발급으로만
생긴다.

  python -m app.adminctl init [--display-name 이름] (--password-file 파일 | --password-stdin | --generate) [--yes]
      빈 설치(계정 0개·초기화 표시 없음)에 최고 관리자 계정(아이디 admin, 역할 owner)을 한 번만 만든다. 계정이나 초기화
      표시가 이미 있으면 아무것도 만들거나 덮지 않는다(종료 2). 두 번 동시에 실행돼도 하나만 성공한다(DB 기본 키).
  python -m app.adminctl transition [--apply] [--yes]
      기존 설치의 유일한 owner 계정의 로그인 아이디를 admin으로 바꾼다. 기본은 확인만(아무것도 바꾸지 않음), --apply로
      적용. 내부 번호·비밀번호 해시·자료 소유·실행/검토/분석 이력은 그대로이고, 그 관리자의 세션만 끝난다(다시 로그인).
      admin이 다른 계정에 쓰이고 있거나(상태와 무관) owner가 없거나 여럿이면 바꾸지 않는다(종료 2). 과거 이력은 그대로
      두고 전환 이벤트를 더한다.
  python -m app.adminctl recover [--username admin] (--password-file 파일 | --password-stdin | --generate) [--yes]
      관리자 비밀번호 분실 복구: 그 owner 계정의 비밀번호를 재설정하고 세션을 모두 끝낸다(번호·자료 그대로, 이력 기록).
      --generate면 서버가 만든 임시 비밀번호로 두어 첫 로그인에서 본인 비밀번호로 바꾸게 한다.

- 대상 DB는 앱과 같은 설정(DATABASE_URL·MOAJWO_DB_MODE)이다 — 외부 DB는 앱 계정 주소면 충분하다(데이터 쓰기만 한다. 예:
  DATABASE_URL=postgresql:///?service=moajwo-app). 실행 전에 DB 종류·이름·리비전을 보이고, --yes가 없으면 'yes' 입력을
  받는다. 리비전이 head가 아니거나(스키마 준비 안 됨 — 먼저 앱을 기동하거나 alembic upgrade head) SQLite 파일이 없으면
  아무것도 만들지 않는다: 연결 실패·빈 DB를 '새 설치'로 보지 않는다. 재시작·재배포·환경 변수 재적용은 이 명령을 다시
  부르지 않으므로 비밀번호를 되돌리지 않는다.
- 비밀번호는 명령 인자로 받지 않는다(셸 기록·프로세스 목록에 남음): 본인만 읽는 파일(첫 줄), 표준 입력 한 줄, 또는
  대화식 숨김 입력(두 번). --generate는 서버가 만든 임시 비밀번호를 이 터미널에 한 번만 보인다(저장·기록 없음). 그 밖의
  출력·이력에 비밀번호·해시는 없다. Render 무료 서비스에는 셸이 없으므로 소유자 PC에서 보호된 직접 DB 연결로
  실행한다(docs/EXTERNAL_DB.md). 종료 코드: 0 반영(또는 바꿀 것 없음) · 2 조건이 맞지 않아 바꾸지 않음 · 1 예상치 못한
  오류
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.account_markers import GENERATED_TEMP_PASSWORD_NOTE
from app.auth import (
    ADMIN_USERNAME,
    SETUP_KEY,
    AuthError,
    SetupDone,
    check_password_rule,
    create_owner,
    end_sessions,
    generate_temp_password,
    hash_password,
    record_event,
    temp_password_expiry,
)
from app.config import DATABASE_URL, DB_MODE
from app.db import SessionLocal
from app.models import (
    AnalysisRun,
    AppSetting,
    AuthSession,
    IntegrationRun,
    ReviewSession,
    SourceFile,
    User,
    ValidationRun,
)

PROG = "python -m app.adminctl"
EXIT_OK, EXIT_STOP, EXIT_ERROR = 0, 2, 1
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Stop(Exception):
    """조건이 맞지 않아 바꾸지 않음 — 사유만 알리고 종료 2."""


def out(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- 대상 DB 확인


def head_revision() -> str | None:
    """이 코드의 마이그레이션 head (alembic 스크립트 폴더에서 읽는다)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    ini = BACKEND_DIR / "alembic.ini"
    cfg = Config(str(ini)) if ini.exists() else Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def db_identity(db: Session) -> dict[str, object]:
    """대상이 어느 DB인지 (주소·비밀번호 없이): 종류, 이름/파일, 리비전. SQLite 파일이 없으면 연결 전에 멈춘다."""
    url = make_url(DATABASE_URL)
    info: dict[str, object] = {"mode": DB_MODE, "dialect": url.get_backend_name()}
    if url.get_backend_name() == "sqlite":
        path = Path(url.database or "")
        info["file"] = str(path)
        if not path.exists():
            raise Stop(
                f"SQLite 파일이 없습니다: {path} — 빈 DB를 새로 만들지 않습니다. "
                "먼저 앱을 한 번 기동하거나 'alembic upgrade head'로 스키마를 만드세요."
            )
    else:
        name, user = db.execute(text("SELECT current_database(), current_user")).one()
        info["database"], info["db_user"] = name, user
    try:
        info["revision"] = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:  # noqa: BLE001 — 표가 없음(스키마 준비 안 됨)
        db.rollback()
        info["revision"] = None
    return info


def require_ready(db: Session) -> dict[str, object]:
    info = db_identity(db)
    head = head_revision()
    info["head"] = head
    if info["revision"] != head:
        raise Stop(
            f"스키마가 준비되지 않았습니다 (DB 리비전 {info['revision'] or '없음'} ≠ 이 코드의 head {head}) — "
            "먼저 앱을 기동하거나 "
            "'alembic upgrade head'를 실행하세요. 아무것도 만들지 않았습니다."
        )
    return info


def describe(info: dict[str, object]) -> str:
    where = info.get("file") or f"database={info.get('database')} user={info.get('db_user')}"
    return f"대상 DB: {info['dialect']} ({info['mode'] or 'sqlite'}) {where} · 리비전 {info['revision']}"


def confirm(args: argparse.Namespace, what: str) -> None:
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise Stop(f"{what} — 대화식 입력이 아니면 --yes를 붙이세요 (확인 없이 바꾸지 않습니다).")
    try:
        answer = input(f"{what} — 계속하려면 yes를 입력하세요: ").strip().lower()
    except EOFError:
        answer = ""
    if answer != "yes":
        raise Stop("취소했습니다 — 아무것도 바꾸지 않았습니다.")


# ---------------------------------------------------------------- 비밀번호 입력 (인자로 받지 않는다)


def read_password(args: argparse.Namespace) -> tuple[str, bool]:
    """(비밀번호, 서버가 만든 임시값인지). 파일·표준 입력·대화식 입력 중 하나. 규칙 위반이면 Stop."""
    chosen = [n for n in ("password_file", "password_stdin", "generate") if getattr(args, n, None)]
    if len(chosen) > 1:
        raise Stop("--password-file · --password-stdin · --generate 가운데 하나만 쓰세요.")
    if args.generate:
        return generate_temp_password(), True
    if args.password_file:
        path = Path(args.password_file)
        if not path.is_file():
            raise Stop(f"비밀번호 파일이 없습니다: {path}")
        if os.name != "nt" and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise Stop(f"비밀번호 파일은 본인만 읽을 수 있어야 합니다 (chmod 600): {path}")
        password = path.read_text(encoding="utf-8").splitlines()[0] if path.read_text(encoding="utf-8").strip() else ""
    elif args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        if not sys.stdin.isatty():
            raise Stop(
                "비밀번호를 받을 방법이 없습니다 — 대화식이 아니면 --password-file 또는 --password-stdin 또는 "
                "--generate를 쓰세요."
            )
        try:
            password = getpass.getpass("비밀번호 (입력이 보이지 않습니다): ")
            if getpass.getpass("비밀번호 확인: ") != password:
                raise Stop("비밀번호 확인이 일치하지 않습니다 — 아무것도 바꾸지 않았습니다.")
        except EOFError as exc:
            raise Stop("비밀번호 입력이 끊겼습니다 — 아무것도 바꾸지 않았습니다.") from exc
    try:
        check_password_rule(password)
    except AuthError as exc:
        raise Stop(str(exc)) from exc
    return password, False


def show_generated(password: str) -> None:
    out(
        "임시 비밀번호 (이 터미널에서 한 번만 보입니다 — 저장·기록되지 않으며 첫 로그인에서 본인 비밀번호로 "
        "바꿔야 합니다):"
    )
    out(f"  {password}")


# ---------------------------------------------------------------- 명령


def cmd_init(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        info = require_ready(db)
        out(describe(info))
        users = db.scalar(select(func.count(User.id))) or 0
        marker = db.get(AppSetting, SETUP_KEY)
        if users or marker is not None:
            raise Stop(
                f"이미 초기화된 설치입니다 (계정 {users}개 · 초기화 표시 {'있음' if marker else '없음'}) — "
                "아무것도 만들거나 덮지 않았습니다. 관리자 비밀번호를 잃었으면 'recover'를 쓰세요."
            )
        password, generated = read_password(args)
        confirm(args, f"이 DB에 최고 관리자 계정 '{ADMIN_USERNAME}'을(를) 만듭니다")
        try:
            owner = create_owner(
                db, ADMIN_USERNAME, args.display_name, password,
                reason="설치 초기화 명령(app.adminctl init) — 최고 관리자 계정"
                + (" · " + GENERATED_TEMP_PASSWORD_NOTE if generated else ""),
                must_change=generated, expires_at=temp_password_expiry() if generated else None,
            )  # fmt: skip
        except AuthError as exc:
            raise Stop(str(exc)) from exc
        except SetupDone as exc:
            raise Stop("그 사이 다른 초기화가 먼저 끝났습니다 — 아무것도 만들지 않았습니다 (계정은 하나뿐).") from exc
        out(
            f"최고 관리자 계정을 만들었습니다: {owner.username} "
            f"(번호 {owner.id}, 역할 {owner.role}, 상태 {owner.status})."
        )
        if generated:
            show_generated(password)
        else:
            out("비밀번호는 출력하지 않습니다. 이 아이디로 로그인한 뒤 사용자 계정을 발급하세요.")
    return EXIT_OK


def _owner_counts(db: Session, uid: int) -> dict[str, int]:
    pairs = (
        ("업로드 파일", SourceFile.uploaded_by),
        ("검증 실행", ValidationRun.created_by),
        ("검토", ReviewSession.created_by),
        ("통합 실행", IntegrationRun.user_id), ("분석 실행", AnalysisRun.created_by), ("세션", AuthSession.user_id),
    )  # fmt: skip
    return {label: db.scalar(select(func.count()).where(col == uid)) or 0 for label, col in pairs}


def cmd_transition(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        info = require_ready(db)
        out(describe(info))
        owners = list(db.scalars(select(User).where(User.role == "owner").order_by(User.id)))
        if not owners:
            raise Stop("owner 역할 계정이 없습니다 — 전환할 대상이 없어 바꾸지 않습니다 (빈 설치면 'init').")
        if len(owners) > 1:
            names = ", ".join(f"{u.username}(번호 {u.id}, {u.status})" for u in owners)
            raise Stop(
                f"owner 역할 계정이 {len(owners)}개입니다: {names} — 자동으로 고르지 않습니다. "
                "먼저 하나로 정리한 뒤 다시 하세요."
            )
        owner = owners[0]
        taken = db.scalar(select(User).where(User.username == ADMIN_USERNAME))
        counts = _owner_counts(db, owner.id)
        out(
        f"owner 계정: {owner.username} (번호 {owner.id}, 상태 {owner.status}, 인증 버전 {owner.auth_version}) · "
        "보존되는 기록: "
            + " · ".join(f"{k} {v}" for k, v in counts.items()))  # fmt: skip
        if owner.username == ADMIN_USERNAME:
            out(f"이미 '{ADMIN_USERNAME}'입니다 — 바꿀 것이 없습니다.")
            return EXIT_OK
        if taken is not None:
            raise Stop(
            f"'{ADMIN_USERNAME}' 아이디를 다른 계정(번호 {taken.id}, 역할 {taken.role}, 상태 {taken.status})이 "
            "쓰고 있어 충돌 — 바꾸지 않습니다. "
                       "그 계정을 지우거나 합치거나 승격하지 않습니다. 먼저 그 계정의 용도를 확인하세요.")  # fmt: skip
        marker = db.get(AppSetting, SETUP_KEY)
        marker_value = None
        if marker is not None:
            try:
                marker_value = json.loads(marker.value)
            except ValueError:
                marker_value = None
        marker_owned = isinstance(marker_value, dict) and marker_value.get("owner") == owner.username
        out(
            f"전환 계획: 로그인 아이디 {owner.username} → {ADMIN_USERNAME} · "
            "내부 번호·비밀번호 해시·자료 소유·이력 그대로 · "
            f"이 관리자의 세션 {counts['세션']}개 종료(다시 로그인) · 초기화 표시(owner_setup) "
            f"{'owner 이름 갱신' if marker_owned else '그대로'} · 전환 이력 1건 추가"
        )
        if not args.apply:
            out("확인만 했습니다 (--apply가 없어 바꾸지 않음).")
            return EXIT_OK
        confirm(args, "위 계획대로 전환합니다")
        old = owner.username
        changed = db.execute(
            update(User)
            .where(User.id == owner.id, User.username == old, User.role == "owner")
            .values(username=ADMIN_USERNAME, auth_version=User.auth_version + 1)
            .execution_options(synchronize_session=False)
        ).rowcount
        if changed != 1:
            db.rollback()
            raise Stop("그 사이 계정이 바뀌어 전환하지 않았습니다 — 다시 확인하세요.")
        if marker is not None and isinstance(marker_value, dict) and marker_value.get("owner") == old:
            marker_value.update(
                {"owner": ADMIN_USERNAME, "renamed_from": old, "renamed_at": datetime.now(UTC).isoformat()}
            )
            marker.value = json.dumps(marker_value)
            marker.updated_at = datetime.now(UTC)
        end_sessions(db, owner.id)
        db.refresh(owner)
        record_event(
            db, None, owner, "owner_renamed", owner.status, owner.status, before_role="owner", after_role="owner",
            reason=f"관리자 아이디 전환: {old} → {ADMIN_USERNAME} (내부 번호·비밀번호·자료 그대로, 관리자 세션 종료)",
        )  # fmt: skip
        db.commit()
        out(
            f"전환했습니다: {old} → {ADMIN_USERNAME} (번호 {owner.id}). "
            f"관리자는 '{ADMIN_USERNAME}'으로 다시 로그인합니다. 다른 계정의 세션은 그대로입니다."
        )
    return EXIT_OK


def cmd_recover(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        info = require_ready(db)
        out(describe(info))
        target = db.scalar(select(User).where(User.username == args.username))
        if target is None or target.role != "owner":
            owners = [u.username for u in db.scalars(select(User).where(User.role == "owner"))]
            raise Stop(
                f"'{args.username}'은(는) owner 역할 계정이 아닙니다 (owner 계정: {', '.join(owners) or '없음'}) — "
                "아이디가 아직 전환되지 않았으면 'transition' 뒤에, "
                "또는 --username으로 그 owner 아이디를 지정하세요."
            )
        password, generated = read_password(args)
        confirm(args, f"'{target.username}'(번호 {target.id})의 비밀번호를 재설정하고 세션을 모두 끝냅니다")
        db.execute(
            update(User)
            .where(User.id == target.id)
            .values(
                password_hash=hash_password(password),
                failed_logins=0,
                locked_until=None,
                must_change_password=generated,
                temp_password_expires_at=temp_password_expiry() if generated else None,
                auth_version=User.auth_version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        end_sessions(db, target.id)
        db.refresh(target)
        # 생성 비밀번호면 사유 끝에 표식(app/account_markers.py 한 곳에서 정의) — 관찰 도구가 '발급'으로 세는 근거
        record_event(db, None, target, "admin_recover", target.status, target.status,
                     reason="설치 담당자의 복구 명령(app.adminctl recover) — 비밀번호 재설정 · 세션 종료"
                     + (" · " + GENERATED_TEMP_PASSWORD_NOTE if generated else ""),
                     )  # fmt: skip
        db.commit()
        out(f"'{target.username}'의 비밀번호를 재설정하고 세션을 끝냈습니다 (번호·자료·이력 그대로).")
        if generated:
            show_generated(password)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog=PROG, description="설치 담당자용 관리자 계정 명령 (초기화·아이디 전환·복구)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def password_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--password-file", help="비밀번호가 첫 줄에 든, 본인만 읽는 파일")
        p.add_argument("--password-stdin", action="store_true", help="표준 입력 첫 줄을 비밀번호로")
        p.add_argument(
            "--generate", action="store_true", help="서버가 임시 비밀번호를 만들어 한 번 보인다 (첫 로그인에서 변경)"
        )

    p_init = sub.add_parser("init", help="빈 설치에 최고 관리자 계정(admin)을 한 번 만든다")
    p_init.add_argument("--display-name", default="최고 관리자", help="표시 이름 (기본 '최고 관리자')")
    password_options(p_init)
    p_init.add_argument("--yes", action="store_true", help="확인 질문 없이 진행")
    p_tr = sub.add_parser("transition", help="유일한 owner 계정의 로그인 아이디를 admin으로 (기본은 확인만)")
    p_tr.add_argument("--apply", action="store_true", help="실제로 바꾼다")
    p_tr.add_argument("--yes", action="store_true", help="확인 질문 없이 진행")
    p_rc = sub.add_parser("recover", help="관리자 비밀번호 재설정·세션 종료 (분실 복구)")
    p_rc.add_argument("--username", default=ADMIN_USERNAME, help="대상 owner 아이디 (기본 admin)")
    password_options(p_rc)
    p_rc.add_argument("--yes", action="store_true", help="확인 질문 없이 진행")
    args = ap.parse_args(argv)
    try:
        return {"init": cmd_init, "transition": cmd_transition, "recover": cmd_recover}[args.cmd](args)
    except Stop as exc:
        out(f"[바꾸지 않음] {exc}")
        return EXIT_STOP
    except Exception as exc:  # noqa: BLE001 — 주소·비밀번호를 담을 수 있는 원문은 내보내지 않는다
        out(
            f"[오류] {type(exc).__name__}: 처리하지 못했습니다 — DB 연결·권한을 확인하세요 "
            "(자세한 원문은 출력하지 않음)."
        )
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
