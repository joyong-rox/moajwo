"""계정·로그인·권한.

- 비밀번호: 표준 라이브러리 scrypt + 임의 salt (외부 인증 서비스·추가 패키지 없음)
- 세션: 브라우저에는 HttpOnly 쿠키로 임의 토큰, DB에는 토큰의 sha256만 저장
- 역할: user(일반) / admin(관리자) / owner(소유자). 기록의 '소유자 열'(업로드·검토·실행·자동화를 만든 사람)로
  조회 범위를 나눈다. 일반 사용자는 자기 것만, 관리자·소유자는 전체를 읽기 전용으로 본다.
  결과 데이터(결과 파일·변경 기록)를 직접 바꾸는 기능은 어떤 역할에도 없다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AuthSession, User

COOKIE_NAME = "moajwo_session"
SESSION_HOURS = 12
COOKIE_SECURE = os.environ.get("MOAJWO_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 5

ROLES = ("user", "admin", "owner")
ROLE_LABEL = {"user": "일반 사용자", "admin": "관리자", "owner": "소유자"}
MANAGERS = ("admin", "owner")
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD = 8

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


class AuthError(Exception):
    pass


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


# ---------------------------------------------------------------- 세션


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def login(db: Session, username: str, password: str) -> tuple[User, str]:
    """성공하면 (사용자, 쿠키에 넣을 토큰). 실패 사유는 아이디 존재 여부를 드러내지 않는다."""
    now = datetime.now(UTC)
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    if user is not None and user.locked_until is not None and _aware(user.locked_until) > now:
        raise AuthError(f"로그인 실패가 반복되어 잠시 잠겼습니다. {LOCK_MINUTES}분 뒤에 다시 시도하세요.")
    if user is None:
        verify_password(password, _dummy_hash())  # 없는 아이디도 같은 시간이 걸리게 (아이디 추측 방지)
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED_LOGINS:
                user.failed_logins = 0
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
            db.commit()
        raise AuthError("아이디 또는 비밀번호가 올바르지 않습니다.")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    db.execute(delete(AuthSession).where(AuthSession.expires_at < now))
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            token_hash=_token_hash(token),
            user_id=user.id,
            created_at=now,
            expires_at=now + timedelta(hours=SESSION_HOURS),
        )
    )
    db.commit()
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
    """비밀번호 변경·계정 비활성화 시 그 사용자의 로그인을 모두 끊는다."""
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


# ---------------------------------------------------------------- 요청 사용자·권한


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    display_name: str
    role: str

    @property
    def is_manager(self) -> bool:
        return self.role in MANAGERS


def current_user(request: Request, db: Annotated[Session, Depends(get_session)]) -> CurrentUser:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        if row is not None and _aware(row.expires_at) > datetime.now(UTC):
            user = db.get(User, row.user_id)
            if user is not None and user.is_active:
                return CurrentUser(user.id, user.username, user.display_name, user.role)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다.")


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]


def require_roles(*roles: str):
    def dependency(user: CurrentUserDep) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "이 기능을 사용할 권한이 없습니다.")
        return user

    return dependency


ManagerDep = Annotated[CurrentUser, Depends(require_roles(*MANAGERS))]
OwnerDep = Annotated[CurrentUser, Depends(require_roles("owner"))]


def can_view(user: CurrentUser, owner_id: int | None) -> bool:
    """관리자·소유자는 전체를, 일반 사용자는 자기 기록만 본다 (소유자 없는 이전 기록은 관리자·소유자만)."""
    return user.is_manager or (owner_id is not None and owner_id == user.id)


def ensure_view(user: CurrentUser, owner_id: int | None, what: str) -> None:
    # 남의 기록은 있는지조차 알리지 않는다 (404)
    if not can_view(user, owner_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what}을(를) 찾을 수 없습니다.")


def ensure_own(user: CurrentUser, owner_id: int | None, what: str) -> None:
    """바꾸는 작업은 만든 사람만 한다 (관리자도 남의 검토·실행을 대신 바꾸지 않음).
    계정 도입 전에 만든 기록(소유자 없음)은 관리자·소유자가 이어서 처리할 수 있다."""
    ensure_view(user, owner_id, what)
    if owner_id != user.id and not (owner_id is None and user.is_manager):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"본인이 만든 {what}만 바꿀 수 있습니다 (다른 사용자는 조회만 가능)."
        )
