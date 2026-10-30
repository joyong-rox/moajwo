"""계정·로그인·권한.

- 비밀번호: 표준 라이브러리 scrypt + 임의 salt (외부 인증 서비스·추가 패키지 없음)
- 세션: 브라우저에는 HttpOnly 쿠키로 임의 토큰, DB에는 토큰의 sha256만 저장
- 역할: user(일반 사용자) / owner(최고 관리자·소유자). 기록의 '소유자 열'(업로드·검토·실행·자동화를 만든 사람)로 조회
  범위를 나눈다. 일반 사용자는 자기 것만, 최고 관리자는 전체를 읽기 전용으로 본다. 결과 데이터(결과 파일·변경 기록)를
  직접 바꾸는 기능은 어떤 역할에도 없다.
- 계정 상태: pending(승인 대기) / active(이용 중) / rejected(가입 반려) / suspended(이용 중지). 업무 API는 active이면서
  is_active인 계정만 쓴다 (요청마다 확인).
- 인증 버전(users.auth_version): 비밀번호 변경·초기화, 반려·이용 중지는 같은 UPDATE에서 버전을 올린다. 세션은 발급
  시점의 버전을 저장하고, 요청마다 사용자 버전과 같아야 유효하다. 로그인은 비밀번호를 확인할 때 읽은 버전·이용 중 상태를
  조건으로 한 UPDATE와 세션 저장을 한 트랜잭션에서 한다 — 확인과 저장 사이에 계정이 바뀌면 세션을 만들지 않거나(조건
  불일치), 이미 만들었어도 버전이 달라 쓸 수 없다. 버전은 내려가지 않으므로 재개해도 되살아나지 않는다.
- 계정이 생기는 길 (0022): 공개 최초 설정·회원가입은 종료됐다. 설치별 최고 관리자는 설치 담당자의 초기화 명령
  (app/adminctl.py init — 로그인 아이디 ADMIN_USERNAME = admin, 권한은 역할 owner로만 판단)이 한 번 만들고, 일반
  사용자는 최고 관리자가 발급한다(서버가 만든 임시 비밀번호, 해시만 저장). 임시 비밀번호로 로그인한
  계정(users.must_change_password)은 본인 비밀번호로 바꾸기 전까지 비밀번호 변경·본인 조회·로그아웃만 할 수
  있고(require_full_access — 업무 API는 403), 만료 시각(users.temp_password_expires_at, config.TEMP_PASSWORD_HOURS)이
  지나면 그 비밀번호로 로그인하지 못한다(재발급).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.config import TEMP_PASSWORD_HOURS
from app.db import get_session
from app.models import AccountEvent, AppSetting, AuthSession, User

COOKIE_NAME = "moajwo_session"
SESSION_HOURS = 12
COOKIE_SECURE = os.environ.get("MOAJWO_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 5

ROLES = ("user", "owner")
ROLE_LABEL = {"user": "일반 사용자", "owner": "최고 관리자(소유자)"}
MANAGERS = ("owner",)  # 전체 기록 조회·관리 기능을 쓰는 역할 (예전 admin은 0016에서 user로 바뀜)
STATUSES = ("pending", "active", "rejected", "suspended")
STATUS_LABEL = {"pending": "승인 대기", "active": "이용 중", "rejected": "가입 반려", "suspended": "이용 중지"}
STATUS_MESSAGE = {
    "pending": "가입 신청을 검토 중입니다. 최고 관리자의 승인 후 로그인할 수 있습니다.",
    "rejected": "가입 신청이 반려되었습니다. 자세한 내용은 최고 관리자에게 문의하세요.",
    "suspended": "이용이 중지된 계정입니다. 최고 관리자에게 문의하세요.",
}
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD = 8
# 설치별 최고 관리자의 로그인 아이디. 권한은 이 이름이 아니라 역할(owner)로 판단한다 — 이름만으로 권한을 주지 않는다.
ADMIN_USERNAME = "admin"
RESERVED_USERNAMES = frozenset({ADMIN_USERNAME})  # 발급으로 만들 수 없는 아이디
# 최초 설정(설치 초기화)이 끝났다는 표시 (app_settings 기본 키 → 동시 초기화 중 하나만 성공).
# 마이그레이션 0015가 계정이 있는 기존 설치에도 넣어 설정 화면이 다시 열리지 않게 한다.
SETUP_KEY = "owner_setup"
# 임시 비밀번호: 헷갈리는 글자(0·O·1·l·I)를 뺀 글자 14자 — 사용자마다 secrets로 새로 만든다 (약 80비트)
TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"
TEMP_PASSWORD_LENGTH = 14
RESTRICTED_MESSAGE = "임시 비밀번호로 로그인한 상태입니다. 먼저 본인 비밀번호로 바꾼 뒤 다시 로그인하세요."
TEMP_EXPIRED_MESSAGE = "임시 비밀번호의 유효 기간이 지났습니다. 관리자에게 재발급을 요청하세요."

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


class AuthError(Exception):
    pass


class AccountStatusError(AuthError):
    """아이디·비밀번호는 맞지만 이용 중이 아닌 계정 (승인 대기·반려·중지). 비밀번호가 맞을 때만 쓴다."""

    def __init__(self, status: str) -> None:
        super().__init__(STATUS_MESSAGE.get(status, "이용할 수 없는 계정입니다. 최고 관리자에게 문의하세요."))
        self.status = status


class TempPasswordExpired(AuthError):
    """아이디·임시 비밀번호는 맞지만 유효 기간이 지남 — 관리자가 재발급해야 한다 (비밀번호가 맞을 때만 쓴다)."""

    def __init__(self) -> None:
        super().__init__(TEMP_EXPIRED_MESSAGE)


class SetupDone(Exception):
    """이미 초기화된 설치(계정 또는 초기화 표시가 있음) — 아무것도 만들지 않는다."""


def role_label(role: str) -> str:
    return ROLE_LABEL.get(role, f"{role}(확인 필요)")


def usable(user: User) -> bool:
    """업무 기능을 쓸 수 있는 계정: 이용 중(status)이면서 활성(is_active) — 둘 중 하나라도 아니면 막는다."""
    return user.is_active and user.status == "active"


def set_status(user: User, status: str, reason: str | None = None) -> None:
    """상태를 바꾸고 is_active를 같게 맞춘다. 상태 전이 검사는 호출한 쪽(auth_api의 조건부 갱신)에서 한다."""
    user.status = status
    user.is_active = status == "active"
    user.status_reason = reason
    user.status_changed_at = datetime.now(UTC)


# ---------------------------------------------------------------- 비밀번호


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    b64 = base64.b64encode
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def check_password_rule(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"비밀번호는 {MIN_PASSWORD}자 이상이어야 합니다.")
    if len(password) > 128:
        raise AuthError("비밀번호는 128자 이하로 정하세요.")


def check_username_rule(username: str) -> None:
    if not USERNAME_RE.match(username):
        raise AuthError("아이디는 영문 소문자·숫자·.-_ 3~32자로, 영문 소문자나 숫자로 시작해야 합니다.")


def check_username_available(username: str) -> None:
    """발급할 수 있는 아이디인지 — 관리자 전용 아이디(admin)는 발급하지 않는다 (중복은 DB 유일 조건과 호출한 쪽이
    본다)."""
    if username in RESERVED_USERNAMES:
        raise AuthError(f"'{username}'은(는) 관리자 전용 아이디라 발급할 수 없습니다.")


def generate_temp_password() -> str:
    return "".join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH))


def temp_password_expiry(now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) + timedelta(hours=TEMP_PASSWORD_HOURS)


def temp_password_expired(user: User, now: datetime | None = None) -> bool:
    """임시 비밀번호 상태인데 만료 시각이 지났는지 (만료 시각이 없는 임시 상태는 만료로 보지 않는다 — 설치 담당자
    복구 등)."""
    if not user.must_change_password or user.temp_password_expires_at is None:
        return False
    return _aware(user.temp_password_expires_at) <= (now or datetime.now(UTC))


# ---------------------------------------------------------------- 세션


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def login(db: Session, username: str, password: str) -> tuple[User, str]:
    """성공하면 (사용자, 쿠키에 넣을 토큰). 실패 사유는 아이디 존재 여부를 드러내지 않는다.

    아이디·비밀번호가 맞을 때만 이용 중이 아닌 계정의 상태(승인 대기·반려·중지)나 임시 비밀번호 만료를 알려 준다 (세션은
    만들지 않음). 임시 비밀번호가 유효하면 세션을 만들되, 그 계정은 비밀번호를 바꾸기 전까지
    제한된다(require_full_access).
    """
    now = datetime.now(UTC)
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    version = user.auth_version if user is not None else None  # 비밀번호 해시와 같은 행에서 읽은 버전
    if user is not None and user.locked_until is not None and _aware(user.locked_until) > now:
        raise AuthError(f"로그인 실패가 반복되어 잠시 잠겼습니다. {LOCK_MINUTES}분 뒤에 다시 시도하세요.")
    if user is None:
        verify_password(password, _dummy_hash())  # 없는 아이디도 같은 시간이 걸리게 (아이디 추측 방지)
    if user is None or not verify_password(password, user.password_hash):
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED_LOGINS:
                user.failed_logins = 0
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            db.commit()
        raise AuthError("아이디 또는 비밀번호가 올바르지 않습니다.")
    if not usable(user):
        user.failed_logins = 0
        db.commit()
        raise AccountStatusError(user.status if user.status != "active" else "suspended")
    if temp_password_expired(user, now):
        user.failed_logins = 0
        db.commit()
        raise TempPasswordExpired()
    # 확인한 그대로일 때만 세션을 만든다: 버전·이용 중 상태를 조건으로 한 UPDATE와 세션 저장을 한 트랜잭션에서.
    # 그 사이 비밀번호 초기화·변경·이용 중지가 커밋됐으면 0행 → 세션 없음 (SQLite 쓰기 잠금으로 직렬화)
    started = db.execute(
        update(User)
        .where(User.id == user.id, User.auth_version == version, User.status == "active", User.is_active.is_(True))
        .values(failed_logins=0, locked_until=None, last_login_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    if started != 1:
        db.rollback()
        raise AuthError("아이디 또는 비밀번호가 올바르지 않습니다.")
    db.execute(delete(AuthSession).where(AuthSession.expires_at < now))
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            token_hash=_token_hash(token),
            user_id=user.id,
            auth_version=version,
            created_at=now,
            expires_at=now + timedelta(hours=SESSION_HOURS),
        )
    )
    db.commit()
    db.refresh(user)
    return user, token


_DUMMY: list[str] = []


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_urlsafe(16)))
    return _DUMMY[0]


def logout(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        db.commit()


def end_sessions(db: Session, user_id: int) -> None:
    """비밀번호 변경·초기화·이용 중지·역할 변경 시 그 사용자의 로그인을 모두 끊는다.

    정리용이다 — 동시 요청에서의 무효화는 인증 버전(auth_version)이 보장한다.
    """
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


# ---------------------------------------------------------------- 요청 사용자·권한


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    display_name: str
    role: str
    must_change_password: bool = False  # 임시 비밀번호로 로그인한 제한된 상태 (업무 API 403)

    @property
    def is_manager(self) -> bool:
        return self.role in MANAGERS


def current_user(request: Request, db: Annotated[Session, Depends(get_session)]) -> CurrentUser:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        if row is not None and _aware(row.expires_at) > datetime.now(UTC):
            user = db.get(User, row.user_id)
            if user is not None and usable(user) and row.auth_version == user.auth_version:
                return CurrentUser(user.id, user.username, user.display_name, user.role, user.must_change_password)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다.")


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]


def require_full_access(user: CurrentUserDep) -> CurrentUser:
    """업무 API는 본인 비밀번호로 로그인한 계정만 쓴다 — 임시 비밀번호 상태는 비밀번호 변경·본인 조회·로그아웃
    (auth 라우터)만.
    화면 이동을 막는 것이 아니라 서버가 요청마다 거절한다 (main.py가 모든 업무 라우터에 건다)."""
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, RESTRICTED_MESSAGE)
    return user


FullUserDep = Annotated[CurrentUser, Depends(require_full_access)]


def require_roles(*roles: str):
    def dependency(user: CurrentUserDep) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "이 기능을 사용할 권한이 없습니다.")
        return user

    return dependency


ManagerDep = Annotated[CurrentUser, Depends(require_roles(*MANAGERS))]
OwnerDep = Annotated[CurrentUser, Depends(require_roles("owner"))]


def can_view(user: CurrentUser, owner_id: int | None) -> bool:
    """최고 관리자는 전체를, 일반 사용자는 자기 기록만 본다 (소유자 없는 이전 기록은 최고 관리자만)."""
    return user.is_manager or (owner_id is not None and owner_id == user.id)


def ensure_view(user: CurrentUser, owner_id: int | None, what: str) -> None:
    # 남의 기록은 있는지조차 알리지 않는다 (404)
    if not can_view(user, owner_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what}을(를) 찾을 수 없습니다.")


def ensure_own(user: CurrentUser, owner_id: int | None, what: str) -> None:
    """바꾸는 작업은 만든 사람만 한다 (최고 관리자도 남의 검토·실행을 대신 바꾸지 않음).
    계정 도입 전에 만든 기록(소유자 없음)은 최고 관리자가 이어서 처리할 수 있다."""
    ensure_view(user, owner_id, what)
    if owner_id != user.id and not (owner_id is None and user.is_manager):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"본인이 만든 {what}만 바꿀 수 있습니다 (다른 사용자는 조회만 가능)."
        )


# ---------------------------------------------------------------- 계정 만들기·관리 이력 (API와 설치 담당자 명령이 함께
# 쓴다)


def record_event(
    db: Session,
    actor: CurrentUser | None,
    target: User,
    action: str,
    before: str | None,
    after: str | None,
    *,
    reason: str | None = None,
    result: str = "ok",
    before_role: str | None = None,
    after_role: str | None = None,
    actor_self: bool = False,
) -> None:
    """관리 이력 1건 (커밋은 호출한 쪽에서 계정 변경과 함께). 비밀번호·해시·쿠키·토큰은 넣지 않는다.
    actor가 None이고 actor_self가 아니면 설치 담당자 명령·마이그레이션 같은 서버 쪽 작업이다."""
    db.add(
        AccountEvent(
            created_at=datetime.now(UTC),
            actor_id=target.id if actor_self else (actor.id if actor else None),
            actor_name=target.username if actor_self else (actor.username if actor else None),
            target_id=target.id,
            target_name=target.username,
            action=action,
            before_status=before,
            after_status=after,
            before_role=before_role,
            after_role=after_role,
            reason=reason,
            result=result,
        )
    )


def conflict(exc: IntegrityError | OperationalError) -> bool:
    """동시 요청끼리의 충돌인지: 기본 키·유일 제약 위반, 또는 SQLite 쓰기 잠금(database is locked).
    그 밖의 OperationalError(연결 실패·인증 실패 등)는 충돌이 아니다."""
    return isinstance(exc, IntegrityError) or "database is locked" in str(getattr(exc, "orig", exc))


def setup_done(db: Session) -> bool:
    """초기화가 끝났는지 — 계정이 하나라도 있거나 초기화 표시가 있으면 끝난 것이다 (소유자가 중지돼도 다시 열리지
    않는다)."""
    return (db.scalar(select(func.count(User.id))) or 0) > 0 or db.get(AppSetting, SETUP_KEY) is not None


def create_owner(
    db: Session,
    username: str,
    display_name: str,
    password: str,
    *,
    reason: str,
    must_change: bool = False,
    expires_at: datetime | None = None,
) -> User:
    """빈 설치에 최고 관리자 계정을 한 번만 만든다 (설치 담당자 명령 app.adminctl init이 부른다).

    동시에 두 번 실행돼도 소유자는 한 명만 생기도록, 계정과 같은 트랜잭션에서 고정 키(app_settings.key = SETUP_KEY, 기본
    키)를 넣는다. 늦은 쪽은 기본 키 충돌로 실패해 SetupDone이 되고 계정을 남기지 않는다 (프로세스 안의 잠금이 아니라 DB
    제약으로 막는다). 규칙 위반은 AuthError. 비밀번호는 해시로만 저장하고 어디에도 기록하지 않는다.
    """
    username = username.strip().lower()
    display_name = display_name.strip()
    check_username_rule(username)
    check_password_rule(password)
    if not display_name or len(display_name) > 64 or "\x00" in display_name or "\x00" in password:
        raise AuthError("표시 이름은 1~64자이고, 표시 이름·비밀번호에 NUL 문자를 쓸 수 없습니다.")
    if setup_done(db):
        raise SetupDone()
    password_hash = hash_password(password)  # 표시 행을 넣기 전에 — 해시(느림) 동안 DB 쓰기 잠금을 쥐지 않게
    try:
        db.add(AppSetting(key=SETUP_KEY, value=json.dumps({"owner": username}), updated_at=datetime.now(UTC)))
        db.flush()  # 다른 초기화가 먼저 넣었으면 여기서(또는 커밋에서) 기본 키 충돌
        owner = User(
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            role="owner",
            failed_logins=0,
            must_change_password=must_change,
            temp_password_expires_at=expires_at,
        )
        set_status(owner, "active")  # 최고 관리자는 승인 대기 없이 바로 이용한다
        db.add(owner)
        db.flush()
        record_event(db, None, owner, "setup", None, "active", after_role="owner", reason=reason)
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        if not conflict(exc):
            raise  # DB에 닿지 못함 — '이미 초기화됨'으로 보이지 않게
        raise SetupDone() from exc
    db.refresh(owner)
    return owner


def issue_temp_password(
    db: Session,
    actor: CurrentUser | None,
    user: User,
    *,
    action: str,
    reason: str,
    expected_version: int | None = None,
) -> tuple[str, datetime] | None:
    """새 임시 비밀번호를 만들어 해시만 저장하고 평문·만료 시각을 돌려준다 (평문은 저장·기록하지 않는다 — 호출한 쪽이
    한 번 보여 준다).

    같은 UPDATE에서 인증 버전을 올리고 세션을 끝내 기존 비밀번호·기존 임시 비밀번호·진행 중 로그인이 그 즉시 효력을 잃게
    한다. expected_version(화면이 본 인증 버전)이 지금 값과 다르면 아무것도 바꾸지 않고 None — 다른 재발급·상태 변경이
    먼저 반영된 것이다. 상태(이용 중·중지)는 바꾸지 않는다 — 중지된 계정은 재발급으로 활성화되지 않는다. 커밋은 호출한
    쪽에서.
    """
    plain = generate_temp_password()
    expires = temp_password_expiry()
    conditions = [User.id == user.id]
    if expected_version is not None:
        conditions.append(User.auth_version == expected_version)
    changed = db.execute(
        update(User)
        .where(*conditions)
        .values(
            password_hash=hash_password(plain),
            must_change_password=True,
            temp_password_expires_at=expires,
            failed_logins=0,
            locked_until=None,
            auth_version=User.auth_version + 1,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    if changed != 1:
        return None
    end_sessions(db, user.id)
    record_event(db, actor, user, action, user.status, user.status, reason=reason)
    return plain, expires
